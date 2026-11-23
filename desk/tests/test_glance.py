"""python3 -m unittest desk.tests.test_glance (from the repo root).
No picture is taken, nothing is sent and no notification is shown: the app, the API and the notification are fakes."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import glance as gl  # noqa: E402

ON = {**gl.DEFAULTS, "on": True}


def at(hour, minute=0, day=23):
    return int(datetime(2026, 11, day, hour, minute, 20).timestamp())  # glance reads the Mac's own clock


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(gl, "DIR", self.tmp.name)
        p.start()
        self.addCleanup(p.stop)
        self.shown, self.asked, self.shots = [], [], 0
        gl.write_json("settings.json", ON)

    def fake_shoot(self, result=None, face=True):
        def shoot(s):
            self.shots += 1
            work = tempfile.mkdtemp(dir=gl.DIR, prefix=".shot-")
            res = result or {"ok": True, "screen": "screen.jpg", "front": {"bundle": "com.google.Chrome", "name": "Google Chrome"}, "hidden": []}
            if res.get("ok"):
                with open(os.path.join(work, "screen.jpg"), "wb") as f:
                    f.write(b"screen")
                if face:
                    with open(os.path.join(work, "face.jpg"), "wb") as f:
                        f.write(b"face")
                    res = {**res, "face": "face.jpg"}
            return res, work
        return shoot

    def fake_ask(self, kind="doing", text="広告の画像を並べています。", fail=None, tokens=3500):
        def ask(face, screen, text_in, s, key):
            self.asked.append({"face": face, "screen": screen, "text": text_in, "model": s["model"]})
            if fail:
                raise gl.Failed(fail)
            return {"kind": kind, "text": text, "activity": "Ad design"}, {"model": s["model"], "input": tokens, "output": 150}
        return ask

    def take(self, now, **kw):
        fakes = {"shoot": self.fake_shoot(), "ask": self.fake_ask(), "show": self.shown.append, "key": lambda: "k",
                 "focus": lambda: False, "tasks": lambda: ["Make the ad images"]}
        return gl.take(now, **{**fakes, **kw})

    def tick(self, now, using=True, **kw):
        fakes = {"shoot": self.fake_shoot(), "ask": self.fake_ask(), "show": self.shown.append, "key": lambda: "k",
                 "focus": lambda: False, "tasks": lambda: []}
        return gl.tick(now, using=using, **{**fakes, **kw})

    def shot_files(self):
        try:
            return sorted(os.listdir(gl.path("shots")))
        except OSError:
            return []


class Take(Case):
    def test_a_remark_is_shown_logged_and_its_pictures_kept(self):
        t = at(14, 15)
        e = self.take(t)
        self.assertEqual((e["status"], e["kind"], e["shown"]), ("ok", "doing", True))
        self.assertEqual(self.shown, ["広告の画像を並べています。"])
        self.assertEqual(self.shot_files(), [f"{t}-face.jpg", f"{t}-screen.jpg"])
        self.assertEqual(gl.read_log(t)[0]["text"], "広告の画像を並べています。")
        self.assertAlmostEqual(e["usd"], (3500 * 0.10 + 150 * 0.50) / 1e6)
        self.assertEqual([n for n in os.listdir(gl.DIR) if n.startswith(".shot-")], [])  # the work folder is gone

    def test_the_request_carries_tasks_the_app_in_front_and_earlier_remarks(self):
        self.take(at(14, 0))
        self.take(at(14, 15), ask=self.fake_ask(text="まだ同じ作業です。"))
        text = self.asked[1]["text"]
        self.assertIn("- Make the ad images", text)
        self.assertIn("App in front: Google Chrome.", text)
        self.assertIn("[doing] 広告の画像を並べています。", text)

    def test_no_photo_means_no_word_about_the_body(self):
        self.take(at(14, 0), shoot=self.fake_shoot(face=False))
        self.assertIsNone(self.asked[0]["face"])
        self.assertIn("no camera photo", self.asked[0]["text"])
        self.assertEqual(self.shot_files(), [f"{at(14, 0)}-screen.jpg"])

    def test_a_skipped_one_takes_nothing_and_says_why(self):
        e = self.take(at(14, 0), shoot=self.fake_shoot({"skipped": "app", "front": {"bundle": "jp.naver.line.mac", "name": "LINE"}}))
        self.assertEqual((e["status"], e["why"], e["front"]), ("skipped", gl.WHY["app"], "LINE"))
        self.assertEqual((self.asked, self.shown, self.shot_files()), ([], [], []))

    def test_a_failed_request_keeps_no_picture(self):
        e = self.take(at(14, 0), ask=self.fake_ask(fail="Could not reach Anthropic"))
        self.assertEqual((e["status"], e["why"]), ("failed", "Could not reach Anthropic"))
        self.assertEqual((self.shown, self.shot_files()), ([], []))
        self.assertEqual([n for n in os.listdir(gl.DIR) if n.startswith(".shot-")], [])

    def test_without_a_key_no_picture_is_taken(self):
        e = self.take(at(14, 0), key=lambda: None)
        self.assertEqual((e["status"], self.shots), ("failed", 0))

    def test_missing_screen_permission_is_told_plainly_and_remembered(self):
        res = {"error": "screen permission", "permissions": {"camera": "allowed", "screen": False}}
        e = self.take(at(14, 0), shoot=self.fake_shoot(res))
        self.assertIn("Screen Recording", e["why"])
        self.assertEqual(gl.view(at(14, 1))["permissions"]["screen"], False)

    def test_the_same_failure_twice_is_one_line(self):
        for m in (0, 15, 30):
            self.take(at(14, m), key=lambda: None)
        log = gl.read_log(at(15))
        self.assertEqual((len(log), log[0]["times"], log[0]["at"]), (1, 3, at(14, 30)))

    def test_during_focus_only_drift_is_shown(self):
        self.take(at(14, 0), focus=lambda: True)
        self.take(at(14, 15), focus=lambda: True, ask=self.fake_ask("drift", "動画を見ています。広告の画像に戻れます。"))
        self.assertEqual(self.shown, ["動画を見ています。広告の画像に戻れます。"])
        self.assertEqual([e["shown"] for e in gl.read_log(at(15))], [False, True])

    def test_notify_some_keeps_plain_remarks_in_the_log_only(self):
        gl.write_json("settings.json", {**ON, "notify": "some"})
        self.take(at(14, 0))
        self.take(at(14, 15), ask=self.fake_ask("body", "画面に顔が近いようです。"))
        self.assertEqual(self.shown, ["画面に顔が近いようです。"])


class Tick(Case):
    def test_every_15_minutes_while_in_use(self):
        took = [m for m in range(0, 46) if self.tick(at(14, 0) + m * 60)]
        self.assertEqual(took, [0, 15, 30, 45])

    def test_off_by_default(self):
        gl.write_json("settings.json", {})
        self.assertIsNone(self.tick(at(14, 0)))
        self.assertEqual(self.shots, 0)

    def test_nothing_when_nobody_is_there_and_at_once_when_they_return(self):
        self.assertIsNone(self.tick(at(14, 0), using=False))
        self.assertIsNotNone(self.tick(at(14, 1), using=True))

    def test_nothing_outside_the_hours(self):
        self.assertIsNone(self.tick(at(22, 0)))
        self.assertIsNone(self.tick(at(5, 59)))
        self.assertIsNotNone(self.tick(at(21, 59)))

    def test_pause_for_an_hour_and_until_tomorrow(self):
        gl.pause({"minutes": 60}, at(14, 0))
        self.assertIsNone(self.tick(at(14, 59)))
        self.assertIsNotNone(self.tick(at(15, 1)))
        gl.pause({"tomorrow": True}, at(15, 5))
        self.assertIsNone(self.tick(at(21, 0)))
        self.assertIsNone(self.tick(at(5, 59, day=24)))
        self.assertIsNotNone(self.tick(at(6, 0, day=24)))

    def test_the_monthly_limit_stops_it(self):
        gl.write_json("settings.json", {**ON, "monthlyLimitUsd": 0.01})
        big = self.fake_ask(tokens=60000)  # about $0.006 each
        self.assertIsNotNone(self.tick(at(14, 0), ask=big))
        self.assertIsNotNone(self.tick(at(14, 15), ask=big))  # about $0.012: over
        self.assertIsNone(self.tick(at(14, 30), ask=big))
        self.assertIn("limit", gl.view(at(14, 31))["problem"])


class Pictures(Case):
    def test_pictures_older_than_24_hours_are_deleted_and_the_remark_stays(self):
        t = at(14, 0)
        self.take(t)
        self.assertEqual(gl.sweep(t + gl.KEEP_SHOTS - 60), 0)
        self.assertEqual(gl.sweep(t + gl.KEEP_SHOTS), 2)
        self.assertEqual(self.shot_files(), [])
        e = gl.view(t + gl.KEEP_SHOTS + 1)["log"][0]
        self.assertEqual((e["text"], "screen" in e, "face" in e), ("広告の画像を並べています。", False, False))

    def test_tick_sweeps_even_when_glance_is_off(self):
        t = at(14, 0)
        self.take(t)
        gl.write_json("settings.json", {**ON, "on": False})
        self.tick(t + gl.KEEP_SHOTS + 60)
        self.assertEqual(self.shot_files(), [])

    def test_only_its_own_file_names_are_served(self):
        t = at(14, 0)
        self.take(t)
        self.assertTrue(gl.shot_path(f"{t}-screen.jpg"))
        for bad in ("../settings.json", f"{t}-screen.png", "settings.json", f"../shots/{t}-screen.jpg", None, 5):
            self.assertIsNone(gl.shot_path(bad))

    def test_forget_one_entry_or_every_picture(self):
        a, b = at(14, 0), at(14, 15)
        self.take(a)
        self.take(b)
        gl.forget({"at": a}, b + 1)
        self.assertEqual(self.shot_files(), [f"{b}-face.jpg", f"{b}-screen.jpg"])
        self.assertEqual([e["at"] for e in gl.read_log(b + 1)], [b])
        gl.forget({"pictures": True}, b + 1)
        self.assertEqual(self.shot_files(), [])
        self.assertEqual(len(gl.read_log(b + 1)), 1)


class Settings(Case):
    def test_only_sane_values_are_kept(self):
        s = gl.clean_settings({"on": 1, "every": 7, "from": -5, "skip": ["ok.app.id", "bad id", 5, "../x"], "model": "gpt", "monthlyLimitUsd": 500,
                               "lang": "xx", "notify": "loud", "extra": True})
        self.assertEqual({**s, "skip": None}, {**gl.DEFAULTS, "skip": None})
        self.assertEqual(s["skip"], ["ok.app.id"])
        s = gl.clean_settings({"on": True, "camera": False, "every": 20, "model": "claude-sonnet-5-5", "monthlyLimitUsd": 5, "lang": "en", "notify": "some"})
        self.assertEqual((s["on"], s["camera"], s["every"], s["model"], s["monthlyLimitUsd"], s["lang"], s["notify"]),
                         (True, False, 20, "claude-sonnet-5-5", 5.0, "en", "some"))

    def test_the_defaults_never_capture_line_or_password_apps(self):
        for b in ("jp.naver.line.mac", "com.1password", "com.apple.keychainaccess", "com.apple.systempreferences"):
            self.assertIn(b, gl.DEFAULTS["skip"])
        self.assertFalse(gl.DEFAULTS["on"])

    def test_view_has_what_the_page_needs(self):
        v = gl.view(at(14, 0))
        for k in ("settings", "bedtime", "log", "now", "paused", "next", "built", "permissions", "month", "problem", "apps", "models", "every"):
            self.assertIn(k, v)
        json.dumps(v)


class Prompt(unittest.TestCase):
    def test_the_guide_keeps_the_screen_private_and_never_blames(self):
        g = gl.GUIDE.format(language=gl.LANG["ja"][0], limit=gl.LANG["ja"][1])
        for part in ("no names of other people", "Never blame", "do not guess", "Japanese"):
            self.assertIn(part, g)

    def test_context_text_without_tasks(self):
        text = gl.context_text(at(14, 0), {"front": {"name": "Figma"}, "hidden": ["LINE"]}, [], [], True)
        self.assertIn("No tasks are planned for today.", text)
        self.assertIn("Left out of the screenshot on purpose (private apps): LINE.", text)

    def test_cost(self):
        self.assertAlmostEqual(gl.cost({"model": "claude-opus-5-5", "input": 1_000_000, "output": 0}), 4.0)
        self.assertAlmostEqual(gl.cost({"model": "something-new", "input": 1_000_000, "output": 0}), 0.10)


if __name__ == "__main__":
    unittest.main()
