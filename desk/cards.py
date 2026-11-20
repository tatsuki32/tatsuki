"""Flashcards for the Desk Reader (collection "cards", review records "cardlog").

One card is one word or phrase: term, lemma (base form), lang, meaning {ja, en}, ipa, example, context (the one sentence it was
met in), source {kind: book|translate|sheet|anki|phone, ref}, book {id, title, chapter}, fsrs {state, due, stability, difficulty,
reps, lapses, last}, lookups, status active|suggested, enrich {state ok|pending|failed, at}. Scheduling (FSRS) runs in the page and
in the phone app; this module keeps the data honest, fills the meaning with Claude, imports old decks and counts what is due.

Claude gets the term and that one sentence only, never a book's text. The key is the Desk's Anthropic key (lifelog.api_key).
Looked-up words are cached in desk/data/reader/dict.json so the same word is asked once.

  python3 desk/cards.py import-sheet <csv> [--lang en]     the old vocabulary sheet (needs the Desk server running)
  python3 desk/cards.py import-anki <txt> [--lang en]      an Anki text export
  python3 desk/cards.py enrich-pending                     fill the cards whose meaning is still missing
"""
import csv
import html
import http.client
import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lifelog  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("DESK_READER_DIR") or os.path.join(ROOT, "data", "reader")
DICT = os.path.join(DIR, "dict.json")
API = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MODEL = "claude-haiku-5-5"
PORT = int(os.environ.get("DESK_PORT", "4747"))
LOCAL = f"http://127.0.0.1:{PORT}/api/"
NEW_PER_DAY = 20
ENRICH_EVERY = 1800  # seconds between retries of cards whose meaning is still missing
ENRICH_BATCH = 20
TERM_MAX = 80
CONTEXT_MAX = 400
LANG_NAMES = {"en": "English", "ja": "Japanese", "ru": "Russian", "es": "Spanish", "fr": "French", "de": "German", "vi": "Vietnamese",
              "zh": "Chinese", "ko": "Korean", "it": "Italian", "pt": "Portuguese", "id": "Indonesian", "th": "Thai", "ar": "Arabic"}
_DICT_LOCK = threading.Lock()

SYSTEM = """You are a dictionary for a language learner whose first language is Japanese and who also reads English.
You get one word or phrase, the language it is in, and the sentence it was met in. Answer about that sense of the word only.
- lemma: the base (dictionary) form of the word as written in its own language (went -> go, 走った -> 走る). A phrase stays a phrase.
- meaning_ja: the meaning in natural Japanese, short (under 40 characters).
- meaning_en: the meaning in simple English, short (under 15 words). For an English word, a plain-English definition.
- ipa: the pronunciation in IPA of the lemma, without slashes. For Japanese or Chinese give the reading (kana or pinyin). Empty if unsure.
- example: one new, short example sentence in the word's language that uses the lemma in the same sense. Not the given sentence.
- pos: part of speech in one English word (noun, verb, adjective, adverb, phrase, ...).
Never invent a word that does not exist; if the term looks like a typo, give the closest real word as lemma."""

SCHEMA = {
    "type": "object",
    "properties": {"lemma": {"type": "string"}, "meaning_ja": {"type": "string"}, "meaning_en": {"type": "string"},
                   "ipa": {"type": "string"}, "example": {"type": "string"}, "pos": {"type": "string"}},
    "required": ["lemma", "meaning_ja", "meaning_en", "ipa", "example", "pos"], "additionalProperties": False,
}


class Unavailable(Exception):
    """Claude could not answer now: the card keeps enrich.state = pending and is tried again later."""


def now_ms():
    return int(time.time() * 1000)


def norm(s):
    """One spelling for the same word: lower case, one space, no quotes or punctuation at the ends."""
    s = " ".join(str(s or "").replace("’", "'").split())
    return re.sub(r"^[\s\"'“”‘(\[«]+|[\s\"'“”’)\].,;:!?»]+$", "", s).lower()


def new_id():
    return "c" + str(now_ms()) + os.urandom(2).hex()


def new_card(term, lang, context="", source=None, book=None, status="active"):
    term = " ".join(str(term or "").split())[:TERM_MAX]
    return {
        "id": new_id(), "term": term, "lemma": "", "lang": (lang or "en")[:8], "meaning": {"ja": "", "en": ""}, "ipa": "",
        "example": "", "pos": "", "context": " ".join(str(context or "").split())[:CONTEXT_MAX],
        "source": source if isinstance(source, dict) else {"kind": "book", "ref": ""},
        "book": book if isinstance(book, dict) else None, "status": status,
        "fsrs": {"state": "new", "due": now_ms(), "stability": 0, "difficulty": 0, "reps": 0, "lapses": 0, "last": 0},
        "lookups": 1, "enrich": {"state": "pending", "at": 0}, "createdAt": now_ms(), "updatedAt": now_ms(), "deleted": False,
    }


def same_word(card, term, lemma, lang):
    if card.get("deleted") or (card.get("lang") or "en") != (lang or "en"):
        return False
    mine = {norm(card.get("term")), norm(card.get("lemma"))} - {""}
    theirs = {norm(term), norm(lemma)} - {""}
    return bool(mine & theirs)


def find_same(cards, term, lemma, lang):
    return next((c for c in cards if same_word(c, term, lemma, lang)), None)


# ---------- the dictionary cache and Claude ----------

def _read_dict():
    try:
        with open(DICT, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_dict(d):
    os.makedirs(DIR, mode=0o700, exist_ok=True)
    tmp = DICT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, DICT)


def cached(term, lang):
    return _read_dict().get(f"{lang}|{norm(term)}")


def remember(term, lang, info):
    with _DICT_LOCK:
        d = _read_dict()
        d[f"{lang}|{norm(term)}"] = info
        if len(d) > 20000:  # the oldest entries go first; the card itself keeps its meaning
            for k in list(d)[: len(d) - 20000]:
                d.pop(k, None)
        _write_dict(d)


def ask_claude(term, lang, context, key=None, opener=None, timeout=25):
    """Term + one sentence -> the fields of SCHEMA. Raises Unavailable when the answer cannot be had now."""
    key = key or lifelog.api_key()
    if not key:
        raise Unavailable("No Anthropic API key is registered")
    text = f"Language: {LANG_NAMES.get(lang, lang)}\nWord or phrase: {term}\nSentence: {context or '(none)'}"
    body = {"model": MODEL, "max_tokens": 400, "system": SYSTEM,
            "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
            "messages": [{"role": "user", "content": text}]}
    req = urllib.request.Request(API, data=json.dumps(body).encode(), headers={
        "x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json", "user-agent": "tatsuki-desk"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        raise Unavailable(f"Anthropic returned {e.code}") from None
    except (http.client.HTTPException, OSError, ValueError):
        raise Unavailable("Could not reach Anthropic") from None
    out = next((b.get("text") for b in res.get("content", []) if b.get("type") == "text"), None)
    try:
        info = json.loads(out)
    except (TypeError, ValueError):
        raise Unavailable("Could not read the answer") from None
    if not isinstance(info, dict):
        raise Unavailable("Could not read the answer")
    return {k: " ".join(str(info.get(k) or "").split())[:300] for k in SCHEMA["properties"]}


def lookup(term, lang, context="", key=None, opener=None, timeout=25):
    """The cache first, then Claude. The cache is per language and spelling."""
    hit = cached(term, lang)
    if hit:
        return hit
    info = ask_claude(term, lang, context, key=key, opener=opener, timeout=timeout)
    remember(term, lang, info)
    return info


def apply_info(card, info):
    card["lemma"] = info.get("lemma") or card.get("lemma") or card["term"]
    card["meaning"] = {"ja": info.get("meaning_ja") or card.get("meaning", {}).get("ja", ""),
                       "en": info.get("meaning_en") or card.get("meaning", {}).get("en", "")}
    for k in ("ipa", "example", "pos"):
        if info.get(k):
            card[k] = info[k]
    card["enrich"] = {"state": "ok", "at": now_ms()}
    card["updatedAt"] = now_ms()
    return card


# ---------- the store: the server passes its own load / upsert so the lock stays in one place ----------

class Store:
    def __init__(self, load, upsert):
        self.load, self.upsert = load, upsert

    def cards(self):
        return self.load("cards")

    def put(self, card):
        card = {**card, "updatedAt": now_ms()}
        return self.upsert("cards", card["id"], {k: v for k, v in card.items() if k != "id"}, False)


def add(store, rec, timeout=10):
    """POST /api/cards/add: a word from the Reader (or typed). The same word in the same language becomes one card.
    The meaning is filled right away when Claude answers within `timeout` seconds; otherwise later (enrich.state = pending)."""
    term = " ".join(str(rec.get("term") or "").split())
    if not term or len(term) > TERM_MAX:
        return 400, {"error": "term"}
    lang = str(rec.get("lang") or "en")[:8]
    context = str(rec.get("context") or "")[:CONTEXT_MAX]
    info = rec.get("info") if isinstance(rec.get("info"), dict) else None  # the lookup the page already has
    if not info:
        try:
            info = lookup(term, lang, context, timeout=timeout)
        except Unavailable:
            info = None
    cards = store.cards()
    have = find_same(cards, term, (info or {}).get("lemma", ""), lang)
    if have:
        have = dict(have)
        have["lookups"] = int(have.get("lookups") or 0) + 1
        if have.get("status") == "suggested" or have.get("deleted"):
            have["status"], have["deleted"] = "active", False
        if have.get("enrich", {}).get("state") != "ok" and info:
            apply_info(have, info)
        if not have.get("context") and context:
            have["context"] = context
        return 200, {"card": store.put(have), "merged": True}
    source = rec.get("source") if isinstance(rec.get("source"), dict) else {"kind": "book", "ref": ""}
    book = rec.get("book") if isinstance(rec.get("book"), dict) else None
    card = new_card(term, lang, context, {"kind": str(source.get("kind") or "book")[:16], "ref": str(source.get("ref") or "")[:80]},
                    {k: str(book.get(k) or "")[:120] for k in ("id", "title", "chapter")} if book else None)
    if info:
        apply_info(card, info)
    return 200, {"card": store.put(card), "merged": False}


def enrich_one(store, card_id):
    card = next((c for c in store.cards() if c.get("id") == card_id), None)
    if not card:
        return 404, {"error": "not found"}
    try:
        info = lookup(card.get("term"), card.get("lang") or "en", card.get("context") or "")
    except Unavailable as e:
        card = dict(card)
        card["enrich"] = {"state": "pending", "at": now_ms(), "error": str(e)}
        return 503, {"error": str(e), "card": store.put(card)}
    card = apply_info(dict(card), info)
    # the lemma may show the card is a twin of one already there: keep both, the page shows the lookups
    return 200, {"card": store.put(card)}


def has_meaning(c):
    m = c.get("meaning") if isinstance(c.get("meaning"), dict) else {}
    return bool(m.get("ja") or m.get("en"))


def pending(cards):
    """Cards whose meaning Claude still has to fill. A card from Translate or an import that already carries a meaning is left alone."""
    out = []
    for c in cards:
        if c.get("deleted") or c.get("status") == "suggested":
            continue
        st = (c.get("enrich") or {}).get("state")
        if st == "ok" or (st is None and has_meaning(c)):
            continue
        out.append(c)
    return out


def enrich_pending(store, limit=ENRICH_BATCH):
    """Fills the cards whose meaning is still missing (added while offline, typed on the phone, imported). Stops at the first
    sign that Claude is not answering so one bad evening does not burn the quota."""
    done = 0
    for c in sorted(pending(store.cards()), key=lambda c: (c.get("enrich") or {}).get("at") or 0)[:limit]:
        code, _ = enrich_one(store, c["id"])
        if code != 200:
            break
        done += 1
    return done


def watch(store):
    time.sleep(90)
    while True:
        try:
            if lifelog.api_key():
                enrich_pending(store)
        except Exception as e:  # a bad answer must never stop the loop
            print("cards:", type(e).__name__, e, file=sys.stderr, flush=True)
        time.sleep(ENRICH_EVERY)


def suggest(store, rec):
    """POST /api/cards/suggest (the translation feature): a word looked up again becomes a suggested card after two lookups.
    Nothing is added to the review queue until the person accepts it on the Cards screen."""
    term = " ".join(str(rec.get("term") or "").split())
    if not term or len(term) > TERM_MAX:
        return 400, {"error": "term"}
    lang = str(rec.get("lang") or "en")[:8]
    cards = store.cards()
    have = find_same(cards, term, "", lang)
    if have:
        have = dict(have)
        have["lookups"] = int(have.get("lookups") or 0) + 1
        return 200, {"card": store.put(have)}
    card = new_card(term, lang, str(rec.get("context") or ""), {"kind": "translate", "ref": str(rec.get("ref") or "")[:80]}, None, "suggested")
    card["lookups"] = 1
    return 200, {"card": store.put(card)}


def today_key(ms=None):
    """The day in Ho Chi Minh time, like wins."""
    return time.strftime("%Y-%m-%d", time.gmtime((ms or now_ms()) / 1000 + 7 * 3600))


def due(cards, logs, now=None):
    """GET /api/cards/due: what the Today line shows. New cards count up to the day's limit."""
    now = now or now_ms()
    live = [c for c in cards if not c.get("deleted") and c.get("status", "active") == "active"]
    f = lambda c: c.get("fsrs") or {}
    state = lambda c: f(c).get("state") or ("new" if not f(c).get("reps") else "review")  # Translate's cards carry no state
    overdue = [c for c in live if state(c) != "new" and (f(c).get("due") or 0) <= now]
    fresh = [c for c in live if state(c) == "new"]
    day = next((d for d in logs if d.get("id") == today_key(now)), None) or {}
    new_today = sum(1 for e in day.get("entries") or [] if e.get("n"))
    reviewed = len(day.get("entries") or [])
    return {"due": len(overdue), "new": min(len(fresh), max(0, NEW_PER_DAY - new_today)), "newTotal": len(fresh),
            "newToday": new_today, "reviewed": reviewed, "total": len(live), "suggested": sum(1 for c in cards if c.get("status") == "suggested" and not c.get("deleted")),
            "pending": len(pending(cards))}


# ---------- importing the old decks ----------

TERM_COLS = ("term", "word", "front", "単語", "語", "expression", "vocabulary", "english", "英語", "phrase")
JA_COLS = ("meaning", "意味", "日本語", "ja", "japanese", "back", "translation", "訳")
EN_COLS = ("definition", "meaning_en", "english meaning", "en", "定義")
EX_COLS = ("example", "例文", "sentence", "context", "用例")
IPA_COLS = ("ipa", "発音", "pronunciation")
LANG_COLS = ("lang", "language", "言語")


def _strip_html(s):
    s = re.sub(r"\[sound:[^\]]*\]", "", str(s or ""))
    s = re.sub(r"<br\s*/?>|</div>|</p>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return " ".join(html.unescape(s).split())


def _col(header, names):
    low = [h.strip().lower() for h in header]
    for n in names:
        if n in low:
            return low.index(n)
    return None


def parse_sheet(text, lang="en"):
    """The vocabulary sheet as CSV. Columns are found by their header names; without a header, column 1 is the word and 2 the meaning."""
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return []
    header = rows[0]
    t, ja, en, ex, ipa, lg = (_col(header, TERM_COLS), _col(header, JA_COLS), _col(header, EN_COLS), _col(header, EX_COLS),
                              _col(header, IPA_COLS), _col(header, LANG_COLS))
    if t is None:
        t, ja, body = 0, 1, rows
    else:
        body = rows[1:]
    out = []
    for r in body:
        get = lambda i: r[i].strip() if i is not None and i < len(r) else ""
        term = get(t)
        if not term:
            continue
        out.append({"term": term, "meaning": {"ja": get(ja), "en": get(en)}, "example": get(ex), "ipa": get(ipa),
                    "lang": (get(lg) or lang)[:8].lower(), "source": {"kind": "sheet", "ref": ""}})
    return out


def parse_anki(text, lang="en"):
    """An Anki text export: tab-separated, lines starting with # are settings. Field 1 is the front, field 2 the back."""
    sep = "\t"
    out = []
    for line in text.splitlines():
        if line.startswith("#"):
            m = re.match(r"#separator:(\w+)", line)
            if m:
                sep = {"tab": "\t", "comma": ",", "semicolon": ";", "pipe": "|", "colon": ":"}.get(m.group(1).lower(), "\t")
            continue
        if not line.strip():
            continue
        parts = line.split(sep)
        term = _strip_html(parts[0])
        if not term:
            continue
        back = _strip_html(parts[1]) if len(parts) > 1 else ""
        ja = back if re.search(r"[ぁ-んァ-ン一-龥]", back) else ""
        out.append({"term": term[:TERM_MAX], "meaning": {"ja": ja, "en": "" if ja else back}, "example": "", "ipa": "",
                    "lang": lang[:8], "source": {"kind": "anki", "ref": ""}})
    return out


def merge_imports(cards, items):
    """Plans the import: new cards to create and existing ones to fill. Duplicates inside the file become one."""
    new, filled, seen = [], [], []
    for it in items:
        have = find_same(cards, it["term"], "", it["lang"]) or find_same(new, it["term"], "", it["lang"])
        if have:
            if have in new or not (have.get("meaning") or {}).get("ja") and it["meaning"].get("ja"):
                for k in ("ja", "en"):
                    if not (have.get("meaning") or {}).get(k) and it["meaning"].get(k):
                        have.setdefault("meaning", {})[k] = it["meaning"][k]
                if have not in new and have not in filled:
                    filled.append(have)
            continue
        c = new_card(it["term"], it["lang"], it.get("example") or "", it["source"])
        c["meaning"] = dict(it["meaning"])
        c["ipa"] = it.get("ipa") or ""
        c["example"] = it.get("example") or ""
        c["lemma"] = it["term"]
        if c["meaning"]["ja"] or c["meaning"]["en"]:
            c["enrich"] = {"state": "ok", "at": now_ms(), "imported": True}  # a meaning the person wrote is kept as it is
        new.append(c)
        seen.append(it["term"])
    return new, filled


def _api(method, path, body=None):
    req = urllib.request.Request(LOCAL + path, data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
                                 method=method, headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{PORT}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def import_file(kind, path, lang="en"):
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        text = f.read()
    items = parse_sheet(text, lang) if kind == "sheet" else parse_anki(text, lang)
    cards = _api("GET", "cards")
    new, filled = merge_imports(cards, items)
    for c in new + filled:
        _api("PUT", "cards/" + urllib.request.quote(c["id"], safe=""), {k: v for k, v in c.items() if k != "id"})
    return {"read": len(items), "added": len(new), "merged": len(items) - len(new), "filled": len(filled)}


class _ApiStore(Store):
    """The CLI talks to the running Desk server so its lock stays the only writer."""

    def __init__(self):
        super().__init__(lambda col: _api("GET", col), lambda col, i, rec, merge: {**_api("PUT", f"{col}/{urllib.request.quote(i, safe='')}", rec), "id": i})


if __name__ == "__main__":
    args = sys.argv[1:]
    lang = "en"
    if "--lang" in args:
        i = args.index("--lang")
        lang = args[i + 1]
        del args[i:i + 2]
    cmd = args[0] if args else ""
    if cmd in ("import-sheet", "import-anki") and len(args) > 1:
        print(json.dumps(import_file(cmd.split("-")[1], args[1], lang), ensure_ascii=False))
    elif cmd == "enrich-pending":
        print(json.dumps({"filled": enrich_pending(_ApiStore())}))
    else:
        print(__doc__)
