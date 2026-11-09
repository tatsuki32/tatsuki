"""通知: what rings, what waits for a digest, and what stays silent. Time and data are passed in; nothing is shown."""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desk"))
import notify as n

TZ = timezone(timedelta(hours=7))


def at(h, m=0, day=6):
    return datetime(2026, 11, day, h, m, tzinfo=TZ).timestamp()


def iso(h, m=0, day=6):
    return datetime(2026, 11, day, h, m, tzinfo=TZ).isoformat()


def data(**kw):
    base = {"tasks": [], "events": [], "calendarAt": None, "mail": [], "whatsapp": {}, "habits": [], "routines": [],
            "routinelog": [], "body": [], "screen": {}, "appNames": {}, "top3": []}
    return {**base, **kw}


def settings(**kw):
    return n.clean_settings(kw)


def run(now, d, s=None, st=None, focus=False):
    return n.plan(now, d, s or settings(), st or n.blank_state(), TZ, focus)


EVENT = {"id": "e1", "title": "打ち合わせ", "start": iso(14), "allDay": False}


class Events(unittest.TestCase):
    def test_thirty_minutes_before_rings_once(self):
        show, log, st = run(at(13, 30), data(events=[EVENT]))
        self.assertEqual([x["kind"] for x in show], ["event30"])
        self.assertEqual(show[0]["tier"], "now")
        self.assertIn("14:00 打ち合わせ", show[0]["body"])
        again, _, _ = run(at(13, 31), data(events=[EVENT]), st=st)
        self.assertEqual(again, [])

    def test_all_day_is_silent(self):
        show, _, _ = run(at(13, 30), data(events=[{"id": "e2", "title": "祝日", "start": "2026-11-06", "allDay": True}]))
        self.assertEqual(show, [])

    def test_six_hours_before_has_no_sound(self):
        ev = {**EVENT, "start": iso(16)}
        show, _, _ = run(at(10), data(events=[ev]))
        self.assertEqual([(x["kind"], x["tier"]) for x in show], [("event6h", "heads")])
        self.assertNotIn("heads", n.SOUNDS)

    def test_quiet_hours_show_nothing_and_keep_a_record(self):
        ev = {**EVENT, "start": iso(19)}
        show, log, _ = run(at(18, 30), data(events=[ev]))
        self.assertEqual(show, [])
        self.assertEqual([(e["shown"], e["held"]) for e in log], [(False, "quiet")])

    def test_morning_event_goes_into_the_morning_digest(self):
        ev = {**EVENT, "start": iso(10)}
        show, _, _ = run(at(8), data(events=[ev]))
        self.assertEqual([x["kind"] for x in show], ["morning"])
        self.assertIn("10:00 打ち合わせ", show[0]["lines"][0])


class Tasks(unittest.TestCase):
    def task(self, **kw):
        return {"id": "t1", "title": "書類を出す", "status": "notyet", "due": int(at(15) * 1000), **kw}

    def test_timed_due_rings_thirty_minutes_before(self):
        show, _, _ = run(at(14, 30), data(tasks=[self.task()]))
        self.assertEqual([x["kind"] for x in show], ["task30"])

    def test_done_and_date_only_are_silent(self):
        self.assertEqual(run(at(14, 30), data(tasks=[self.task(status="done")]))[0], [])
        date_only = self.task(due=int(at(23, 59) * 1000))
        self.assertEqual(run(at(23, 40), data(tasks=[date_only]), settings(ringFrom=0, ringTo=1440))[0], [])

    def test_rewritten_title_is_used(self):
        show, _, _ = run(at(14, 30), data(tasks=[self.task(clear={"title": "申請書をポストに入れる"})]))
        self.assertIn("申請書", show[0]["body"])


class Replies(unittest.TestCase):
    def mail(self, key, urgent=True):
        return {"key": key, "urgent": urgent, "subject_ja": "面談の時間", "from": "Aさん <a@example.com>", "gmail_url": "u" + key}

    def test_only_marked_ones_ring_and_no_address_shows(self):
        show, _, _ = run(at(16), data(mail=[self.mail("1"), self.mail("2", urgent=False)]))
        self.assertEqual(len(show), 1)
        self.assertNotIn("@", show[0]["body"])
        self.assertIn("Aさん", show[0]["body"])

    def test_answered_mail_is_silent(self):
        tasks = [{"id": "tm", "status": "done", "sourceRef": "mail:u1"}]
        self.assertEqual(run(at(16), data(mail=[self.mail("1")], tasks=tasks))[0], [])

    def test_more_than_three_become_one(self):
        show, log, st = run(at(16), data(mail=[self.mail(str(i)) for i in range(5)]))
        self.assertEqual(len(show), 1)
        self.assertIn("5 messages", show[0]["title"])
        self.assertEqual(st["urgent"]["2026-11-06"], 1)

    def test_cap_of_five_a_day(self):
        st = {**n.blank_state(), "urgent": {"2026-11-06": 5}}
        show, log, _ = run(at(16), data(mail=[self.mail("9")]), st=st)
        self.assertEqual(show, [])
        self.assertEqual(log[0]["held"], "cap")

    def test_night_reply_waits_for_the_morning_digest(self):
        wa = {"tw1": {"urgent": True, "topic": "明日の集合", "name": "Bさん", "lastId": "m1"}}
        show, _, st = run(at(3), data(whatsapp=wa))
        self.assertEqual(show, [])
        show, _, st = run(at(8), data(whatsapp=wa), st=st)
        self.assertEqual([x["kind"] for x in show], ["morning"])
        self.assertIn("明日の集合", show[0]["lines"][0])
        self.assertEqual(st["carry"], [])


class Digests(unittest.TestCase):
    def test_empty_digest_is_not_shown_and_not_retried(self):
        show, _, st = run(at(12, 30), data(), settings(on={"meal": False}))
        self.assertEqual(show, [])
        self.assertIn("noon", st["digests"]["2026-11-06"])

    def test_noon_digest_once(self):
        d = data(habits=[{"id": "h", "name": "ストレッチ", "done": []}])
        show, _, st = run(at(12, 30), d)
        self.assertEqual([x["kind"] for x in show], ["noon"])
        self.assertTrue(any("ストレッチ" in line for line in show[0]["lines"]))
        self.assertTrue(any("No meals logged" in line for line in show[0]["lines"]))
        self.assertEqual(run(at(12, 31), d, st=st)[0], [])

    def test_switching_a_kind_off_removes_its_line(self):
        d = data(screen={"2026-11-06": {"mac": {"apps": {"com.x.App": 7200}}}})
        on, _, _ = run(at(16, 30), d, settings(on={"move": False}))
        self.assertIn("Screen time", on[0]["lines"][0])
        off, _, _ = run(at(16, 30), d, settings(on={"move": False, "screen": False}))
        self.assertEqual(off, [])

    def test_missed_by_sleep_is_dropped(self):
        d = data(habits=[{"id": "h", "name": "ストレッチ", "done": []}])
        show, _, st = run(at(14), d)
        self.assertEqual(show, [])
        self.assertIn("noon", st["digests"]["2026-11-06"])

    def test_focus_holds_the_digest_and_lets_now_through(self):
        d = data(habits=[{"id": "h", "name": "ストレッチ", "done": []}], events=[{**EVENT, "start": iso(13)}])
        show, _, st = run(at(12, 30), d, focus=True)
        self.assertEqual([x["kind"] for x in show], ["event30"])
        show, _, st = run(at(13, 20), d, st=st)
        self.assertEqual([x["kind"] for x in show], ["noon"])

    def test_overuse_needs_a_real_average(self):
        day = lambda i: (datetime(2026, 11, 6, tzinfo=TZ) - timedelta(days=i)).strftime("%Y-%m-%d")
        screen = {day(i): {"mac": {"apps": {"com.x.Video": 1800}}} for i in range(1, 5)}
        screen[day(0)] = {"mac": {"apps": {"com.x.Video": 5400}}}
        lines = [t[1] for t in n.screen_lines(at(16, 30), data(screen=screen, appNames={"com.x.Video": "Video"}))]
        self.assertTrue(any("ran 1h 0m longer" in line for line in lines))

    def test_words_do_not_blame(self):
        d = data(habits=[{"id": "h", "name": "ストレッチ", "done": []}], screen={"2026-11-06": {"mac": {"apps": {"a.b": 9000}}}})
        text = " ".join(line for slot in ((12, 30), (16, 30)) for x in run(at(*slot), d)[0] for line in x["lines"])
        for bad in ("使いすぎ", "サボ", "ダメ", "できていません", "怠"):
            self.assertNotIn(bad, text)


class Nudge(unittest.TestCase):
    def test_one_task_and_not_again_within_a_week(self):
        old = int((at(16, 30) - 5 * 86400) * 1000)
        tasks = [{"id": "a", "title": "調べもの", "status": "doing", "updatedAt": old},
                 {"id": "b", "title": "別の作業", "status": "doing", "updatedAt": old + 1}]
        show, _, st = run(at(16, 30), data(tasks=tasks), settings(on={"move": False, "progress": False}))
        self.assertEqual(len(show[0]["lines"]), 1)
        self.assertIn("調べもの", show[0]["lines"][0])
        tid, _ = n.nudge(at(16, 30, day=7), data(tasks=tasks), st, TZ)
        self.assertEqual(tid, "b")

    def test_todays_three_untouched_after_three(self):
        tasks = [{"id": "a", "title": "企画を書く", "status": "notyet", "updatedAt": int(at(9, day=1) * 1000)}]
        tid, text = n.nudge(at(16, 30), data(tasks=tasks, top3=[{"id": "a", "title": "企画を書く", "status": "notyet"}]), n.blank_state(), TZ)
        self.assertEqual(tid, "a")
        self.assertIn("企画を書く", text)


class Settings(unittest.TestCase):
    def test_pause_holds_everything(self):
        show, log, _ = run(at(13, 30), data(events=[EVENT]), settings(pauseUntil=int(at(14))))
        self.assertEqual(show, [])
        self.assertEqual(log[0]["held"], "paused")

    def test_bad_values_are_ignored(self):
        s = n.clean_settings({"ringFrom": "8", "ringTo": 99999, "on": {"reply": "no", "nope": False}, "sound": 0, "extra": 1})
        self.assertEqual(s, n.DEFAULTS)

    def test_ring_hours_follow_the_clock_given(self):
        tokyo = timezone(timedelta(hours=9))
        ev = {**EVENT, "start": iso(15, 30)}  # 17:30 in Tokyo: after the ring hours there
        self.assertEqual(n.plan(at(15), data(events=[ev]), settings(), n.blank_state(), tokyo)[0], [])
        self.assertEqual(len(n.plan(at(15), data(events=[ev]), settings(), n.blank_state(), TZ)[0]), 1)

    def test_text_never_enters_the_script(self):
        self.assertFalse(any("{" in line or "%" in line for line in n.SCRIPT))


if __name__ == "__main__":
    unittest.main()
