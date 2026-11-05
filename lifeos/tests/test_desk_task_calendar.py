"""Desk tasks on Google Calendar: which task gets an event, and when it goes away."""
import importlib.util
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desk"))
spec = importlib.util.spec_from_file_location("task_calendar", os.path.join(ROOT, "desk", "task_calendar.py"))
tc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tc)

HCM = timezone(timedelta(hours=7))


def ms(*args):
    return int(datetime(*args, tzinfo=HCM).timestamp() * 1000)


NOW = ms(2026, 11, 5, 11, 0)
TIMED = ms(2026, 11, 20, 15, 0)
ENTRY = {"eventId": "e1", "start": "2026-11-17T15:00:00", "title": "書く"}


def task(status="notyet", due=TIMED, title="書く", **more):
    return {"id": "t1", "title": title, "status": status, "due": due, **more}


class Plan(unittest.TestCase):
    def test_not_started_with_a_time_goes_3_days_before_at_the_same_time(self):
        made = tc.plan([task()], {}, NOW)["create"]
        self.assertEqual(len(made), 1)
        self.assertEqual(made[0]["startTime"], "2026-11-17T15:00:00")
        self.assertEqual(made[0]["endTime"], "2026-11-17T15:30:00")
        self.assertEqual(made[0]["summary"], "☑️ 書く")
        self.assertEqual(made[0]["timeZone"], "Asia/Ho_Chi_Minh")

    def test_the_note_stays_off_the_calendar(self):
        self.assertNotIn("秘密", str(tc.plan([task(note="秘密")], {}, NOW)))

    def test_nothing_new_for_these(self):
        for t in (task(due=ms(2026, 11, 20, 23, 59)), task(due=None), task(status="doing"), task(status="done"),
                  task(status="paused"), task(status="archived"), task(deleted=True),
                  task(due=ms(2026, 11, 7, 15, 0))):  # 3 days before has already gone
            self.assertEqual(tc.plan([t], {}, NOW)["create"], [], t)

    def test_in_progress_keeps_its_event(self):
        p = tc.plan([task(status="doing")], {"t1": ENTRY}, NOW)
        self.assertEqual((p["create"], p["update"], p["delete"]), ([], [], []))

    def test_these_remove_the_event(self):
        for t in (task(status="done"), task(status="paused"), task(status="archived"), task(deleted=True),
                  task(due=None), task(due=ms(2026, 11, 20, 23, 59))):
            self.assertEqual(tc.plan([t], {"t1": ENTRY}, NOW)["delete"], [{"taskId": "t1", "eventId": "e1"}], t)
        self.assertEqual(len(tc.plan([], {"t1": ENTRY}, NOW)["delete"]), 1)

    def test_new_due_date_or_title_updates_the_event(self):
        moved = tc.plan([task(due=ms(2026, 11, 21, 9, 30))], {"t1": ENTRY}, NOW)["update"]
        self.assertEqual(moved[0]["startTime"], "2026-11-18T09:30:00")
        self.assertEqual(moved[0]["eventId"], "e1")
        self.assertEqual(tc.plan([task(title="直す")], {"t1": ENTRY}, NOW)["update"][0]["summary"], "☑️ 直す")

    def test_moved_into_the_past_removes_the_event(self):
        self.assertEqual(len(tc.plan([task(due=ms(2026, 11, 7, 15, 0))], {"t1": ENTRY}, NOW)["delete"]), 1)

    def test_an_event_deleted_by_hand_stays_deleted(self):
        state = {"t1": {**ENTRY, "eventId": None}}
        p = tc.plan([task(title="直す")], state, NOW)
        self.assertEqual((p["create"], p["update"], p["delete"], p["forget"]), ([], [], [], []))
        self.assertEqual(tc.plan([task(status="done")], state, NOW)["forget"], ["t1"])

    def test_back_from_paused_goes_on_again(self):
        state = {"t1": ENTRY}
        self.assertEqual(len(tc.plan([task(status="paused")], state, NOW)["delete"]), 1)
        tc.record(state, "delete", "t1")
        self.assertEqual(len(tc.plan([task()], state, NOW)["create"]), 1)


class Record(unittest.TestCase):
    def test_create_then_plan_again_makes_nothing_new(self):
        state = {}
        tc.record(state, "create", "t1", task(), "e9")
        self.assertEqual(state["t1"], {"eventId": "e9", "start": "2026-11-17T15:00:00", "title": "書く"})
        p = tc.plan([task()], state, NOW)
        self.assertEqual((p["create"], p["update"]), ([], []))

    def test_update_keeps_the_event_id(self):
        state = {"t1": dict(ENTRY)}
        tc.record(state, "update", "t1", task(title="直す"))
        self.assertEqual(state["t1"]["eventId"], "e1")
        self.assertEqual(state["t1"]["title"], "直す")


class Feed(unittest.TestCase):
    def test_task_events_stay_out_of_the_desk_calendar(self):
        import write_feed
        got = write_feed.calendar({"events": [{"id": "a", "summary": "☑️ 書く", "start": {"dateTime": "x"}},
                                              {"id": "b", "summary": "会議", "start": {"dateTime": "x"}}]})
        self.assertEqual([e["id"] for e in got["events"]], ["b"])


if __name__ == "__main__":
    unittest.main()
