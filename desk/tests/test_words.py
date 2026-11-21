import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ["DESK_WORDS_DIR"] = tempfile.mkdtemp()
import words  # noqa: E402

DAY = 86400000
NOW = 1795219000000


def lk(text, frm, to, result, via="desk", at=NOW, **info):
    return {"text": text, "from": frm, "to": to, "result": result, "via": via, "at": at, "key": f"{frm}:{text.lower()}", "word": True, "info": info}


def card(term, lang, ja="", kind="book", anki="sent", at=NOW, example=""):
    return {"id": "c" + term, "term": term, "lang": lang, "meaning": {"ja": ja, "en": ""}, "source": {"kind": kind}, "status": "active",
            "anki": {"state": anki, "at": at} if anki else {}, "createdAt": at, "example": example}


class WordsTest(unittest.TestCase):
    def test_counts_per_language_and_japanese_lookups_count_for_the_other_side(self):
        b = words.build([lk("спасибо", "ru", "ja", "ありがとう"), lk("спасибо", "ru", "ja", "ありがとう"), lk("使う", "ja", "en", "use")],
                        [card("ягода", "ru", "ベリー")])
        s = words.summary(b)
        ru = next(r for r in s["langs"] if r["lang"] == "ru")
        en = next(r for r in s["langs"] if r["lang"] == "en")
        self.assertEqual((ru["looked"], ru["inAnki"]), (2, 1))  # the same word twice is one word
        self.assertEqual(en["looked"], 1)
        self.assertNotIn("ja", [r["lang"] for r in s["langs"]])
        self.assertEqual(b["ru:спасибо"]["looked"], 2)

    def test_learned_comes_from_the_anki_snapshot(self):
        b = words.build([], [card("ягода", "ru", "ベリー")], {"words": ["ru:ягода"], "byLang": {"ru": 7, "eo": 3}})
        self.assertTrue(b["ru:ягода"]["learned"])
        s = words.summary(b, {"byLang": {"ru": 7, "eo": 3}})
        self.assertEqual({r["lang"]: r["learned"] for r in s["langs"]}, {"ru": 7, "eo": 3})

    def page(self, book, lang, page=None, today=None):
        items, new = words._plan(book, lang, page, today)
        return words.page_lines(items), items, new

    def test_only_learned_and_safe_words_go_on_the_page(self):
        learned = {"words": ["ru:дом", "ru:кабан", "ru:иван", "ru:кот", "ru:река", "ru:я люблю тебя очень сильно"]}
        b = words.build([lk("Иван", "ru", "ja", "イワン", pos="proper noun"),
                         lk("я люблю тебя очень сильно", "ru", "ja", "とても愛してる"),
                         lk("кот", "ru", "ja", "猫", via="search"),
                         lk("дом", "ru", "ja", "家", examples=[{"text": "Это мой дом.", "translation": "これは私の家です。"}]),
                         lk("вода", "ru", "ja", "水")],
                        [card("кабан", "ru", "イノシシ", kind="sheet", example="my diary line"), card("река", "ru", "川")],
                        learned, hidden=["ru:река"])
        lines, _, _ = self.page(b, "ru")
        text = "\n".join(lines)
        self.assertIn(" дом — 家", lines)
        self.assertIn("  Это мой дом. — これは私の家です。", lines)
        self.assertIn(" кабан — イノシシ", lines)
        for bad in ("Иван", "люблю", "кот", "diary", "река", "вода"):  # вода is looked up but not learned yet
            self.assertNotIn(bad, text)

    def test_markup_cannot_get_into_the_page(self):
        b = words.build([lk("[x] #tag", "en", "ja", "[link]")], [], {"words": ["en:[x] #tag"]})
        self.assertEqual(self.page(b, "en")[0][1], " (x) tag — (link)")

    def test_sent_words_stay_and_new_ones_go_below_with_the_date(self):
        import datetime as dt
        d1, d2 = dt.date(2026, 11, 21), dt.date(2026, 12, 3)
        b = words.build([lk("house", "en", "ja", "家"), lk("water", "en", "ja", "水")], [], {"words": ["en:house"]})
        _, items, new = self.page(b, "en", today=d1)
        self.assertEqual([i["k"] for i in new], ["en:house"])
        # later: the meaning changes on the Desk and a second word is learned
        b = words.build([lk("house", "en", "ja", "住宅"), lk("water", "en", "ja", "水")], [], {"words": ["en:house", "en:water"]})
        lines, items2, new2 = self.page(b, "en", {"items": items}, today=d2)
        self.assertEqual(lines, ["[* Added 2026-11-21]", " house — 家", "", "[* Added 2026-12-03]", " water — 水"])
        self.assertEqual([i["k"] for i in new2], ["en:water"])

    def test_publish_only_languages_turned_on_then_only_changes(self):
        sent = []

        class R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(req, timeout=0):
            sent.append((req.get_method(), req.full_url, req.headers.get("Authorization"), json.loads(req.data)))
            return R(json.dumps({"page": {"id": "p1"}}).encode())

        snap = {"words": ["ru:дом", "en:house"]}
        b = words.build([lk("дом", "ru", "ja", "家"), lk("house", "en", "ja", "家")], [], snap)
        words._write("publish.json", {})
        words.publish(b, k="K", site="https://rb.test", opener=opener)
        self.assertEqual(sent, [])  # nothing is on yet
        words.set_lang("ru", True)
        st = words.publish(b, k="K", site="https://rb.test", opener=opener)
        self.assertEqual(len(sent), 1)
        m, url, auth, body = sent[0]
        self.assertEqual((m, url, auth, body["title"], body["setPub"]), ("PUT", "https://rb.test/_/api/desk/words", "Bearer K", "Russian words", True))
        self.assertEqual(st["langs"]["ru"]["url"], "https://rb.test/Russian_words")
        words.publish(b, k="K", site="https://rb.test", opener=opener)
        self.assertEqual(len(sent), 1)  # nothing changed, nothing sent
        words.set_lang("ru", False)
        words.publish(b, k="K", site="https://rb.test", opener=opener)
        self.assertEqual((len(sent), sent[-1][3]["setPub"]), (2, False))  # made private once, words kept
        words.publish(b, k="K", site="https://rb.test", opener=opener)
        self.assertEqual(len(sent), 2)

    def test_reader_lookup_is_one_per_word_and_day(self):
        got = {}
        up = lambda col, i, rec, merge: got.setdefault(col, {}).__setitem__(i, rec)
        a = words.reader_lookup(up, "Press", "en", {"meaning_ja": "出版社", "meaning_en": "publisher", "pos": "noun", "example": "x"})
        b2 = words.reader_lookup(up, "press", "en", {"meaning_ja": "出版社"})
        self.assertEqual(a, b2)
        rec = got["lookups"][a]
        self.assertEqual((rec["via"], rec["from"], rec["result"], rec["key"]), ("reader", "en", "出版社", "en:press"))

    def test_deck_names_say_the_language(self):
        self.assertEqual(words.deck_lang("Desk::Russian"), "ru")
        self.assertEqual(words.deck_lang("Learn Esperanto: en"), "eo")
        self.assertEqual(words.deck_lang("Top 5000 - 1,395 Russian Verbs"), "ru")
        self.assertEqual(words.deck_lang("Default"), "")


if __name__ == "__main__":
    unittest.main()
