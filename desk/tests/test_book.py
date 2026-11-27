"""python3 -m unittest desk.tests.test_book  (from the repository root). Nothing here reaches tany.im."""
import json
import os
import sys
import tempfile
import unittest

TMP = tempfile.mkdtemp()
os.environ["DESK_BOOK_DIR"] = os.path.join(TMP, "book")
os.environ["DESK_BOOK_KEY"] = "testkey"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import book  # noqa: E402


class Settings(unittest.TestCase):
    def test_defaults_when_empty(self):
        s = book.clean_settings({})
        self.assertEqual(s["buffer"], 15)
        self.assertEqual(len(s["purposes"]), 4)
        self.assertEqual(s["hours"]["1"], [["09:00", "18:00"]])
        self.assertEqual(s["hours"]["0"], [])

    def test_bad_values_fall_back(self):
        s = book.clean_settings({"buffer": 999, "daysAhead": 0, "step": 7, "hours": {"1": [["25:00", "10:00"], ["10:00", "10:00"], ["08:00", "12:00"]]},
                                 "special": {"2026-12-25": [], "bad": [["10:00", "11:00"]]}, "replyTo": "not an address", "purposes": [{"id": "x y", "name": {"en": "Odd"}}]})
        self.assertEqual(s["buffer"], 15)
        self.assertEqual(s["daysAhead"], 14)
        self.assertEqual(s["step"], 30)
        self.assertEqual(s["hours"]["1"], [["08:00", "12:00"]])
        self.assertEqual(list(s["special"]), ["2026-12-25"])
        self.assertEqual(s["replyTo"], "")
        self.assertEqual(s["purposes"][0], {"id": "xy", "min": 30, "name": {"en": "Odd", "ja": "", "ru": "", "vi": ""}})

    def test_purpose_name_falls_back_to_english(self):
        s = book.clean_settings({})
        self.assertEqual(book.purpose_name(s, "advice"), "Need advice")
        self.assertEqual(book.purpose_name(s, "advice", "ja"), "相談したい")
        self.assertEqual(book.purpose_name(s, "gone"), "gone")


class Pull(unittest.TestCase):
    def setUp(self):
        os.makedirs(book.DIR, exist_ok=True)
        for f in os.listdir(book.DIR):  # each test starts from an empty folder
            os.remove(os.path.join(book.DIR, f))
        self.calls = []
        remote = {"settings": {**book.DEFAULTS, "updatedAt": 5, "ownerName": "Remote"},
                  "bookings": [{"id": "b1", "start": 1, "end": 2, "purpose": "first", "name": "A", "status": "booked", "created": 1}],
                  "links": [{"id": "l1", "note": "for A", "windows": [], "expires": None, "off": False, "created": 1, "used": 0}],
                  "google": {"connected": True, "ok": True, "error": ""}, "mail": True}

        def fake(method, path, body=None, timeout=30, auth=True):
            self.calls.append((method, path, body))
            if method == "PUT":
                return {**book.DEFAULTS, **body}
            return remote
        self.orig = book.worker
        book.worker = fake

    def tearDown(self):
        book.worker = self.orig

    def test_remote_rules_are_adopted_and_bookings_named(self):
        st = book.pull()
        self.assertEqual(st["error"], "")
        self.assertEqual(book.settings()["ownerName"], "Remote")
        v = book.view()
        self.assertEqual(v["bookings"][0]["purposeName"], "First meeting")
        self.assertEqual(v["links"][0]["id"], "l1")
        self.assertEqual([c[0] for c in self.calls], ["GET"])

    def test_newer_local_rules_are_pushed(self):
        book._write("settings.json", {**book.DEFAULTS, "updatedAt": 99, "ownerName": "Local"})
        book.pull()
        self.assertIn(("PUT", "/book/api/desk/settings"), [(c[0], c[1]) for c in self.calls])
        self.assertEqual(book.settings()["ownerName"], "Local")

    def test_link_key_survives_a_pull(self):
        book._write("links.json", {"l1": {"id": "l1", "key": "abc", "url": "https://tany.im/book/p/abc"}})
        book.pull()
        self.assertEqual(book.view()["links"][0]["url"], "https://tany.im/book/p/abc")

    def test_link_made_on_the_web_has_no_address_here(self):
        book.pull()  # tany.im/book/admin made l1: only the hash of its key exists, so this Mac never saw the address
        link = book.view()["links"][0]
        self.assertEqual((link["id"], link["key"], link["url"]), ("l1", "", ""))

    def test_rules_saved_on_the_web_win_over_older_ones_here(self):
        book._write("settings.json", {**book.DEFAULTS, "updatedAt": 3, "ownerName": "Local"})
        book.pull()  # the Worker stamped the web's save with a later time (5)
        self.assertEqual(book.settings()["ownerName"], "Remote")
        self.assertEqual([c[0] for c in self.calls], ["GET"])

    def test_home_line_names_the_next_booking(self):
        book.pull()
        book._write("bookings.json", [{"id": "b2", "start": 4e15, "end": 4e15 + 1, "name": "B", "purposeName": "Need advice", "status": "booked"}])
        h = book.home()
        self.assertEqual(h["next"]["name"], "B")
        self.assertEqual(h["problem"], "")

    def test_problem_when_google_is_not_connected(self):
        book.pull()
        st = book._state()
        st["google"] = {"connected": False}
        book._write("state.json", st)
        self.assertIn("not connected", book.home()["problem"])


class OpenTimes(unittest.TestCase):
    def test_text_groups_by_local_day(self):
        import time
        now = int(time.time() * 1000)
        day = 86400000
        base = (now // day + 1) * day + 2 * 3600000  # tomorrow 02:00 UTC
        orig = book.worker
        book.worker = lambda *a, **k: {"down": False, "ownerName": "T", "free": [{"s": base, "e": base + 2 * 3600000}, {"s": base + day, "e": base + day + 90 * 60000}]}
        try:
            out = book.open_times(7, "UTC")
        finally:
            book.worker = orig
        lines = out["text"].split("\n")
        self.assertIn("book at https://tany.im/book", lines[0])
        self.assertEqual(len(lines), 3)
        self.assertIn("02:00–04:00", lines[1])
        self.assertIn("02:00–03:30", lines[2])

    def test_paused_page_says_so(self):
        orig = book.worker
        book.worker = lambda *a, **k: {"down": True, "free": []}
        try:
            self.assertIn("paused", book.open_times()["error"])
        finally:
            book.worker = orig


class Handle(unittest.TestCase):
    def test_unknown_is_404_and_worker_errors_are_502(self):
        self.assertEqual(book.handle("GET", ["nope"], {})[0], 404)
        orig = book.worker
        book.worker = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
        try:
            code, body = book.handle("POST", ["links"], {"note": "x"})
        finally:
            book.worker = orig
        self.assertEqual((code, body["error"]), (502, "down"))


if __name__ == "__main__":
    unittest.main()
