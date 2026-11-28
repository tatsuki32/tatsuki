"""Reflections: what is found, the page, the publish check (no Claude, no Rhizome Box)."""
import datetime as dt
import json
import os
import sys
import tempfile
import threading
import unittest

os.environ["REFLECT_DIR"] = tempfile.mkdtemp()
os.environ["REFLECT_FAKE"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import reflect  # noqa: E402

DAY = 86400000


def ms(day, hour=12):
    return int(dt.datetime.fromisoformat(f"{day}T{hour:02d}:00:00").timestamp() * 1000)


NOW = ms("2026-11-28", 10)
S = {"since": ms("2026-11-20", 0), "replyHours": 72, "sign": "Me"}


class Find(unittest.TestCase):
    def test_open_past_due_and_late_done(self):
        tasks = [{"id": "a", "title": "A", "status": "notyet", "due": ms("2026-11-26")},
                 {"id": "b", "title": "B", "status": "doing", "due": ms("2026-11-28")},  # due today: not yet
                 {"id": "c", "title": "C", "status": "done", "due": ms("2026-11-21"), "doneAt": ms("2026-11-24")},
                 {"id": "d", "title": "D", "status": "done", "due": ms("2026-11-10"), "doneAt": ms("2026-11-12")},  # before since
                 {"id": "e", "title": "E", "status": "dropped", "due": ms("2026-11-21")},
                 {"id": "f", "title": "F", "status": "notyet", "due": ms("2026-11-21"), "by": "claude"}]
        out = reflect.find(tasks, s=S, now=NOW)
        self.assertEqual(sorted(out), ["late-a", "latedone-c"])
        self.assertEqual(out["late-a"]["facts"]["days"], 2)
        self.assertEqual(out["latedone-c"]["facts"]["days"], 3)

    def test_moves_group_by_project(self):
        mv = lambda i, p: {"id": i, "title": i, "status": "notyet", "due": ms("2026-12-20"), "projectId": p,
                           "dueMoves": [{"from": ms("2026-12-08"), "to": ms("2026-12-20"), "at": ms("2026-11-28", 8)}]}
        out = reflect.find([mv("x1", "eo"), mv("x2", "eo"), mv("x3", "eo"), mv("y", "")], s=S, now=NOW)
        self.assertIn("moved-eo-2026-11-28", out)
        self.assertEqual(out["moved-eo-2026-11-28"]["facts"]["count"], 3)
        self.assertEqual(out["moved-eo-2026-11-28"]["facts"]["days"], 12)
        self.assertIn("moved-y-" + str(ms("2026-11-28", 8)), out)

    def test_replies(self):
        tasks = [{"id": "r1", "title": "Email reply: about x (A)", "source": "mail", "status": "notyet", "createdAt": ms("2026-11-24")},
                 {"id": "r2", "title": "Email reply: about y (B)", "source": "mail", "status": "notyet", "createdAt": ms("2026-11-27")},
                 {"id": "r3", "title": "x", "source": "whatsapp", "status": "done", "createdAt": ms("2026-11-21"), "doneAt": ms("2026-11-25"), "due": ms("2026-11-21")}]
        out = reflect.find(tasks, s=S, now=NOW)
        self.assertEqual(sorted(out), ["reply-r1", "reply-r3"])  # a reply is never a "late" task as well

    def test_promises_only_when_marked(self):
        habits = [{"id": "h1", "name": "Log sleep", "done": ["2026-11-25", "2026-11-26"], "promise": True, "promiseAt": ms("2026-11-25")},
                  {"id": "h2", "name": "Water", "done": []}]
        out = reflect.find([], habits=habits, s=S, now=NOW)
        self.assertEqual(sorted(out), ["promise-h1-2026-11-27"])

    def test_week_goal(self):
        goals = [{"id": "g", "kind": "goal", "title": "Study", "period": "week", "target": 3, "promise": True, "promiseAt": ms("2026-11-16")}]
        log = [{"id": "2026-11-23", "goals": {"g": 1}}, {"id": "2026-11-25", "goals": {"g": 1}}]
        out = reflect.find([], goals=goals, routinelog=log, s=S, now=NOW)
        # the week of 11-16 started before `since` but ended after it: counted; this week is not over
        self.assertEqual(sorted(out), ["promise-g-2026-11-16"])
        log2 = log + [{"id": "2026-11-17", "goals": {"g": 3}}]
        self.assertEqual(sorted(reflect.find([], goals=goals, routinelog=log2, s=S, now=NOW)), [])

    def test_scan_never_makes_one_twice(self):
        lock, store = threading.Lock(), {"tasks": [{"id": "a", "title": "A", "status": "notyet", "due": ms("2026-11-01")}],
                                         "reflections": [{"id": "late-a", "status": "notmiss"}]}
        load = lambda c: list(store.get(c, []))
        save = lambda c, v: store.__setitem__(c, v)
        reflect._write("settings.json", S)
        self.assertEqual(reflect.scan(load, save, lock, now=NOW, money=[]), 0)


class Tasks(unittest.TestCase):
    def test_each_waiting_reflection_is_a_task_until_written(self):
        items = [{"id": "late-a", "status": "open", "title": "A"}, {"id": "late-b", "status": "notmiss", "title": "B"}]
        tasks = []
        self.assertTrue(reflect.sync_tasks(items, tasks, NOW))
        self.assertEqual(len(tasks), 1)
        t = tasks[0]
        self.assertEqual((t["title"], t["source"], t["sourceRef"], t["status"]), ("Write a reflection: A", "reflect-write", "late-a", "notyet"))
        self.assertEqual(reflect.day_of(t["due"]).isoformat(), "2026-11-28")
        self.assertFalse(reflect.sync_tasks(items, tasks, NOW), "not made twice")
        items[0]["status"] = "published"
        reflect.sync_tasks(items, tasks, NOW)
        self.assertEqual(tasks[0]["status"], "done")

    def test_reflection_tasks_never_count_as_late(self):
        tasks = [{"id": "rw-x", "title": "Write a reflection: X", "status": "notyet", "due": ms("2026-11-20"), "source": "reflect-write"},
                 {"id": "rf1", "title": "Try: x", "status": "notyet", "due": ms("2026-11-20"), "source": "reflect"}]
        self.assertEqual(reflect.find(tasks, s=S, now=NOW), {})


class Page(unittest.TestCase):
    def test_pledge_signature_and_language(self):
        r = {"id": "late-a", "title": "Lessons 1-5", "pageTitle": "Lessons 1-5", "final": "[* 何があった]\n12日遅れた。\n", "day": "2026-11-28"}
        title, lines = reflect.page_lines(r, S)
        self.assertEqual(title, "Reflection: Lessons 1-5 (2026-11-28)")
        self.assertEqual(lines[0], title)
        self.assertEqual(lines[-2:], ["今後起こさないように誓います。", "Me — 2026-11-28"])
        r["final"] = "[* What happened]\nIt ran late."
        self.assertEqual(reflect.page_lines(r, S)[1][-2], "I pledge not to let this happen again.")

    def test_titles_lose_brackets(self):
        self.assertEqual(reflect.clean_title("[a] b\nc"), "a b c")

    def test_public_check(self):
        self.assertTrue(reflect.check_public("write to someone@example.com"))
        self.assertTrue(reflect.check_public("call +84 90 123 4567"))
        self.assertEqual(reflect.check_public("It ran 12 days late."), "")
        self.assertEqual(reflect.check_public("Due 2026-11-26, done 2026-11-28 at 10:30 - 12 days."), "")

    def test_index_newest_first_public_only(self):
        items = [{"id": "1", "rb": {"title": "Reflection: A (2026-11-01)", "pub": True, "at": 1}},
                 {"id": "2", "rb": {"title": "Reflection: B (2026-11-02)", "pub": True, "at": 2}},
                 {"id": "3", "rb": {"title": "Reflection: C (2026-11-03)", "pub": False, "at": 3}}]
        self.assertEqual(reflect.index_lines(items), [" [Reflection: B (2026-11-02)]", " [Reflection: A (2026-11-01)]"])

    def test_fake_write_up(self):
        r = {"id": "late-a", "kind": "late", "title": "A", "facts": {"due": ms("2026-11-26"), "days": 2},
             "answers": {"causes": ["estimate", "late"], "ifThen": "If I plan, I add half again"}}
        d = reflect.write_up(r, [], S)
        self.assertIn("[* What happened]", d["text"])
        self.assertIn("Estimate too small", d["text"])


if __name__ == "__main__":
    unittest.main()
