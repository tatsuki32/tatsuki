"""python3 -m unittest desk.tests.test_startup_clean (from the repo root). No app is closed or opened: those calls are fakes."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import startup_clean as sc  # noqa: E402

LOGIN = 1_000_000


def app(pid, bundle, launched=LOGIN, active=False, name=None):
    return {"pid": pid, "bundle": bundle, "path": f"/Applications/{name or bundle}.app", "name": name or bundle,
            "launched": launched, "active": active}


class Pick(unittest.TestCase):
    def test_keeps_claude_desk_terminal_and_helpers(self):
        apps = [app(1, sc.CLAUDE), app(2, sc.DESK_APP), app(3, "com.apple.Terminal"), app(4, "com.apple.finder"),
                app(5, "com.tatsuki.desk.linewatch"), app(6, "com.google.Chrome"), app(7, "com.spotify.client")]
        self.assertEqual([a["pid"] for a in sc.pick(apps, sc.KEEP, LOGIN)], [6, 7])

    def test_leaves_an_app_the_person_just_opened_and_is_using(self):
        apps = [app(1, "com.google.Chrome", launched=LOGIN + 30, active=True), app(2, "com.apple.Notes", launched=LOGIN + 2, active=True)]
        self.assertEqual([a["pid"] for a in sc.pick(apps, sc.KEEP, LOGIN)], [2])

    def test_second_look_skips_apps_opened_later(self):
        apps = [app(1, "com.apple.Notes", launched=LOGIN + 60), app(2, "com.apple.Mail", launched=LOGIN + 80)]
        self.assertEqual([a["pid"] for a in sc.pick(apps, sc.KEEP, LOGIN, until=LOGIN + sc.LATE)], [1])

    def test_parse(self):
        text = "12\tcom.google.Chrome\t/Applications/Google Chrome.app\tGoogle Chrome\t1700\t1\nbad line\n"
        self.assertEqual(sc.parse(text), [{"pid": 12, "bundle": "com.google.Chrome", "path": "/Applications/Google Chrome.app",
                                           "name": "Google Chrome", "launched": 1700, "active": True}])


class Due(unittest.TestCase):
    cfg = {"enabled": True, "keep": sc.KEEP}

    def test_first_login_after_boot(self):
        self.assertTrue(sc.due(LOGIN + 60, LOGIN, {}, self.cfg))

    def test_once_per_boot(self):
        self.assertFalse(sc.due(LOGIN + 60, LOGIN, {"boot": LOGIN}, self.cfg))

    def test_not_long_after_boot(self):
        self.assertFalse(sc.due(LOGIN + sc.RUN_WINDOW + 1, LOGIN, {}, self.cfg))

    def test_off(self):
        self.assertFalse(sc.due(LOGIN + 60, LOGIN, {}, {"enabled": False, "keep": []}))


class Reopen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = mock.patch.object(sc, "DIR", self.tmp.name)
        self.dir.start()
        sc.write("last.json", {"at": 2_000_000, "apps": [
            {"name": "Chrome", "bundle": "com.google.Chrome", "path": "/Applications/Google Chrome.app"},
            {"name": "Notes", "bundle": "com.apple.Notes", "path": "/System/Applications/Notes.app"}], "reopened": [], "dismissed": False})

    def tearDown(self):
        self.dir.stop()
        self.tmp.cleanup()

    def test_view_shows_until_all_are_back(self):
        self.assertTrue(sc.view(now=2_000_100)["show"])
        self.assertFalse(sc.view(now=2_000_000 + sc.SHOW_FOR + 1)["show"])

    def test_reopen_one_then_all(self):
        with mock.patch.object(sc, "open_app", return_value=True) as op:
            r = sc.reopen(["com.apple.Notes"])
            self.assertEqual(r["opened"], ["Notes"])
            op.assert_called_once_with("com.apple.Notes", "/System/Applications/Notes.app")
            r = sc.reopen()
            self.assertEqual(r["opened"], ["Chrome"])
        self.assertFalse(sc.view(now=2_000_100)["show"])

    def test_failed_open_stays_on_the_list(self):
        with mock.patch.object(sc, "open_app", return_value=False):
            r = sc.reopen()
        self.assertEqual(r["failed"], ["Chrome", "Notes"])
        self.assertTrue(sc.view(now=2_000_100)["show"])

    def test_dismiss(self):
        sc.dismiss()
        self.assertFalse(sc.view(now=2_000_100)["show"])


if __name__ == "__main__":
    unittest.main()
