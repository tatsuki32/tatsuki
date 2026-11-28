import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


class StudyNotifyTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["DESK_DATA"] = self.dir
        os.environ["NOTIFY_DIR"] = os.path.join(self.dir, "notify")
        import importlib
        import notify
        importlib.reload(notify)
        import study_notify
        self.m = importlib.reload(study_notify)
        self.t0 = 1_800_000_000_000
        self.shown = []

    def write(self, **kw):
        doc = {"id": "sf1", "start": self.t0, "rounds": 2, "work": 25, "brk": 5, "status": "running", **kw}
        with open(os.path.join(self.dir, "studylog.json"), "w") as f:
            json.dump([doc], f)

    def tick(self, minutes):
        return [n["title"] for n in self.m.tick(self.t0 + int(minutes * 60_000), show=lambda n, s: self.shown.append(n))]

    def test_each_turn_once(self):
        self.write()
        self.assertEqual(self.tick(0.1), ["Focus started"])
        self.assertEqual(self.tick(10), [])
        self.assertEqual(self.tick(25.05), ["5-minute break"])
        self.assertEqual(self.tick(26), [])
        self.assertEqual(self.tick(30.05), ["Break over — focus again"])
        self.assertEqual(self.tick(55.05), ["Session done"])
        self.assertEqual(self.tick(56), [])

    def test_done_saved_by_page_first(self):
        self.write()
        self.tick(0.1)
        self.tick(30.1)
        self.write(status="done", end=self.t0 + 55 * 60_000)
        self.assertEqual(self.tick(55.02), ["Session done"])
        self.assertEqual(self.tick(55.2), [])

    def test_old_turn_and_stopped_not_told(self):
        self.write()
        self.assertEqual(self.tick(40), [])  # server was off when round 2 began
        self.write(status="stopped", end=self.t0 + 41 * 60_000)
        self.assertEqual(self.tick(41), [])


if __name__ == "__main__":
    unittest.main()
