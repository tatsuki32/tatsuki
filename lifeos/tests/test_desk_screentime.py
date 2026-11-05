"""スクリーンタイム: totals per day from a knowledgeC-shaped database, and old days survive the database's cleanup."""
import datetime as dt
import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desk"))
import screentime_sync as st

HCM = dt.timezone(dt.timedelta(hours=7))


def at(*args):
    return dt.datetime(*args, tzinfo=HCM).timestamp() - st.EPOCH


def make_db(path, rows):
    con = sqlite3.connect(path)
    con.executescript("""
      CREATE TABLE ZOBJECT (ZSTREAMNAME, ZVALUESTRING, ZSTARTDATE, ZENDDATE, ZSOURCE, ZSTRUCTUREDMETADATA);
      CREATE TABLE ZSOURCE (Z_PK INTEGER PRIMARY KEY, ZDEVICEID);
      CREATE TABLE ZSTRUCTUREDMETADATA (Z_PK INTEGER PRIMARY KEY, Z_DKDIGITALHEALTHMETADATAKEY__WEBDOMAIN);
      INSERT INTO ZSOURCE VALUES (1, NULL), (2, 'PHONE');
    """)
    for i, (stream, value, start, end, source, domain) in enumerate(rows):
        con.execute("INSERT INTO ZSTRUCTUREDMETADATA VALUES (?, ?)", (i + 1, domain))
        con.execute("INSERT INTO ZOBJECT VALUES (?, ?, ?, ?, ?, ?)", (stream, value, start, end, source, i + 1))
    con.commit()
    con.close()


class ScreenTime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.was = st.DIR
        st.DIR = self.tmp.name
        self.addCleanup(lambda: setattr(st, "DIR", self.was))
        self.db = os.path.join(self.tmp.name, "k.db")

    def test_midnight_splits_days_and_devices_stay_apart(self):
        make_db(self.db, [
            ("/app/usage", "com.apple.Safari", at(2026, 11, 4, 23, 30), at(2026, 11, 5, 0, 30), 1, None),
            ("/app/webUsage", "com.apple.Safari", at(2026, 11, 4, 23, 30), at(2026, 11, 5, 0, 30), 1, "www.YouTube.com"),
            ("/app/usage", "com.phone.app", at(2026, 11, 5, 9), at(2026, 11, 5, 9, 10), 2, None),
        ])
        days = st.read_days(self.db)
        self.assertEqual(days["2026-11-04"]["mac"]["apps"]["com.apple.Safari"], 1800)
        self.assertEqual(days["2026-11-05"]["mac"]["sites"]["youtube.com"], 1800)
        self.assertEqual(days["2026-11-05"]["mac"]["hours"][0], 1800)
        self.assertEqual(days["2026-11-05"]["other"]["apps"]["com.phone.app"], 600)

    def test_saved_days_survive_and_the_cut_first_day_does_not_replace(self):
        old = {"2026-10-01": {"mac": st.blank()}, "2026-10-02": {"mac": {"apps": {"a": 500}, "sites": {}, "hours": [0] * 24}}}
        new = {"2026-10-02": {"mac": {"apps": {"a": 5}, "sites": {}, "hours": [0] * 24}}, "2026-10-03": {"mac": st.blank()}}
        out = st.merge(old, new)
        self.assertIn("2026-10-01", out)
        self.assertEqual(out["2026-10-02"]["mac"]["apps"]["a"], 500)
        self.assertIn("2026-10-03", out)

    def test_view_compares_with_the_period_before(self):
        day = lambda secs: {"mac": {"apps": {"x": secs}, "sites": {"a.com": secs}, "hours": [0] * 24}}
        st.write_json("days.json", {"2026-11-05": day(600), "2026-10-29": day(1200)})
        v = st.view("week", today=dt.date(2026, 11, 5))
        self.assertEqual(v["total"], 600)
        self.assertEqual(v["prev"], 1200)
        self.assertEqual(v["sites"][0]["key"], "a.com")
        self.assertEqual(v["sites"][0]["prev"], 1200)
        self.assertEqual(len(v["days"]), 7)
        two = st.view("today", today=dt.date(2026, 11, 5))
        self.assertIsNone(two["prev"])

    def test_tab_minutes_join_the_sites(self):
        os.makedirs(os.path.join(st.DIR, "tabs"))
        with open(os.path.join(st.DIR, "tabs", "2026-11-05.log"), "w", encoding="utf-8") as f:
            f.write("chrome youtube.com\nbrave youtube.com\nchrome x.com\n")
        st.write_json("days.json", {"2026-11-05": {"mac": {"apps": {}, "sites": {"youtube.com": 60}, "hours": [0] * 24}}})
        v = st.view("today", today=dt.date(2026, 11, 5))
        self.assertEqual([(r["key"], r["sec"]) for r in v["sites"]], [("youtube.com", 180), ("x.com", 60)])
        self.assertEqual(st.view("today", "other", today=dt.date(2026, 11, 5))["sites"], [])

    def test_denied_database_is_reported(self):
        was, st.DB = st.DB, os.path.join(self.tmp.name, "missing", "k.db")
        self.addCleanup(lambda: setattr(st, "DB", was))
        self.assertEqual(st.run(), 1)
        self.assertFalse(st.read_json("status.json", {})["ok"])


if __name__ == "__main__":
    unittest.main()
