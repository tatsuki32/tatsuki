"""毎日世界遺産: three new countries a day, no make-up days, and nothing saved without sources and credits."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desk"))
spec = importlib.util.spec_from_file_location("heritage", os.path.join(ROOT, "desk", "heritage.py"))
heritage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(heritage)
server = heritage.server

PHOTO = "0123456789abcdef.jpg"


class Heritage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old = server.DATA
        server.DATA = self.tmp.name
        self.addCleanup(lambda: setattr(server, "DATA", old))
        os.makedirs(heritage.photos_dir())
        with open(os.path.join(heritage.photos_dir(), PHOTO), "wb") as f:
            f.write(b"x")

    def photo(self, **kw):
        return {"file": PHOTO, "author": "作者", "license": "CC BY-SA 4.0", "page": "https://commons.wikimedia.org/wiki/File:X.jpg", **kw}

    def country(self, c, **kw):
        site = {"name": "遺産", "kind": "cultural", "year": 1993, "why": "理由", "url": "https://www.sekaken.jp/whinfo/list/", "photos": [self.photo()]}
        return {"code": c["code"], "name": c["name"], "capital": "首都", "about": "紹介", "sources": [{"name": "出典", "url": "https://example.org/a"}], "photos": [], "sites": [site], **kw}

    def issue(self, date="2026-11-05", **kw):
        return {"countries": [self.country(c, **kw) for c in heritage.next_countries(date)["countries"]]}

    def test_the_list_has_every_country_once(self):
        codes = [c["code"] for c in heritage.countries()]
        self.assertEqual(len(codes), 199)
        self.assertEqual(len(set(codes)), 199)
        self.assertEqual(codes[0], "JP")

    def test_a_saved_issue_moves_to_the_next_countries(self):
        first = heritage.next_countries("2026-11-05")["countries"]
        self.assertEqual(heritage.save(self.issue(), "2026-11-05")["saved"], "2026-11-05")
        following = heritage.next_countries("2026-11-06")["countries"]
        self.assertEqual(len(following), 3)
        self.assertFalse({c["code"] for c in first} & {c["code"] for c in following})

    def test_a_second_run_on_the_same_day_adds_nothing(self):
        doc = self.issue()
        heritage.save(doc, "2026-11-05")
        self.assertEqual(heritage.save(doc, "2026-11-05")["done"], "today")
        self.assertEqual(len(heritage.issues()), 1)

    def test_missed_days_are_not_made_up(self):
        heritage.save(self.issue(), "2026-11-05")
        self.assertEqual(heritage.next_countries("2026-11-09")["read"], 3)
        heritage.save(self.issue("2026-11-09"), "2026-11-09")
        self.assertEqual(len(heritage.issues()), 2)
        self.assertEqual(heritage.next_countries("2026-11-10")["read"], 6)

    def test_no_source_or_credit_is_not_saved(self):
        doc = self.issue()
        doc["countries"][0]["sources"] = []
        doc["countries"][1]["sites"][0]["photos"][0]["author"] = ""
        doc["countries"][2]["sites"][0]["photos"][0]["license"] = "All rights reserved"
        out = heritage.save(doc, "2026-11-05")
        self.assertEqual(len(out["errors"]), 3)
        self.assertEqual(heritage.issues(), [])

    def test_a_country_without_sites_needs_the_reason(self):
        doc = self.issue(sites=[], photos=[self.photo()])
        self.assertTrue(any("noSites" in e for e in heritage.save(doc, "2026-11-05")["errors"]))
        doc = self.issue(sites=[], photos=[self.photo()], noSites="条約を結んで間もないため")
        self.assertIn("saved", heritage.save(doc, "2026-11-05"))

    def test_only_free_licenses_pass(self):
        for name in ("CC0", "CC BY 4.0", "CC BY-SA 3.0", "Public domain", "CC-BY-SA-4.0"):
            self.assertTrue(heritage.FREE_LICENSE.match(name), name)
        for name in ("CC BY-NC 4.0", "CC BY-ND 4.0", "CC BY-NC-SA 2.0", "All rights reserved", ""):
            self.assertFalse(heritage.FREE_LICENSE.match(name), name)

    def test_the_feed_reaches_the_page(self):
        heritage.save(self.issue(), "2026-11-05")
        with open(os.path.join(server.DATA, "heritage.js"), encoding="utf-8") as f:
            text = f.read()
        self.assertTrue(text.startswith("window.HERITAGE="))
        self.assertEqual(json.loads(text[len("window.HERITAGE="):].rstrip(";\n"))[0]["countries"][0]["code"], "JP")


if __name__ == "__main__":
    unittest.main()
