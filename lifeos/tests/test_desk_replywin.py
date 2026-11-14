"""The reply window: which tasks are replies, and when Replies is open (5:00 and 16:00 on the Mac's clock)."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desk"))
import replywin as rw
import notify as n


def local(h, m=0, day=15):
    return datetime(2026, 11, day, h, m).timestamp()  # the Mac's own clock


class Kinds(unittest.TestCase):
    def test_sources_and_titles(self):
        self.assertEqual(rw.reply_kind({"source": "mail"}), "mail")
        self.assertEqual(rw.reply_kind({"source": "line", "title": "LINE reply: x"}), "line")
        self.assertEqual(rw.reply_kind({"source": "lifeos-02", "title": "anything"}), "nudge")
        self.assertEqual(rw.reply_kind({"source": "manual", "title": "Email nudge: Esperanto (a@b)"}), "nudge")
        self.assertEqual(rw.reply_kind({"source": "manual", "title": "WhatsApp催促 用件について（名前）"}), "nudge")
        self.assertEqual(rw.reply_kind({"source": "manual", "title": "メール返信 用件について"}), "mail")
        self.assertIsNone(rw.reply_kind({"source": "manual", "title": "Write the reply guide"}))

    def test_moved_to_tasks_is_not_a_reply(self):
        self.assertFalse(rw.is_reply({"source": "mail", "asTask": True}))
        self.assertTrue(rw.is_reply({"source": "lifeos-02"}))


class Window(unittest.TestCase):
    def test_slots(self):
        self.assertEqual(rw.last_slot(local(4, 59)), local(16, 0, 14))
        self.assertEqual(rw.last_slot(local(5, 0)), local(5))
        self.assertEqual(rw.last_slot(local(15, 0)), local(5))
        self.assertEqual(rw.next_slot(local(15, 0)), local(16))
        self.assertEqual(rw.next_slot(local(23, 0)), local(5, 0, 16))

    def test_open_until_done_then_until_next_slot(self):
        self.assertTrue(rw.is_open(local(10), state={}))
        st = {"closedAt": local(10) * 1000}
        self.assertFalse(rw.is_open(local(15, 59), state=st))
        self.assertTrue(rw.is_open(local(16), state=st))
        self.assertTrue(rw.is_open(local(10), state={"closedAt": local(4) * 1000}))

    def test_reads_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "replywin.json"), "w") as f:
                json.dump([{"id": "state", "closedAt": local(6) * 1000}], f)
            self.assertFalse(rw.is_open(local(7), data_dir=d))
            self.assertTrue(rw.is_open(local(17), data_dir=d))


class Notify(unittest.TestCase):
    def test_urgent_reply_waits_while_closed(self):
        wa = {"t1": {"urgent": True, "name": "A", "topic": "x", "lastAt": 1}}
        base = {"tasks": [], "allTasks": [{"id": "t1", "status": "notyet", "source": "whatsapp"}], "mail": [], "whatsapp": wa}
        self.assertEqual(n.urgent({**base, "replyOpen": False}), [])
        self.assertEqual(len(n.urgent({**base, "replyOpen": True})), 1)


if __name__ == "__main__":
    unittest.main()
