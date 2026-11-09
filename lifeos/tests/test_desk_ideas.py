"""提案: a quote must be in the source it names, an idea does not come back, and only what the list names is read."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desk"))
import ideas as x
import server

URL = "https://scrapbox.io/p/%E4%BC%81%E7%94%BB"
SRC = {"kind": "cosense", "title": "企画", "url": URL, "date": "2026-10-01", "ref": "企画", "version": 5,
       "text": "読んだ記事を[即座に]残しておける場所を作りたい\n別の行", "lines": [{"id": "abc", "text": "読んだ記事を[即座に]残しておける場所を作りたい"}]}


def idea(title="記事の保管箱", text="読んだ記事を即座に残しておける場所を作りたい", url=URL, **more):
    return {"title": title, "advice": "書いた言葉がそのまま機能の説明になっています。", "next": "保存の1ボタンだけ作る", "effort": "半日",
            "quotes": [{"text": text, "url": url}], **more}


class Temp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        p = mock.patch.object(server, "DATA", self.dir.name)
        p.start()
        self.addCleanup(p.stop)
        q = mock.patch.object(x, "today", lambda: "2026-11-09")
        q.start()
        self.addCleanup(q.stop)
        x.write_json(os.path.join(x.base(), "sources.json"), {"drive": {"folders": [{"id": "F"}], "files": [{"id": "D"}], "excludeTitles": ["Letter"]}, "cosense": {"project": "p"}})

    def save(self, items, replace=False):
        path = os.path.join(self.dir.name, "issue.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"items": items}, f, ensure_ascii=False)
        return x.cmd_save(path, replace)

    def fill(self):
        x.put_corpus(SRC)


class Quotes(Temp):
    def test_a_quote_in_the_source_is_saved_with_its_line_and_date(self):
        self.fill()
        out = self.save([idea()])
        self.assertEqual(out["saved"], "2026-11-09")
        q = x.issues()[0]["items"][0]["quotes"][0]
        self.assertEqual((q["title"], q["date"]), ("企画", "2026-10-01"))
        self.assertTrue(q["url"].endswith("#abc"))

    def test_words_not_written_are_not_saved(self):
        self.fill()
        out = self.save([idea(text="本人が書いていない立派な言葉です")])
        self.assertIn("errors", out)
        self.assertEqual(x.issues(), [])

    def test_a_source_not_read_this_run_is_refused(self):
        self.fill()
        self.assertIn("errors", self.save([idea(url="https://scrapbox.io/p/other")]))

    def test_an_idea_without_a_quote_is_refused(self):
        self.fill()
        self.assertIn("errors", self.save([{**idea(), "quotes": []}]))

    def test_a_quote_with_an_address_is_refused(self):
        x.put_corpus({**SRC, "text": "連絡は someone@example.com まで", "lines": []})
        self.assertIn("errors", self.save([idea(text="連絡は someone@example.com まで")]))

    def test_more_than_five_is_refused(self):
        self.fill()
        self.assertIn("errors", self.save([idea(title=f"案{i}と全く違う名前の案その{i * 7}") for i in range(6)]))

    def test_blaming_words_are_refused(self):
        self.fill()
        self.assertIn("errors", self.save([{**idea(), "advice": "これを必ず作るべきだ"}]))

    def test_the_text_read_is_gone_after_saving(self):
        self.fill()
        self.save([idea()])
        self.assertEqual(x.corpus(), [])
        self.assertNotIn("別の行", json.dumps(x.issues(), ensure_ascii=False))


class Again(Temp):
    def issue_last_week(self, status=None):
        rec = {"date": "2026-11-02", "items": [{"id": "2026-11-02-1", "title": "記事の保管箱", "quotes": [{"text": SRC["lines"][0]["text"]}]}]}
        server.put_doc("ideas", "2026-11-02", rec)
        if status:
            server.upsert("ideaslog", "2026-11-02-1", {"status": status}, merge=False)
        self.fill()

    def test_the_same_title_does_not_come_back(self):
        self.issue_last_week()
        self.assertIn("errors", self.save([idea(title="記事の保管箱")]))

    def test_a_passed_quote_does_not_come_back_under_a_new_name(self):
        self.issue_last_week("passed")
        self.assertIn("errors", self.save([idea(title="全く別の名前を付けた提案")]))

    def test_one_issue_a_week(self):
        self.fill()
        self.save([idea()])
        self.fill()
        self.assertIn("errors", self.save([idea(title="全く別の名前を付けた提案")]))

    def test_replacing_is_refused_after_a_decision(self):
        self.fill()
        self.save([idea()])
        server.upsert("ideaslog", "2026-11-09-1", {"status": "doing"}, merge=False)
        self.fill()
        self.assertIn("errors", self.save([idea(title="全く別の名前を付けた提案")], replace=True))


class Scope(Temp):
    def test_only_listed_folders_and_files(self):
        conf = x.sources()["drive"]
        self.assertTrue(x.drive_allowed(conf, "any", "F", "note"))
        self.assertTrue(x.drive_allowed(conf, "D", "-", "note"))
        self.assertFalse(x.drive_allowed(conf, "any", "OTHER", "note"))
        self.assertFalse(x.drive_allowed(conf, "any", "F", "Letter_to_Someone"))

    def test_drive_url_keeps_no_query(self):
        self.assertEqual(x.clean_drive_url("https://docs.google.com/document/d/A/edit?usp=drivesdk&ouid=1"), "https://docs.google.com/document/d/A/edit")
        self.assertIsNone(x.clean_drive_url("https://example.com/a"))

    def test_private_names_are_never_read_and_a_page_is_read_once(self):
        now = 2_000_000_000
        conf = {"pages": ["企画"], "exclude": "カウンセリング|相談"}
        pages = [{"title": "企画", "updated": 1}, {"title": "カウンセリング_3", "updated": now}, {"title": "最近", "updated": now}, {"title": "昔", "updated": 1}, {"title": "読んだ", "updated": now}]
        got = [p["title"] for p in x.choose_pages(pages, conf, {"読んだ": now}, now)]
        self.assertEqual(got, ["企画", "最近"])

    def test_nothing_runs_without_the_list(self):
        os.remove(os.path.join(x.base(), "sources.json"))
        with self.assertRaises(SystemExit):
            x.sources()


if __name__ == "__main__":
    unittest.main()
