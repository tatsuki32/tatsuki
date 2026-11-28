"""Desk's words -> Anki (AnkiConnect on 127.0.0.1:8765). Reviewing happens in Anki; the Desk's cards are the place words wait.

Only this server talks to AnkiConnect, and only while Anki is open on this Mac. Anki's files are never written directly.
Each card that has its meaning goes to a deck named after its language and where it came from (deck_for):
  Esperanto::New Express::Lesson 01 (a textbook word list), Esperanto::Reader::<book>, Esperanto::Translate, ...
as a note of the type "Desk Word" (Term, Meaning, Context, Example, IPA, Audio, Source, DeskId, Image).
The word (and its sentence) is read aloud: Google's natural voice when there is a key and a voice for the language,
else OmniVoice on this Mac (Esperanto, Serbian), else the Mac's own voice for that language; no audio rather than
a voice of another language. A photo from Wikimedia Commons goes on the back when the card has one (card_media.py).
Cards already in Anki follow: a note in an older deck is moved (its review history stays), and audio or a photo the
Desk makes later is added to the note, only into fields that are empty or still hold what the Desk put there.

The card keeps what happened in `anki`: {state: sent|exists|waiting|error, noteId, deck, at, error, sent: {field: value}}.
- Before adding, the whole collection is searched for the same word; if it is there the card is marked exists, nothing is added.
- Cards that came from Anki (source.kind "anki") are never sent back.
- A meaning filled in later changes only notes never reviewed, and only the fields that still hold what the Desk sent
  (a field edited in Anki is left alone).
- Existing decks and review history are never touched: the only writes are createDeck, createModel / modelFieldAdd /
  updateModelTemplates (Desk Word only), addNote, storeMediaFile (desk-* files), updateNoteFields and changeDeck on
  Desk Word notes the Desk made.

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
FIELDS = ["Term", "Meaning", "Context", "Example", "IPA", "Audio", "Source", "DeskId", "Image"]
MEDIA_BATCH = 10  # audio or photos added to notes already in Anki, per round (OmniVoice takes a few seconds a word)
MOVE_BATCH = 300
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
{{#Image}}<div class="img">{{Image}}</div>{{/Image}}
{{#Source}}<div class="src">{{Source}}</div>{{/Source}}"""
CSS = """.card{font-family:-apple-system,"Hiragino Sans",sans-serif;font-size:20px;text-align:center;color:#222;background:#fafafa}
.term{font-size:32px;font-weight:700}.ctx{margin-top:12px;font-size:18px;color:#444}.mean{font-size:22px}
.ipa,.src{margin-top:8px;font-size:15px;color:#777}.img{margin-top:12px}.img img{max-width:100%;max-height:260px;border-radius:6px}
.credit{font-size:12px;color:#999;margin-top:4px}.ex{margin-top:12px;font-size:17px;font-style:italic}
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

def lang_name(card):
    lang = (card.get("lang") or "en").split("-")[0]
    return LANGS.get(lang, lang.upper())


def _part(s, n=60):
    return " ".join(str(s or "").replace("::", " ").replace('"', "").replace("_", " ").split())[:n].strip() or "Untitled"


def _chapter(ch):
    m = re.fullmatch(r"(?i)lesson\s*(\d+)", ch or "")
    return f"Lesson {int(m.group(1)):02d}" if m else _part(ch)


def deck_for(card):
    """<Language>::<where it came from>[::<part>]: a textbook by lesson, a Reader book by title, else the tool."""
    lang = lang_name(card)
    book = card.get("book") or {}
    kind = (card.get("source") or {}).get("kind")
    if book.get("title") and not book.get("id"):  # a textbook word list put in by hand (no Reader book behind it)
        return f"{lang}::{_part(book['title'])}::{_chapter(book.get('chapter'))}"
    if book.get("id"):
        return f"{lang}::Reader::{_part(book.get('title') or 'Book')}"
    return f"{lang}::" + {"translate": "Translate", "sheet": "Language log", "phone": "iPhone", "desk": "Added on the Desk"}.get(kind, "Desk")


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


def image_html(card):
    """The Image field: the photo and who made it. Empty when the card has no photo."""
    im = card.get("image") or {}
    if im.get("state") != "ok" or not im.get("file"):
        return ""
    return (f'<img src="{html.escape(media_name(card, im["file"]))}"><div class="credit">Photo: {html.escape(im.get("author", ""))}'
            f' · {html.escape(im.get("license", ""))} · Wikimedia Commons</div>')


def media_name(card, file):
    return "desk-img-" + file


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

def audio_text(card):
    text = card.get("term") or ""
    if card.get("context"):
        text += ". " + card["context"]
    return " ".join(text.split())[:600]


def audio(card):
    """(bytes, file extension, voice kind) for the word and its sentence, or None.
    Google's natural voice first (only with a key, only the text goes), then OmniVoice on this Mac, then the Mac's own
    voice for the language. A language with none of them gets no audio: a voice of another language would teach it wrong."""
    if sys.platform != "darwin" or os.environ.get("ANKI_NO_AUDIO"):
        return None
    lang = (card.get("lang") or "en").split("-")[0].lower()
    text = audio_text(card)
    if not text:
        return None
    try:
        import gtts
        vs = gtts.voices(lang)
        if vs:
            return gtts.speak(text, vs[0]["name"]), "mp3", "google"
    except Exception:  # gtts.Unavailable, or anything odd from Google: the next voice reads it
        pass
    try:
        import eo_tts
        if eo_tts.voices(lang):
            return eo_tts.speak(text, "female", lang), "m4a", "omni"
    except Exception:
        pass
    try:
        import reader  # the same voice choice as the Reader
        voices = reader.voices_for(lang)
    except Exception:
        voices = []
    if not voices:
        return None
    with tempfile.TemporaryDirectory() as d:
        src, out = os.path.join(d, "a.txt"), os.path.join(d, "a.m4a")
        with open(src, "w", encoding="utf-8") as f:  # through a file: a sentence starting with "-" is never an option
            f.write(text)
        cmd = ["/usr/bin/say", "-o", out, "--file-format=m4af", "--data-format=aac", "-f", src, "-v", voices[0]["name"]]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0 or not os.path.exists(out):
            return None
        with open(out, "rb") as f:
            return f.read(), "m4a", "mac"


# ---------- Anki side ----------

def ensure_model():
    """The note type, made once. A type made before the Image field gets it added, with the back showing it."""
    if MODEL not in (call("modelNames") or []):
        call("createModel", modelName=MODEL, inOrderFields=FIELDS, css=CSS, isCloze=False,
             cardTemplates=[{"Name": "Word", "Front": FRONT, "Back": BACK}])
        return
    have = call("modelFieldNames", modelName=MODEL) or []
    if "Image" not in have:
        call("modelFieldAdd", modelName=MODEL, fieldName="Image", index=len(have))
        call("updateModelTemplates", model={"name": MODEL, "templates": {"Word": {"Front": FRONT, "Back": BACK}}})
        call("updateModelStyling", model={"name": MODEL, "css": CSS})


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
        data, ext, _ = sound
        note["audio"] = [{"data": base64.b64encode(data).decode(), "filename": f"desk-{card['id']}.{ext}", "fields": ["Audio"]}]
    img = store_image(card)
    if img:
        fields["Image"] = img
    nid = call("addNote", note=note)
    return {"state": "sent", "noteId": nid, "deck": deck, "at": now_ms(), "sent": fields,
            "audio": sound[2] if sound else "none", "image": (card.get("image") or {}).get("file", "") if img else ""}


def store_image(card):
    """Puts the card's photo into Anki's media (desk-img-<file>) and returns the Image field, or ""."""
    im = card.get("image") or {}
    if im.get("state") != "ok":
        return ""
    import card_media
    p = card_media.image_path(im.get("file"))
    if not p:
        return ""
    with open(p, "rb") as f:
        call("storeMediaFile", filename=media_name(card, im["file"]), data=base64.b64encode(f.read()).decode())
    return image_html(card)


def changed_fields(card):
    """The fields whose Desk value differs from what was sent (Audio and DeskId never change)."""
    sent = (card.get("anki") or {}).get("sent") or {}
    return {k: v for k, v in fields_for(card).items() if k not in ("Audio", "DeskId", "Image") and v != sent.get(k)}


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


def _desk_made(value, card):
    """A media field the Desk filled (or empty): only those may be changed."""
    v = value or ""
    return not v.strip() or f"desk-{card['id']}." in v or "desk-img-" in v


def wants_move(c):
    a = c.get("anki") or {}
    return a.get("state") == "sent" and a.get("noteId") and not c.get("deleted") and a.get("deck") != deck_for(c)


def wants_media(c):
    """A note the Desk made that still lacks the audio or photo the Desk would give it now."""
    a = c.get("anki") or {}
    if a.get("state") != "sent" or not a.get("noteId") or c.get("deleted"):
        return False
    if "audio" not in a:  # sent before the voices were chosen by language
        return True
    im = c.get("image") or {}
    if im.get("state") == "want":
        return False  # a photo is being looked for
    return a.get("image", "") != (im.get("file", "") if im.get("state") == "ok" else "")


def follow(store, all_cards, known_decks):
    """Notes already in Anki: move them to the deck their card belongs in now, then add audio and photos. Counts."""
    out = {"moved": 0, "media": 0}
    groups = {}
    for c in [c for c in all_cards if wants_move(c)][:MOVE_BATCH]:
        groups.setdefault(deck_for(c), []).append(c)
    for deck, cs in groups.items():
        ensure_deck(deck, known_decks)
        ids = call("findCards", query="nid:" + ",".join(str(int(c["anki"]["noteId"])) for c in cs)) or []
        if ids:
            call("changeDeck", cards=ids, deck=deck)
        for c in cs:
            store.put({**c, "anki": {**c["anki"], "deck": deck, "at": now_ms()}})
            out["moved"] += 1
    todo = [c for c in store.cards() if wants_media(c)][:MEDIA_BATCH]
    if not todo:
        return out
    notes = {n.get("noteId"): n for n in call("notesInfo", notes=[int(c["anki"]["noteId"]) for c in todo]) or [] if n}
    for c in todo:
        a = dict(c["anki"])
        n = notes.get(a["noteId"])
        if not n or n.get("modelName") != MODEL:
            store.put({**c, "anki": {**a, "audio": a.get("audio", "none"), "image": a.get("image", ""), "at": now_ms()}})
            continue
        now = {k: (v or {}).get("value", "") for k, v in (n.get("fields") or {}).items()}
        put = {}
        if "audio" not in a:
            sound = audio(c)
            if sound and _desk_made(now.get("Audio"), c):
                data, ext, kind = sound
                fname = f"desk-{c['id']}.{ext}"
                call("storeMediaFile", filename=fname, data=base64.b64encode(data).decode())
                put["Audio"] = f"[sound:{fname}]"
            elif not sound and now.get("Audio") and _desk_made(now.get("Audio"), c):
                put["Audio"] = ""  # read by a voice of another language before: none is better
            a["audio"] = sound[2] if sound else "none"
        im = c.get("image") or {}
        want = im.get("file", "") if im.get("state") == "ok" else ""
        if im.get("state") != "want" and a.get("image", "") != want:
            if "Image" in now and _desk_made(now.get("Image"), c):
                put["Image"] = store_image(c) if want else ""
            a["image"] = want
        if put:
            call("updateNoteFields", note={"id": a["noteId"], "fields": put})
            out["media"] += 1
        store.put({**c, "anki": {**a, "at": now_ms()}})
    return out


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
    if any(wants_move(c) or wants_media(c) for c in all_cards):
        try:
            if not todo:
                ensure_model()
            out.update(follow(store, store.cards(), set(call("deckNames") or [])))
        except AnkiError as e:
            print("anki: follow", e, file=sys.stderr, flush=True)
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
            "moving": sum(1 for c in all_cards if wants_move(c)), "media": sum(1 for c in all_cards if wants_media(c)),
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
            cs = store.cards()
            if any(sendable(c) or wants_move(c) or wants_media(c) for c in cs) or now_ms() - _state["lastRound"] > 10 * 60000:
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
