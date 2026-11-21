"""Should we meet? — the rules, the cost and the pre-read doc (no Claude, no Google)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import meetcheck  # noqa: E402


def good():
    return {'q1': {'text': '来期の採用を2人にするか3人にするか'},
            'q2': {'choice': 'live', 'why': '予算と現場の意見を、その場ですり合わせたい'},
            'q3': {'decider': '自分', 'proposal': '2人にする', 'options': '3人：予算が足りない',
                   'required': ['自分', '人事', '現場リーダー'], 'optional': ['経理']},
            'q4': {'len': 25, 'worth': 'yes'}, 'q5': {'people': ['人事']}}


class Rules(unittest.TestCase):
    def test_all_pass_means_meet(self):
        self.assertEqual(meetcheck.verdict(good()), ('meet', ''))

    def test_a_topic_is_not_a_decision(self):
        for text in ('採用について', '進捗の共有', 'About the launch', 'Weekly sync', ''):
            a = good()
            a['q1']['text'] = text
            self.assertEqual(meetcheck.verdict(a), ('skip', 'q1'), text)

    def test_decisions_in_english_pass(self):
        for text in ('Whether to launch in March', 'Launch in March or April?', 'Which vendor to pick'):
            a = good()
            a['q1']['text'] = text
            self.assertEqual(meetcheck.verdict(a)[0], 'meet', text)

    def test_text_can_decide_it(self):
        a = good()
        a['q2']['choice'] = 'text'
        self.assertEqual(meetcheck.verdict(a), ('skip', 'q2'))
        a = good()
        a['q2']['why'] = ' '
        self.assertEqual(meetcheck.verdict(a), ('skip', 'q2'))

    def test_one_decider_and_a_proposal(self):
        for decider in ('', '全員', 'Me, Ken'):
            a = good()
            a['q3']['decider'] = decider
            self.assertEqual(meetcheck.verdict(a), ('skip', 'q3'), decider)
        a = good()
        a['q3']['proposal'] = ''
        self.assertEqual(meetcheck.verdict(a), ('skip', 'q3'))
        a = good()
        a['q3']['required'] = [str(i) for i in range(9)]
        self.assertEqual(meetcheck.verdict(a), ('skip', 'q3'))

    def test_worth_it(self):
        a = good()
        a['q4']['worth'] = 'no'
        self.assertEqual(meetcheck.verdict(a), ('skip', 'q4'))
        a = good()
        a['q4']['len'] = 90
        self.assertEqual(meetcheck.verdict(a), ('skip', 'q4'))

    def test_first_failure_is_named(self):
        a = good()
        a['q2']['choice'] = 'text'
        a['q4']['worth'] = 'no'
        self.assertEqual(meetcheck.verdict(a), ('skip', 'q2'))

    def test_cost(self):
        c = meetcheck.cost(good(), 3000)
        self.assertEqual(c, {'people': 4, 'minutes': 25, 'personMinutes': 100, 'yen': 5000})
        self.assertEqual(meetcheck.cost({'q4': {'len': 15}}, 3000)['people'], 2)  # never fewer than two

    def test_people_from_text(self):
        self.assertEqual(meetcheck.people('Ken, Aya、 Bo\n'), ['Ken', 'Aya', 'Bo'])


class Doc(unittest.TestCase):
    def test_japanese_doc_has_every_part(self):
        page, lang, _ = meetcheck.doc_html({'title': '採用の人数', 'when': 'Nov 25', 'answers': good()}, 3000)
        self.assertEqual(lang, 'ja')
        for part in ('決めること', '決める人', '提案する結論', '必須', '任意', '¥5,000', '進め方', '記録', 'アクション', '決まらなかったこと', '事前に話す人'):
            self.assertIn(part, page)

    def test_english_doc_and_escaping(self):
        a = {'q1': {'text': 'Whether to ship <b>now</b>'}, 'q2': {'choice': 'live', 'why': 'x'},
             'q3': {'decider': 'Me', 'proposal': 'Ship', 'required': ['Me', 'Ken']}, 'q4': {'len': 15, 'worth': 'yes'}}
        page, lang, _ = meetcheck.doc_html({'title': 'Ship it', 'answers': a}, 3000)
        self.assertEqual(lang, 'en')
        self.assertIn('Proposed answer', page)
        self.assertIn('&lt;b&gt;now&lt;/b&gt;', page)
        self.assertNotIn('<b>now', page)

    def test_no_doc_for_a_meeting_that_failed(self):
        os.environ['MEETCHECK_FAKE'] = '1'
        try:
            a = good()
            a['q4']['worth'] = 'no'
            code, body = meetcheck.handle('doc', {'record': {'title': 'x', 'answers': a}})
            self.assertEqual(code, 400)
            code, body = meetcheck.handle('doc', {'record': {'title': 'x', 'answers': a, 'override': 'the client asked'}})
            self.assertEqual(code, 200)
            self.assertIn('docs.google.com', body['url'])
        finally:
            del os.environ['MEETCHECK_FAKE']

    def test_test_server_never_reads_keys(self):
        os.environ['DESK_DATA_DIR'] = '/tmp/claude/x'
        try:
            self.assertFalse(meetcheck.status()['connected'])
            code, body = meetcheck.handle('draft', {'title': 'Weekly sync'})
            self.assertEqual(code, 400)
            self.assertIn('Write the answers yourself', body['error'])
        finally:
            del os.environ['DESK_DATA_DIR']


if __name__ == '__main__':
    unittest.main()
