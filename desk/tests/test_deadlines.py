"""Deadlines found in mail -> the Deadlines calendar (desk/deadlines.py), with the fake calendar and mail only."""
import importlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


class DeadlinesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ.update(DEADLINES_DIR=self.tmp, DEADLINES_FAKE="1", DEADLINES_TZ="Asia/Ho_Chi_Minh")
        import deadlines
        self.d = importlib.reload(deadlines)
        self.d.connect()
        self.now = self.d._now()

    def tearDown(self):
        for k in ("DEADLINES_DIR", "DEADLINES_FAKE", "DEADLINES_TZ"):
            os.environ.pop(k, None)

    def day(self, n):
        return (self.now + timedelta(days=n)).date().isoformat()

    def found(self, **kw):
        f = {"kind": "report", "title": "History report", "due": self.day(5) + "T23:59", "account": "zen",
             "messageId": "<a@uni>", "thread": "t1", "subject": "Report 3", "from": "Prof. A", "summary": "Two pages."}
        f.update(kw)
        return f

    def events(self):
        return list(self.d._fake_cal()["events"].values())

    def mails(self):
        return self.d._read("fake_mail.json", [])

    def test_timed_deadline_goes_on_the_calendar_and_one_mail_comes(self):
        r = self.d.add([self.found()])
        self.assertEqual(len(r["added"]), 1)
        ev = self.events()
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["summary"], "Due: History report")
        self.assertEqual(ev[0]["transparency"], "transparent")
        self.assertEqual([o["minutes"] for o in ev[0]["reminders"]["overrides"]], [1440, 180])
        end = datetime.fromisoformat(ev[0]["end"]["dateTime"])
        start = datetime.fromisoformat(ev[0]["start"]["dateTime"])
        self.assertEqual(end - start, timedelta(minutes=30))
        self.assertEqual(end.strftime("%H:%M"), "23:59")
        self.assertNotIn("Two pages", self.mails()[0]["text"])  # the mail carries titles and dates, not the summary
        self.assertEqual(len(self.mails()), 1)
        self.assertIn("1 deadline", self.mails()[0]["subject"])

    def test_date_only_is_all_day(self):
        self.d.add([self.found(due=self.day(3))])
        ev = self.events()[0]
        self.assertEqual(ev["start"], {"date": self.day(3)})
        self.assertEqual(ev["end"], {"date": self.day(4)})

    def test_reminder_mail_for_the_same_thing_makes_no_second_event(self):
        self.d.add([self.found()])
        r = self.d.add([self.found(messageId="<b@uni>", thread="t2", title="history report")])
        self.assertEqual(r["same"], 1)
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(len(self.mails()), 1)  # nothing new: no mail

    def test_a_new_date_moves_the_event(self):
        self.d.add([self.found()])
        r = self.d.add([self.found(messageId="<c@uni>", due=self.day(8) + "T12:00")])
        self.assertEqual(len(r["moved"]), 1)
        ev = self.events()
        self.assertEqual(len(ev), 1)
        self.assertTrue(ev[0]["end"]["dateTime"].startswith(self.day(8) + "T12:00"))
        self.assertIn("moved", self.mails()[-1]["subject"])

    def test_removed_never_comes_back(self):
        r = self.d.add([self.found()])
        self.d.remove(r["added"][0])
        self.assertEqual(self.events(), [])
        r2 = self.d.add([self.found()])
        self.assertEqual(r2["added"], [])
        self.assertEqual(self.events(), [])

    def test_deleted_in_google_calendar_counts_as_removed(self):
        self.d.add([self.found()])
        fc = self.d._fake_cal()
        fc["events"] = {}
        self.d._write("fake_calendar.json", fc)
        self.d.run()
        self.assertEqual(list(self.d.items().values())[0]["state"], "removed")
        self.d.add([self.found()])
        self.assertEqual(self.events(), [])

    def test_past_and_far_and_bad_are_skipped(self):
        r = self.d.add([self.found(due=self.day(-2)), self.found(title="x", due=self.day(500)), self.found(kind="party"), self.found(due="next friday")])
        self.assertEqual(r["added"], [])
        self.assertEqual(sorted(r["skipped"]), ["due", "kind", "past", "too far"])

    def test_suggested_dates_are_all_day_capped_and_can_be_off(self):
        many = [self.found(kind="doby", title=f"Answer {i}", messageId=f"<{i}@x>", thread=f"s{i}", due=self.day(2)) for i in range(7)]
        r = self.d.add(many)
        self.assertEqual(len(r["added"]), self.d.SOFT_MAX)
        ev = self.events()[0]
        self.assertTrue(ev["summary"].startswith("Do by: "))
        self.assertIn("Suggested date", ev["description"])
        self.assertIn("date", ev["start"])
        self.d.put_settings({"soft": False})
        r = self.d.add([self.found(kind="doby", title="Other", messageId="<z@x>", thread="z", due=self.day(2))])
        self.assertEqual(r["added"], [])

    def test_waits_until_connected_then_writes(self):
        os.environ.pop("DEADLINES_FAKE")
        os.environ["DEADLINES_FAKE_REFRESH"] = ""
        try:
            self.d.connected = lambda: False
            r = self.d.add([self.found()])
            self.assertEqual(r["waiting"], 1)
        finally:
            os.environ["DEADLINES_FAKE"] = "1"
        self.d = importlib.reload(self.d)
        self.d.run()
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(len(self.mails()), 1)

    def test_edit_fixes_the_date_and_later_mails_do_not_move_it(self):
        r = self.d.add([self.found()])
        iid = r["added"][0]
        self.d.edit(iid, {"due": self.day(6)})
        self.assertEqual(self.events()[0]["start"], {"date": self.day(6)})
        self.d.add([self.found(messageId="<d@uni>", due=self.day(9) + "T10:00")])
        self.assertEqual(self.events()[0]["start"], {"date": self.day(6)})

    def test_offsets_become_mac_time(self):
        self.d.add([self.found(due=self.day(5) + "T23:59+09:00")])
        self.assertTrue(self.events()[0]["end"]["dateTime"].endswith("21:59:00+07:00"))

    def test_japanese_titles_match(self):
        self.assertTrue(self.d.similar("西洋史 レポート課題", "西洋史レポート課題（再送）"))
        self.assertFalse(self.d.similar("西洋史 レポート", "統計学 小テスト"))
        self.assertFalse(self.d.similar("Report 2", "Report 3"))

    def test_mail_link_must_be_gmail(self):
        rec, _ = self.d.clean(self.found(gmail_url="https://evil.example/x"))
        self.assertEqual(rec["gmail_url"], "")


if __name__ == "__main__":
    unittest.main()
