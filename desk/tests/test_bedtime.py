"""python3 -m unittest desk.tests.test_bedtime (from the repo root). No notification is shown: the calls are fakes."""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bedtime as bt  # noqa: E402

TZ = timezone(timedelta(hours=9))
S = dict(bt.DEFAULTS)


def at(hour, minute=0, day=23):
    return datetime(2026, 11, day, hour, minute, 20, tzinfo=TZ).timestamp()


def run(times, s=S, using=True):
    """Walk the watch through `times`; returns the notifications shown, in order."""
    st, out = {}, []
    for t in times:
        note, st = bt.plan(t, s, st, using(t) if callable(using) else using, TZ)
        if note:
            out.append((datetime.fromtimestamp(t, TZ).strftime("%H:%M"), note))
    return out


def every_minute(start, minutes):
    return [start + i * 60 for i in range(minutes)]


class Plan(unittest.TestCase):
    def test_first_one_at_bedtime_rings_then_repeats_every_15_minutes_silently(self):
        shown = run(every_minute(at(21, 55), 45))
        self.assertEqual([c for c, _ in shown], ["22:00", "22:15", "22:30"])
        self.assertEqual([n["tier"] for _, n in shown], ["now", "quiet", "quiet"])
        self.assertIn("10:00 PM", shown[0][1]["body"])
        self.assertIn("15 min past bedtime", shown[1][1]["body"])
        self.assertIn("30 min past bedtime", shown[2][1]["body"])

    def test_nothing_before_bedtime_or_after_four(self):
        self.assertEqual(run(every_minute(at(20, 0), 119)), [])
        self.assertEqual(run(every_minute(at(4, 0, day=24), 120)), [])

    def test_a_closed_mac_gets_nothing_and_an_opened_one_hears_at_once(self):
        away = lambda t: not (at(22, 5) <= t < at(23, 10))
        shown = run(every_minute(at(21, 58), 90), using=away)
        self.assertEqual([c for c, _ in shown], ["22:00", "23:10", "23:25"])

    def test_first_one_late_in_the_night_says_how_late_and_rings(self):
        shown = run(every_minute(at(23, 10), 2))
        self.assertEqual(shown[0][1]["tier"], "now")
        self.assertIn("1h 10m past bedtime", shown[0][1]["body"])

    def test_the_night_runs_past_midnight_as_one_night(self):
        shown = run(every_minute(at(23, 50), 30))
        self.assertEqual([c for c, _ in shown], ["23:50", "00:05"])
        self.assertEqual([n["tier"] for _, n in shown], ["now", "quiet"])
        self.assertIn("2h 5m past bedtime", shown[1][1]["body"])

    def test_the_next_night_starts_again_with_sound(self):
        first = run(every_minute(at(22, 0), 2))
        st = {"night": "2026-11-23", "count": 5, "last": int(at(23, 0))}
        note, st = bt.plan(at(22, 0, day=24), S, st, True, TZ)
        self.assertEqual((first[0][1]["tier"], note["tier"], st["count"]), ("now", "now", 1))

    def test_settings_move_the_hour_change_the_gap_and_turn_it_off(self):
        early = run(every_minute(at(21, 25), 30), s={**S, "at": 21 * 60 + 30, "every": 10})
        self.assertEqual([c for c, _ in early], ["21:30", "21:40", "21:50"])
        self.assertEqual(run(every_minute(at(22, 0), 30), s={**S, "on": False}), [])

    def test_a_bedtime_after_midnight(self):
        shown = run(every_minute(at(0, 50), 30), s={**S, "at": 60})
        self.assertEqual([c for c, _ in shown], ["01:00", "01:15"])

    def test_words_never_blame(self):
        for text in (bt.FIRST, bt.LATE) + bt.REPEATS:
            self.assertNotRegex(text.lower(), r"should|must|again|still up|too late|bad|!")

    def test_past_text(self):
        self.assertEqual([bt.past_text(m) for m in (15, 60, 75, 125)], ["15 min", "1h", "1h 15m", "2h 5m"])


class Settings(unittest.TestCase):
    def test_only_sane_values_are_kept(self):
        s = bt.clean_settings({"on": "yes", "at": 5000, "every": 7, "extra": 1})
        self.assertEqual(s, bt.DEFAULTS)
        self.assertEqual(bt.clean_settings({"on": False, "at": 21 * 60, "every": 20}), {"on": False, "at": 21 * 60, "every": 20})
        self.assertEqual(bt.clean_settings({"at": bt.UNTIL})["at"], bt.DEFAULTS["at"])  # bedtime at 4:00 would be no night at all


class InUse(unittest.TestCase):
    def test_recent_input_and_no_lock(self):
        self.assertTrue(bt.in_use(10, False))
        self.assertFalse(bt.in_use(10, True))
        self.assertFalse(bt.in_use(bt.IN_USE + 1, False))
        self.assertTrue(bt.in_use(None, False))

    def test_watching_a_video_counts_for_an_hour(self):
        self.assertTrue(bt.in_use(20 * 60, False, watching=True))
        self.assertFalse(bt.in_use(20 * 60, False, watching=False))
        self.assertFalse(bt.in_use(bt.WATCHING + 1, False, watching=True))
        self.assertFalse(bt.in_use(20 * 60, True, watching=True))

    def test_a_longer_limit_for_callers_that_ask(self):
        self.assertTrue(bt.in_use(8 * 60, False, limit=10 * 60))


class Tick(unittest.TestCase):
    def test_tick_shows_once_and_keeps_its_state(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(bt, "DIR", d):
            shown = []
            night = datetime(2026, 11, 23, 22, 0, 20).timestamp()  # tick reads the Mac's own clock
            bt.tick(night, show=lambda n, sound: shown.append(n), using=True)
            bt.tick(night + 60, show=lambda n, sound: shown.append(n), using=True)
            self.assertEqual(len(shown), 1)
            self.assertEqual(bt.view(night + 60)["told"], 1)
            self.assertEqual(bt.put_settings({"on": False})[1]["settings"]["on"], False)
            bt.tick(night + 16 * 60, show=lambda n, sound: shown.append(n), using=True)
            self.assertEqual(len(shown), 1)


if __name__ == "__main__":
    unittest.main()
