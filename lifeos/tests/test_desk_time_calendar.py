"""Time log to the "Desk Log" calendar: which runs go, what they say, and that a second run adds nothing."""
import os
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desk"))
import time_calendar as tc  # noqa: E402
import server  # noqa: E402

NOW = 1_800_000_000_000
M = 60000


class Pending(unittest.TestCase):
    def test_only_finished_runs_of_5_minutes_or_more(self):
        recs = [{"id": "a", "taskId": "t1", "start": NOW - 60 * M, "end": NOW - 30 * M, "pausedMs": 0, "via": "chrome"},
                {"id": "b", "taskId": "t1", "start": NOW - 10 * M, "end": NOW - 6 * M},
                {"id": "c", "taskId": "t1", "start": NOW - 20 * M, "end": NOW, "pausedMs": 16 * M},
                {"id": "d", "taskId": "t1", "start": NOW - 20 * M},
                {"id": "e", "taskId": "t1", "start": NOW - 60 * M, "end": NOW - 30 * M, "calEventId": "x"},
                {"id": "f", "taskId": "t1", "start": NOW - 9 * 86400000, "end": NOW - 8 * 86400000},
                {"id": "_calendar", "kind": "status"}]
        out = tc.pending(recs, [{"id": "t1", "title": "Write", "cat": "Write a post", "estMin": 20, "note": "secret"}], NOW)
        self.assertEqual([x["id"] for x in out], ["a"])
        self.assertEqual(out[0]["summary"], "⏱ Write")
        self.assertIn("Measured: 30 min", out[0]["description"])
        self.assertIn("Measured in: Chrome", out[0]["description"])
        self.assertNotIn("secret", out[0]["description"])

    def test_mark_keeps_the_first_event(self):
        recs = [{"id": "a", "start": 1, "end": 2}, {"id": "b", "start": 1, "end": 2, "calEventId": "old"}]
        out = tc.mark(recs, [{"id": "a", "eventId": "e1"}, {"id": "b", "eventId": "e2"}, {"id": "zz", "eventId": "e3"}])
        self.assertEqual([(r["id"], r["calEventId"]) for r in out], [("a", "e1")])


class ExtensionMayLogTime(unittest.TestCase):
    def check(self, command, path, origin):
        h = SimpleNamespace(headers={"Host": "localhost:4747", "Origin": origin}, command=command, path=path)
        return server.Handler.allowed(h)

    def test_task_desk_extension(self):
        o = server.TASKDESK_ORIGIN
        self.assertTrue(self.check("PUT", "/api/timelog/tl-1", o))
        self.assertTrue(self.check("PUT", "/api/tasks/t1", o))
        self.assertFalse(self.check("PUT", "/api/genres/g1", o))
        self.assertFalse(self.check("PUT", "/api/timelog/tl-1", "chrome-extension://other"))


if __name__ == "__main__":
    unittest.main()
