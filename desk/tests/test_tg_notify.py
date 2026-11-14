"""python3 -m unittest desk/tests/test_tg_notify.py  (from the repository root)"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp()
os.environ["DESK_DATA"] = TMP
os.environ["TGNOTIFY_DIR"] = os.path.join(TMP, "tgnotify")
os.environ["TGNOTIFY_NO_KEYCHAIN"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import tg_notify as tg  # noqa: E402

S = tg.clean_settings({})


def at(h, m=0, days=0):
    return (datetime.now().replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=days)).timestamp()


def task(i, status="notyet", due_days=None, **kw):
    t = {"id": i, "title": f"Task {i}", "status": status, "deleted": False, "updatedAt": at(9) * 1000}
    if due_days is not None:
        d = datetime.now().date() + timedelta(days=due_days)
        t["due"] = datetime(d.year, d.month, d.day, 12, tzinfo=tg.HCMC).timestamp() * 1000
    t.update(kw)
    return t


def texts(out):
    return [m["text"].split("\n")[0] for m in out]


class Plan(unittest.TestCase):
    def test_first_run_tells_no_changes(self):
        out, st = tg.plan(at(10), [task("a")], [], S, tg.blank_state())
        self.assertEqual(texts(out), [])
        self.assertEqual(st["seen"], {"a": "notyet"})

    def test_added_done_deleted(self):
        _, st = tg.plan(at(10), [task("a"), task("b")], [], S, tg.blank_state())
        out, st = tg.plan(at(10, 1), [task("a", "done"), task("b", deleted=True), task("c")], [], S, st)
        self.assertEqual(sorted(texts(out)), ["✅ Done: Task a", "🆕 Task added: Task c", "🗑️ Deleted: Task b"])
        out, st = tg.plan(at(10, 2), [task("a", "done"), task("b", deleted=True), task("c")], [], S, st)
        self.assertEqual(texts(out), [])

    def test_night_waits_for_four(self):
        _, st = tg.plan(at(22), [task("a")], [], S, tg.blank_state())
        out, st = tg.plan(at(23), [task("a"), task("c")], [], S, st)
        self.assertEqual(texts(out), [])
        self.assertEqual(len(st["queue"]), 1)
        out, st = tg.plan(at(4, 0, 1), [task("a"), task("c")], [], S, st)
        self.assertIn("🆕 Task added: Task c", texts(out))

    def test_due_and_overdue_once_a_day(self):
        tasks = [task("t", due_days=0), task("m", due_days=1), task("o", due_days=-9)]
        out, st = tg.plan(at(10), tasks, [], S, tg.blank_state())
        self.assertEqual(texts(out), ["🚨 Overdue 9 days: Task o", "⏰ Due today: Task t", "📅 Due tomorrow: Task m"])
        self.assertIn("http://localhost:4747/#task/o", out[0]["text"])
        out, st = tg.plan(at(11), tasks, [], S, st)
        self.assertEqual(texts(out), [])

    def test_stuck_and_doing(self):
        old = (at(10) - 5 * tg.DAY) * 1000
        out, _ = tg.plan(at(10), [task("s", "doing", updatedAt=old), task("d", "doing")], [], S, tg.blank_state())
        self.assertEqual(len(out), 2)
        self.assertTrue(texts(out)[0].startswith("⏸️ In progress, untouched for"))
        self.assertEqual(texts(out)[1], "▶️ In progress: Task d")

    def test_event_any_hour(self):
        ev = [{"id": "e1", "title": "Call", "start": int(at(2, 10))}]
        out, st = tg.plan(at(2, 1), [], ev, S, tg.blank_state())
        self.assertEqual(texts(out), ["🗓️ In 9 min: Call"])
        out, _ = tg.plan(at(2, 2), [], ev, S, st)
        self.assertEqual(out, [])

    def test_limit_then_one_line(self):
        tasks = [task(f"o{i}", due_days=-1) for i in range(40)]
        out, st = tg.plan(at(10), tasks, [], S, tg.blank_state())
        self.assertEqual(len(out), tg.PER_TICK)
        out, st = tg.plan(at(10, 1), tasks, [], S, st)
        self.assertEqual(len(out), tg.CAP - tg.PER_TICK + 1)
        self.assertTrue(out[-1]["text"].startswith("➕ 10 more task notifications"))

    def test_off_kind_not_sent(self):
        s = tg.clean_settings({"on": {"due0": False}})
        out, _ = tg.plan(at(10), [task("t", due_days=0)], [], s, tg.blank_state())
        self.assertEqual(out, [])

    def test_reply_only_when_open(self):
        r = task("r", source="mail", title="Email reply: about it (Name)")
        _, st = tg.plan(at(9), [task("a")], [], S, tg.blank_state(), reply_is_open=False)
        out, st2 = tg.plan(at(10), [task("a"), r], [], S, st, reply_is_open=False)
        self.assertEqual(out, [])
        out, _ = tg.plan(at(10, 1), [task("a"), r], [], S, st2, reply_is_open=True)
        self.assertEqual(texts(out), ["💬 Reply needed: Email reply: about it (Name)"])

    def test_replies_waiting_at_setup_not_told(self):
        r = task("r", source="mail", title="Email reply: x")
        out, _ = tg.plan(at(10), [r], [], S, tg.blank_state(), reply_is_open=True)
        self.assertEqual(out, [])

    def test_unreadable_file_keeps_seen(self):
        _, st = tg.plan(at(10), [task("a")], [], S, tg.blank_state())
        _, st = tg.plan(at(10, 1), [], [], S, st)
        out, _ = tg.plan(at(10, 2), [task("a")], [], S, st)
        self.assertEqual(texts(out), [])


ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:w1
SUMMARY:Weekly\\, team
DTSTART;TZID=Asia/Ho_Chi_Minh:20260105T090000
RRULE:FREQ=WEEKLY;BYDAY=MO,WE
EXDATE;TZID=Asia/Ho_Chi_Minh:20261111T090000
END:VEVENT
BEGIN:VEVENT
UID:o1
SUMMARY:Once
DTSTART:20261116T020000Z
END:VEVENT
BEGIN:VEVENT
UID:d1
SUMMARY:All day
DTSTART;VALUE=DATE:20261116
END:VEVENT
BEGIN:VEVENT
UID:w1
RECURRENCE-ID;TZID=Asia/Ho_Chi_Minh:20261116T090000
SUMMARY:Weekly moved
DTSTART;TZID=Asia/Ho_Chi_Minh:20261116T100000
END:VEVENT
END:VCALENDAR
"""


class Ics(unittest.TestCase):
    def ts(self, *a):
        return datetime(*a, tzinfo=tg.HCMC).timestamp()

    def test_expand(self):
        evs = tg.ics_events(ICS)
        got = tg.expand(evs, self.ts(2026, 11, 9), self.ts(2026, 11, 17))
        names = sorted((datetime.fromtimestamp(e["start"], tg.HCMC).strftime("%m-%d %H:%M"), e["title"]) for e in got)
        self.assertEqual(names, [("11-09 09:00", "Weekly, team"), ("11-16 09:00", "Once"), ("11-16 10:00", "Weekly moved")])


if __name__ == "__main__":
    unittest.main()
