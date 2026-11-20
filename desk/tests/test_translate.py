"""python3 -m unittest desk.tests.test_translate (from the repo root). No network, no keychain: the openers are fake."""
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("LIFELOG_DATA", "/nonexistent")  # never touch the keychain
TMP = tempfile.mkdtemp(prefix="desk-translate-test-")
os.environ["TRANSLATE_DIR"] = os.path.join(TMP, "translate")
os.environ["WEBHISTORY_DIR"] = os.path.join(TMP, "webhistory")
import translate  # noqa: E402
import chrome_web_sync  # noqa: E402


class R(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def google(answers, sent):
    """answers: list of (translatedText, detectedSourceLanguage), used in order."""
    def opener(req, timeout):
        sent.append(json.loads(req.data))
        t, d = answers.pop(0)
        return R(json.dumps({"data": {"translations": [{"translatedText": t, "detectedSourceLanguage": d}]}}).encode())
    return opener


def claude(answers, sent):
    def opener(req, timeout):
        sent.append(json.loads(req.data))
        return R(json.dumps({"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(answers.pop(0))}]}).encode())
    return opener


def reset():
    translate._mem.clear()
    for n in os.listdir(translate.data_dir()) if os.path.isdir(translate.data_dir()) else []:
        os.remove(os.path.join(translate.data_dir(), n))


class TranslateTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_japanese_goes_to_english_and_the_rest_to_japanese(self):
        sent = []
        out = translate.translate("使う", gkey="g", g_opener=google([("use", "ja")], sent))
        self.assertEqual((out["from"], out["to"], out["result"], out["engine"]), ("ja", "en", "use", "google"))
        self.assertEqual(sent[0]["target"], "en")
        out = translate.translate("use", gkey="g", g_opener=google([("使う", "en")], sent))
        self.assertEqual((out["from"], out["to"], out["result"]), ("en", "ja", "使う"))
        self.assertEqual(sent[1]["target"], "ja")

    def test_kanji_only_japanese_is_turned_around_after_detection(self):
        sent = []
        out = translate.translate("勉強", gkey="g", g_opener=google([("勉強", "ja"), ("study", "ja")], sent))
        self.assertEqual((out["to"], out["result"]), ("en", "study"))
        self.assertEqual([s["target"] for s in sent], ["ja", "en"])

    def test_same_text_is_translated_once(self):
        sent = []
        translate.translate("use", gkey="g", g_opener=google([("使う", "en")], sent))
        out = translate.translate("use", gkey="g", g_opener=google([], sent))
        self.assertTrue(out["cached"])
        self.assertEqual(len(sent), 1)
        self.assertEqual(translate.usage()[translate.month_key()], 3)

    def test_claude_takes_over_without_a_key_or_past_the_limit(self):
        sent = []
        out = translate.translate("hello", akey="a", a_opener=claude([{"from": "en", "result": "こんにちは"}], sent))
        self.assertEqual((out["engine"], out["result"]), ("claude", "こんにちは"))
        translate._write("settings.json", {"limit": 10})
        translate.add_usage(10)
        self.assertEqual(translate.engine_state("g")["engine"], "claude")
        out = translate.translate("world", gkey="g", akey="a", a_opener=claude([{"from": "en", "result": "世界"}], sent))
        self.assertEqual(out["engine"], "claude")

    def test_lookups_are_kept_and_counted(self):
        code, out = translate.handle_translate({"text": "use"}, gkey="g", g_opener=google([("使う", "en")], []))
        self.assertEqual(code, 200)
        self.assertEqual(out["count"], 1)
        self.assertTrue(out["lookup"]["word"])
        code, out = translate.handle_translate({"text": "use"}, gkey="g")
        self.assertEqual(out["count"], 2)
        self.assertEqual([x["via"] for x in translate._load("lookups")], ["desk", "desk"])
        code, out = translate.handle_translate({"text": "", "to": "en"})
        self.assertEqual(code, 400)

    def test_enrich_picks_only_a_candidate_and_joins_the_same_meaning(self):
        a_sent = []
        translate.handle_translate({"text": "使う"}, gkey="g", g_opener=google([("use", "ja")], []))
        lk = translate._load("lookups")[0]
        code, out = translate.handle_enrich({"id": lk["id"]}, key="a", opener=claude([{
            "lemma": "使う", "pos": "verb", "reading": "つかう", "gloss": "employ something for a purpose", "senses": [],
            "examples": [{"text": "ペンを使う", "translation": "use a pen"}, {"text": "時間を使う", "translation": "spend time"}],
            "others": [{"lang": "ru", "term": "использовать"}, {"lang": "zh", "term": "用"}, {"lang": "vi", "term": "dùng"}],
            "conceptId": "c-made-up"}], a_sent))
        self.assertEqual(code, 200)
        c = out["concept"]
        self.assertNotEqual(c["id"], "c-made-up")
        self.assertEqual(c["gloss"], "employ something for a purpose")
        self.assertEqual({(m["lang"], m["term"], m["by"]) for m in c["members"]},
                         {("ja", "使う", "me"), ("en", "use", "me"), ("ru", "использовать", "claude"), ("zh", "用", "claude"), ("vi", "dùng", "claude")})
        self.assertIn("(none)", a_sent[0]["messages"][0]["content"])
        # 用 looked up next: the concept is a candidate (same translation), Claude picks it, 用 becomes looked up
        translate.handle_translate({"text": "用", "to": "en"}, gkey="g", g_opener=google([("use", "zh-CN")], []))
        lk2 = [x for x in translate._load("lookups") if x["text"] == "用"][0]
        code, out = translate.handle_enrich({"id": lk2["id"]}, key="a", opener=claude([{
            "lemma": "用", "pos": "verb", "reading": "yòng", "gloss": "employ something for a purpose", "senses": [], "examples": [],
            "others": [], "conceptId": c["id"]}], a_sent))
        self.assertEqual(out["concept"]["id"], c["id"])
        self.assertIn(c["id"], a_sent[1]["messages"][0]["content"])
        m = next(m for m in out["concept"]["members"] if m["term"] == "用")
        self.assertEqual((m["by"], m["lang"]), ("me", "zh"))
        # the same word again: the concept is reused without Claude
        code, out = translate.handle_translate({"text": "使う"}, gkey="g")
        self.assertEqual(out["concept"]["id"], c["id"])
        self.assertEqual(out["count"], 2)

    def test_two_lookups_make_a_card_candidate_and_add_makes_a_card(self):
        for _ in range(2):
            translate.handle_translate({"text": "bank"}, gkey="g", g_opener=google([("銀行", "en")], []))
        s = translate.suggestions()
        self.assertEqual([x["text"] for x in s], ["bank"])
        self.assertEqual(translate._load("cards"), [])  # nothing on its own
        code, out = translate.add_card(s[0]["lookupId"])
        card = out["card"]
        self.assertEqual((card["term"], card["lang"], card["meaning"]["ja"], card["status"], card["source"]["kind"]), ("bank", "en", "銀行", "active", "translate"))
        self.assertEqual(translate.suggestions(), [])
        translate.handle_translate({"text": "river"}, gkey="g", g_opener=google([("川", "en")], []))
        translate.handle_translate({"text": "river"}, gkey="g")
        translate.not_now("en:river")
        self.assertEqual(translate.suggestions(), [])

    def test_merge_and_split(self):
        a = translate._upsert("concepts", {"id": "c1", "gloss": "employ", "members": [{"lang": "ja", "term": "使う", "by": "me", "count": 1}], "note": ""})
        b = translate._upsert("concepts", {"id": "c2", "gloss": "use", "members": [{"lang": "en", "term": "use", "by": "me", "count": 1}], "note": ""})
        translate._upsert("lookups", {"id": "l1", "text": "使う", "key": "ja:使う", "conceptId": "c1"})
        code, out = translate.merge("c1", "c2")
        self.assertEqual(code, 200)
        self.assertEqual({m["term"] for m in out["concept"]["members"]}, {"使う", "use"})
        self.assertEqual(translate._get("concepts", "c1")["mergedInto"], "c2")
        self.assertEqual(translate._get("lookups", "l1")["conceptId"], "c2")
        code, out = translate.split("c2", "ja", "使う")
        self.assertEqual(code, 200)
        self.assertEqual([m["term"] for m in out["new"]["members"]], ["使う"])
        self.assertEqual(translate._get("lookups", "l1")["conceptId"], out["new"]["id"])
        self.assertEqual(translate.split(out["new"]["id"], "ja", "使う")[0], 400)

    def test_search_forms(self):
        for term, want in [("使う とは", "使う"), ("エポケーとは", "エポケー"), ("ubiquitous meaning", "ubiquitous"), ("стол 意味", "стол"),
                           ("雰囲気 英語で", "雰囲気"), ("what does serendipity mean", "serendipity"), ("weather tokyo", ""),
                           ("とは", ""), ("python list comprehension", "")]:
            self.assertEqual(translate.search_word(term), want, term)

    def test_ingest_from_the_web_history(self):
        con = chrome_web_sync.connect()
        try:
            with con:
                con.execute("INSERT INTO visits(id, at, date, url, title, site, est_sec) VALUES(1, 1000, '2026-11-20', 'https://translate.google.com/?sl=auto&tl=ja&text=serendipity&op=translate', 'Google Translate', 'translate.google.com', 5)")
                con.execute("INSERT INTO visits(id, at, date, url, title, site, est_sec) VALUES(2, 1001, '2026-11-20', 'https://translate.google.com/', 'Google Translate', 'translate.google.com', 5)")
                con.execute("INSERT INTO searches(id, term, at, date, site, url) VALUES(1, '使う とは', 900, '2026-11-20', 'google.com', 'https://www.google.com/search')")
                con.execute("INSERT INTO searches(id, term, at, date, site, url) VALUES(2, 'weather tokyo', 901, '2026-11-20', 'google.com', 'https://www.google.com/search')")
            out = translate.ingest(con, now=2000)
        finally:
            con.close()
        self.assertEqual(out["added"], 2)
        ids = {x["id"]: x for x in translate._load("lookups")}
        self.assertEqual(ids["g1"]["text"], "serendipity")
        self.assertEqual((ids["g1"]["via"], ids["g1"]["to"], ids["g1"]["pending"]), ("gtranslate", "ja", True))
        self.assertEqual((ids["s1"]["text"], ids["s1"]["via"]), ("使う", "search"))
        # deleted stays deleted on the next pass
        translate._upsert("lookups", {**ids["s1"], "deleted": True})
        translate._write("ingest.json", {})
        con = chrome_web_sync.connect()
        try:
            self.assertEqual(translate.ingest(con, now=2000)["added"], 0)
        finally:
            con.close()
        self.assertTrue(translate._get("lookups", "s1")["deleted"])
        n = translate.fill_pending(gkey="g", g_opener=google([("セレンディピティ", "en")], []))
        self.assertEqual(n, 1)
        self.assertEqual(translate._get("lookups", "g1")["result"], "セレンディピティ")
        self.assertFalse(translate._get("lookups", "g1")["pending"])

    def test_keep_params_keeps_the_translated_text(self):
        url, site = chrome_web_sync.clean_url("https://translate.google.com/?sl=auto&tl=ja&text=hello%20world&op=translate")
        self.assertEqual(site, "translate.google.com")
        self.assertIn("text=hello+world", url)
        self.assertNotIn("op=", url)
        self.assertEqual(translate.gtranslate_params(url), ("hello world", "auto", "ja"))


if __name__ == "__main__":
    unittest.main()
