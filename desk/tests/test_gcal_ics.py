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


class MeetingMarks(unittest.TestCase):
    def rows(self, body, owner="me@example.com"):
        text = f"BEGIN:VCALENDAR\r\nX-WR-CALNAME:me@example.com\r\nBEGIN:VEVENT\r\nUID:x\r\nDTSTART:20261121T020000Z\r\nDTEND:20261121T030000Z\r\nSUMMARY:Call\r\n{body}END:VEVENT\r\n"
        lo = datetime(2026, 11, 21, tzinfo=UTC).timestamp()
        return g.events(g.parse(text, owner=owner), lo, lo + 86400)[0]

    def test_links_that_are_meetings(self):
        for line in ("X-GOOGLE-CONFERENCE:https://meet.google.com/abc-defg-hij\r\n", "LOCATION:https://us02web.zoom.us/j/123?pwd=x\r\n",
                     "DESCRIPTION:Join: https://teams.microsoft.com/l/meetup-join/19\r\n", "DESCRIPTION:see https://meet.goo\r\n gle.com/abc-defg-hij\r\n"):
            r = self.rows(line)
            self.assertTrue(r.get("meet"), line)
            self.assertNotIn("description", r)
            self.assertNotIn("pwd", r["title"])

    def test_links_that_are_not(self):
        for line in ("LOCATION:Cafe\r\n", "DESCRIPTION:Help: https://support.google.com/meet\r\n", "DESCRIPTION:https://zoom.us/pricing\r\n",
                     "DESCRIPTION:https://meet.google.com.evil.example/abc-defg-hij\r\n"):
            self.assertNotIn("meet", self.rows(line), line)

    def test_mine_is_made_or_accepted_here(self):
        att = "ATTENDEE;PARTSTAT=%s;CN=x:mailto:%s\r\n"
        org = "ORGANIZER:mailto:%s\r\n"
        yes = {"no guests": "", "accepted": org % "a@example.com" + att % ("ACCEPTED", "me@example.com"),
               "made here": org % "me@example.com" + att % ("NEEDS-ACTION", "a@example.com")}
        no = {"unanswered": org % "a@example.com" + att % ("NEEDS-ACTION", "me@example.com"), "declined": org % "a@example.com" + att % ("DECLINED", "me@example.com"),
              "maybe": att % ("TENTATIVE", "me@example.com"), "someone else said yes": org % "a@example.com" + att % ("ACCEPTED", "b@example.com"),
              "two answers": org % "a@example.com" + att % ("ACCEPTED", "me@example.com") + att % ("NEEDS-ACTION", "me@example.com"),
              "two organizers": org % "a@example.com" + org % "me@example.com",
              "a yes in a name": org % "a@example.com" + 'ATTENDEE;CN="PARTSTAT=ACCEPTED":mailto:me@example.com\r\n'}
        for name, body in yes.items():
            self.assertTrue(self.rows(body).get("mine"), name)
        for name, body in no.items():
            self.assertNotIn("mine", self.rows(body), name)
        self.assertNotIn("mine", self.rows(att % ("ACCEPTED", "me@example.com"), owner=""))  # whose calendar is not known
        self.assertNotIn("mine", self.rows("", owner=""))

    def test_whose_calendar_comes_from_the_address_only(self):
        ok = "https://calendar.google.com/calendar/ical/me%40example.com/private-abc/basic.ics"
        self.assertEqual(g.owner_of(ok), "me@example.com")
        for u in (ok.replace("calendar.google.com", "calendar.google.com.evil.example"), ok.replace("calendar.google.com", "x@example.com"),
                  ok.replace("private-abc", "public"), ok.replace("https", "http"), ok.replace("me%40example.com", "abc%40group.calendar.google.com"),
                  ok.replace("me%40example.com", "holiday"), "https://evil.example/me.ics", ""):
            self.assertEqual(g.owner_of(u), "", u)


if __name__ == "__main__":
    unittest.main()
