"""Transcribe's auto-recording: when it starts, when it ends, and what it must not do. Run: python3 -m unittest desk.tests.test_auto_record

The helper app is a small stand-in script: it says who uses the microphone from a file, and writes a little sound when told to record.
Nothing listens to this Mac.
"""
import datetime
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(os.path.dirname(HERE), 'transcribe', 'server.py')

FAKE_APP = r'''
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
if sys.argv[1] == 'who':
    try:
        print(open(os.path.join(here, 'who.json')).read())
    except OSError:
        print(json.dumps({'ok': True, 'locked': False, 'users': []}))
elif sys.argv[1] in ('status', 'setup'):
    json.dump({'microphone': 'allowed'}, open(os.path.join(sys.argv[2], 'mic-permission.json'), 'w'))
else:
    folder = sys.argv[2]
    state = open(os.path.join(here, 'mic.txt')).read() if os.path.exists(os.path.join(here, 'mic.txt')) else 'recording'
    if state == 'recording':
        with open(os.path.join(folder, 'audio.pcm'), 'ab') as f:
            f.write(b'\x10\x03' * 16000)
    json.dump({'state': state, 'error': 'permission' if state == 'error' else None, 'started': time.time(), 'updated': time.time() + 9999, 'pid': 0},
              open(os.path.join(folder, 'mic.json'), 'w'))
'''
ENV = ('TRANSCRIBE_DIR', 'TRANSCRIBE_ENGINE', 'TRANSCRIBE_TAP_CMD', 'TRANSCRIBE_WATCH_CMD', 'TRANSCRIBE_CALENDAR', 'TRANSCRIBE_NO_NOTIFY')


class AutoRecord(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        app = os.path.join(cls.tmp, 'app.py')
        with open(app, 'w') as f:
            f.write(FAKE_APP)
        os.environ.update(TRANSCRIBE_DIR=os.path.join(cls.tmp, 'transcribe'), TRANSCRIBE_ENGINE=os.path.join(cls.tmp, 'engine'),
                          TRANSCRIBE_TAP_CMD='/usr/bin/true', TRANSCRIBE_WATCH_CMD=f'{sys.executable} {app}',
                          TRANSCRIBE_CALENDAR=os.path.join(cls.tmp, 'upcoming.json'), TRANSCRIBE_NO_NOTIFY='1')
        sys.modules.pop('auto', None)
        spec = importlib.util.spec_from_file_location('tr_server_auto', SERVER)
        cls.s = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.s)
        cls.a = cls.s.auto

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        sys.modules.pop('auto', None)
        for k in ENV:
            os.environ.pop(k, None)

    def setUp(self):
        shutil.rmtree(self.s.DATA, ignore_errors=True)
        os.makedirs(self.s.SESSIONS)
        for n in ('who.json', 'mic.txt', 'upcoming.json'):
            if os.path.exists(os.path.join(self.tmp, n)):
                os.remove(os.path.join(self.tmp, n))
        self.a.now_state.update(since=None, app='', event=None, last_mic=0.0, problem='', sizes=None, next=None)
        self.told = []
        self.a.notify = lambda title, text: self.told.append(title)
        self.real_free = self.a.shutil.disk_usage
        self.a.save(on=True)

    def tearDown(self):
        self.a.shutil.disk_usage = self.real_free

    def mic(self, *bundles, locked=False, ok=True):
        with open(os.path.join(self.tmp, 'who.json'), 'w') as f:
            json.dump({'ok': ok, 'locked': locked, 'users': [{'bundle': b, 'name': b} for b in bundles]}, f)

    def event(self, start=-60, end=1800, eid='u_1', **more):
        iso = lambda t: datetime.datetime.fromtimestamp(time.time() + t).astimezone().isoformat(timespec='seconds')
        return {'id': eid, 'title': 'Call with Ura', 'start': iso(start), 'end': iso(end), 'allDay': False, 'meet': True, 'mine': True, **more}

    def calendar(self, *events):
        with open(os.path.join(self.tmp, 'upcoming.json'), 'w') as f:
            json.dump({'events': list(events)}, f)

    def run_until_started(self):
        t = time.time()
        self.a.look(t)
        return self.a.look(t + 11)

    def wait_mic(self, sid):
        for _ in range(50):
            if os.path.exists(os.path.join(self.s.sdir(sid), 'mic.json')):
                return
            time.sleep(0.1)
        self.fail('the stand-in did not record')

    def test_starts_after_ten_seconds_of_a_call_in_a_meeting(self):
        self.calendar(self.event())
        self.mic('com.google.Chrome.helper')
        t = time.time()
        self.assertEqual(self.a.look(t), 'hearing')
        self.assertEqual(self.a.look(t + 5), 'hearing')
        self.assertEqual(self.a.look(t + 11), 'started')
        m = self.a.active()
        self.assertEqual((m['title'], m['mode'], m['auto'], m['event']['id'], m['language']), ('Call with Ura', 'meeting', True, 'u_1', 'auto'))
        self.assertEqual(self.told, ['Recording started. Tell the others.'])
        self.assertEqual(self.a.look(), 'recording')          # and only one
        self.assertEqual(len(self.s.list_sessions()), 1)

    def test_a_short_blip_does_not_start(self):
        self.calendar(self.event())
        t = time.time()
        self.mic('us.zoom.xos')
        self.a.look(t)
        self.mic()
        self.a.look(t + 5)
        self.mic('us.zoom.xos')
        self.assertEqual(self.a.look(t + 11), 'hearing')

    def test_what_must_not_start(self):
        self.mic('com.google.Chrome.helper')
        cases = {'no calendar': [], 'no link': [self.event(meet=False)], 'all day': [self.event(allDay=True)], 'an invitation nobody answered': [self.event(mine=False)],
                 'too early': [self.event(start=600, end=1200)], 'long over': [self.event(start=-9000, end=-2400)]}
        for name, evs in cases.items():
            self.calendar(*evs)
            self.assertEqual(self.run_until_started(), 'idle', name)
        self.calendar(self.event())
        for name, kw in {'dictation': {'b': ['com.typeless.app']}, 'Fathom': {'b': ['video.fathom.electron.helper']}, 'nobody': {'b': []},
                         'locked': {'b': ['us.zoom.xos'], 'locked': True}, 'macOS will not say': {'b': ['us.zoom.xos'], 'ok': False}}.items():
            self.mic(*kw.pop('b'), **kw)
            self.assertEqual(self.run_until_started(), 'waiting', name)
        self.mic('us.zoom.xos')
        self.a.save(on=False)
        self.assertEqual(self.run_until_started(), 'idle')
        self.assertEqual(self.s.list_sessions(), [])

    def test_five_minutes_early_and_thirty_late(self):
        self.mic('us.zoom.xos')
        for a, b in ((240, 1800), (-3600, -1700)):
            self.calendar(self.event(start=a, end=b, eid=f'u_{a}'))
            self.assertEqual(self.run_until_started(), 'started')
            self.a.discard(self.a.active()['id'])

    def test_a_recording_by_hand_is_left_alone(self):
        self.calendar(self.event())
        self.mic('com.google.Chrome.helper')
        self.s.create_session('By hand', 'ja', 'recording', 'recording', 'mic')
        self.assertEqual(self.run_until_started(), 'waiting')

    def test_ends_two_minutes_after_the_call_and_a_pause_does_not_split_it(self):
        self.calendar(self.event())
        self.mic('us.zoom.xos')
        self.assertEqual(self.run_until_started(), 'started')
        sid = self.a.active()['id']
        self.wait_mic(sid)
        t = time.time()
        self.mic()
        self.a.look(t + 60)
        self.mic('us.zoom.xos')
        self.a.look(t + 100)
        self.mic()
        self.a.look(t + 200)
        self.assertIsNotNone(self.a.active())
        self.a.look(t + 221)
        self.assertIsNone(self.a.active())
        m = self.s.load_meta(sid)
        self.assertEqual((m['status'], m['auto_end']), ('queued', 'call over'))
        self.assertTrue(m['mic_start'])
        self.assertGreater(self.s.file_size(self.s.pcm_path(sid)), 0)
        self.mic('us.zoom.xos')                                # joining again is a new recording
        self.assertEqual(self.run_until_started(), 'started')

    def test_stop_keeps_it_and_does_not_start_again(self):
        self.calendar(self.event())
        self.mic('us.zoom.xos')
        self.run_until_started()
        sid = self.a.active()['id']
        self.wait_mic(sid)
        self.assertEqual(self.a.act({'op': 'stop', 'id': sid})[0], 200)
        self.assertEqual(self.s.load_meta(sid)['status'], 'queued')
        self.assertEqual(self.run_until_started(), 'idle')
        self.assertEqual(self.a.act({'op': 'stop', 'id': sid})[0], 409)

    def test_discard_erases_it_and_does_not_start_again(self):
        self.calendar(self.event())
        self.mic('us.zoom.xos')
        self.run_until_started()
        sid = self.a.active()['id']
        self.wait_mic(sid)
        self.assertEqual(self.a.act({'op': 'discard', 'id': sid})[0], 200)
        self.assertFalse(os.path.exists(self.s.sdir(sid)))
        self.assertEqual(self.run_until_started(), 'idle')

    def test_discard_never_touches_a_recording_by_hand(self):
        sid = self.s.create_session('By hand', 'ja', 'recording', 'recording', 'mic')['id']
        self.assertEqual(self.a.act({'op': 'discard', 'id': sid})[0], 409)
        self.assertFalse(self.a.discard(sid))
        self.assertTrue(os.path.exists(self.s.sdir(sid)))

    def test_four_hours_at_most(self):
        self.calendar(self.event(end=6 * 3600))
        self.mic('us.zoom.xos')
        self.run_until_started()
        sid = self.a.active()['id']
        self.wait_mic(sid)
        self.a.look(time.time() + 4 * 3600 + 60)
        self.assertEqual(self.s.load_meta(sid)['auto_end'], 'four hours')
        self.assertEqual(self.run_until_started(), 'idle')

    def test_nothing_to_record_with_is_told_once(self):
        self.calendar(self.event())
        self.mic('us.zoom.xos')
        with open(os.path.join(self.tmp, 'mic.txt'), 'w') as f:
            f.write('error')
        self.run_until_started()
        sid = self.a.active()['id']
        self.wait_mic(sid)
        self.s.write_json(os.path.join(self.s.sdir(sid), 'tap.json'), {'state': 'error', 'error': 'permission', 'updated': time.time()})
        self.a.look()
        self.assertIsNone(self.a.active())
        self.assertEqual(self.told[-1], 'This meeting is not being recorded')
        self.assertEqual(self.run_until_started(), 'idle')

    def test_one_side_missing_goes_on_and_is_told_once(self):
        self.calendar(self.event())
        self.mic('us.zoom.xos')
        self.run_until_started()
        sid = self.a.active()['id']
        self.wait_mic(sid)
        self.s.write_json(os.path.join(self.s.sdir(sid), 'tap.json'), {'state': 'error', 'error': 'permission', 'updated': time.time()})
        for _ in range(3):
            self.a.look(time.time() + 30)
        self.assertEqual(self.told.count('Only one side is being recorded'), 1)
        self.assertEqual(self.a.view()['problem'], 'screen')
        self.assertIsNotNone(self.a.active())

    def test_a_full_disk_does_not_record(self):
        self.calendar(self.event())
        self.mic('us.zoom.xos')
        self.a.shutil.disk_usage = lambda p: shutil._ntuple_diskusage(1, 1, 1)
        self.assertEqual(self.run_until_started(), 'full')
        self.assertEqual(self.run_until_started(), 'idle')

    def test_a_restart_of_the_recorder_goes_on_with_the_same_one(self):
        self.calendar(self.event())
        self.mic('us.zoom.xos')
        self.run_until_started()
        sid = self.a.active()['id']
        self.wait_mic(sid)
        self.a.now_state.update(last_mic=0.0, sizes=None)
        self.mic()
        self.assertEqual(self.a.look(), 'recording')
        self.assertEqual(self.a.active()['id'], sid)

    def test_the_microphone_is_not_opened_again_without_a_call(self):
        self.calendar(self.event())
        self.mic('us.zoom.xos')
        self.run_until_started()
        sid = self.a.active()['id']
        self.wait_mic(sid)
        mic = os.path.join(self.s.sdir(sid), 'mic.json')
        self.s.write_json(mic, {'state': 'recording', 'updated': 0, 'started': time.time()})  # the app is gone
        self.s.update_meta(sid, mic_started_at=0)
        opened = []
        real, self.a.start_mic = self.a.start_mic, opened.append
        try:
            for kw in ({'b': []}, {'b': ['us.zoom.xos'], 'locked': True}, {'b': ['us.zoom.xos'], 'ok': False}):
                self.mic(*kw.pop('b'), **kw)
                self.a.look()
            self.assertEqual(opened, [])
            self.mic('us.zoom.xos')
            self.a.look()
            self.assertEqual(opened, [sid])
            self.assertEqual(self.told[-1], 'Still recording. Tell the others.')
        finally:
            self.a.start_mic = real

    def test_switching_off_ends_the_recording_and_keeps_it(self):
        self.calendar(self.event())
        self.mic('us.zoom.xos')
        self.run_until_started()
        sid = self.a.active()['id']
        self.wait_mic(sid)
        self.a.act({'op': 'set', 'on': False})
        self.assertEqual((self.s.load_meta(sid)['status'], self.s.load_meta(sid)['auto_end']), ('queued', 'switched off'))

    def test_the_meeting_going_on_wins_over_the_next_one(self):
        self.calendar(self.event(start=-1800, end=120, eid='now'), self.event(start=120, end=3600, eid='next'))
        self.assertEqual(self.a.pick(time.time())['id'], 'now')
        self.assertEqual(self.a.pick(time.time() + 130)['id'], 'next')

    def test_settings(self):
        self.a.act({'op': 'set', 'on': False, 'language': 'en'})
        self.assertEqual((self.a.view()['on'], self.a.view()['language']), (False, 'en'))
        self.a.act({'op': 'set', 'language': 'xx'})
        self.assertEqual(self.a.view()['language'], 'en')
        self.assertEqual(self.a.act({'op': 'settings', 'pane': '../x'})[0], 400)
        self.assertEqual(self.a.view(check=True)['permissions']['microphone'], 'allowed')

    def test_off_by_default(self):
        os.remove(self.a.path())
        self.assertFalse(self.a.load()['on'])


if __name__ == '__main__':
    unittest.main()
