"""python3 -m unittest desk.tests.test_cards (from the repo root). No network and no keychain: Claude is a fake."""
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("LIFELOG_DATA", "/nonexistent")  # never touch the keychain
TMP = tempfile.mkdtemp()
os.environ["DESK_READER_DIR"] = TMP
import cards  # noqa: E402


class MemStore(cards.Store):
    def __init__(self, items=None):
        self.items = list(items or [])
        super().__init__(lambda col: list(self.items), self._upsert)

    def _upsert(self, col, doc_id, rec, merge):
        self.items = [x for x in self.items if x["id"] != doc_id] + [{**rec, "id": doc_id}]
        return {**rec, "id": doc_id}


def fake(answer, sent):
    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def opener(req, timeout):
        sent.append(json.loads(req.data))
        return R(json.dumps({"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(answer)}]}).encode())
    return opener


INFO = {"lemma": "go", "meaning_ja": "行く", "meaning_en": "to move from one place to another", "ipa": "ɡoʊ", "example": "I go home.", "pos": "verb"}


class LookupTest(unittest.TestCase):
    def test_claude_gets_only_the_word_and_sentence_and_the_cache_answers_next(self):
        sent = []
        info = cards.lookup("went", "en", "She went home.", key="k", opener=fake(INFO, sent))
        self.assertEqual(info["lemma"], "go")
        self.assertEqual(len(sent), 1)
        text = sent[0]["messages"][0]["content"]
        self.assertIn("went", text)
        self.assertIn("She went home.", text)
        self.assertNotIn("chapter", text.lower())
        again = cards.lookup("Went", "en", "other", key="k", opener=fake({}, sent))
        self.assertEqual(again["lemma"], "go")
        self.assertEqual(len(sent), 1, "the cache must answer the second time")

    def test_no_key_is_unavailable(self):
        with self.assertRaises(cards.Unavailable):
            cards.ask_claude("x", "en", "", key=None)


class AddTest(unittest.TestCase):
    def test_same_word_becomes_one_card(self):
        st = MemStore()
        code, out = cards.add(st, {"term": "went", "lang": "en", "context": "She went home.", "info": INFO,
                                   "source": {"kind": "book", "ref": "b1"}, "book": {"id": "b1", "title": "A Book", "chapter": "1"}})
        self.assertEqual(code, 200)
        self.assertFalse(out["merged"])
        c = out["card"]
        self.assertEqual(c["lemma"], "go")
        self.assertEqual(c["meaning"]["ja"], "行く")
        self.assertEqual(c["enrich"]["state"], "ok")
        self.assertEqual(c["fsrs"]["state"], "new")
        code, out2 = cards.add(st, {"term": "go", "lang": "en", "context": "Go now."})
        self.assertTrue(out2["merged"])
        self.assertEqual(out2["card"]["id"], c["id"])
        self.assertEqual(out2["card"]["lookups"], 2)
        self.assertEqual(len([x for x in st.items if not x.get("deleted")]), 1)

    def test_without_claude_the_card_waits_for_its_meaning(self):
        st = MemStore()
        code, out = cards.add(st, {"term": "serendipity", "lang": "en", "context": "Pure serendipity."}, timeout=1)
        self.assertEqual(code, 200)
        self.assertEqual(out["card"]["enrich"]["state"], "pending")
        self.assertEqual([c["term"] for c in cards.pending(st.items)], ["serendipity"])

    def test_a_different_language_is_a_different_card(self):
        st = MemStore()
        cards.add(st, {"term": "pain", "lang": "en", "info": {**INFO, "lemma": "pain"}})
        cards.add(st, {"term": "pain", "lang": "fr", "info": {**INFO, "lemma": "pain"}})
        self.assertEqual(len(st.items), 2)

    def test_translate_cards_without_state_are_not_pending(self):
        tr = {"id": "cd1", "term": "chat", "lang": "fr", "meaning": {"ja": "猫", "en": "cat"}, "fsrs": {"due": 0, "reps": 0}, "status": "active"}
        self.assertEqual(cards.pending([tr]), [])
        d = cards.due([tr], [], now=10)
        self.assertEqual((d["new"], d["due"], d["total"]), (1, 0, 1))

    def test_suggest_needs_two_lookups_and_stays_out_of_the_queue(self):
        st = MemStore()
        cards.suggest(st, {"term": "ubiquitous", "lang": "en"})
        self.assertEqual(st.items[0]["status"], "suggested")
        d = cards.due(st.items, [])
        self.assertEqual((d["total"], d["suggested"]), (0, 1))
        code, out = cards.add(st, {"term": "ubiquitous", "lang": "en", "info": {**INFO, "lemma": "ubiquitous"}})
        self.assertTrue(out["merged"])
        self.assertEqual(out["card"]["status"], "active")


class DueTest(unittest.TestCase):
    def test_counts(self):
        now = 1_700_000_000_000
        mk = lambda i, state, due: {"id": f"c{i}", "term": f"w{i}", "status": "active", "fsrs": {"state": state, "due": due, "reps": 0 if state == "new" else 3}}
        items = [mk(1, "review", now - 1), mk(2, "review", now + DAY_MS), mk(3, "new", now)] + [mk(10 + i, "new", now) for i in range(25)]
        log = [{"id": cards.today_key(now), "entries": [{"c": "x", "r": 3, "at": now, "n": True}] * 5}]
        d = cards.due(items, log, now=now)
        self.assertEqual(d["due"], 1)
        self.assertEqual(d["new"], 15)  # 20 a day, 5 already seen
        self.assertEqual(d["newTotal"], 26)
        self.assertEqual(d["reviewed"], 5)


DAY_MS = 86400000


class ImportTest(unittest.TestCase):
    def test_sheet_by_header_names_and_duplicates(self):
        csv_text = "単語,意味,例文\nwent,行った,She went home.\nGo,行く,\nserendipity,偶然の発見,\n"
        items = cards.parse_sheet(csv_text, "en")
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["meaning"]["ja"], "行った")
        have = [{"id": "c1", "term": "go", "lemma": "go", "lang": "en", "meaning": {"ja": "", "en": ""}, "status": "active"}]
        new, filled = cards.merge_imports(have, items)
        self.assertEqual([c["term"] for c in new], ["went", "serendipity"])
        self.assertEqual(filled[0]["meaning"]["ja"], "行く")
        self.assertEqual(new[0]["enrich"]["state"], "ok")

    def test_sheet_without_header(self):
        items = cards.parse_sheet("apple,りんご\nbanana,バナナ\n", "en")
        self.assertEqual([i["term"] for i in items], ["apple", "banana"])

    def test_anki_export(self):
        text = "#separator:tab\n#html:true\napple<br>[sound:a.mp3]\t<b>りんご</b>\nrun\tto move fast\n"
        items = cards.parse_anki(text, "en")
        self.assertEqual(items[0]["term"], "apple")
        self.assertEqual(items[0]["meaning"], {"ja": "りんご", "en": ""})
        self.assertEqual(items[1]["meaning"], {"ja": "", "en": "to move fast"})
        new, _ = cards.merge_imports([], items + items)
        self.assertEqual(len(new), 2, "duplicates inside the file become one card")


if __name__ == "__main__":
    unittest.main()
