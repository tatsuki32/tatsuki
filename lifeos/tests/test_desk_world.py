"""World Desk: the edition with a reading view (desk/sync/WORLD_EDITION.md) and what the person writes while reading."""
import json
import os
import tempfile
import unittest

from lifeos.tests.desk_import import server

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SAMPLE = os.path.join(REPO, "desk", "mock", "world-edition-sample.json")


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        old = server.DATA
        server.DATA = self.td.name
        self.addCleanup(lambda: setattr(server, "DATA", old))


class Worldlog(Base):
    def test_the_page_can_save_and_read_back_a_prediction(self):
        self.assertIn("worldlog", server.COLLECTIONS)
        server.upsert("worldlog", "2026-11-05-0", {"pick": 1, "why": "理由", "pickedAt": 1}, merge=False)
        self.assertEqual(server.load("worldlog"), [{"pick": 1, "why": "理由", "pickedAt": 1, "id": "2026-11-05-0"}])

    def test_a_later_save_replaces_the_same_story_and_keeps_the_others(self):
        server.upsert("worldlog", "2026-11-05-0", {"pick": 1}, merge=False)
        server.upsert("worldlog", "2026-11-05-1", {"pick": 0}, merge=False)
        server.upsert("worldlog", "2026-11-05-0", {"pick": 1, "forecast": [{"at": 2, "p": 60, "why": ""}]}, merge=False)
        got = {x["id"]: x for x in server.load("worldlog")}
        self.assertEqual(got["2026-11-05-0"]["forecast"][0]["p"], 60)
        self.assertEqual(got["2026-11-05-1"]["pick"], 0)


class SampleEdition(Base):
    def setUp(self):
        super().setUp()
        with open(SAMPLE, encoding="utf-8") as f:
            self.ed = json.load(f)

    def test_every_story_keeps_the_old_fields_so_an_old_page_still_shows_it(self):
        for s in self.ed["stories"]:
            for key in ("region", "title", "summary", "sources"):
                self.assertTrue(s.get(key), key)
            self.assertTrue(all(x.get("name") and x.get("url") for x in s["sources"]))

    def test_every_story_has_what_the_reading_view_needs(self):
        for s in self.ed["stories"]:
            q = s["question"]
            self.assertEqual(len(q["options"]), 3)
            self.assertIn(q["answer"], (0, 1, 2))
            self.assertTrue(q["text"] and q["result"])
            self.assertIn(len(s["media"]), (2, 3))
            for m in s["media"]:
                self.assertTrue(m["name"] and m["url"] and m["gist"] and m["full"])
                self.assertTrue(all(len(a) == 2 for a in m["axes"]))
                for line in m.get("study", []):
                    self.assertTrue(line["ja"])
                    self.assertTrue(all(1 <= len(w) <= 4 for w in line["words"]))
            self.assertEqual(len(s["whys"]), 5)
            self.assertTrue(all(w["q"] and w["a"] and (w["src"] is None or w["src"]["url"]) for w in s["whys"]))
            self.assertTrue(s["root"] and s["forecast"])

    def test_the_title_and_summary_do_not_give_the_answer_away(self):
        for s in self.ed["stories"]:
            answer = s["question"]["options"][s["question"]["answer"]]
            self.assertNotIn(answer, s["title"])
            self.assertNotIn(answer, s["summary"])

    def test_it_reaches_the_page_feed(self):
        server.put_doc("editions", self.ed["date"], self.ed)
        server.rebuild_feed("editions")
        with open(os.path.join(self.td.name, "world.js"), encoding="utf-8") as f:
            text = f.read().strip()
        feed = json.loads(text[len("window.WORLD=") : -1])
        self.assertEqual(feed[0]["id"], self.ed["date"])
        self.assertEqual(feed[0]["data"]["stories"][0]["media"][1]["lang"], "ru")


if __name__ == "__main__":
    unittest.main()
