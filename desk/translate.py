"""Translate inside Desk, keep every lookup, and tie the same meaning across languages.

Requirements: https://claude.ai/artifact/FF9Tx7iqYRVoLsTo7Nf8kj

- A translation goes to Google Cloud Translation (Basic v2) with the key in the keychain `tatsuki-desk-gtranslate`.
  Without a key, or past the monthly limit, Claude translates instead and the page shows one line about it.
  The same sentence is translated once (translate/cache.json); the month's characters are counted (translate/usage.json).
- Every lookup is one doc in the collection `lookups`. A word (3 words or fewer) is then enriched by Claude: lemma,
  part of speech, senses, two examples, the word in the other languages, and a short English gloss. Concepts live in
  `concepts`: one gloss, members per language (looked up = by "me", added by Claude = by "claude").
  Claude may only pick a concept id from the candidates we send it (same text, same lemma or same translation).
- Looked up twice or more -> a candidate for `cards` (Reader と暗記カード). Add makes a card with status "active"
  and source.kind "translate"; nothing goes into cards on its own.
- Web history (desk/chrome_web_sync.py) brings in Google Translate pages (text, sl, tl kept in the address) and
  searches shaped like 「〜とは」「〜 意味」「〜 meaning」「〜 英語で」. Those get via "gtranslate" / "search".
  Deleted ones keep their id with deleted:true, so the next pass never brings them back.
- Only the text being translated is sent out (to Google and Anthropic). Nothing else leaves the Mac.

The server binds the collections: translate.bind(load, save, lock). Settings and caches are files in
desk/data/translate/ (TRANSLATE_DIR in tests). Nothing here is written to the repository.

  python3 desk/translate.py ingest     one pass over the web history
  python3 desk/translate.py state      what the page would see
"""
import datetime as dt
import http.client
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lifelog  # noqa: E402  (api_key: the Anthropic key from the keychain)

ROOT = os.path.dirname(os.path.abspath(__file__))
GOOGLE_URL = "https://translation.googleapis.com/language/translate/v2"
GOOGLE_KEYCHAIN = ("tatsuki-desk-gtranslate", "api")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MODEL = "claude-haiku-5-5"  # fast: the translation is shown first and the enrichment arrives a moment later
# Google Cloud Translation Basic: the first 500,000 characters a month are free (Google's pricing page; check it when
# the limit matters). The default stays well under that; the person can change it on the Translate screen.
DEFAULT_LIMIT = 400000
DEFAULT_LANGS = ["ja", "en", "ru", "zh", "vi"]
LANG_NAMES = {"ja": "Japanese", "en": "English", "ru": "Russian", "zh": "Chinese", "zh-CN": "Chinese", "zh-TW": "Chinese (Taiwan)",
              "vi": "Vietnamese", "ko": "Korean", "fr": "French", "de": "German", "es": "Spanish", "it": "Italian",
              "pt": "Portuguese", "th": "Thai", "id": "Indonesian", "ar": "Arabic", "hi": "Hindi", "tr": "Turkish",
              "uk": "Ukrainian", "pl": "Polish", "nl": "Dutch", "sv": "Swedish", "sr": "Serbian", "hr": "Croatian"}
MAX_TEXT = 5000
WORD_MAX = 3  # a "word" that gets lemma, examples and a concept: this many words or fewer
CACHE_MAX = 2000
CANDIDATES_MAX = 10
NIGHT_HOURS = (3, 4)  # the nightly batch (enrich lookups that came in from outside) runs in this hour window
INGEST_EVERY = 3600
# searches that look things up: 「X とは」「X 意味」「X meaning」「X 英語で」 (and the English "what does X mean")
SEARCH_FORMS = (
    re.compile(r"^(?P<w>.+?)\s*とは(?:\s*(?:何|なに|意味))?[?？]?$"),
    re.compile(r"^(?P<w>.+?)\s+(?:意味|いみ|英語で|英語|日本語で|日本語|読み方|とは何|とは何か)[?？]?$"),
    re.compile(r"^(?P<w>.+?)\s+(?:meaning|means|definition|translation|in english|in japanese)[?？]?$", re.I),
    re.compile(r"^what (?:does|is) (?P<w>.+?) (?:mean|means)[?？]?$", re.I),
)
# the forms above must not be the whole of a question like 「なぜ とは」; a word longer than this is a sentence, not a lookup
SEARCH_WORD_MAX = 40
JA_KANA = re.compile(r"[ぁ-んァ-ヶー]")
CJK = re.compile(r"[一-龥]")
HANGUL = re.compile(r"[가-힣]")
CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")

_store = {"load": None, "save": None, "lock": threading.Lock()}
_mem = {}  # the in-memory store when nothing is bound (tests)


class Unavailable(Exception):
    """Neither Google nor Claude could translate right now."""


# ---------- files ----------

def data_dir():
    return os.environ.get("TRANSLATE_DIR") or os.path.join(ROOT, "data", "translate")


def _read(name, default):
    try:
        with open(os.path.join(data_dir(), name), encoding="utf-8") as f:
            v = json.load(f)
    except (OSError, ValueError):
        return default
    return v if isinstance(v, type(default)) else default


def _write(name, value):
    d = data_dir()
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(d, name))


def settings():
    s = _read("settings.json", {})
    langs = [x for x in (s.get("langs") or []) if isinstance(x, str) and re.fullmatch(r"[a-zA-Z-]{2,7}", x)] or list(DEFAULT_LANGS)
    limit = s.get("limit")
    if not isinstance(limit, int) or limit < 0:
        limit = DEFAULT_LIMIT
    return {"langs": langs[:12], "limit": limit, "notNow": s.get("notNow") if isinstance(s.get("notNow"), dict) else {}}


def put_settings(rec):
    s = settings()
    if isinstance(rec.get("langs"), list):
        langs = [str(x) for x in rec["langs"] if isinstance(x, str) and re.fullmatch(r"[a-zA-Z-]{2,7}", x)]
        if langs:
            s["langs"] = langs[:12]
    if isinstance(rec.get("limit"), int) and rec["limit"] >= 0:
        s["limit"] = rec["limit"]
    _write("settings.json", s)
    return 200, state()


def month_key(now=None):
    return (now or dt.datetime.now()).strftime("%Y-%m")


def usage():
    return _read("usage.json", {})


def add_usage(chars):
    u = usage()
    k = month_key()
    u[k] = int(u.get(k, 0)) + int(chars)
    for old in [m for m in u if m < k][:-12]:  # keep a year
        u.pop(old, None)
    _write("usage.json", u)
    return u[k]


# ---------- the collections (bound by the server) ----------

def bind(load, save, lock):
    _store.update(load=load, save=save, lock=lock)


def _load(col):
    if _store["load"]:
        return _store["load"](col)
    return [dict(x) for x in _mem.get(col, [])]


def _save(col, items):
    if _store["save"]:
        return _store["save"](col, items)
    _mem[col] = [dict(x) for x in items]


def _upsert(col, rec):
    """Writes one doc (whole). Returns the saved doc."""
    rec = dict(rec)
    rec.setdefault("updatedAt", int(time.time() * 1000))
    with _store["lock"]:
        items = _load(col)
        for i, x in enumerate(items):
            if x.get("id") == rec["id"]:
                items[i] = rec
                break
        else:
            items.append(rec)
        _save(col, items)
    return rec


def _get(col, doc_id):
    return next((x for x in _load(col) if x.get("id") == doc_id), None)


def new_id(prefix):
    return prefix + format(int(time.time() * 1000), "x") + secrets.token_hex(2)


# ---------- language helpers ----------

def norm(text):
    return " ".join(str(text or "").split()).strip()


def key_of(text, lang=""):
    """Groups the same word: lower-cased, spaces squeezed. Language is kept apart so 'use' (en) and 'use' (fr) differ."""
    return (lang or "").split("-")[0].lower() + ":" + norm(text).lower()


def word_count(text):
    t = norm(text)
    if not t:
        return 0
    if re.search(r"\s", t):
        return len(t.split())
    if JA_KANA.search(t) or CJK.search(t):
        return 1 if len(t) <= 6 else 2 + len(t) // 8  # a short run of Japanese or Chinese is a word; a long one is a sentence
    return 1


def is_word(text):
    return 0 < word_count(text) <= WORD_MAX and len(norm(text)) <= 60


def guess_lang(text):
    """Only what the script gives away. Chinese-looking text without kana may still be Japanese; Google decides then."""
    t = norm(text)
    if JA_KANA.search(t):
        return "ja"
    if HANGUL.search(t):
        return "ko"
    if CYRILLIC.search(t):
        return "ru"
    if CJK.search(t):
        return "zh?"
    return ""


def default_target(from_lang, langs=None):
    """Japanese by default; English when the text is Japanese."""
    langs = langs or settings()["langs"]
    first = langs[0] if langs else "ja"
    if (from_lang or "").split("-")[0] == first:
        return next((x for x in langs if x.split("-")[0] != first), "en")
    return first


# ---------- Google ----------

def google_key():
    v = os.environ.get("GTRANSLATE_KEY")
    if v:
        return v
    if os.environ.get("TRANSLATE_DIR"):
        return None  # tests never touch the keychain
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", GOOGLE_KEYCHAIN[0], "-a", GOOGLE_KEYCHAIN[1], "-w"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def google_translate(text, target, source=None, key=None, opener=urllib.request.urlopen):
    """(translated text, detected source). The key rides in the header, never in the address."""
    body = {"q": text, "target": target, "format": "text"}
    if source:
        body["source"] = source
    req = urllib.request.Request(GOOGLE_URL, data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json", "x-goog-api-key": key, "user-agent": "tatsuki-desk"})
    try:
        with opener(req, timeout=15) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        raise Unavailable(f"Google returned {e.code}") from None
    except (http.client.HTTPException, OSError, ValueError):
        raise Unavailable("could not reach Google") from None
    try:
        t = res["data"]["translations"][0]
    except (KeyError, IndexError, TypeError):
        raise Unavailable("Google sent an unexpected answer") from None
    return str(t.get("translatedText", "")), str(t.get("detectedSourceLanguage") or source or "")


# ---------- Claude ----------

def _claude(system, user, schema, key=None, opener=urllib.request.urlopen, max_tokens=1500, timeout=25):
    key = key or lifelog.api_key()
    if not key:
        raise Unavailable("no Anthropic key")
    body = {"model": MODEL, "max_tokens": max_tokens, "system": system,
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
            "messages": [{"role": "user", "content": user}]}
    req = urllib.request.Request(ANTHROPIC_URL, data=json.dumps(body).encode(),
                                 headers={"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION,
                                          "content-type": "application/json", "user-agent": "tatsuki-desk"})
    try:
        with opener(req, timeout=timeout) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        raise Unavailable(f"Anthropic returned {e.code}") from None
    except (http.client.HTTPException, OSError, ValueError):
        raise Unavailable("could not reach Anthropic") from None
    if res.get("stop_reason") == "refusal":
        raise Unavailable("declined")
    text = "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")
    try:
        return json.loads(text)
    except ValueError:
        raise Unavailable("bad answer") from None


TRANSLATE_SYSTEM = """You translate for a person who reads Japanese, English, Russian, Chinese and Vietnamese.
Return JSON: "from" = the ISO 639-1 code of the text's language (zh for Chinese), "result" = the translation into the target
language. Translate the whole text faithfully; do not add notes. Keep names and numbers as they are."""
TRANSLATE_SCHEMA = {"type": "object", "properties": {"from": {"type": "string"}, "result": {"type": "string"}},
                    "required": ["from", "result"], "additionalProperties": False}


def claude_translate(text, target, key=None, opener=urllib.request.urlopen):
    v = _claude(TRANSLATE_SYSTEM, f"Target language: {target}\n\nText:\n{text}", TRANSLATE_SCHEMA, key, opener, 2000)
    return norm(v.get("result")), norm(v.get("from"))[:7].lower()


ENRICH_SYSTEM = """You are a dictionary for a language learner (Japanese native; learning English, Russian, Chinese, Vietnamese).
You get a word or short phrase, its language, the translation the person saw, and a list of existing concepts
(id | gloss | words). Return JSON:
- lemma: the dictionary form of the word (same language as the word).
- pos: part of speech in one English word (noun, verb, adjective, ...), or "phrase".
- reading: how it is read, when the script does not show it (kana for Japanese, pinyin for Chinese, IPA for English); else "".
- gloss: the meaning as a short English definition of at most 8 words, like "employ something for a purpose". For a word with
  several meanings, the one that fits the translation shown. The gloss names ONE sense; "bank" (money) and "bank" (river) are different.
- senses: up to 3 other common meanings, each a short English phrase (empty list if there is only one).
- examples: exactly 2 short natural example sentences in the word's language, each with its translation into the target language.
- others: the same sense in each of the languages listed under "Other languages", one word each (the most common everyday word).
  Skip the word's own language and the translation's language. Use the language codes given.
- conceptId: the id of the concept in the list that has the SAME sense (not just a related word), or "" when none fits.
Write glosses and senses in plain American English. Never invent a concept id."""
ENRICH_SCHEMA = {
    "type": "object",
    "properties": {
        "lemma": {"type": "string"}, "pos": {"type": "string"}, "reading": {"type": "string"}, "gloss": {"type": "string"},
        "senses": {"type": "array", "items": {"type": "string"}},
        "examples": {"type": "array", "items": {"type": "object", "properties": {"text": {"type": "string"}, "translation": {"type": "string"}},
                                                "required": ["text", "translation"], "additionalProperties": False}},
        "others": {"type": "array", "items": {"type": "object", "properties": {"lang": {"type": "string"}, "term": {"type": "string"}},
                                              "required": ["lang", "term"], "additionalProperties": False}},
        "conceptId": {"type": "string"},
    },
    "required": ["lemma", "pos", "reading", "gloss", "senses", "examples", "others", "conceptId"], "additionalProperties": False,
}


def claude_enrich(lookup, candidates, langs, key=None, opener=urllib.request.urlopen):
    cand = "\n".join(f"{c['id']} | {c.get('gloss', '')} | " + ", ".join(f"{m.get('term')} ({m.get('lang')})" for m in c.get("members", [])[:8])
                     for c in candidates) or "(none)"
    others = ", ".join(x for x in langs if x.split("-")[0] not in ((lookup.get("from") or "").split("-")[0], (lookup.get("to") or "").split("-")[0]))
    user = (f"Word: {lookup['text']}\nLanguage: {lookup.get('from') or 'unknown'}\nTranslation shown ({lookup.get('to')}): {lookup.get('result')}\n"
            f"Other languages: {others or '(none)'}\n\nExisting concepts:\n{cand}")
    return _claude(ENRICH_SYSTEM, user, ENRICH_SCHEMA, key, opener, 1500)


# ---------- translate ----------

def cache_get(text, target):
    c = _read("cache.json", {})
    v = c.get(f"{target}|{norm(text)}")
    return v if isinstance(v, dict) and v.get("result") else None


def cache_put(text, target, result, from_lang, engine):
    c = _read("cache.json", {})
    c[f"{target}|{norm(text)}"] = {"result": result, "from": from_lang, "engine": engine, "at": int(time.time())}
    if len(c) > CACHE_MAX:
        for k in sorted(c, key=lambda k: c[k].get("at", 0))[: len(c) - CACHE_MAX]:
            c.pop(k, None)
    _write("cache.json", c)


def engine_state(key=None):
    """Which engine a translation would use now, and why."""
    s = settings()
    used = usage().get(month_key(), 0)
    has_key = bool(key or google_key())
    if not has_key:
        return {"engine": "claude", "why": "No Google key yet (keychain tatsuki-desk-gtranslate). Claude translates for now.", "used": used, "limit": s["limit"]}
    if s["limit"] and used >= s["limit"]:
        return {"engine": "claude", "why": f"This month's Google limit ({s['limit']:,} characters) is used up. Claude translates until next month.", "used": used, "limit": s["limit"]}
    why = ""
    if s["limit"] and used >= s["limit"] * 0.8:
        why = f"Google: {used:,} of {s['limit']:,} characters used this month."
    return {"engine": "google", "why": why, "used": used, "limit": s["limit"]}


def translate(text, to=None, gkey=None, akey=None, g_opener=urllib.request.urlopen, a_opener=urllib.request.urlopen, source=None):
    """{text, from, to, result, engine, cached}. Google first (while the key and the month allow), else Claude.
    `source`: the language the text is known to be in (the Reader passes its book's), so it is never guessed."""
    text = norm(text)
    if not text:
        raise ValueError("nothing to translate")
    if len(text) > MAX_TEXT:
        raise ValueError(f"The limit is {MAX_TEXT} characters at a time")
    langs = settings()["langs"]
    source = source.split("-")[0].lower() if source and re.fullmatch(r"[a-zA-Z-]{2,7}", source) else None
    hint = source or guess_lang(text)
    if to and not re.fullmatch(r"[a-zA-Z-]{2,7}", to):
        to = None
    target = to or default_target(hint.rstrip("?") if hint and hint != "zh?" else "", langs)
    hit = cache_get(text, target)
    if hit:
        return {"text": text, "from": source or hit.get("from") or hint.rstrip("?"), "to": target, "result": hit["result"], "engine": hit.get("engine", ""), "cached": True}
    st = engine_state(gkey)
    gkey = gkey or (google_key() if st["engine"] == "google" else None)
    from_lang, result, engine = "", "", ""
    if st["engine"] == "google" and gkey:
        try:
            result, from_lang = google_translate(text, target, source=source, key=gkey, opener=g_opener)
            engine = "google"
            if not to and from_lang.split("-")[0] == target.split("-")[0]:  # the text was already in the default language: go the other way
                target = default_target(from_lang, langs)
                result, from_lang = google_translate(text, target, source=from_lang, key=gkey, opener=g_opener)
                add_usage(len(text))
            add_usage(len(text))
        except Unavailable:
            engine = ""
    if not engine:
        result, from_lang = claude_translate(text, target, key=akey, opener=a_opener)
        engine = "claude"
        if not to and from_lang.split("-")[0] == target.split("-")[0]:
            target = default_target(from_lang, langs)
            result, from_lang = claude_translate(text, target, key=akey, opener=a_opener)
    from_lang = source or (from_lang or hint.rstrip("?") or "").split("-")[0]  # zh-CN and zh-TW both count as zh: one word, one member
    cache_put(text, target, result, from_lang, engine)
    return {"text": text, "from": from_lang, "to": target, "result": result, "engine": engine, "cached": False}


def record(tr, via="desk", lookup_id=None, at=None):
    """Saves one lookup and returns it with the group count."""
    rec = {"id": lookup_id or new_id("lk"), "text": tr["text"], "from": tr.get("from", ""), "to": tr.get("to", ""),
           "result": tr.get("result", ""), "at": at or int(time.time() * 1000), "via": via,
           "key": key_of(tr["text"], tr.get("from", "")), "word": is_word(tr["text"]), "engine": tr.get("engine", "")}
    return _upsert("lookups", rec)


def count_of(key):
    return sum(1 for x in _load("lookups") if not x.get("deleted") and x.get("key") == key)


def handle_translate(rec, **kw):
    """POST /api/translate {text, to?, via?, save?} -> {lookup, count, concept?, engine, why}."""
    try:
        tr = translate(rec.get("text"), rec.get("to") or None, source=str(rec.get("from") or "") or None, **kw)
    except ValueError as e:
        return 400, {"error": str(e)}
    except Unavailable as e:
        return 503, {"error": "Could not translate right now (" + str(e) + "). What you typed stays in the box."}
    st = engine_state(kw.get("gkey"))
    out = {"engine": tr["engine"], "why": st["why"] if tr["engine"] != "google" or st["why"] else ""}
    if rec.get("save") is False:
        return 200, {**out, "lookup": {**tr, "word": is_word(tr["text"])}, "count": 0}
    via = rec.get("via") if rec.get("via") in ("desk", "reader", "search", "gtranslate") else "desk"
    lk = record(tr, via)
    # the same word, looked up before and already tied to a concept: reuse it right away
    prev = next((x for x in _load("lookups") if x.get("key") == lk["key"] and x.get("conceptId") and x["id"] != lk["id"] and not x.get("deleted")), None)
    if prev:
        lk = _upsert("lookups", {**lk, "conceptId": prev["conceptId"], "lemma": prev.get("lemma", ""), "info": prev.get("info")})
    c = _get("concepts", lk.get("conceptId")) if lk.get("conceptId") else None
    return 200, {**out, "lookup": lk, "count": count_of(lk["key"]), "concept": c if c and not c.get("deleted") else None}


# ---------- concepts ----------

def _members_match(c, words):
    return any(norm(m.get("term", "")).lower() in words or norm(m.get("lemma", "")).lower() in words for m in c.get("members", []))


def _members_near(c, words):
    """A member that contains the word or is contained in it (用 in 使用, 'use' in 'to use')."""
    for m in c.get("members", []):
        t = norm(m.get("term", "")).lower()
        if t and any(len(w) > 1 and (w in t or t in w) for w in words):
            return True
    return False


def candidates_for(lookup):
    """Concepts Claude may pick from: the same text, lemma or translation first, then near matches, then the newest
    concepts to fill up to 10. Claude sees the glosses and keeps only the one with the same sense."""
    words = {norm(lookup.get("text", "")).lower(), norm(lookup.get("result", "")).lower(), norm(lookup.get("lemma", "")).lower()} - {""}
    live = [c for c in _load("concepts") if not c.get("deleted") and not c.get("mergedInto")]
    live.sort(key=lambda c: -(c.get("updatedAt") or 0))
    exact = [c for c in live if _members_match(c, words)]
    near = [c for c in live if c not in exact and _members_near(c, words)]
    rest = [c for c in live if c not in exact and c not in near]
    return (exact + near + rest)[:CANDIDATES_MAX]


def _add_member(c, lang, term, lemma="", by="claude", count=0):
    lang = (lang or "").split("-")[0].lower()
    term = norm(term)
    if not term or not lang:
        return
    for m in c.setdefault("members", []):
        if m.get("lang") == lang and norm(m.get("term", "")).lower() == term.lower():
            if by == "me":
                m["by"] = "me"
                m["count"] = int(m.get("count") or 0) + count
            if lemma and not m.get("lemma"):
                m["lemma"] = lemma
            return
    c["members"].append({"lang": lang, "term": term, "lemma": lemma or "", "by": by, "count": count})


def enrich(lookup_id, key=None, opener=urllib.request.urlopen):
    """Lemma, examples, the other languages and the concept for one lookup. Returns (lookup, concept)."""
    lk = _get("lookups", lookup_id)
    if not lk or lk.get("deleted"):
        raise ValueError("no such lookup")
    if not is_word(lk.get("text", "")):
        raise ValueError("only a word or a short phrase gets a concept")
    langs = settings()["langs"]
    cands = candidates_for(lk)
    v = claude_enrich(lk, cands, langs, key, opener)
    ids = {c["id"] for c in cands}
    cid = v.get("conceptId") if v.get("conceptId") in ids else ""
    gloss = norm(v.get("gloss"))[:120]
    lemma = norm(v.get("lemma"))[:80] or lk["text"]
    info = {"lemma": lemma, "pos": norm(v.get("pos"))[:30], "reading": norm(v.get("reading"))[:80], "gloss": gloss,
            "senses": [norm(s)[:120] for s in (v.get("senses") or []) if norm(s)][:3],
            "examples": [{"text": norm(e.get("text"))[:300], "translation": norm(e.get("translation"))[:300]}
                         for e in (v.get("examples") or []) if isinstance(e, dict) and norm(e.get("text"))][:2],
            "others": [{"lang": norm(o.get("lang"))[:7].lower(), "term": norm(o.get("term"))[:80]}
                       for o in (v.get("others") or []) if isinstance(o, dict) and norm(o.get("term"))][:12]}
    c = _get("concepts", cid) if cid else None
    if not c:
        c = {"id": new_id("c"), "gloss": gloss or lemma, "members": [], "note": "", "createdAt": int(time.time() * 1000)}
    _add_member(c, lk.get("from"), lk["text"], lemma, "me", 1)
    _add_member(c, lk.get("to"), lk.get("result"), "", "me", 0)
    for o in info["others"]:
        _add_member(c, o["lang"], o["term"], "", "claude", 0)
    c = _upsert("concepts", {**c, "deleted": False})
    lk = _upsert("lookups", {**lk, "lemma": lemma, "conceptId": c["id"], "info": info})
    return lk, c


def handle_enrich(rec, **kw):
    try:
        lk, c = enrich(rec.get("id"), **kw)
    except ValueError as e:
        return 400, {"error": str(e)}
    except Unavailable as e:
        return 503, {"error": "Claude could not add the details (" + str(e) + ")."}
    return 200, {"lookup": lk, "concept": c, "count": count_of(lk["key"])}


def merge(src_id, into_id):
    """All members of src go into `into`; src keeps mergedInto. Lookups and cards follow."""
    a, b = _get("concepts", src_id), _get("concepts", into_id)
    if not a or not b or a["id"] == b["id"] or a.get("deleted") or b.get("deleted"):
        return 400, {"error": "pick two different concepts"}
    for m in a.get("members", []):
        _add_member(b, m.get("lang"), m.get("term"), m.get("lemma", ""), m.get("by", "claude"), int(m.get("count") or 0))
    if a.get("note") and a["note"] not in (b.get("note") or ""):
        b["note"] = ((b.get("note") or "") + "\n" + a["note"]).strip()
    b = _upsert("concepts", b)
    _upsert("concepts", {**a, "mergedInto": b["id"]})
    _repoint(a["id"], b["id"])
    return 200, {"concept": b}


def _repoint(old, new):
    for col in ("lookups", "cards"):
        with _store["lock"]:
            items = _load(col)
            changed = False
            for x in items:
                if x.get("conceptId") == old:
                    x["conceptId"] = new
                    x["updatedAt"] = int(time.time() * 1000)
                    changed = True
            if changed:
                _save(col, items)


def split(concept_id, lang, term):
    """One member leaves into a new concept of its own. Its lookups follow."""
    c = _get("concepts", concept_id)
    if not c or c.get("deleted"):
        return 400, {"error": "no such concept"}
    idx = next((i for i, m in enumerate(c.get("members", [])) if m.get("lang") == lang and norm(m.get("term", "")).lower() == norm(term).lower()), None)
    if idx is None:
        return 400, {"error": "that word is not in this concept"}
    if len(c["members"]) < 2:
        return 400, {"error": "a concept needs one word left"}
    m = c["members"].pop(idx)
    n = _upsert("concepts", {"id": new_id("c"), "gloss": "", "members": [m], "note": "", "createdAt": int(time.time() * 1000), "deleted": False})
    c = _upsert("concepts", c)
    key = key_of(m.get("term"), m.get("lang"))
    with _store["lock"]:
        items = _load("lookups")
        changed = False
        for x in items:
            if x.get("conceptId") == c["id"] and x.get("key") == key:
                x["conceptId"] = n["id"]
                x["updatedAt"] = int(time.time() * 1000)
                changed = True
        if changed:
            _save("lookups", items)
    return 200, {"concept": c, "new": n}


def handle_concepts(action, rec):
    if action == "merge":
        return merge(str(rec.get("from") or ""), str(rec.get("into") or ""))
    if action == "split":
        return split(str(rec.get("id") or ""), str(rec.get("lang") or ""), str(rec.get("term") or ""))
    return 404, {"error": "unknown action"}


# ---------- cards ----------

def card_for(key):
    return next((c for c in _load("cards") if not c.get("deleted") and c.get("key") == key), None)


def suggestions():
    """Groups looked up twice or more, without a card, not set aside. Newest first."""
    s = settings()
    groups = {}
    for x in _load("lookups"):
        if x.get("deleted") or not x.get("word") or not x.get("key"):
            continue
        g = groups.setdefault(x["key"], {"key": x["key"], "count": 0, "last": 0, "lookup": x})
        g["count"] += 1
        if (x.get("at") or 0) >= g["last"]:
            g["last"] = x.get("at") or 0
            g["lookup"] = x
    cards = {c.get("key") for c in _load("cards") if not c.get("deleted")}
    out = [g for g in groups.values() if g["count"] >= 2 and g["key"] not in cards and g["key"] not in s["notNow"]]
    out.sort(key=lambda g: -g["last"])
    return [{"key": g["key"], "count": g["count"], "text": g["lookup"].get("text"), "from": g["lookup"].get("from"),
             "result": g["lookup"].get("result"), "to": g["lookup"].get("to"), "conceptId": g["lookup"].get("conceptId"), "lookupId": g["lookup"]["id"]} for g in out]


def add_card(lookup_id):
    """A card in the shape of Reader と暗記カード (①): term, lemma, lang, meaning{ja,en}, example, source, conceptId."""
    lk = _get("lookups", lookup_id)
    if not lk or lk.get("deleted"):
        return 400, {"error": "no such lookup"}
    have = card_for(lk.get("key"))
    if have:
        return 200, {"card": have, "already": True}
    info = lk.get("info") or {}
    c = _get("concepts", lk.get("conceptId")) if lk.get("conceptId") else None
    meaning = {"ja": "", "en": ""}
    for lang in ("ja", "en"):
        if (lk.get("to") or "").split("-")[0] == lang:
            meaning[lang] = lk.get("result", "")
        elif c:
            m = next((m for m in c.get("members", []) if m.get("lang") == lang), None)
            if m:
                meaning[lang] = m.get("term", "")
    if not meaning["en"] and info.get("gloss"):
        meaning["en"] = info["gloss"]
    ex = (info.get("examples") or [{}])[0]
    card = {"id": new_id("cd"), "term": lk["text"], "lemma": lk.get("lemma") or info.get("lemma") or lk["text"], "lang": lk.get("from", ""),
            "meaning": meaning, "ipa": info.get("reading", "") if (lk.get("from") or "") == "en" else "", "reading": info.get("reading", ""),
            "example": ex.get("text", ""), "exampleTranslation": ex.get("translation", ""), "context": "",
            "source": {"kind": "translate", "ref": lk["id"]}, "deck": "translate", "status": "active",
            "fsrs": {"due": int(time.time() * 1000), "stability": 0, "difficulty": 0, "reps": 0, "lapses": 0},
            "lookups": count_of(lk.get("key")), "conceptId": lk.get("conceptId") or "", "key": lk.get("key"),
            "createdAt": int(time.time() * 1000), "deleted": False}
    return 200, {"card": _upsert("cards", card), "already": False}


def not_now(key):
    s = _read("settings.json", {})
    nn = s.get("notNow") if isinstance(s.get("notNow"), dict) else {}
    nn[str(key)] = int(time.time() * 1000)
    s["notNow"] = nn
    _write("settings.json", s)
    return 200, {"ok": True, "suggestions": suggestions()}


def handle_cards(rec):
    if rec.get("action") == "notnow":
        return not_now(rec.get("key"))
    code, out = add_card(str(rec.get("id") or ""))
    if code == 200:
        out["suggestions"] = suggestions()
    return code, out


# ---------- state for the page ----------

def state(gkey=None):
    st = engine_state(gkey)
    s = settings()
    return {"langs": s["langs"], "limit": s["limit"], "used": st["used"], "engine": st["engine"], "why": st["why"],
            "hasGoogle": bool(gkey or google_key()), "hasClaude": bool(lifelog.api_key()), "names": LANG_NAMES,
            "suggestions": suggestions(), "ingest": _read("ingest.json", {}).get("last", {})}


# ---------- from outside: Google Translate pages and 「〜とは」 searches in the web history ----------

def search_word(term):
    t = norm(term)
    for rx in SEARCH_FORMS:
        m = rx.match(t)
        if m:
            w = norm(m.group("w")).strip("「」\"' ")
            if 0 < len(w) <= SEARCH_WORD_MAX and word_count(w) <= 4:
                return w
    return ""


def gtranslate_params(url):
    """(text, sl, tl) from a translate.google.com address, or None."""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    host = (p.hostname or "").lower()
    if not host.startswith("translate.google."):
        return None
    q = dict(urllib.parse.parse_qsl(p.query))
    text = norm(q.get("text", ""))
    if not text or len(text) > MAX_TEXT:
        return None
    return text, q.get("sl", "auto"), q.get("tl", "")


def ingest(con=None, now=None, limit=200):
    """One pass over the web history: new Google Translate pages and lookup-shaped searches become lookups.
    First pass: all searches so far (they are cheap to keep), translate pages only from here on (older addresses lost their text)."""
    import chrome_web_sync  # here, so the tests can point WEBHISTORY_DIR somewhere else first
    own = con is None
    if own:
        try:
            con = chrome_web_sync.connect()
        except (sqlite3.Error, OSError) as e:
            return {"error": str(e)}
    st = _read("ingest.json", {})
    now = now or time.time()
    have = {x["id"] for x in _load("lookups")}
    added = 0
    try:
        since_v = st.get("visitAt") or (now - 3600)
        rows = con.execute("SELECT id, at, url FROM visits WHERE deleted = 0 AND site LIKE 'translate.google.%' AND at > ? ORDER BY at LIMIT ?", (since_v, limit)).fetchall()
        for r in rows:
            vid, at, url = r[0], r[1], r[2]
            st["visitAt"] = max(st.get("visitAt") or 0, at)
            pr = gtranslate_params(url)
            lid = f"g{vid}"
            if not pr or lid in have:
                continue
            text, sl, tl = pr
            _upsert("lookups", {"id": lid, "text": text, "from": "" if sl in ("", "auto") else sl, "to": tl or "", "result": "",
                                "at": int(at * 1000), "via": "gtranslate", "key": key_of(text, "" if sl == "auto" else sl),
                                "word": is_word(text), "pending": True})
            have.add(lid)
            added += 1
        since_s = st.get("searchAt") or 0
        rows = con.execute("SELECT id, at, term FROM searches WHERE deleted = 0 AND at > ? ORDER BY at LIMIT ?", (since_s, limit)).fetchall()
        for r in rows:
            sid, at, term = r[0], r[1], r[2]
            st["searchAt"] = max(st.get("searchAt") or 0, at)
            w = search_word(term)
            lid = f"s{sid}"
            if not w or lid in have:
                continue
            _upsert("lookups", {"id": lid, "text": w, "from": "", "to": "", "result": "", "at": int(at * 1000), "via": "search",
                                "key": key_of(w, ""), "word": is_word(w), "pending": True})
            have.add(lid)
            added += 1
    finally:
        if own:
            con.close()
    st["last"] = {"at": int(now * 1000), "added": added}
    _write("ingest.json", st)
    return {"added": added}


def fill_pending(limit=20, **kw):
    """Lookups brought in from outside have no translation yet: translate them (cache first), keys permitting."""
    done = 0
    for lk in [x for x in _load("lookups") if x.get("pending") and not x.get("deleted")][:limit]:
        try:
            tr = translate(lk["text"], lk.get("to") or None, **kw)
        except (ValueError, Unavailable):
            continue
        from_lang = lk.get("from") or tr["from"]
        _upsert("lookups", {**lk, "from": from_lang, "to": tr["to"], "result": tr["result"], "engine": tr["engine"],
                            "key": key_of(lk["text"], from_lang), "pending": False})
        done += 1
    return done


def nightly(limit=40, **kw):
    """Concepts for words that have none yet (the ones that came in from outside, mostly). Claude, once a night."""
    done = 0
    for lk in [x for x in _load("lookups") if x.get("word") and not x.get("conceptId") and not x.get("pending") and not x.get("deleted") and x.get("result")]:
        if done >= limit:
            break
        prev = next((x for x in _load("lookups") if x.get("key") == lk["key"] and x.get("conceptId") and not x.get("deleted")), None)
        if prev:
            _upsert("lookups", {**lk, "conceptId": prev["conceptId"], "lemma": prev.get("lemma", ""), "info": prev.get("info")})
            continue
        try:
            enrich(lk["id"], **kw)
        except (ValueError, Unavailable):
            break
        done += 1
    return done


def tick(now=None):
    """Hourly: web history -> lookups -> translations. In the night window: concepts for the new words."""
    st = _read("ingest.json", {})
    now = now or time.time()
    out = {}
    if now - (st.get("tickAt") or 0) >= INGEST_EVERY - 5:
        out["ingest"] = ingest(now=now)
        out["filled"] = fill_pending()
        st = _read("ingest.json", {})
        st["tickAt"] = now
        _write("ingest.json", st)
    h = dt.datetime.fromtimestamp(now).hour
    day = dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    if NIGHT_HOURS[0] <= h < NIGHT_HOURS[1] and st.get("nightDay") != day:
        out["enriched"] = nightly()
        st = _read("ingest.json", {})
        st["nightDay"] = day
        _write("ingest.json", st)
    return out


def watch():
    """The server's background thread (4747 only)."""
    time.sleep(90)
    while True:
        try:
            tick()
        except Exception as e:  # keep the loop alive
            print("translate:", type(e).__name__, e, file=sys.stderr, flush=True)
        time.sleep(300)


if __name__ == "__main__":
    import server  # noqa: F401  binds nothing; the CLI reads and writes the same files the server does
    bind(server.load, server.save, server.LOCK)
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "ingest":
        print(json.dumps({**ingest(), "filled": fill_pending()}, ensure_ascii=False))
    elif cmd == "state":
        print(json.dumps(state(), ensure_ascii=False, indent=1))
    elif cmd == "nightly":
        print(json.dumps({"enriched": nightly()}, ensure_ascii=False))
    else:
        print(__doc__)
