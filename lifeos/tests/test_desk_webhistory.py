"""Web履歴: visits build up without duplicates, addresses are trimmed, and measured time and estimates are never added together."""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desk"))
import chrome_web_sync as web

END = 0x20000000


def micro(at):
    return int((at + web.CHROME_EPOCH) * 1e6)


def chrome_file(path, visits, searches=(), synced=()):
    """A file shaped like Chrome's History. visits: (id, url, title, at, duration seconds, transition)."""
    con = sqlite3.connect(path)
    con.executescript(
        """CREATE TABLE urls(id INTEGER PRIMARY KEY, url TEXT, title TEXT, hidden INTEGER DEFAULT 0);
           CREATE TABLE visits(id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER, visit_duration INTEGER, transition INTEGER, originator_cache_guid TEXT DEFAULT '');
           CREATE TABLE visit_source(id INTEGER PRIMARY KEY, source INTEGER);
           CREATE TABLE keyword_search_terms(keyword_id INTEGER, url_id INTEGER, term TEXT, normalized_term TEXT);"""
    )
    ids = {}
    for vid, url, title, at, dur, trans in visits:
        uid = ids.setdefault(url, len(ids) + 1)
        con.execute("INSERT OR REPLACE INTO urls(id, url, title) VALUES(?, ?, ?)", (uid, url, title))
        con.execute("INSERT INTO visits(id, url, visit_time, visit_duration, transition) VALUES(?, ?, ?, ?, ?)", (vid, uid, micro(at), int(dur * 1e6), trans))
    for vid in synced:
        con.execute("INSERT INTO visit_source(id, source) VALUES(?, 0)", (vid,))
    for url, term in searches:
        con.execute("INSERT INTO keyword_search_terms VALUES(1, ?, ?, ?)", (ids[url], term, term))
    con.commit()
    con.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.old = {k: os.environ.get(k) for k in ("WEBHISTORY_DIR", "CHROME_HISTORY")}
        os.environ["WEBHISTORY_DIR"] = os.path.join(self.dir, "store")
        os.environ["CHROME_HISTORY"] = os.path.join(self.dir, "History")
        self.now = time.time() - 3600

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.dir)

    def chrome(self, visits, searches=(), synced=()):
        path = os.environ["CHROME_HISTORY"]
        if os.path.exists(path):
            os.remove(path)
        chrome_file(path, visits, searches, synced)

    def rows(self, sql, *args):
        con = web.connect()
        try:
            return [dict(r) for r in con.execute(sql, args)]
        finally:
            con.close()


class CleanUrl(unittest.TestCase):
    def test_drops_query_fragment_and_password(self):
        self.assertEqual(web.clean_url("https://user:pw@www.Example.com/a/b?token=x#y"), ("https://www.example.com/a/b", "example.com"))

    def test_keeps_only_the_named_parameter(self):
        self.assertEqual(web.clean_url("https://www.youtube.com/watch?v=abc&t=9&list=z")[0], "https://www.youtube.com/watch?v=abc")

    def test_refuses_what_is_not_a_page_on_the_net(self):
        for url in ("chrome://settings", "file:///etc/hosts", "http://localhost:4747/", "http://127.0.0.1/", "", "http://[bad"):
            self.assertIsNone(web.clean_url(url), url)

    def test_estimate_is_capped_and_ignores_long_open_tabs(self):
        self.assertEqual(web.estimate(0, 60, 86400), 60)
        self.assertEqual(web.estimate(0, 9000, 0), web.EST_CAP)
        self.assertEqual(web.estimate(0, None, 40), 40)
        self.assertEqual(web.estimate(0, None, 0), 0)


class Import(Base):
    def test_twice_adds_nothing_and_old_visits_survive(self):
        t = self.now
        self.chrome([(1, "https://a.com/x?secret=1", "A", t, 10, END), (2, "https://b.com/", "B", t + 20, 10, END)])
        web.run()
        web.run()
        self.assertEqual(len(self.rows("SELECT id FROM visits")), 2)
        self.assertEqual(self.rows("SELECT url FROM visits ORDER BY at")[0]["url"], "https://a.com/x")
        self.chrome([(3, "https://c.com/", "C", t + 40, 10, END)])  # Chrome forgot the first two
        web.run()
        self.assertEqual(len(self.rows("SELECT id FROM visits")), 3)

    def test_skips_frames_redirect_steps_and_other_devices(self):
        t = self.now
        self.chrome([
            (1, "https://frame.com/", "", t, 1, 3 | END),
            (2, "https://step.com/", "", t + 1, 1, 0),
            (3, "https://other.com/", "", t + 2, 1, END),
            (4, "https://real.com/", "", t + 3, 1, END),
        ], synced=[3])
        web.run()
        self.assertEqual([r["site"] for r in self.rows("SELECT site FROM visits")], ["real.com"])

    def test_keeps_search_terms(self):
        t = self.now
        self.chrome([(1, "https://www.google.com/search?q=hello", "hello", t, 5, END)], [("https://www.google.com/search?q=hello", "hello")])
        web.run()
        r = web.searches_view("all", q="hel")
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["rows"][0]["term"], "hello")
        self.assertEqual(self.rows("SELECT url FROM searches")[0]["url"], "https://www.google.com/search")

    def test_unreadable_file_is_reported_not_raised(self):
        self.assertEqual(web.run(), 1)
        self.assertIs(web.sites_view()["status"]["importOk"], False)


class Time(Base):
    def test_same_span_sent_twice_counts_once(self):
        t = self.now
        span = {"id": "s1", "start": t, "end": t + 60, "url": "https://a.com/p?k=1", "title": "A"}
        self.assertEqual(web.add_spans([span]), 1)
        self.assertEqual(web.add_spans([span]), 0)
        self.assertEqual(self.rows("SELECT url FROM spans")[0]["url"], "https://a.com/p")

    def test_an_empty_call_does_not_count_as_hearing_from_the_extension(self):
        self.assertEqual(web.add_spans([]), 0)
        self.assertIsNone(web.sites_view()["status"]["extensionAt"])

    def test_diagnosis_keeps_only_short_words_and_numbers(self):
        web.add_spans([], {"tick": 3, "not-focused": 2, "https://a.com/x": 1, "idle": "x"})
        self.assertEqual(web.sites_view()["status"]["extensionDiag"], {"tick": 3, "not-focused": 2})
        self.assertIsNone(web.sites_view()["status"]["extensionAt"])

    def test_bad_spans_are_dropped(self):
        t = self.now
        bad = [
            {"id": "a", "start": t + 9, "end": t, "url": "https://a.com/"},
            {"id": "b", "start": t, "end": t + 99999, "url": "https://a.com/"},
            {"id": "c", "start": t, "end": t + 1, "url": "chrome://x"},
            {"id": "d", "start": "t", "end": t + 1, "url": "https://a.com/"},
            {"id": "e", "start": t, "end": time.time() + 9999, "url": "https://a.com/"},
            "nope",
        ]
        self.assertEqual(web.add_spans(bad), 0)
        self.assertIsNone(web.add_spans("nope"))

    def test_a_measured_day_does_not_add_estimates(self):
        t = self.now
        self.chrome([(1, "https://a.com/", "A", t, 100, END), (2, "https://b.com/", "B", t + 100, 100, END)])
        web.run()
        before = web.sites_view("week")
        self.assertEqual(before["total"], 200)
        self.assertEqual(before["measured"], 0)
        web.add_spans([{"id": "s", "start": t, "end": t + 30, "url": "https://a.com/", "title": "A"}])
        after = web.sites_view("week")
        self.assertEqual((after["total"], after["measured"]), (30, 30))
        self.assertEqual(after["sites"][0]["estimated"], 0)
        row = next(r for r in web.visits_view("week")["rows"] if r["site"] == "a.com")
        self.assertEqual((row["sec"], row["kind"]), (30, "measured"))


class Manage(Base):
    def setUp(self):
        super().setUp()
        t = self.now
        self.chrome(
            [(1, "https://bank.com/in", "Bank", t, 100, END), (2, "https://my.bank.com/x", "Sub", t + 100, 100, END), (3, "https://a.com/=1", "=SUM(1)", t + 200, 100, END)],
            [("https://a.com/=1", "secret word")],
        )
        web.run()
        self.t = t

    def test_excluding_deletes_and_stays_out(self):
        web.add_spans([{"id": "s", "start": self.t, "end": self.t + 60, "url": "https://bank.com/in", "title": "Bank"}])
        self.assertEqual(web.set_excluded("www.bank.com"), ["bank.com"])
        self.assertEqual(self.rows("SELECT site FROM visits WHERE site LIKE '%bank.com'"), [])
        self.assertEqual(self.rows("SELECT id FROM spans"), [])
        web.run()
        self.assertEqual(self.rows("SELECT site FROM visits WHERE site LIKE '%bank.com'"), [])
        self.assertEqual(web.add_spans([{"id": "t", "start": self.t, "end": self.t + 60, "url": "https://my.bank.com/", "title": ""}]), 0)
        self.assertEqual(web.set_excluded("bank.com", False), [])
        self.assertIsNone(web.set_excluded("not a site/"))

    def test_hidden_stays_hidden_after_import(self):
        rows = web.visits_view("all")["rows"]
        self.assertTrue(web.hide("visits", rows[0]["id"]))
        web.run()
        self.assertEqual(web.visits_view("all")["total"], 2)
        sid = web.searches_view("all")["rows"][0]["id"]
        self.assertTrue(web.hide("searches", sid))
        web.run()
        self.assertEqual(web.searches_view("all")["total"], 0)
        self.assertFalse(web.hide("spans", "1"))
        self.assertFalse(web.hide("visits", "1 OR 1=1"))

    def test_search_treats_percent_as_a_letter(self):
        self.assertEqual(web.visits_view("all", q="%")["total"], 0)
        self.assertEqual(web.visits_view("all", q="bank")["total"], 2)
        self.assertEqual(web.visits_view("all", site="a.com")["total"], 1)

    def test_csv_matches_the_screen_and_defuses_formulas(self):
        text = web.export_csv("visits", "all", q="SUM")
        lines = text.lstrip("﻿").strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("'=SUM(1)", lines[1])
        self.assertEqual(len(web.export_csv("sites", "all").strip().splitlines()), 4)
        self.assertIn("secret word", web.export_csv("searches", "all"))


class Period(unittest.TestCase):
    def test_ranges(self):
        import datetime as dt
        today = dt.date(2026, 11, 5)
        self.assertEqual(web.period_range("today", today=today), ("2026-11-05", "2026-11-05", "2026-11-04", "2026-11-04"))
        self.assertEqual(web.period_range("week", today=today)[:2], ("2026-10-30", "2026-11-05"))
        self.assertEqual(web.period_range("all", today=today), (None, None, None, None))
        self.assertEqual(web.period_range("custom", "2026-11-01", "2026-11-02", today=today), ("2026-11-01", "2026-11-02", "2026-10-30", "2026-10-31"))
        self.assertEqual(web.period_range("custom", "x", "2026-11-02", today=today)[:2], ("2026-10-30", "2026-11-05"))


class Backup(unittest.TestCase):
    def test_folder_never_goes_to_the_backup(self):
        import backup_data
        self.assertIn("webhistory/", backup_data.IGNORE)


if __name__ == "__main__":
    unittest.main()
