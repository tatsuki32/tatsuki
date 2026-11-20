"""desk/anki.py against a fake AnkiConnect (desk/tests/fake_ankiconnect.py). No real Anki is touched.
python3 -m unittest desk.tests.test_anki"""
import os
import socket
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

with socket.socket() as _s:
    _s.bind(("127.0.0.1", 0))
    PORT = _s.getsockname()[1]
os.environ["ANKI_URL"] = f"http://127.0.0.1:{PORT}"
os.environ["ANKI_NO_AUDIO"] = "1"

import anki  # noqa: E402
import cards  # noqa: E402
import fake_ankiconnect  # noqa: E402


class MemStore:
    def __init__(self, items):
        self.items = {c["id"]: dict(c) for c in items}

    def cards(self):
        return [dict(c) for c in self.items.values()]

    def put(self, card):
        self.items[card["id"]] = dict(card)
        return card


def card(term, lang="en", kind="book", meaning=("意味", "meaning"), context="", **kw):
    c = cards.new_card(term, lang, context, {"kind": kind, "ref": ""})
    c["meaning"] = {"ja": meaning[0], "en": meaning[1]}
    c["enrich"] = {"state": "ok" if any(meaning) else "pending", "at": 1}
    c.update(kw)
    return c


class AnkiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, _ = fake_ankiconnect.serve(PORT)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.fake = fake_ankiconnect.FakeAnki()  # same server, a fresh collection for each test
        anki._state.update(checkedAt=0, dueAt=0, due=None)
        self._swap(self.fake)

    def _swap(self, fake):
        handler = self.srv.RequestHandlerClass

        def do_POST(h):
            import json
            out = json.dumps(fake.respond(h.rfile.read(int(h.headers.get("Content-Length") or 0)))).encode()
            h.send_response(200)
            h.send_header("Content-Type", "application/json")
            h.end_headers()
            h.wfile.write(out)
        handler.do_POST = do_POST

    def notes_in(self, deck):
        return [n for n in self.fake.notes.values() if self.fake.cards[n["cards"][0]]["deckName"] == deck]

    def test_sends_to_language_deck_with_model(self):
        s = MemStore([card("went", lemma="go", context="She went to the market early.")])
        out = anki.round_(s)
        self.assertEqual(out["sent"], 1)
        self.assertIn("Desk Word", self.fake.models)
        self.assertEqual(self.fake.models["Desk Word"], anki.FIELDS)
        notes = self.notes_in("Desk::English")
        self.assertEqual(len(notes), 1)
        f = notes[0]["fields"]
        self.assertEqual(f["Term"]["value"], "went")
        self.assertIn("<b>went</b>", f["Context"]["value"])
        self.assertIn("意味", f["Meaning"]["value"])
        c = s.cards()[0]
        self.assertEqual(c["anki"]["state"], "sent")
        self.assertEqual(c["anki"]["noteId"], notes[0]["noteId"])
        self.assertEqual(anki.round_(s)["sent"], 0)  # never twice

    def test_existing_word_anywhere_is_not_added(self):
        s = MemStore([card("esti", lang="eo")])
        out = anki.round_(s)
        self.assertEqual(out, {"sent": 0, "exists": 1, "updated": 0, "failed": 0})
        a = s.cards()[0]["anki"]
        self.assertEqual((a["state"], a["deck"]), ("exists", "Esperanto"))
        self.assertEqual(len(self.fake.notes), 1)

    def test_substring_is_not_the_same_word(self):
        s = MemStore([card("est", lang="eo")])
        self.assertEqual(anki.round_(s)["sent"], 1)

    def test_anki_cards_and_unmeaningful_ones_wait(self):
        s = MemStore([card("Saluton", lang="eo", kind="anki"), card("later", meaning=("", "")),
                      card("Molim kafu.", lang="sr", kind="sheet"), card("idea", status="suggested")])
        out = anki.round_(s)
        self.assertEqual(out["sent"], 1)
        self.assertEqual(len(self.notes_in("Desk::Serbian")), 1)
        st = anki.status(s.cards())
        self.assertEqual((st["waiting"], st["noMeaning"]), (1, 1))  # "later" waits for its meaning; Anki's own and suggestions are not counted

    def test_old_decks_and_history_untouched(self):
        before = (dict(self.fake.cards), {k: v["fields"]["Front"]["value"] for k, v in self.fake.notes.items()})
        anki.round_(MemStore([card("went"), card("esti", lang="eo")]))
        old = next(iter(before[0]))
        self.assertEqual(self.fake.cards[old], before[0][old])
        self.assertEqual(self.fake.notes[self.fake.cards[old]["note"]]["fields"]["Front"]["value"], "esti")
        self.assertNotIn("deleteNotes", self.fake.calls)
        self.assertTrue(set(self.fake.calls) <= {"version", "modelNames", "createModel", "deckNames", "createDeck", "findNotes",
                                                  "notesInfo", "cardsInfo", "addNote", "updateNoteFields", "findCards"})

    def test_later_meaning_updates_unreviewed_and_unedited_fields_only(self):
        c = card("ubiquitous", meaning=("", "everywhere"), context="Phones are ubiquitous.")
        s = MemStore([c])
        anki.round_(s)
        c = s.cards()[0]
        nid = c["anki"]["noteId"]
        self.fake.notes[nid]["fields"]["Example"]["value"] = "my own example"  # edited in Anki
        c["meaning"] = {"ja": "至る所にある", "en": "everywhere"}
        c["example"] = "Desk example"
        s.put(c)
        self.assertEqual(anki.round_(s)["updated"], 1)
        f = self.fake.notes[nid]["fields"]
        self.assertIn("至る所にある", f["Meaning"]["value"])
        self.assertEqual(f["Example"]["value"], "my own example")
        # once reviewed in Anki, the Desk no longer writes
        self.fake.cards[self.fake.notes[nid]["cards"][0]]["reps"] = 1
        c = s.cards()[0]
        c["ipa"] = "juːˈbɪkwɪtəs"
        s.put(c)
        anki.round_(s)
        self.assertEqual(f["IPA"]["value"], "")
        self.assertTrue(s.cards()[0]["anki"].get("frozen"))

    def test_closed_anki_keeps_cards_waiting(self):
        old = anki.URL
        anki.URL = "http://127.0.0.1:9"
        try:
            s = MemStore([card("went")])
            with self.assertRaises(anki.Closed):
                anki.round_(s)
            self.assertNotIn("anki", s.cards()[0])
            st = anki.status(s.cards())
            self.assertFalse(st["connected"])
            self.assertEqual(st["waiting"], 1)
        finally:
            anki.URL = old
            anki._state["checkedAt"] = 0
        anki.round_(s)
        self.assertEqual(s.cards()[0]["anki"]["state"], "sent")

    def test_due_count(self):
        anki._state.update(dueAt=0, due=None)
        self.assertEqual(anki.due_count(), 1)

    def test_html_in_context_is_escaped(self):
        f = anki.fields_for(card("tag", context="<script>x</script> a tag here"))
        self.assertNotIn("<script>", f["Context"])
        self.assertIn("<b>tag</b>", f["Context"])

    def test_deck_names(self):
        self.assertEqual(anki.deck_for({"lang": "sr"}), "Desk::Serbian")
        self.assertEqual(anki.deck_for({"lang": "eo"}), "Desk::Esperanto")
        self.assertEqual(anki.deck_for({"lang": "ru"}), "Desk::Russian")

    @unittest.skipUnless(sys.platform == "darwin", "say is macOS only")
    def test_audio_is_aac(self):
        os.environ.pop("ANKI_NO_AUDIO", None)
        try:
            data = anki.audio(card("hello", context="Hello there."))
        finally:
            os.environ["ANKI_NO_AUDIO"] = "1"
        self.assertTrue(data and b"ftyp" in data[:16])


if __name__ == "__main__":
    unittest.main()
