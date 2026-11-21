"""python3 -m unittest desk/tests/test_gcal_ics.py  (from the repository root)"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

TMP = tempfile.mkdtemp()
os.environ["DESK_DATA"] = TMP
os.environ["TGNOTIFY_DIR"] = os.path.join(TMP, "tgnotify")
os.environ["TGNOTIFY_NO_KEYCHAIN"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import gcal_ics as g  # noqa: E402

UTC = timezone.utc
ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:one
DTSTART:20261121T020000Z
DTEND:20261121T030000Z
SUMMARY:Call with\\, Ura
LOCATION:Zoom
END:VEVENT
BEGIN:VEVENT
UID:allday
DTSTART;VALUE=DATE:20261122
DTEND;VALUE=DATE:20261123
SUMMARY:Grandma
END:VEVENT
BEGIN:VEVENT
UID:weekly
DTSTART:20261116T010000Z
DTEND:20261116T013000Z
RRULE:FREQ=WEEKLY;BYDAY=MO,WE
EXDATE:20261125T010000Z
SUMMARY:Lesson
END:VEVENT
BEGIN:VEVENT
UID:weekly
RECURRENCE-ID:20261123T010000Z
DTSTART:20261123T050000Z
DTEND:20261123T053000Z
SUMMARY:Lesson (moved)
END:VEVENT
BEGIN:VEVENT
UID:gone
DTSTART:20261121T050000Z
DTEND:20261121T060000Z
STATUS:CANCELLED
SUMMARY:Gone
END:VEVENT
BEGIN:VEVENT
UID:mine
DTSTART:20261121T070000Z
DTEND:20261121T080000Z
SUMMARY:☑️ Desk task
END:VEVENT
BEGIN:VEVENT
UID:old
DTSTART:20261101T070000Z
DTEND:20261101T080000Z
SUMMARY:Last month
END:VEVENT
END:VCALENDAR
"""


def ts(*a):
    return datetime(*a, tzinfo=UTC).timestamp()


class Calendar(unittest.TestCase):
    def rows(self):
        return g.events(g.parse(ICS), ts(2026, 11, 20, 17), ts(2026, 11, 28, 17))

    def test_titles_in_order(self):
        titles = [r["title"] for r in self.rows()]
        self.assertEqual(titles, ["Call with, Ura", "Grandma", "Lesson (moved)"])

    def test_timed_event(self):
        r = self.rows()[0]
        self.assertFalse(r["allDay"])
        self.assertEqual(datetime.fromisoformat(r["start"]).timestamp(), ts(2026, 11, 21, 2))
        self.assertEqual(datetime.fromisoformat(r["end"]).timestamp(), ts(2026, 11, 21, 3))
        self.assertEqual(r["location"], "Zoom")

    def test_all_day(self):
        r = [x for x in self.rows() if x["title"] == "Grandma"][0]
        self.assertEqual((r["start"], r["end"], r["allDay"]), ("2026-11-22", "2026-11-23", True))

    def test_recurring_with_exdate_and_moved_copy(self):
        lessons = [x for x in self.rows() if x["title"].startswith("Lesson")]
        starts = sorted(datetime.fromisoformat(x["start"]).timestamp() for x in lessons)
        # Mon 23rd moved to 05:00, Wed 25th skipped, the window ends before Mon 30th
        self.assertEqual(starts, [ts(2026, 11, 23, 5)])
        self.assertEqual(len({x["id"] for x in lessons}), len(lessons))

    def test_event_running_into_the_window_is_kept(self):
        evs = g.parse(ICS.replace("DTSTART:20261121T020000Z", "DTSTART:20261120T160000Z"))
        rows = g.events(evs, ts(2026, 11, 20, 17), ts(2026, 11, 28, 17))
        self.assertIn("Call with, Ura", [r["title"] for r in rows])

    def test_no_address_writes_nothing(self):
        wrote = []
        self.assertIsNone(g.once(wrote.append))
        self.assertEqual(wrote, [])
        self.assertIn("No iCal address", g.status()["error"])


if __name__ == "__main__":
    unittest.main()
