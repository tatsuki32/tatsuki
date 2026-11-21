"""Desk Reader, side by side: a chapter next to its translation, word by word.

A chapter is translated only when Tatsuki presses "Translate this chapter" in the Parallel mode. Then, and only then, the
chapter's paragraphs go to Claude (claude-haiku-5-5, the Desk's Anthropic key), a few paragraphs per call. This is the one place
where a book's text leaves the Mac; the page says so next to the button. The answer is kept in
desk/data/reader/parallel/<book id>/<chapter>.json, so a chapter is never sent twice (the reader/ folder stays out of the backup).

The target is Japanese, or English when the book is in Japanese.

Claude returns the sentences (copied as written) with their translations, and pairs of words or idioms (source text and the
text of the translation it became). Claude never gives character positions: the code finds every piece in the text itself and
drops what it cannot find, so a pair on the page always points at real words.

A chapter file: {book, ch, lang, to, model, at, state running|done|partial|error, total, paras[]}.
A paragraph: {t (the whole translation), s [[a, b, c, d]] (sentence: source a-b, translation c-d),
p [[a, b, c, d, idiom 0|1]] (pairs), err (true when this paragraph failed)}. Positions are characters inside the paragraph.

Tests: python3 -m unittest desk.tests.test_reader_parallel
"""
import http.client
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lifelog  # noqa: E402
import reader  # noqa: E402

DIR = os.path.join(reader.DIR, "parallel")
API = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MODEL = "claude-haiku-5-5"
CHUNK_CHARS = 3000  # source characters per call
CHUNK_PARAS = 12
WORKERS = 3
MAX_TOKENS = 16000
LANG_NAMES = {"en": "English", "ja": "Japanese", "ru": "Russian", "es": "Spanish", "fr": "French", "de": "German", "vi": "Vietnamese",
              "zh": "Chinese", "ko": "Korean", "it": "Italian", "pt": "Portuguese", "sr": "Serbian", "eo": "Esperanto"}

SCHEMA = {
    "type": "object",
    "properties": {"paras": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "sents": {"type": "array", "items": {"type": "object", "properties": {"src": {"type": "string"}, "tr": {"type": "string"}},
                                                  "required": ["src", "tr"], "additionalProperties": False}},
            "pairs": {"type": "array", "items": {"type": "object", "properties": {
                "i": {"type": "integer"}, "src": {"type": "string"}, "tr": {"type": "string"}, "idiom": {"type": "boolean"}},
                "required": ["i", "src", "tr", "idiom"], "additionalProperties": False}},
        },
        "required": ["id", "sents", "pairs"], "additionalProperties": False}}},
    "required": ["paras"], "additionalProperties": False,
}

SYSTEM = """You translate a book for a language learner who reads it side by side with your translation.
You get numbered paragraphs in {src}. Translate each into natural, faithful {dst}, sentence by sentence.
For every paragraph return:
- id: its number.
- sents: the paragraph's sentences in order. src is the sentence copied exactly as written (same characters, same punctuation).
  tr is its translation. Together the src values must cover the whole paragraph.
- pairs: which words of the source became which words of your translation. For each pair: i = the index of the sentence in sents
  (0 based), src = the source word or phrase copied exactly from that sentence, tr = the exact text inside that sentence's tr that
  translates it, idiom = true when src is an idiom, a phrasal verb or a fixed expression that is translated as a whole.
  Give a pair for every content word and every idiom (nouns, verbs, adjectives, adverbs, names, numbers). Skip articles and
  punctuation. Keep an idiom as one pair, not word by word. src and tr must be substrings that really occur, never paraphrases."""


class Unavailable(Exception):
    """Claude could not answer now."""


_lock = threading.Lock()
_running = set()


def now_ms():
    return int(time.time() * 1000)


def target(lang):
    return "en" if lang == "ja" else "ja"


def _file(book_id, ch):
    reader._path(book_id)  # raises ValueError for a bad id
    return os.path.join(DIR, book_id, f"{int(ch)}.json")


def read(book_id, ch):
    try:
        with open(_file(book_id, ch), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


def _write(book_id, ch, rec):
    path = _file(book_id, ch)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    os.replace(tmp, path)


def _chapter(book_id, ch):
    b = reader.read_book(book_id)
    if not b:
        return None, None
    chs = b.get("chapters") or []
    if not isinstance(ch, int) or not 0 <= ch < len(chs):
        return b, None
    return b, chs[ch].get("paras") or []


def chunks(paras, todo):
    """Paragraph numbers in groups small enough for one call."""
    out, cur, size = [], [], 0
    for i in todo:
        n = len(paras[i])
        if cur and (size + n > CHUNK_CHARS or len(cur) >= CHUNK_PARAS):
            out.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += n
    if cur:
        out.append(cur)
    return out


def ask_claude(items, src, dst, key=None, opener=None, timeout=180):
    """[(id, text)] -> the parsed answer {"paras": [...]}."""
    key = key or lifelog.api_key()
    if not key:
        raise Unavailable("No Anthropic API key is registered")
    text = "\n\n".join(f"[{i}]\n{t}" for i, t in items)
    body = {"model": MODEL, "max_tokens": MAX_TOKENS,
            "system": SYSTEM.format(src=LANG_NAMES.get(src, src), dst=LANG_NAMES.get(dst, dst)),
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
        v = json.loads(out)
    except (TypeError, ValueError):
        raise Unavailable("Could not read the answer") from None
    if not isinstance(v, dict) or not isinstance(v.get("paras"), list):
        raise Unavailable("Could not read the answer")
    return v


def _find(hay, needle, start, end):
    """needle inside hay[start:end]; spaces and curly quotes may differ. -> (a, b) or None."""
    needle = (needle or "").strip()
    if not needle:
        return None
    i = hay.find(needle, start, end)
    if i >= 0:
        return i, i + len(needle)
    norm = lambda s: s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    i = norm(hay).find(norm(needle), start, end)
    if i >= 0:
        return i, i + len(needle)
    parts = [re.escape(norm(w)) for w in needle.split()]
    if not parts:
        return None
    m = re.compile(r"\s+".join(parts)).search(norm(hay), start, end)
    return (m.start(), m.end()) if m else None


def align(src, ans, dst):
    """One paragraph: its text and Claude's answer for it -> {t, s, p}. Everything is checked against the real text."""
    sents = [s for s in (ans.get("sents") or []) if isinstance(s, dict) and str(s.get("tr") or "").strip()]
    if not sents:
        return None
    glue = "" if dst in ("ja", "zh") else " "
    t, spans, pos = "", [], 0
    for s in sents:
        tr = " ".join(str(s.get("tr")).split())
        if t:
            t += glue
        c = len(t)
        t += tr
        hit = _find(src, str(s.get("src") or ""), pos, len(src))
        if hit:
            pos = hit[1]
        spans.append([hit[0] if hit else -1, hit[1] if hit else -1, c, len(t)])
    pairs = []
    for p in ans.get("pairs") or []:
        if not isinstance(p, dict) or not isinstance(p.get("i"), int) or not 0 <= p["i"] < len(spans):
            continue
        a, b, c, d = spans[p["i"]]
        x = _find(src, str(p.get("src") or ""), a, b) if a >= 0 else None
        y = _find(t, str(p.get("tr") or ""), c, d)
        if x and y:
            pairs.append([x[0], x[1], y[0], y[1], 1 if p.get("idiom") else 0])
    return {"t": t, "s": [s for s in spans if s[0] >= 0], "p": pairs}


def state(book_id, ch):
    """What the page needs: the translation so far, or how big the chapter is before anything is sent."""
    try:
        b, paras = _chapter(book_id, ch)
    except ValueError:
        return 404, {"error": "not found"}
    if paras is None:
        return 404, {"error": "not found"}
    rec = read(book_id, ch)
    with _lock:
        running = (book_id, ch) in _running
    if rec:
        return 200, {**rec, "state": "running" if running else rec.get("state"), "to": rec.get("to") or target(b.get("lang"))}
    words = sum(len(p.split()) for p in paras) if b.get("lang") not in ("ja", "zh", "th") else sum(len(p) for p in paras)
    return 200, {"book": book_id, "ch": ch, "state": "none", "total": len(paras), "words": words, "chars": sum(len(p) for p in paras),
                 "lang": b.get("lang"), "to": target(b.get("lang"))}


def start(book_id, ch, again=False, key=None, opener=None, wait=False):
    """Translate the paragraphs still missing (all of them when again). Runs in the background unless wait."""
    try:
        b, paras = _chapter(book_id, ch)
    except ValueError:
        return 404, {"error": "not found"}
    if paras is None:
        return 404, {"error": "not found"}
    key = key or lifelog.api_key()
    if not key:
        return 503, {"error": "No Anthropic API key is registered"}
    with _lock:
        busy = (book_id, ch) in _running
        _running.add((book_id, ch))
    if busy:
        return state(book_id, ch)
    lang = b.get("lang") or "en"
    rec = None if again else read(book_id, ch)
    if not rec or len(rec.get("paras") or []) != len(paras):
        rec = {"book": book_id, "ch": ch, "lang": lang, "to": target(lang), "model": MODEL, "at": now_ms(), "paras": [None] * len(paras)}
    rec.update(state="running", total=len(paras))
    todo = [i for i, p in enumerate(rec["paras"]) if (p is None or p.get("err")) and paras[i].strip()]
    for i, p in enumerate(paras):
        if not p.strip():
            rec["paras"][i] = {"t": "", "s": [], "p": []}
    _write(book_id, ch, rec)

    def one(group):
        try:
            ans = ask_claude([(i, paras[i]) for i in group], lang, rec["to"], key=key, opener=opener)
            got = {a.get("id"): a for a in ans["paras"] if isinstance(a, dict)}
        except Exception:  # Unavailable, or anything odd in the answer: these paragraphs get "Try again"
            got = {}
        with _lock:
            for i in group:
                try:
                    v = align(paras[i], got[i], rec["to"]) if i in got else None
                except (TypeError, ValueError, AttributeError):
                    v = None
                rec["paras"][i] = v or {"err": True}
            _write(book_id, ch, rec)

    def run():
        try:
            with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                list(ex.map(one, chunks(paras, todo)))
            bad = sum(1 for p in rec["paras"] if not p or p.get("err"))
            rec["state"] = "done" if not bad else ("error" if bad == len(todo) and todo else "partial")
            rec["at"] = now_ms()
            with _lock:
                _write(book_id, ch, rec)
        finally:
            with _lock:
                _running.discard((book_id, ch))

    if wait:
        run()
        return state(book_id, ch)
    threading.Thread(target=run, daemon=True).start()
    return state(book_id, ch)
