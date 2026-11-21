"""Transcribe: Me / Them for meetings, A / B for one mic. Run: python3 -m unittest desk.tests.test_transcribe

The speech engine, the speaker engine and the Mac-audio app are small fake scripts, so nothing is downloaded or recorded.
"""
import array
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(os.path.dirname(HERE), 'transcribe', 'server.py')

# whisper-cli stand-in: one line per file. Loud audio says "them", quiet audio says "me".
FAKE_WHISPER = r'''#!/usr/bin/env python3
import array, json, sys, wave
a = sys.argv
wav, stem = a[a.index('-f') + 1], a[a.index('-of') + 1]
with wave.open(wav) as w:
    n = w.getnframes()
    x = array.array('h'); x.frombytes(w.readframes(n))
level = sum(abs(v) for v in x) / max(len(x), 1)
text = 'see you on friday' if level > 4000 else 'good morning' if level > 100 else ''
items = [{'offsets': {'from': 0, 'to': int(n / 16)}, 'text': text}] if text else []
json.dump({'transcription': items}, open(stem + '.json', 'w'))
'''

FAKE_DIAR = r'''#!/usr/bin/env python3
print('Started')
print('0.000 -- 1.900 speaker_01')
print('2.100 -- 4.000 speaker_00')
'''


def tone(seconds, amp):
    x = array.array('h', [amp if i % 2 else -amp for i in range(int(seconds * 16000))])
    return x.tobytes()


def write_exe(path, body):
    with open(path, 'w') as f:
        f.write(body)
    os.chmod(path, 0o755)


class TranscribeSpeakers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        engine = os.path.join(cls.tmp, 'engine')
        os.makedirs(os.path.join(engine, 'models'))
        os.makedirs(os.path.join(engine, 'diar', 'bin'))
        write_exe(os.path.join(engine, 'whisper-cli'), FAKE_WHISPER.replace('#!/usr/bin/env python3', '#!' + sys.executable))
        write_exe(os.path.join(engine, 'diar', 'bin', 'sherpa-onnx-offline-speaker-diarization'),
                  FAKE_DIAR.replace('#!/usr/bin/env python3', '#!' + sys.executable))
        for name in ('models/ggml-large-v3-turbo.bin', 'models/ggml-silero-v5.1.2.bin', 'diar/segmentation.onnx', 'diar/embedding.onnx'):
            open(os.path.join(engine, name), 'w').close()
        os.environ.update(TRANSCRIBE_DIR=os.path.join(cls.tmp, 'data'), TRANSCRIBE_ENGINE=engine, TRANSCRIBE_TAP_CMD='/usr/bin/true',
                          LIFEOS_DESK_DATA=os.path.join(cls.tmp, 'desk'), LIFEOS_DESK_API='http://127.0.0.1:9')  # tasks go to a file
        spec = importlib.util.spec_from_file_location('tr_server', SERVER)
        cls.s = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.s)
        os.makedirs(cls.s.SESSIONS, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        for k in ('TRANSCRIBE_DIR', 'TRANSCRIBE_ENGINE', 'TRANSCRIBE_TAP_CMD', 'LIFEOS_DESK_DATA', 'LIFEOS_DESK_API'):
            os.environ.pop(k, None)

    def run_session(self, sid):
        self.s.queue_transcription(sid)
        self.s.process(sid)
        return self.s.load_meta(sid)

    def test_meeting_puts_me_and_them_in_time_order(self):
        s = self.s
        meta = s.create_session('Call', 'en', 'recording', 'recording', 'meeting')
        sid = meta['id']
        mic_start = time.time()
        s.update_meta(sid, mic_start=mic_start)
        s.append_chunk(sid, 0, tone(3, 1000))                     # Me: 3 s, quiet
        with open(os.path.join(s.sdir(sid), 'them.pcm'), 'wb') as f:
            f.write(tone(1, 8000))                                 # Them: 1 s, loud, starting 2 s later
        s.write_json(os.path.join(s.sdir(sid), 'tap.json'),
                     {'state': 'stopped', 'started': mic_start + 2.0, 'updated': time.time(), 'apps': ['zoom.us']})
        meta = self.run_session(sid)
        self.assertEqual(meta['status'], 'done')
        self.assertAlmostEqual(meta['them_offset'], 2.0, places=2)
        self.assertEqual(meta['tap_apps'], ['zoom.us'])
        rows = s.assemble(sid)
        self.assertEqual([(r['spk'], r['text']) for r in rows], [('me', 'good morning'), ('them', 'see you on friday')])
        self.assertAlmostEqual(rows[1]['start'], 2.0, places=1)
        self.assertTrue(rows[1]['key'].startswith('t'))
        # the mix is as long as the longer side and plays instead of the mic alone
        self.assertEqual(s.file_size(s.mix_path(sid)), 3 * 32000)
        self.assertEqual(s.play_path(sid), s.mix_path(sid))
        self.assertEqual(s.public_meta(s.load_meta(sid))['speakers'], {'me': 'Me', 'them': 'Them'})

    def test_rename_and_fix_one_line(self):
        s = self.s
        sid = s.create_session('Call', 'en', 'recording', 'recording', 'meeting')['id']
        s.update_meta(sid, mic_start=time.time(), them_offset=0.0)
        s.append_chunk(sid, 0, tone(1, 1000))
        meta = self.run_session(sid)
        s.update_meta(sid, speakers={'them': 'Aki'})
        self.assertEqual(s.speakers_of(s.load_meta(sid)), {'me': 'Me', 'them': 'Aki'})
        key = s.assemble(sid)[0]['key']
        s.write_json(os.path.join(s.sdir(sid), 'speakers.json'), {key: 'them'})
        self.assertEqual(s.assemble(sid)[0]['spk'], 'them')
        self.assertEqual(s.transcript_lines(sid, False), ['Aki: good morning'])
        self.assertEqual(meta['status'], 'done')

    def test_in_person_splits_into_a_and_b(self):
        s = self.s
        sid = s.create_session('Talk', 'en', 'recording', 'recording', 'inperson')['id']
        s.append_chunk(sid, 0, tone(4, 1000))
        # two lines from the fake engine: split the audio into two parts so each gets its own line
        s.write_json(os.path.join(s.sdir(sid), 'plan.json'), [[0, 2 * 32000], [2 * 32000, 4 * 32000]])
        meta = self.run_session(sid)
        self.assertEqual(meta['status'], 'done')
        self.assertIsNone(meta.get('diar_error'))
        rows = s.assemble(sid)
        # the first voice heard is A, whatever number the engine gave it
        self.assertEqual([r['spk'] for r in rows], ['A', 'B'])
        self.assertEqual(s.transcript_lines(sid, True), ['[00:00:00] Speaker A: good morning', '[00:00:02] Speaker B: good morning'])

    def test_people_count_sets_the_split_and_the_names(self):
        s = self.s
        meta = s.create_session('Room', 'ja', 'recording', 'recording', 'inperson', 'mac', 3)
        self.assertEqual(meta['people'], 3)
        self.assertEqual(list(s.speakers_of(meta)), ['A', 'B', 'C'])
        self.assertEqual(s.public_meta(s.create_session('Room', 'ja', 'recording', 'recording', 'inperson', 'mac', 9))['people'], 2)
        seen = {}
        real = s.subprocess.Popen

        def spy(cmd, *a, **k):
            if cmd and cmd[0].endswith('speaker-diarization'):
                seen['cmd'] = cmd
            return real(cmd, *a, **k)
        s.append_chunk(meta['id'], 0, tone(1, 1000))
        s.subprocess.Popen = spy
        try:
            self.run_session(meta['id'])
        finally:
            s.subprocess.Popen = real
        self.assertIn('--clustering.num-clusters=3', seen['cmd'])

    def test_split_an_old_recording_later(self):
        s = self.s
        sid = s.create_session('Old', 'ja', 'recording', 'recording', 'mic')['id']
        s.append_chunk(sid, 0, tone(1, 1000))
        self.run_session(sid)
        self.assertNotIn('spk', s.assemble(sid)[0])
        s.update_meta(sid, mode='inperson', status='queued')
        s.process(sid)  # the text is kept; only the speakers are added
        self.assertEqual(s.assemble(sid)[0]['spk'], 'A')

    def test_same_speaker_lines_join_without_spaces_in_japanese(self):
        s = self.s
        sid = s.create_session('JP', 'ja', 'recording', 'recording', 'inperson')['id']
        s.append_chunk(sid, 0, tone(1, 1000))
        s.write_json(os.path.join(s.sdir(sid), 'plan.json'), [[0, 16000], [16000, 32000]])
        s.write_json(os.path.join(s.sdir(sid), 'diar.json'), [[0, 1, 'A']])
        self.run_session(sid)
        self.assertEqual(s.transcript_lines(sid, False), ['Speaker A: good morninggood morning'])

    def test_room_parts_are_split_by_voice_and_mac_parts_are_not(self):
        s = self.s
        # The room (0-2 s) → This Mac (2 s-). The fake splitter hears voice "A" first (until 1.9 s), then "B" (from 2.1 s)
        sid = s.create_session('Phone then Mac', 'en', 'recording', 'recording', 'meeting', 'room')['id']
        self.assertEqual(s.current_voice(s.load_meta(sid)), 'room')
        self.assertFalse(os.path.exists(os.path.join(s.sdir(sid), 'alive')))  # no Mac audio while in the room
        mic_start = time.time() - 2.0
        s.update_meta(sid, mic_start=mic_start)
        s.switch_voice(sid, 'mac')
        meta = s.load_meta(sid)
        self.assertEqual([v for _, v in meta['src_log']], ['room', 'mac'])
        self.assertAlmostEqual(meta['src_log'][1][0], 2.0, delta=0.5)
        s.update_meta(sid, src_log=[[0.0, 'room'], [2.0, 'mac']])
        s.append_chunk(sid, 0, tone(4, 1000))
        s.write_json(os.path.join(s.sdir(sid), 'plan.json'), [[0, 2 * 32000], [2 * 32000, 4 * 32000]])
        s.write_json(os.path.join(s.sdir(sid), 'tap.json'), {'state': 'stopped', 'started': mic_start + 2.0, 'updated': time.time()})
        meta = self.run_session(sid)
        self.assertTrue(os.path.exists(os.path.join(s.sdir(sid), 'diar.json')))
        rows = s.assemble(sid)
        # the Mac part is Me by the mic and its voice is "B", so the other voice "A" in the room part is Them
        self.assertEqual([(round(r['start']), r['spk']) for r in rows], [(0, 'them'), (2, 'me')])
        self.assertTrue(s.public_meta(meta)['has_room'])
        s.update_meta(sid, room_swap=True)
        self.assertEqual([r['spk'] for r in s.assemble(sid)], ['me', 'me'])

    def test_room_only_guesses_the_first_voice_is_me(self):
        turns = [[0, 1.9, 'A'], [2.1, 4, 'B']]
        self.assertEqual(self.s.room_me_voice([], turns), 'A')
        self.assertEqual(self.s.room_me_voice([], turns, swap=True), 'B')

    def test_echo_from_speakers_is_dropped_from_me(self):
        s = self.s
        me = [{'start': 1.0, 'end': 3.0, 'text': 'See you on Friday.'}, {'start': 5.0, 'end': 6.0, 'text': 'Sounds good'}]
        them = [{'start': 0.8, 'end': 3.1, 'text': 'see you on friday'}]
        self.assertEqual([x['text'] for x in s.drop_echo(me, them)], ['Sounds good'])

    def test_speaker_for_uses_overlap_then_nearest(self):
        s = self.s
        turns = [[0, 2, 'A'], [2, 5, 'B']]
        self.assertEqual(s.speaker_for({'start': 1.5, 'end': 4.0}, turns), 'B')
        self.assertEqual(s.speaker_for({'start': 6.0, 'end': 7.0}, turns), 'B')

    def test_lost_tap_is_shown_and_missing_app_is_reported(self):
        s = self.s
        sid = s.create_session('Call', 'en', 'recording', 'recording', 'meeting')['id']
        s.write_json(os.path.join(s.sdir(sid), 'tap.json'), {'state': 'recording', 'updated': time.time() - 20})
        self.assertEqual(s.tap_state(sid)['state'], 'lost')
        old = s.TAP_CMD
        s.TAP_CMD, s.TAP_APP = None, '/nowhere/TatsukiAudioTap.app'
        try:
            sid2 = s.create_session('Call', 'en', 'recording', 'recording', 'meeting')['id']
            self.assertEqual(s.tap_state(sid2), {'state': 'error', 'error': 'missing'})
        finally:
            s.TAP_CMD = old


    # ---------- notes and minutes ----------

    def test_text_from_files(self):
        docs = self.s.docs
        d = tempfile.mkdtemp(dir=self.tmp)
        txt = os.path.join(d, 'a.txt')
        with open(txt, 'w', encoding='utf-8') as f:
            f.write('議事録\r\n\r\n\r\n\r\n決まったこと：来週までに見積もり  \n')
        self.assertEqual(docs.extract(txt), '議事録\n\n決まったこと：来週までに見積もり')
        # a real Word file, made by macOS from the text file
        docx = os.path.join(d, 'a.docx')
        subprocess.run(['/usr/bin/textutil', '-convert', 'docx', txt, '-output', docx], check=True)
        self.assertIn('決まったこと', docs.extract(docx))
        with self.assertRaises(docs.DocError):
            docs.extract(os.path.join(d, 'a.xlsx'))
        empty = os.path.join(d, 'b.md')
        open(empty, 'w').close()
        with self.assertRaises(docs.DocError):
            docs.extract(empty)

    def test_dates_and_links(self):
        docs = self.s.docs
        self.assertEqual(docs.guess_date('2026-06-23_Meeting_Summary'), '2026-06-23')
        self.assertEqual(docs.guess_date('Meeting started 2026/08/01 01:06 JST'), '2026-08-01')
        self.assertEqual(docs.guess_date('03042025今日のまとめ'), '2025-04-03')
        self.assertEqual(docs.guess_date('Meetings'), '')
        fid = '1FlnZGn1E6SJ_K8E5GRd8MTbTjXLgr6V7fF4Lo1f68vE'
        self.assertEqual(docs.file_id(f'https://docs.google.com/document/d/{fid}/edit?usp=drivesdk'), fid)
        self.assertEqual(docs.file_id(f'https://drive.google.com/open?id={fid}'), fid)
        with self.assertRaises(docs.DocError):
            docs.file_id('https://example.com/nothing')

    def test_notes_are_listed_by_meeting_day_and_searchable(self):
        s = self.s
        a = s.create_text_session('Kickoff', '2026-06-23', 'We chose the blue design.', {'kind': 'paste'})
        b = s.create_text_session('Review', '2026-08-10', 'Budget is 300 dollars.', {'kind': 'file', 'name': 'r.docx'})
        ids = [m['id'] for m in s.list_sessions() if m['id'] in (a['id'], b['id'])]
        self.assertEqual(ids, [b['id'], a['id']])  # newest meeting first
        self.assertEqual([m['id'] for m in s.list_sessions('blue design')], [a['id']])
        self.assertEqual(s.public_meta(s.load_meta(a['id']))['kind'], 'text')
        self.assertFalse(s.valid_date('2026-13-01'))

    def test_minutes_and_a_task_from_one_line(self):
        s = self.s
        sid = s.create_text_session('Planning', '2026-11-20', 'Aki sends the quote by 2026-12-01.', {'kind': 'paste'})['id']
        sent = {}

        def fake(title, text, met=''):
            sent.update(title=title, text=text, met=met)
            return s.docs.normalize_minutes({'summary': ['Quote'], 'decisions': [], 'next': ['Price'],
                                             'actions': [{'what': 'Send the quote', 'who': 'Aki', 'due': '2026-12-01'}, {'what': ' '}]}), \
                {'model': 'claude-sonnet-5-5', 'input': 10, 'output': 5}
        old = s.docs.make_minutes
        s.docs.make_minutes = fake
        try:
            s.run_minutes(sid)
        finally:
            s.docs.make_minutes = old
        self.assertEqual(sent, {'title': 'Planning', 'text': 'Aki sends the quote by 2026-12-01.', 'met': '2026-11-20'})
        self.assertEqual(s.load_meta(sid)['minutes_state'], 'done')
        m = s.read_json(s.minutes_path(sid))
        self.assertEqual(len(m['actions']), 1)  # the empty line is dropped
        task_id = s.action_to_task(sid, 0)
        self.assertEqual(s.action_to_task(sid, 0), task_id)  # pressing twice makes one task
        with open(os.path.join(self.tmp, 'desk', 'tasks.json'), encoding='utf-8') as f:
            task = [t for t in json.load(f) if t['id'] == task_id][0]
        self.assertEqual((task['title'], task['source'], task['sourceRef']), ('Send the quote', 'transcribe', f'{sid}:0'))
        self.assertIn('Who: Aki', task['note'])
        self.assertIsNotNone(task['due'])
        self.assertTrue(s.matches(s.load_meta(sid), 'send the quote'))  # minutes are searched too

    def test_minutes_failure_is_shown_not_raised(self):
        s = self.s
        sid = s.create_text_session('Empty', '', '   ', {'kind': 'paste'})['id']
        s.run_minutes(sid)
        meta = s.load_meta(sid)
        self.assertEqual(meta['minutes_state'], 'error')
        self.assertIn('no text', meta['minutes_error'])

    def test_minutes_text_for_copy(self):
        txt = self.s.docs.minutes_text('Planning', '2026-11-20', {'summary': ['A'], 'decisions': [], 'next': [],
                                                                 'actions': [{'what': 'Send', 'who': 'Aki', 'due': 'Fri'}]})
        self.assertEqual(txt, 'Planning (2026-11-20)\n\nSummary\n- A\n\nActions\n- Send (Aki, Fri)\n')


if __name__ == '__main__':
    unittest.main()
