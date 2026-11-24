"""Followers: imports, reactions, profile checks, forgetting a person, and the measured calls (no Meta, no go.tany.im)."""
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
TMP = tempfile.mkdtemp()
os.environ['IGFANS_DIR'] = TMP
import igfans  # noqa: E402

A = 'demo.page'
igfans.connected = lambda: {A: '111'}
REAL = igfans._transport


def imp(followers, following=None, mark='0' * 16, **more):
    return igfans.handle('POST', ['import'], {'account': A, 'followers': followers, 'following': following or {}, 'mark': mark, **more})


class Base(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(TMP, ignore_errors=True)
        igfans._busy.clear()
        igfans.PACE = 0
        igfans._transport = lambda i, w, p: igfans._fake(w, p)

    def tearDown(self):
        igfans._transport = REAL


class Imports(Base):
    def test_same_export_counts_once(self):
        self.assertEqual(imp({'a': 1})[1]['same'], False)
        self.assertEqual(imp({'a': 1})[1]['same'], True)
        self.assertEqual(len(igfans.imports(A)), 1)

    def test_bad_lists_are_refused(self):
        for f in ({}, {'../x': 1}, {'A': 1}, {'a': 'x'}, {'a': True}, ['a'], {'__proto__': 1}):
            self.assertEqual(imp(f)[0], 400, f)
        for acc in ('', '..', '.', '../../x', 'a/b'):
            self.assertEqual(igfans.add_import({'account': acc, 'followers': {'a': 1}, 'mark': '0' * 16})[0], 400, acc)
        self.assertEqual(igfans.add_import({'account': A, 'followers': {'a': 1}, 'mark': '../../../etc'})[0], 400)

    def test_only_the_owner_can_open_the_files(self):
        imp({'a': 1})
        self.assertEqual(stat.S_IMODE(os.stat(TMP).st_mode), 0o700)
        path = os.path.join(TMP, 'accounts', A, 'imports', igfans.imports(A)[0]['id'] + '.json')
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_trust_and_remove(self):
        imp({'a': 1}, partial='short')
        i = igfans.imports(A)[0]['id']
        self.assertEqual(igfans.waiting(A), [])
        igfans.handle('POST', ['imports', i, 'trust'], {'account': A})
        self.assertEqual(igfans.waiting(A), ['a'])
        igfans.handle('POST', ['imports', i, 'remove'], {'account': A})
        self.assertEqual(igfans.imports(A), [])


class Reactions(Base):
    def test_daily_read_counts_people_without_text(self):
        r = igfans.sync(A)
        self.assertTrue(r['ok'], r)
        ev = igfans.flat(A)
        self.assertEqual({k: sum(1 for e in ev if e['k'] == k) for k in ('comment', 'dm', 'tag')}, {'comment': 12, 'dm': 1, 'tag': 2})
        n = len(igfans._usage_rows(0))
        igfans.sync(A)  # nothing new: the post whose count did not change is not read again
        kinds = [x['kind'] for x in igfans._usage_rows(0)[n:]]
        self.assertNotIn('comments', kinds)
        self.assertEqual(len(igfans.flat(A)), 15)

    def test_one_part_failing_keeps_the_rest_and_the_old_numbers(self):
        igfans.sync(A)

        def half(i, w, p):
            if w == 'media':
                return {'ok': False, 'error': 'needs permission', 'code': 10, 'meter': {'calls': 1}}
            return igfans._fake(w, p)
        igfans._transport = half
        r = igfans.sync(A)
        self.assertEqual(list(r['problems']), ['comments'])
        self.assertEqual(sum(1 for e in igfans.flat(A) if e['k'] == 'comment'), 12)

    def test_comments_without_names_are_read_again_once_allowed(self):
        bare = [True]

        def answer(i, w, p):
            if w == 'comments' and bare[0]:
                return {'ok': True, 'meter': {'calls': 1}, 'after': '', 'items': [{'id': 'c1', 'u': '', 'at': igfans.now_ms()}]}
            return igfans._fake(w, p)
        igfans._transport = answer
        igfans.sync(A)
        self.assertEqual(sum(1 for x in igfans.flat(A) if x['k'] == 'comment'), 0)
        bare[0] = False
        igfans.sync(A)
        self.assertEqual(sum(1 for x in igfans.flat(A) if x['k'] == 'comment'), 12)

    def test_one_post_that_fails_does_not_hold_up_the_rest(self):
        def answer(i, w, p):
            r = igfans._fake(w, p)
            if w == 'media':
                r['items'] = [{**r['items'][0], 'id': str(n)} for n in (1, 2, 3)]
            if w == 'comments' and p['media'] == '2':
                return {'ok': False, 'error': 'odd', 'code': 1, 'meter': {'calls': 1}}
            return r
        igfans._transport = answer
        r = igfans.sync(A)
        self.assertEqual(sum(1 for x in igfans.flat(A) if x['k'] == 'comment'), 24)
        self.assertIn('1 posts', r['problems']['comments'])

    def test_stops_before_the_limit(self):
        def near(i, w, p):
            return {**igfans._fake(w, p), 'meter': {'calls': 1, 'app': 71, 'buc': 1, 'wait': 0}}
        igfans._transport = near
        r = igfans.sync(A)
        self.assertEqual(sum(x['calls'] for x in igfans._usage_rows(0)), 1)
        self.assertIn('comments', r['problems'])


class Profiles(Base):
    def go(self, n):
        igfans.set_enrich(A, n)
        return igfans.enrich(A, n)

    def test_fans_first_and_not_asked_again(self):
        igfans.sync(A)
        imp({'user0': 5, 'user1': 6, 'zoe': 9, 'old': 1})
        self.assertEqual(igfans.waiting(A)[-2:], ['zoe', 'old'])
        r = self.go(50)
        self.assertEqual(r['done'], 7)
        self.assertEqual(self.go(50)['done'], 0)
        self.assertEqual(igfans.view(A)['enrich']['waiting'], 0)

    def test_pauses_at_seventy_percent_and_goes_on(self):
        imp({'u%d' % i: i + 1 for i in range(0, 20, 2)})
        calls = []

        def hot(i, w, p):
            calls.append(p['u'])
            return {**igfans._fake(w, p), 'meter': {'calls': 1, 'app': 71 if len(calls) == 3 else 5, 'buc': 0, 'wait': 0}}
        igfans._transport = hot
        self.assertEqual(self.go(10)['done'], 3)
        st = igfans.status(A)['enrich']
        self.assertEqual(st['left'], 7)
        self.assertGreater(st['pauseUntil'], igfans.now_ms())
        self.assertEqual(igfans.enrich(A, 7)['done'], 7)
        self.assertEqual(len(set(calls)), 10)

    def test_a_limit_answer_loses_nobody(self):
        imp({'a': 1, 'b': 2})
        igfans._transport = lambda i, w, p: {'ok': False, 'error': 'limit', 'code': 4, 'meter': {'calls': 1, 'app': 100, 'wait': 90}}
        self.go(2)
        self.assertEqual(len(igfans.waiting(A)), 2)
        self.assertGreater(igfans.status(A)['enrich']['pauseUntil'], igfans.now_ms() + 80 * 60000)

    def test_missing_permission_stops_at_once(self):
        imp({'a': 1, 'b': 2})
        igfans._transport = lambda i, w, p: {'ok': False, 'error': 'perm', 'code': 10, 'meter': {'calls': 1}}
        self.go(2)
        self.assertEqual(len(igfans._usage_rows(0)), 1)
        self.assertEqual(igfans.profiles(A), {})

    def test_lookup(self):
        self.assertEqual(igfans.discover(A, '@Some.One2')[1]['found'], True)
        self.assertEqual(igfans.discover(A, 'some1')[1]['found'], False)
        self.assertEqual(igfans.discover(A, 'a b')[0], 400)


class Forget(Base):
    def test_one_person_is_gone_and_stays_gone(self):
        igfans.sync(A)
        imp({'user1': 1, 'user2': 2}, {'user1': 3})
        igfans.set_enrich(A, 9)
        igfans.enrich(A, 9)
        igfans.forget('user1')
        igfans.sync(A)
        imp({'user1': 1, 'user2': 2, 'x': 3}, mark='1' * 16)
        blob = ''
        for root, _, files in os.walk(TMP):
            for f in files:
                with open(os.path.join(root, f)) as h:
                    blob += h.read()
        self.assertNotIn('"user1"', blob)
        self.assertIn('"user2"', blob)
        self.assertNotIn('user1', [e['u'] for e in igfans.flat(A)] + igfans.waiting(A))


    def test_erased_during_a_run_is_neither_asked_about_nor_written(self):
        imp({'a': 9, 'b': 8, 'c': 7})
        asked = []

        def odd(i, w, p):
            asked.append(p['u'])
            if p['u'] == 'a':
                igfans.forget('b')  # waits its turn in this run
            if p['u'] == 'c':
                igfans.forget('c')  # while its own check is under way
            return {'ok': False, 'error': 'odd thing about ' + p['u'] + '.', 'code': 999, 'meter': {'calls': 1}}
        igfans._transport = odd
        igfans.set_enrich(A, 3)
        igfans.enrich(A, 3)
        self.assertEqual(asked, ['a', 'c'])
        self.assertEqual(list(igfans.profiles(A)), ['a'])
        self.assertNotIn('about c', json.dumps([igfans._usage_rows(0), igfans.status(A)]))

    def test_choosing_an_account_waits_for_the_erasure_list(self):
        imp({'a': 1})
        t = threading.Thread(target=igfans.use, args=(A,))
        with igfans._lock:
            t.start()
            t.join(0.2)
            self.assertTrue(t.is_alive())
            igfans.forget('a')
        t.join(5)
        self.assertEqual(len(igfans.settings()['forgotten']), 1)


class ReadOnlyGets(Base):
    def test_reading_makes_nothing_and_only_known_accounts_are_read(self):
        for name in ('nobody', '..', '../../x', ''):
            self.assertEqual(igfans.view(name)['account'], A)
            self.assertEqual(igfans.status(name)['account'], A)
        self.assertFalse(os.path.exists(os.path.join(TMP, 'accounts')))
        self.assertEqual(igfans.accounts(), [A])


class Usage(Base):
    def test_calls_are_measured(self):
        imp({'u%d' % i: 1 for i in range(1000)})
        igfans.sync(A)
        u = igfans.usage(A, 1000)
        self.assertEqual(u['month'], 9)  # profile, media, comments, tags, dms, four audience counts
        self.assertEqual(u['perThousand'], 9.0)
        self.assertEqual((u['app'], u['buc']), (12, 3))

    def test_failed_calls_are_counted_too(self):
        igfans._transport = lambda i, w, p: {'ok': False, 'error': 'limit', 'code': 4, 'meter': {'calls': 1, 'app': 99}}
        igfans.sync(A)
        u = igfans.usage(A, 10)
        self.assertEqual((u['month'], u['kinds'][0]['failed'], u['peakApp']), (1, 1, 99))

    def test_hourly_guess_counts_only_calls_of_that_limit(self):
        now = igfans.now_ms()
        os.makedirs(igfans._dir(), exist_ok=True)
        with open(os.path.join(igfans._dir(), 'usage.jsonl'), 'a', encoding='utf-8') as f:
            for i in range(40):  # reads of the account: another limit
                f.write(json.dumps({'at': now - 900000 + i, 'account': A, 'kind': 'comments', 'ok': True, 'code': 0, 'calls': 1, 'app': None, 'buc': 1, 'wait': 0, 'ms': 1}) + '\n')
            for i in range(50):
                f.write(json.dumps({'at': now - 600000 + i, 'account': A, 'kind': 'discover', 'ok': True, 'code': 0, 'calls': 1, 'app': (i + 1) * 2 // 5, 'buc': None, 'wait': 0, 'ms': 1}) + '\n')
        u = igfans.usage(A, 1000)
        self.assertEqual((u['app'], u['buc'], u['limitGuess']), (20, 1, 250))

    def test_settings_and_csv(self):
        self.assertEqual(igfans.handle('PUT', ['settings'], {'weights': {'comment': 1, 'dm': 1, 'tag': -1}})[0], 400)
        self.assertEqual(igfans.handle('PUT', ['settings'], {'weights': {'comment': 1, 'dm': 0, 'tag': 2.5}})[0], 200)
        self.assertEqual(igfans.settings()['weights']['tag'], 2.5)
        c, r = igfans.handle('POST', ['csv'], {'name': '../../x', 'text': 'a'})
        self.assertEqual(c, 200)
        self.assertNotIn('/', igfans.utilities.get_file(r['file'])['name'])

    def test_unknown_account(self):
        self.assertEqual(igfans.handle('POST', ['sync'], {'account': 'nobody'})[0], 404)
        self.assertEqual(igfans.handle('POST', ['imports', '../../../x', 'remove'], {'account': A})[0], 404)
        json.dumps(igfans.view(A))


if __name__ == '__main__':
    unittest.main()
