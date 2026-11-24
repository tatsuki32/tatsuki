"""Minutes → the mail to the owner's own inbox and the automatic thank-you draft. Gmail, Claude and the calendar are replaced."""
import base64
import email
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime
from email.header import decode_header, make_header
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
os.environ.setdefault('MINUTES_DIR', tempfile.mkdtemp(prefix='desk-minutes-test-'))  # never the real minutes
import gcal_ics  # noqa: E402
import meetcheck  # noqa: E402
import minutes  # noqa: E402
import minutes_mail as mail  # noqa: E402
import minutes_share as share  # noqa: E402

BODY = '''## 会議の情報
- 日時：2026-11-24 10:00–10:40（40分）
- 参加者：田中、自分

## 目的・決めること
- 見積もりを頼む会社を決める

## 要点
- 2社を比べた

## 決まったこと
- A社に頼む

## アクション
| 何を | 誰が | いつまで |
|---|---|---|
| 発注書を送る | 自分 | 2026-11-28 |
| 日程を返す | 田中 | 2026-11-26 |
| 予算を見直す | 田中 | — |
| 写真を選ぶ | タツキ | — |

## 決まらなかったこと・確認すること
- 納品日

## 話の流れ
- **見積もり**
  - 長い話

## 元のメモ
秘密のメモ'''


def minute(i='a', **kw):
    now = time.time()
    m = {'id': i * 32, 'title': '見積もりの会議', 'body': BODY, 'status': 'draft', 'place': '', 'at': now - 3600,
         'source': {'kind': 'transcribe', 'sid': i * 32}, 'event': None, 'calendar': None, 'created': now, 'updated': now, **kw}
    if 'gdoc' not in kw:
        m['gdoc'] = {'id': 'DOC' + i * 20, 'url': 'https://docs.google.com/document/d/DOC/edit', 'at': now, 'fp': share.fingerprint(m)}
    minutes._write(m)
    return m


class Base(unittest.TestCase):
    def setUp(self):
        if 'desk-minutes-test-' not in minutes.DIR:
            raise RuntimeError('minutes.DIR is not a test folder')
        shutil.rmtree(minutes.DIR, ignore_errors=True)
        mail._own.update(list=[], at=0)
        mail._problem['text'] = ''
        self.calls = []
        for p in (mock.patch.object(mail, '_gmail', side_effect=self.gmail),
                  mock.patch.object(meetcheck, '_ask', side_effect=meetcheck.MeetError('off')),
                  mock.patch.object(mail, 'calendar_rows', side_effect=mail.MailError('no calendar')),
                  mock.patch('builtins.print')):
            p.start()
            self.addCleanup(p.stop)

    def gmail(self, method, path, body=None, tries=2):
        self.calls.append((method, path, body))
        if path == '/profile':
            return {'emailAddress': 'owner@example.com'}
        if path == '/settings/sendAs':
            return {'sendAs': [{'sendAsEmail': 'owner@example.com'}, {'sendAsEmail': 'Work@example.com'}]}
        if path.startswith('/messages/import'):
            if getattr(self, 'down', False):
                raise mail.MailError("Couldn't reach Gmail. Trying again in an hour")
            return {'id': 'g1'}
        return {'messages': [{'id': 'old1'}]} if getattr(self, 'arrived', False) else {}

    def imported(self):
        return [email.message_from_bytes(base64.urlsafe_b64decode(b['raw'])) for m, p, b in self.calls if p.startswith('/messages/import')]


class Mail(Base):
    def test_one_mail_for_a_new_doc_and_only_to_the_owner(self):
        mail.settings()
        minute()
        self.assertEqual(mail.auto(), 1)
        self.assertEqual(mail.auto(), 0)
        [msg] = self.imported()
        self.assertEqual(msg['To'], 'owner@example.com')
        self.assertIsNone(msg['Cc'])
        self.assertIn('見積もりの会議', str(make_header(decode_header(msg['Subject']))))
        text, page = [p.get_payload(decode=True).decode() for p in msg.get_payload()]
        self.assertTrue(text.startswith('A社に頼む'))
        self.assertIn('https://docs.google.com/document/d/DOC/edit', text)
        for part in (text, page):
            self.assertNotIn('秘密', part)
            self.assertNotIn('長い話', part)
        self.assertFalse([p for m, p, b in self.calls if 'send' in p.lower() and p != '/settings/sendAs'])
        [(_, path, body)] = [c for c in self.calls if c[1].startswith('/messages/import')]
        self.assertEqual(body['labelIds'], ['INBOX', 'UNREAD'])

    def test_minutes_that_had_a_doc_before_are_never_mailed(self):
        minute('a')
        self.assertEqual(mail.auto(), 0)
        minute('b')
        self.assertEqual(mail.auto(), 1)

    def test_an_unreadable_settings_file_mails_nothing(self):
        minute()
        with open(mail._file(), 'w') as f:
            f.write('{broken')
        self.assertEqual(mail.auto(), 0)

    def test_an_edit_does_not_mail_again_but_the_button_does(self):
        mail.settings()
        m = minute()
        mail.auto()
        minutes.put(m['id'], {'body': BODY.replace('A社', 'B社')})
        cur = minutes.get(m['id'])
        minutes.set_share(m['id'], 'gdoc', {**cur['gdoc'], 'fp': share.fingerprint(cur)})
        self.assertEqual(mail.auto(), 0)
        self.assertTrue(mail.again(m['id'])['ok'])
        a, b = self.imported()
        self.assertNotEqual(a['Message-ID'], b['Message-ID'])

    def test_waits_for_the_doc_and_for_a_typed_minute_to_be_ready(self):
        mail.settings()
        m = minute('a')
        minutes.set_share(m['id'], 'gdoc', {**m['gdoc'], 'fp': 'old'})
        n = minute('b', source={'kind': 'manual'})
        self.assertEqual(mail.auto(), 0)
        minutes.put(n['id'], {'status': 'ready'})
        self.assertEqual(mail.auto(), 1)

    def test_a_failure_waits_and_never_makes_two(self):
        mail.settings()
        m = minute()
        self.down = True
        self.assertEqual(mail.auto(), 0)
        self.assertIn('Gmail', mail.status()['problem'])
        self.down = False
        self.assertEqual(mail.auto(), 0)  # waits an hour
        cur = minutes.get(m['id'])['selfmail']
        minutes.set_share(m['id'], 'selfmail', {**cur, 'failedAt': time.time() - 4000})
        self.arrived, before = True, len(self.imported())
        self.assertEqual(mail.auto(), 1)
        self.assertEqual(len(self.imported()), before)  # the first one had arrived after all
        self.assertEqual(minutes.get(m['id'])['selfmail']['id'], 'old1')

    def test_off_means_off(self):
        mail.put_settings({'on': False, 'skip': ['x']})
        minute()
        self.assertEqual(mail.auto(), 0)
        self.assertEqual(mail.settings()['skip'], [])


class Text(Base):
    def test_to_dos_need_a_person_and_a_date(self):
        mine, theirs, ideas = mail.split(mail.plain(minute())['actions'])
        self.assertEqual([x['what'] for x in mine], ['発注書を送る'])
        self.assertEqual([(x['who'], x['what']) for x in theirs], [('田中', '日程を返す')])
        self.assertEqual([x['what'] for x in ideas], ['写真を選ぶ', '予算を見直す'])  # the owner's own first
        _, _, ideas = mail.split([{'what': 'x', 'who': 'Speaker A', 'due': '2026-11-28'}])
        self.assertEqual(ideas[0]['who'], '')

    def test_order_and_info(self):
        m = minute()
        _, text, page = mail.compose(m, mail.plain(m), {'to': ['t@example.com']})
        marks = ['A社に頼む', 'docs.google.com', '■ 決まったこと', '■ 自分のやること', '■ 相手のやること', '■ 提案', '■ 決まらなかったこと', '■ 会議の情報', '■ お礼']
        at = [text.index(x) for x in marks]
        self.assertEqual(at, sorted(at))
        self.assertIn('発注書を送る（2026-11-28まで）', text)
        self.assertIn('写真を選ぶ（自分・期限なし）', text)
        self.assertIn('2026-11-24 10:00–10:40（40分）', text)
        self.assertIn('t@example.com', text)

    def test_html_is_escaped(self):
        m = minute(title='<script>x</script>')
        _, _, page = mail.compose(m, {**mail.plain(m), 'conclusion': '<img src=x>'})
        self.assertNotIn('<img', page)

    def test_claude_cannot_add_a_person_or_a_date(self):
        d = mail.check({'conclusion': 'A社', 'decisions': [], 'points': [], 'open': [{'what': '納品日', 'who': '鈴木', 'due': '2027-01-01'}],
                        'actions': [{'what': '発注書', 'who': '自分', 'due': '2026-11-28'}, {'what': 'y', 'who': '鈴木', 'due': '2026-12-31'}]}, BODY)
        self.assertEqual(d['actions'], [{'what': '発注書', 'who': '自分', 'due': '2026-11-28'}, {'what': 'y', 'who': '', 'due': ''}])
        self.assertEqual(d['open'][0]['who'], '')
        self.assertEqual(mail.check({'conclusion': 3, 'actions': 'x', 'decisions': [1]}, BODY)['actions'], [])

    def test_claude_gets_no_raw_notes(self):
        m = minute()
        with mock.patch.object(meetcheck, '_ask', return_value={'conclusion': 'A社にする', 'decisions': [], 'actions': [], 'open': [], 'points': []}) as ask:
            self.assertEqual(mail.shorten(m)['conclusion'], 'A社にする')
        self.assertNotIn('秘密', ask.call_args[0][1])

    def test_old_notes_without_the_record_format_still_make_a_mail(self):
        m = minute(body='## Summary\n- We met\n\n## Original notes\nraw')
        subject, text, _ = mail.compose(m, mail.plain(m))
        self.assertTrue(text.startswith('We met'))


ICS = '''BEGIN:VCALENDAR
BEGIN:VEVENT
UID:e1
SUMMARY:Call
DTSTART:20261124T010000Z
DTEND:20261124T014500Z
ORGANIZER;CN="A: B":mailto:owner@example.com
ATTENDEE;CUTYPE=INDIVIDUAL;PARTSTAT=ACCEPTED;CN="Tanaka; Taro: X";X-NUM-GUE
 STS=0:mailto:Tanaka@example.com
ATTENDEE;PARTSTAT=DECLINED;CN=No:mailto:no@example.com
ATTENDEE;CUTYPE=RESOURCE;PARTSTAT=ACCEPTED:mailto:room@example.com
ATTENDEE;PARTSTAT=ACCEPTED:mailto:work@example.com
END:VEVENT
BEGIN:VEVENT
UID:mine
SUMMARY:Work log
DTSTART:20261124T003000Z
DTEND:20261124T030000Z
END:VEVENT
END:VCALENDAR
'''


class Thanks(Base):
    own = ['owner@example.com', 'work@example.com']

    def rows(self, extra=''):
        t = datetime.fromisoformat('2026-11-24T01:00:00+00:00').timestamp()
        self.start = t
        text = ICS.replace('END:VCALENDAR', extra + 'END:VCALENDAR')
        return gcal_ics.events(gcal_ics.parse(text, guests=True), t - 86400, t + 86400)

    def test_guests_of_the_one_event(self):
        rows = self.rows()
        self.assertEqual(mail.guests(minute(at=self.start + 120), rows, self.own), (['tanaka@example.com'], ''))
        self.assertEqual(mail.guests(minute(at=self.start + 7200), rows, self.own), ([], 'no-event'))

    def test_the_calendar_itself_keeps_no_guests(self):
        t = datetime.fromisoformat('2026-11-24T01:00:00+00:00').timestamp()
        self.assertNotIn('example.com', json.dumps(gcal_ics.events(gcal_ics.parse(ICS), t - 86400, t + 86400)))

    def test_two_events_make_no_draft_unless_one_is_linked(self):
        rows = self.rows('BEGIN:VEVENT\nUID:e2\nSUMMARY:Other\nDTSTART:20261124T010000Z\nDTEND:20261124T014500Z\nATTENDEE:mailto:x@example.com\nEND:VEVENT\n')
        self.assertEqual(mail.guests(minute(at=self.start), rows, self.own), ([], 'two'))
        m = minute(at=self.start, event={'id': f'e2_{int(self.start)}', 'uid': 'e2'})
        self.assertEqual(mail.guests(m, rows, self.own), (['x@example.com'], ''))

    def test_an_event_the_owner_declined_is_not_the_meeting(self):
        rows = self.rows()
        for r in rows:
            for g in r.get('guests') or []:
                if g['email'] == 'owner@example.com':
                    g['status'] = 'DECLINED'
        self.assertEqual(mail.guests(minute(at=self.start), rows, self.own), ([], 'no-event'))

    def test_a_crowd_gets_no_draft(self):
        rows = self.rows('BEGIN:VEVENT\nUID:big\nSUMMARY:Big\nDTSTART:20261124T010000Z\nDTEND:20261124T014500Z\n'
                         + ''.join(f'ATTENDEE:mailto:p{i}@example.com\n' for i in range(12)) + 'END:VEVENT\n')
        rows = [r for r in rows if r['id'].startswith('big')]
        self.assertEqual(mail.guests(minute(at=self.start), rows, self.own), ([], 'many'))

    def test_the_draft_is_made_once_and_named_in_the_mail(self):
        mail.settings()
        rows = [{'id': 'e1_1', 'allDay': False, 'start': datetime.fromtimestamp(time.time() - 3600).astimezone().isoformat(),
                 'end': datetime.fromtimestamp(time.time() - 600).astimezone().isoformat(), 'guests': [{'email': 't@example.com', 'status': '', 'room': False}]}]
        m = minute()

        def fake_thanks(mid, rec):
            minutes.set_share(mid, 'thanks', {'to': rec['to']})
        with mock.patch.object(mail, 'calendar_rows', side_effect=None, return_value=rows), \
                mock.patch.object(share, 'thanks', side_effect=fake_thanks) as th:
            self.assertEqual(mail.auto(), 1)
            self.assertEqual(th.call_count, 1)
            self.assertEqual(th.call_args[0][1]['to'], ['t@example.com'])
            self.assertEqual(mail.auto_thanks(minutes.get(m['id'])), {'already': True})
        self.assertIn('t@example.com', self.imported()[0].get_payload()[0].get_payload(decode=True).decode())

    def test_old_meetings_and_notes_get_no_draft(self):
        self.assertEqual(mail.auto_thanks(minute(at=time.time() - 5 * 86400)), {'why': 'old'})
        self.assertEqual(mail.auto_thanks(minute(source={'kind': 'transcribe', 'sid': 'a' * 32, 'from': 'notes'})), {'why': 'no-event'})

    def test_no_calendar_still_mails(self):
        mail.settings()
        minute()
        self.assertEqual(mail.auto(), 1)
        self.assertIn('カレンダーを読めなかった', self.imported()[0].get_payload()[0].get_payload(decode=True).decode())


if __name__ == '__main__':
    unittest.main()
