"""Tests for desk/unimail.py (the university Gmail in Replies). No network: gmail() is replaced."""
import base64
import email
import email.policy
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import unimail  # noqa: E402

ME = "me@example.com"


def b64(s):
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def msg(sender, text, extra=None, labels=None, mid="<m@x>"):
    hs = [{"name": "From", "value": sender}, {"name": "To", "value": ME}, {"name": "Subject", "value": "授業の件"},
          {"name": "Date", "value": "Thu, 26 Nov 2026 10:00:00 +0900"}, {"name": "Message-ID", "value": mid}]
    hs += [{"name": k, "value": v} for k, v in (extra or {}).items()]
    return {"labelIds": labels or ["INBOX"], "payload": {"mimeType": "text/plain", "headers": hs, "body": {"data": b64(text)}}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(unimail, "DIR", self.tmp.name)
        self.p.start()
        unimail._save({"email": ME})

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def fake(self, threads):
        def gmail(method, path, body=None):
            if path.startswith("/threads?"):
                return {"threads": [{"id": k} for k in threads]}
            tid = path.split("/threads/")[1].split("?")[0]
            return {"messages": threads[tid]}
        return mock.patch.object(unimail, "gmail", side_effect=gmail)


class Pending(Base):
    def test_person_waiting_is_listed(self):
        with self.fake({"t1": [msg("Teacher <teacher@example.com>", "来週の件、ご都合いかがですか。")]}):
            out = unimail.pending()
        self.assertEqual([i["thread"] for i in out["items"]], ["t1"])
        item = out["items"][0]
        self.assertEqual(item["account"], "zen")
        self.assertIn("authuser=", item["gmail_url"])
        self.assertTrue(item["gmail_url"].endswith("#all/t1"))
        self.assertIn("ご都合", item["messages"][-1]["text"])

    def test_skips_own_last_message_bulk_and_noreply(self):
        threads = {
            "mine": [msg("Teacher <teacher@example.com>", "質問です"), msg(ME, "お答えします")],
            "news": [msg("Office <office@example.com>", "お知らせ", {"List-Unsubscribe": "<mailto:x@y>"})],
            "auto": [msg("System <system@example.com>", "自動送信", {"Auto-Submitted": "auto-generated"})],
            "noreply": [msg("Forms <forms-receipts-noreply@google.com>", "回答のコピー")],
            "bulk": [msg("Club <club@example.com>", "全員へ", {"Precedence": "bulk"})],
            "invite": [{**msg("T <t@example.com>", "招待"), "payload": {"mimeType": "multipart/mixed", "headers": msg("T <t@example.com>", "")["payload"]["headers"],
                                                               "parts": [{"mimeType": "text/plain", "body": {"data": b64("招待")}},
                                                                         {"mimeType": "text/calendar", "body": {"data": b64("BEGIN:VCALENDAR")}}]}}],
        }
        with self.fake(threads):
            self.assertEqual(unimail.pending()["items"], [])

    def test_quoted_mail_is_cut(self):
        text = "ありがとうございます。\n\n2026年11月19日(木) 10:30 池山 <a@b.jp>:\n> 前のメール"
        self.assertEqual(unimail._quote_cut(text), "ありがとうございます。")
        self.assertEqual(unimail._quote_cut("Thanks!\nOn Thu, Nov 19, 2026 at 10:30 AM Someone <a@b> wrote:\n> old"), "Thanks!")

    def test_nothing_written_but_the_account_and_time(self):
        with self.fake({"t1": [msg("Teacher <teacher@example.com>", "秘密の本文")]}):
            unimail.pending()
        for name in os.listdir(self.tmp.name):
            with open(os.path.join(self.tmp.name, name), encoding="utf-8") as f:
                self.assertNotIn("秘密の本文", f.read())


class Replied(Base):
    def test_own_message_last(self):
        threads = {"a": [msg("T <t@example.com>", "q"), msg(ME, "a")], "b": [msg("T <t@example.com>", "q")],
                   "c": [msg("T <t@example.com>", "q"), msg(ME, "draft", labels=["DRAFT"])]}
        with self.fake(threads):
            self.assertEqual(unimail.replied(["a", "https://mail.google.com/mail/?authuser=x#all/b", "c"]),
                             {"a": True, "b": False, "c": False})


class Draft(Base):
    def test_reply_mime_keeps_the_thread(self):
        raw = unimail.build_draft({"thread": "t1", "to": ["teacher@example.com"], "subject": "授業の件", "body": "承知しました。\n三橋",
                                   "messageId": "<m@x>", "references": "<a@x>"}, ME)
        m = email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=email.policy.default)
        self.assertEqual(str(m["Subject"]), "Re: 授業の件")
        self.assertEqual(m["In-Reply-To"], "<m@x>")
        self.assertEqual(m["References"], "<a@x> <m@x>")
        self.assertIn("承知しました", m.get_payload(decode=True).decode())

    def test_draft_needs_body(self):
        with self.assertRaises(unimail.MailError):
            unimail.draft({"thread": "t1", "to": ["x@example.com"], "body": " "})

    def test_draft_calls_drafts_only(self):
        calls = []

        def gmail(method, path, body=None):
            calls.append((method, path))
            return {"id": "d1", "message": {"id": "m9"}}
        with mock.patch.object(unimail, "gmail", side_effect=gmail):
            res = unimail.draft({"thread": "t1", "to": ["x@example.com"], "subject": "s", "body": "b"})
        self.assertEqual(calls, [("POST", "/drafts")])
        self.assertTrue(res["view_url"].endswith("#drafts?compose=m9"))


if __name__ == "__main__":
    unittest.main()
