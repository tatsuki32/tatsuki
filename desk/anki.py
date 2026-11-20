"""Desk's words -> Anki (AnkiConnect on 127.0.0.1:8765). Reviewing happens in Anki; the Desk's cards are the place words wait.

Only this server talks to AnkiConnect, and only while Anki is open on this Mac. Anki's files are never written directly.
Each card that has its meaning goes to the deck "Desk::<Language>" as a note of the type "Desk Word"
(Term, Meaning, Context, Example, IPA, Audio, Source, DeskId), with the word and its sentence read by `say` as AAC audio.

The card keeps what happened in `anki`: {state: sent|exists|waiting|error, noteId, deck, at, error, sent: {field: value}}.
- Before adding, the whole collection is searched for the same word; if it is there the card is marked exists, nothing is added.
- Cards that came from Anki (source.kind "anki") are never sent back.
- A meaning filled in later changes only notes never reviewed, and only the fields that still hold what the Desk sent
  (a field edited in Anki is left alone).
- Existing decks and review history are never touched: the only writes are createDeck (Desk::...), createModel (Desk Word),
  addNote, storeMediaFile (through addNote) and updateNoteFields on Desk Word notes.

AnkiConnect: https://ankiweb.net/shared/info/2055492159 (add-on code 2055492159). In Anki: Tools -> Add-ons -> Get Add-ons...

  python3 desk/anki.py status       is Anki answering, what is waiting
  python3 desk/anki.py once         one round now (needs the Desk server for the cards)
"""
import base64
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cards  # noqa: E402

# The real Anki only from the Desk itself (4747). A test server reaches nothing unless ANKI_URL points at a fake AnkiConnect.
URL = os.environ.get("ANKI_URL") or ("http://127.0.0.1:8765" if os.environ.get("DESK_PORT", "4747") == "4747" else "")
ADDON = "2055492159"
MODEL = "Desk Word"
FIELDS = ["Term", "Meaning", "Context", "Example", "IPA", "Audio", "Source", "DeskId"]
ROOT_DECK = "Desk"
EVERY = 60
BATCH = 30
LANGS = {**cards.LANG_NAMES, "sr": "Serbian", "eo": "Esperanto", "tok": "Toki Pona", "uk": "Ukrainian", "pl": "Polish",
         "nl": "Dutch", "tr": "Turkish", "hi": "Hindi", "el": "Greek", "cs": "Czech", "hr": "Croatian", "bg": "Bulgarian"}
FRONT = """<div class="term">{{Term}}</div>
{{#Context}}<div class="ctx">{{Context}}</div>{{/Context}}
{{Audio}}"""
BACK = """{{FrontSide}}
<hr id="answer">
<div class="mean">{{Meaning}}</div>
{{#IPA}}<div class="ipa">/{{IPA}}/</div>{{/IPA}}
{{#Example}}<div class="ex">{{Example}}</div>{{/Example}}
{{#Source}}<div class="src">{{Source}}</div>{{/Source}}"""
CSS = """.card{font-family:-apple-system,"Hiragino Sans",sans-serif;font-size:20px;text-align:center;color:#222;background:#fafafa}
.term{font-size:32px;font-weight:700}.ctx{margin-top:12px;font-size:18px;color:#444}.mean{font-size:22px}
.ipa,.src{margin-top:8px;font-size:15px;color:#777}.ex{margin-top:12px;font-size:17px;font-style:italic}
.nightMode .card,.card.nightMode{color:#eee;background:#222}.nightMode .ctx{color:#ccc}"""

_kick = threading.Event()
_round_lock = threading.Lock()  # the minute loop and "Send now" must not send the same card twice
_state = {"connected": False, "version": None, "checkedAt": 0, "error": "", "due": None, "dueAt": 0, "lastRound": 0}


class Closed(Exception):
    """Anki is not open, or AnkiConnect is not installed: cards wait."""


class AnkiError(Exception):
    """AnkiConnect answered with an error for this request."""


def now_ms():
    return int(time.time() * 1000)


def call(action, timeout=5, **params):
    if not URL:
        raise Closed("Test server: no Anki")
    body = json.dumps({"action": action, "version": 6, "params": params}).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.load(r)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Closed(str(getattr(e, "reason", e))) from None
    if not isinstance(res, dict) or "result" not in res:
        raise Closed("Not AnkiConnect")
    if res.get("error"):
        raise AnkiError(str(res["error"]))
    return res["result"]


def kick():
    """Something new to send: wake the loop now instead of within the minute."""
    _kick.set()


# ---------- what a card becomes in Anki ----------

def deck_for(card):
    lang = (card.get("lang") or "en").split("-")[0]
    return f"{ROOT_DECK}::{LANGS.get(lang, lang.upper())}"


def bold_word(context, words):
    """The sentence with the word in bold (escaped first, so nothing from the book becomes markup)."""
    out = html.escape(context or "")
    for w in sorted({w for w in words if w}, key=len, reverse=True):
        pat = re.compile(r"(?<!\w)(" + re.escape(html.escape(w)) + r")(?!\w)", re.I)
        out, n = pat.subn(r"<b>\1</b>", out, count=1)
        if n:
            break
    return out


def source_text(card):
    src = card.get("source") or {}
    book = card.get("book") or {}
    if book.get("title"):
        return " · ".join(x for x in (book.get("title"), book.get("chapter")) if x)
    return {"translate": "Translate", "sheet": "Language log", "desk": "Desk", "phone": "iPhone"}.get(src.get("kind"), "Desk")


def fields_for(card):
    m = card.get("meaning") if isinstance(card.get("meaning"), dict) else {}
    meaning = "<br>".join(html.escape(x) for x in (m.get("ja"), m.get("en")) if x)
    term = card.get("term") or ""
    return {
        "Term": html.escape(term),
        "Meaning": meaning,
        "Context": bold_word(card.get("context"), [term, card.get("lemma")]),
        "Example": html.escape(card.get("example") or ""),
        "IPA": html.escape(card.get("ipa") or ""),
        "Source": html.escape(source_text(card)),
        "DeskId": card.get("id") or "",
    }


def sendable(card):
    """Cards the Desk sends: live, accepted, with a meaning, not from Anki itself, not sent already."""
    if card.get("deleted") or card.get("status", "active") != "active":
        return False
    if (card.get("source") or {}).get("kind") == "anki":
        return False
    if not cards.has_meaning(card):
        return False
    return (card.get("anki") or {}).get("state") not in ("sent", "exists")


def waiting(all_cards):
    return [c for c in all_cards if not c.get("deleted") and c.get("status", "active") == "active"
            and (c.get("source") or {}).get("kind") != "anki" and (c.get("anki") or {}).get("state") not in ("sent", "exists")]


# ---------- audio ----------

def audio(card):
    """The word, then its sentence, read by `say` into AAC (m4a). Nothing leaves the Mac. None when there is no say."""
    if sys.platform != "darwin" or os.environ.get("ANKI_NO_AUDIO"):
        return None
    try:
        import reader  # the same voice choice as the Reader
        voices = reader.voices_for((card.get("lang") or "en")[:2])
    except Exception:
        voices = []
    text = card.get("term") or ""
    if card.get("context"):
        text += ". " + card["context"]
    with tempfile.TemporaryDirectory() as d:
        src, out = os.path.join(d, "a.txt"), os.path.join(d, "a.m4a")
        with open(src, "w", encoding="utf-8") as f:  # through a file: a sentence starting with "-" is never an option
            f.write(" ".join(text.split())[:600])
        cmd = ["/usr/bin/say", "-o", out, "--file-format=m4af", "--data-format=aac", "-f", src]
        if voices:
            cmd += ["-v", voices[0]["name"]]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0 or not os.path.exists(out):
            return None
        with open(out, "rb") as f:
            return f.read()


# ---------- Anki side ----------

def ensure_model():
    if MODEL in (call("modelNames") or []):
        return
    call("createModel", modelName=MODEL, inOrderFields=FIELDS, css=CSS, isCloze=False,
         cardTemplates=[{"Name": "Word", "Front": FRONT, "Back": BACK}])


def ensure_deck(name, known):
    if name not in known:
        call("createDeck", deck=name)
        known.add(name)


def _plain(v):
    return cards.norm(html.unescape(re.sub(r"<[^>]+>|\[sound:[^\]]*\]", " ", v or "")))


def _quote(s):
    return '"' + re.sub(r'(["\\*_:])', r"\\\1", s) + '"'


def find_existing(card):
    """A note anywhere in the collection whose first field is the same word (or its base form). None when there is none."""
    words = [w for w in {card.get("term"), card.get("lemma")} if w and cards.norm(w)]
    if not words:
        return None
    ids = call("findNotes", query=" OR ".join(_quote(w) for w in words)) or []
    if not ids:
        return None
    want = {cards.norm(w) for w in words}
    for n in call("notesInfo", notes=ids[:50]) or []:
        flds = n.get("fields") or {}
        first = min(flds.values(), key=lambda f: f.get("order", 0)).get("value") if flds else ""
        if _plain(first) in want:
            deck = ""
            if n.get("cards"):
                info = call("cardsInfo", cards=n["cards"][:1]) or []
                deck = info[0].get("deckName", "") if info else ""
            return {"noteId": n.get("noteId"), "deck": deck, "model": n.get("modelName")}
    return None


def find_own(card):
    """The note this Desk card already made (its DeskId field), or None."""
    if not card.get("id"):
        return None
    ids = call("findNotes", query='"note:' + MODEL + '" "DeskId:' + re.sub(r'(["\\*_])', r"\\\1", card["id"]) + '"' ) or []
    if not ids:
        return None
    n = (call("notesInfo", notes=ids[:1]) or [None])[0] or {}
    deck = ""
    if n.get("cards"):
        info = call("cardsInfo", cards=n["cards"][:1]) or []
        deck = info[0].get("deckName", "") if info else ""
    sent = {k: (v or {}).get("value", "") for k, v in (n.get("fields") or {}).items()}
    return {"state": "sent", "noteId": n.get("noteId") or ids[0], "deck": deck, "at": now_ms(), "sent": sent}


def send(card, known_decks):
    """One card to Anki. Returns the card's new `anki` record."""
    own = find_own(card)
    if own:
        return own
    have = find_existing(card)
    if have:
        return {"state": "exists", "noteId": have["noteId"], "deck": have["deck"], "at": now_ms()}
    deck = deck_for(card)
    ensure_deck(deck, known_decks)
    fields = fields_for(card)
    note = {"deckName": deck, "modelName": MODEL, "fields": fields, "tags": ["desk"],
            "options": {"allowDuplicate": False, "duplicateScope": "collection"}}
    sound = audio(card)
    if sound:
        note["audio"] = [{"data": base64.b64encode(sound).decode(), "filename": f"desk-{card['id']}.m4a", "fields": ["Audio"]}]
    nid = call("addNote", note=note)
    return {"state": "sent", "noteId": nid, "deck": deck, "at": now_ms(), "sent": fields}


def changed_fields(card):
    """The fields whose Desk value differs from what was sent (Audio and DeskId never change)."""
    sent = (card.get("anki") or {}).get("sent") or {}
    return {k: v for k, v in fields_for(card).items() if k not in ("Audio", "DeskId") and v != sent.get(k)}


def refresh(card):
    """A card sent before whose fields changed on the Desk: update the note only if never reviewed, field by field."""
    a = card.get("anki") or {}
    nid, sent = a.get("noteId"), a.get("sent") or {}
    changed = changed_fields(card)
    if not nid or not changed:
        return None
    info = (call("notesInfo", notes=[nid]) or [None])[0]
    if not info or info.get("modelName") != MODEL:
        return None
    if any((c.get("reps") or 0) > 0 for c in call("cardsInfo", cards=info.get("cards") or []) or []):
        return {**a, "frozen": True}  # reviewed in Anki: Anki's copy is now the real one
    now = {k: (v or {}).get("value", "") for k, v in (info.get("fields") or {}).items()}
    put = {k: v for k, v in changed.items() if now.get(k, "") == sent.get(k, "")}  # a field edited in Anki stays as it is
    if put:
        call("updateNoteFields", note={"id": nid, "fields": put})
    return {**a, "sent": {**sent, **put}, "at": now_ms()}


def round_(store):
    """One pass: send what waits, refresh what changed. Returns counts. Raises Closed when Anki is not there.
    Only one pass runs at a time; a second caller waits and then sees the cards the first one sent."""
    with _round_lock:
        return _round(store)


def _round(store):
    version = call("version", timeout=3)
    _state.update(connected=True, version=version, checkedAt=now_ms(), error="")
    all_cards = store.cards()
    todo = [c for c in all_cards if sendable(c)]
    stale = [c for c in all_cards if (c.get("anki") or {}).get("state") == "sent" and not (c.get("anki") or {}).get("frozen")
             and not c.get("deleted") and changed_fields(c)]
    out = {"sent": 0, "exists": 0, "updated": 0, "failed": 0}
    if todo:
        ensure_model()
        known = set(call("deckNames") or [])
        for c in todo[:BATCH]:
            try:
                rec = send(c, known)
            except AnkiError as e:
                rec = {"state": "error", "error": str(e)[:200], "at": now_ms()}
                out["failed"] += 1
            else:
                out[rec["state"]] += 1
            store.put({**c, "anki": rec})
    for c in stale[:BATCH]:
        try:
            rec = refresh(c)
        except AnkiError:
            continue
        if rec:
            store.put({**c, "anki": rec})
            out["updated"] += 1
    _state["lastRound"] = now_ms()
    return out


def due_count():
    """Cards due today in Anki (learning + review), the number Anki's own deck list adds up. Cached for a minute."""
    if now_ms() - _state["dueAt"] < 60000 and _state["due"] is not None:
        return _state["due"]
    try:
        n = len(call("findCards", query="is:due -is:suspended -is:buried", timeout=3) or [])
    except (Closed, AnkiError):
        _state.update(connected=False, due=None)
        return None
    _state.update(due=n, dueAt=now_ms(), connected=True)
    return n


def status(all_cards):
    """GET /api/anki: what the Cards screen and Home show."""
    w = waiting(all_cards)
    try:
        if now_ms() - _state["checkedAt"] > 20000:
            _state.update(version=call("version", timeout=2), connected=True, checkedAt=now_ms(), error="")
    except Closed as e:
        _state.update(connected=False, checkedAt=now_ms(), error=str(e)[:120], due=None)
    except AnkiError as e:
        _state.update(connected=False, checkedAt=now_ms(), error=str(e)[:120])
    return {"connected": _state["connected"], "version": _state["version"], "due": due_count() if _state["connected"] else None,
            "waiting": len(w), "noMeaning": sum(1 for c in w if not cards.has_meaning(c)),
            "errors": sum(1 for c in w if (c.get("anki") or {}).get("state") == "error"),
            "sent": sum(1 for c in all_cards if (c.get("anki") or {}).get("state") == "sent" and not c.get("deleted")),
            "lastRound": _state["lastRound"], "addon": ADDON}


def open_anki():
    """POST /api/anki/open: bring Anki to the front (or open it)."""
    if sys.platform != "darwin":
        return 501, {"error": "mac only"}
    try:
        subprocess.run(["/usr/bin/open", "-a", "Anki"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return 500, {"error": "Could not open Anki"}
    return 200, {"ok": True}


def watch(store):
    """Every minute (or right after a word is added): send what waits. Quiet while Anki is closed."""
    time.sleep(20)
    while True:
        try:
            if any(sendable(c) for c in store.cards()) or now_ms() - _state["lastRound"] > 10 * 60000:
                round_(store)
        except Closed as e:
            _state.update(connected=False, error=str(e)[:120], checkedAt=now_ms())
        except Exception as e:  # one odd answer must never stop the loop
            print("anki:", type(e).__name__, e, file=sys.stderr, flush=True)
        _kick.wait(EVERY)
        _kick.clear()
        time.sleep(2)  # the meaning usually lands a moment after the card


class _ApiStore:
    """For the command line: the cards through the running Desk server."""
    def cards(self):
        with urllib.request.urlopen(cards.LOCAL + "cards", timeout=10) as r:
            return json.load(r)

    def put(self, card):
        card = {**card, "updatedAt": now_ms()}
        req = urllib.request.Request(cards.LOCAL + "cards/" + card["id"], data=json.dumps(card).encode(), method="PUT",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)


if __name__ == "__main__":
    if sys.argv[1:2] == ["status"]:
        print(json.dumps(status(_ApiStore().cards()), indent=1))
    elif sys.argv[1:2] == ["once"]:
        try:
            print(json.dumps(round_(_ApiStore())))
        except Closed as e:
            print("Anki is not answering:", e)
    else:
        print(__doc__)
