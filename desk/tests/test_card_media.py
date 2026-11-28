"""desk/card_media.py with a fake Claude and a fake Commons. Nothing is sent anywhere.
python3 -m unittest desk.tests.test_card_media"""
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("DESK_READER_DIR", tempfile.mkdtemp())  # never the real images folder
os.environ["COMMONS_API"] = "https://fake-commons.test/w/api.php"

import card_media  # noqa: E402
import cards  # noqa: E402


class Resp(io.BytesIO):
    def __init__(self, body, ctype="application/json"):
        super().__init__(body)
        self.headers = type("H", (), {"get_content_type": lambda _s: ctype})()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def page(title, lic="CC BY-SA 4.0", author="<a href='x'>Ann</a>", mime="image/jpeg", index=1):
    return {"title": title, "index": index, "imageinfo": [{"mime": mime, "thumburl": "https://upload.wikimedia.org/" + title,
            "url": "https://upload.wikimedia.org/" + title, "descriptionurl": "https://commons.wikimedia.org/wiki/" + title,
            "extmetadata": {"Artist": {"value": author}, "LicenseShortName": {"value": lic}}}]}


class FakeCommons:
    def __init__(self, pages):
        self.pages, self.queries = pages, []

    def __call__(self, req, timeout=30):
        url = req.full_url
        if "api.php" in url:
            self.queries.append(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["gsrsearch"][0])
            return Resp(json.dumps({"query": {"pages": {str(i): p for i, p in enumerate(self.pages)}}}).encode())
        return Resp(b"\xff\xd8jpeg " + url.encode(), "image/jpeg")


class FakeClaude:
    def __init__(self, answer):
        self.answer, self.sent = answer, None

    def __call__(self, req, timeout=60):
        self.sent = json.loads(req.data)
        return Resp(json.dumps({"content": [{"type": "text", "text": json.dumps({"items": self.answer})}]}).encode())


class MemStore:
    def __init__(self, items):
        self.items = {c["id"]: dict(c) for c in items}

    def cards(self):
        return [dict(c) for c in self.items.values()]

    def put(self, card):
        self.items[card["id"]] = dict(card)
        return card


def card(term, ja, en="", **kw):
    c = cards.new_card(term, "eo", "", {"kind": "book", "ref": ""})
    c["meaning"] = {"ja": ja, "en": en}
    c["enrich"] = {"state": "ok", "at": 1}
    c.update(kw)
    return c


class CardMediaTest(unittest.TestCase):
    def test_only_picture_words_get_a_photo_and_only_word_and_meaning_go_out(self):
        cat, and_ = card("kato", "猫", context="La kato dormas sur la tablo."), card("kaj", "そして")
        cl = FakeClaude([{"id": cat["id"], "picture": True, "query": "cat"}, {"id": and_["id"], "picture": False, "query": ""}])
        commons = FakeCommons([page("File:Cat.jpg")])
        s = MemStore([cat, and_])
        out = card_media.round_(s, key="k", opener=commons, a_opener=cl)
        self.assertEqual((out["classified"], out["fetched"]), (2, 1))
        sent = cl.sent["messages"][0]["content"]
        self.assertIn("kato", sent)
        self.assertNotIn("dormas", sent)  # the sentence a word came from is not sent
        got = {c["term"]: c["image"] for c in s.cards()}
        self.assertEqual(got["kaj"]["state"], "none")
        self.assertEqual(got["kato"]["state"], "ok")
        self.assertEqual(got["kato"]["author"], "Ann")
        self.assertEqual(commons.queries, ["cat filetype:bitmap"])
        self.assertTrue(card_media.image_path(got["kato"]["file"]))

    def test_unfree_or_authorless_files_are_skipped(self):
        c = card("pomo", "リンゴ", image={"state": "want", "q": "apple", "at": 1})
        commons = FakeCommons([page("File:A.jpg", lic="All rights reserved"), page("File:B.jpg", author=""),
                               page("File:C.svg", mime="image/svg+xml"), page("File:D.jpg", index=4)])
        im = card_media.fetch(c, commons)
        self.assertEqual((im["state"], im["title"]), ("ok", "File:D.jpg"))

    def test_next_never_uses_the_same_photo_again_and_off_stays_off(self):
        c = card("hundo", "犬", image={"state": "ok", "q": "dog", "title": "File:Dog1.jpg", "file": "x", "at": 1})
        s = MemStore([c])
        card_media.act(s, {"id": c["id"], "action": "next"})
        im = s.cards()[0]["image"]
        self.assertEqual((im["state"], im["no"]), ("want", ["File:Dog1.jpg"]))
        got = card_media.fetch(s.cards()[0], FakeCommons([page("File:Dog1.jpg"), page("File:Dog2.jpg", index=2)]))
        self.assertEqual(got["title"], "File:Dog2.jpg")
        card_media.act(s, {"id": c["id"], "action": "off"})
        self.assertFalse(card_media.wants_classify(s.cards()[0]))
        self.assertFalse(card_media.wants_fetch(s.cards()[0]))

    def test_nothing_usable_is_missing_not_an_error(self):
        im = card_media.fetch(card("x", "x", image={"state": "want", "q": "zzz"}), FakeCommons([]))
        self.assertEqual(im["state"], "missing")

    def test_bad_image_names_are_refused(self):
        self.assertIsNone(card_media.image_path("../../etc/passwd"))
        self.assertIsNone(card_media.image_path("0123456789abcdef.svg"))


if __name__ == "__main__":
    unittest.main()
