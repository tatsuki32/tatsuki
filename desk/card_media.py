"""Pictures for flashcards, from Wikimedia Commons only (free licenses, with the author named).

Only words a photo can show get one: things, animals, food, places, body parts, clothes (a cat, an apple, a train).
Words like "and", "because" or most verbs get none. Claude Haiku decides which words, and gives a short English
search phrase for each, in batches of 40 (only the word, its language and its meaning are sent; never a book's text).
The phrase is the only thing sent to Commons. The author and license are read from Commons by this code, never typed.

A card keeps `image`: {state, q, file, title, author, license, page, no: [Commons titles not to use again], by, at}
  state: want (a phrase, no photo yet) · ok (a photo) · none (no photo fits this word) · missing (Commons had nothing usable)
         · off (the person removed it; never filled again unless they ask for one)
Photos are saved in desk/data/reader/images/ (kept out of the GitHub backup with the rest of reader/).

  python3 desk/card_media.py once     one round now (needs the Desk server)
"""
import hashlib
import html
import http.client
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cards  # noqa: E402
import lifelog  # noqa: E402

IMG_DIR = os.path.join(cards.DIR, "images")
COMMONS_API = os.environ.get("COMMONS_API") or "https://commons.wikimedia.org/w/api.php"
COMMONS_HOSTS = ("commons.wikimedia.org", "upload.wikimedia.org", "thumb.wikimedia.org")
USER_AGENT = "TatsukiDesk/1.0 (personal flashcards)"
FREE_LICENSE = re.compile(r"^(cc0|cc[ -]by([ -]sa)?([ -]\d(\.\d)?.*)?|public domain|pd\b.*)$", re.I)
TYPES = {"image/jpeg": "jpg", "image/png": "png"}
MAX_BYTES = 4 * 1024 * 1024
WIDTH = 480
CLASSIFY_BATCH = 40
FETCH_BATCH = 8
EVERY = 120
NO_MAX = 20
# only a real Desk (4747) or a test pointed at a fake Commons asks anyone
LIVE = os.environ.get("DESK_PORT", "4747") == "4747" or bool(os.environ.get("COMMONS_API"))

SYSTEM = """You help make picture flashcards for a language learner.
For each word you get its language and meaning. Decide if ONE ordinary photo would clearly show what the word means,
so the learner could guess the word from the photo alone. Yes for concrete things: animals, plants, food and drink,
objects, tools, vehicles, buildings and places, body parts, clothes, weather you can see (snow, rain), a few clear actions
(to swim, to run). No for function words (and, because, of, the), pronouns, numbers, abstract ideas, feelings, time words,
most verbs and adjectives, greetings and grammar endings.
For each yes, give a short English search phrase (1 to 3 words) for Wikimedia Commons that would find a plain, typical
photo of exactly that meaning (for "banko" meaning bank give "bank building"; for "kato" give "cat").
Answer for every id you were given."""

SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {
    "type": "object", "properties": {"id": {"type": "string"}, "picture": {"type": "boolean"}, "query": {"type": "string"}},
    "required": ["id", "picture", "query"], "additionalProperties": False}}}, "required": ["items"], "additionalProperties": False}

_lock = threading.Lock()


class Unavailable(Exception):
    pass


def now_ms():
    return int(time.time() * 1000)


def _plain(text):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", str(text or ""))).split())


def wants_classify(c):
    """Live cards with a meaning and no decision about a picture yet."""
    return (not c.get("deleted") and c.get("status", "active") == "active" and cards.has_meaning(c)
            and not (c.get("image") or {}).get("state"))


def wants_fetch(c):
    return not c.get("deleted") and (c.get("image") or {}).get("state") == "want"


# ---------- Claude: which words get a picture ----------

def classify(batch, key=None, opener=None, timeout=60):
    """[{id, picture, query}] for the cards given. Only id, language, word and meaning go out."""
    key = key or lifelog.api_key()
    if not key:
        raise Unavailable("No Anthropic API key is registered")
    lines = []
    for c in batch:
        m = c.get("meaning") or {}
        lines.append(json.dumps({"id": c["id"], "lang": cards.LANG_NAMES.get(c.get("lang") or "en", c.get("lang") or "en"),
                                 "word": c.get("term", ""), "meaning_ja": m.get("ja", ""), "meaning_en": m.get("en", "")}, ensure_ascii=False))
    body = {"model": cards.MODEL, "max_tokens": 4000, "system": SYSTEM,
            "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
            "messages": [{"role": "user", "content": "\n".join(lines)}]}
    req = urllib.request.Request(cards.API, data=json.dumps(body).encode(), headers={
        "x-api-key": key, "anthropic-version": cards.API_VERSION, "content-type": "application/json", "user-agent": "tatsuki-desk"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        raise Unavailable(f"Anthropic returned {e.code}") from None
    except (http.client.HTTPException, OSError, ValueError):
        raise Unavailable("Could not reach Anthropic") from None
    out = next((b.get("text") for b in res.get("content", []) if b.get("type") == "text"), None)
    try:
        items = json.loads(out).get("items") or []
    except (TypeError, ValueError, AttributeError):
        raise Unavailable("Could not read the answer") from None
    ids = {c["id"] for c in batch}
    return [{"id": x["id"], "picture": bool(x.get("picture")), "query": " ".join(str(x.get("query") or "").split())[:60]}
            for x in items if isinstance(x, dict) and x.get("id") in ids]


# ---------- Commons ----------

def _get(url, opener=None):
    host = urllib.parse.urlsplit(url).hostname or ""
    if LIVE and not os.environ.get("COMMONS_API") and host not in COMMONS_HOSTS:
        raise ValueError("Not a Commons URL")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for wait in (20, 60, None):  # Commons answers 429 when asked too fast
        try:
            with (opener or urllib.request.urlopen)(req, timeout=30) as r:
                return r.read(MAX_BYTES + 1), r.headers.get_content_type()
        except urllib.error.HTTPError as e:
            if e.code != 429 or wait is None:
                raise
            time.sleep(wait)


def candidates(query, opener=None):
    """Commons files for the phrase, best first, each with its license and author already read."""
    q = urllib.parse.urlencode({"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": 12,
                                "gsrsearch": query + " filetype:bitmap", "prop": "imageinfo", "iiprop": "url|mime|extmetadata",
                                "iiurlwidth": WIDTH})
    data, _ = _get(COMMONS_API + "?" + q, opener)
    pages = (json.loads(data).get("query") or {}).get("pages") or {}
    out = []
    for p in sorted(pages.values(), key=lambda p: p.get("index", 99)):
        info = (p.get("imageinfo") or [None])[0] or {}
        meta = info.get("extmetadata") or {}
        field = lambda k: _plain((meta.get(k) or {}).get("value"))
        author, lic = field("Artist"), field("LicenseShortName")
        if info.get("mime") not in TYPES or not author or not FREE_LICENSE.match(lic):
            continue
        out.append({"title": p.get("title", ""), "thumb": info.get("thumburl") or info.get("url"), "page": info.get("descriptionurl", ""),
                    "author": author[:120], "license": lic[:40], "mime": info["mime"]})
    return out


def fetch(card, opener=None):
    """The card's new `image`: the first usable photo not refused before, saved on this Mac."""
    im = dict(card.get("image") or {})
    q = im.get("q") or ""
    if not q:
        return {**im, "state": "none", "at": now_ms()}
    no = set(im.get("no") or [])
    for cand in candidates(q, opener):
        if cand["title"] in no or not cand["thumb"]:
            continue
        try:
            body, ctype = _get(cand["thumb"], opener)
        except (urllib.error.URLError, OSError, ValueError):
            continue
        if ctype not in TYPES or len(body) > MAX_BYTES:
            continue
        name = hashlib.sha256(cand["title"].encode("utf-8")).hexdigest()[:16] + "." + TYPES[ctype]
        os.makedirs(IMG_DIR, mode=0o700, exist_ok=True)
        with open(os.path.join(IMG_DIR, name + ".tmp"), "wb") as f:
            f.write(body)
        os.replace(os.path.join(IMG_DIR, name + ".tmp"), os.path.join(IMG_DIR, name))
        return {**im, "state": "ok", "file": name, "title": cand["title"], "author": cand["author"], "license": cand["license"],
                "page": cand["page"], "at": now_ms()}
    return {**im, "state": "missing", "file": "", "at": now_ms()}


def image_path(name):
    """A saved photo's path, or None (names are what fetch() made: hex + .jpg/.png)."""
    if not re.fullmatch(r"[0-9a-f]{16}\.(jpg|png)", name or ""):
        return None
    p = os.path.join(IMG_DIR, name)
    return p if os.path.exists(p) else None


# ---------- what the page asks for ----------

def act(store, rec):
    """POST /api/cards/image {id, action: next|off|on, q?}.
    next: this photo is wrong, find another (it is never used again). off: no photo on this card. on: look for one (q: own phrase)."""
    card = next((c for c in store.cards() if c.get("id") == rec.get("id")), None)
    if not card:
        return 404, {"error": "not found"}
    im = dict(card.get("image") or {})
    action = rec.get("action")
    if action == "next":
        if im.get("title"):
            im["no"] = ([t for t in im.get("no") or [] if t != im["title"]] + [im["title"]])[-NO_MAX:]
        im.update(state="want", file="", title="", by="me", at=now_ms())
    elif action == "off":
        im.update(state="off", file="", title="", by="me", at=now_ms())
    elif action == "on":
        q = " ".join(str(rec.get("q") or im.get("q") or (card.get("meaning") or {}).get("en") or "").split())[:60]
        if not q:
            return 400, {"error": "A search phrase is needed"}
        im.update(state="want", q=q, file="", title="", by="me", at=now_ms())
    else:
        return 400, {"error": "action"}
    card = store.put({**card, "image": im})
    kick()
    return 200, {"card": card}


# ---------- the loop ----------

_kick = threading.Event()


def kick():
    _kick.set()


def round_(store, key=None, opener=None, a_opener=None):
    """One pass: decide pictures for up to 40 cards, then fetch up to 8 photos. Returns counts."""
    out = {"classified": 0, "fetched": 0, "missing": 0}
    with _lock:
        todo = [c for c in store.cards() if wants_classify(c)][:CLASSIFY_BATCH]
        if todo:
            try:
                got = {x["id"]: x for x in classify(todo, key=key, opener=a_opener)}
            except Unavailable as e:
                print("card_media:", e, file=sys.stderr, flush=True)
                got = {}
            for c in todo:
                x = got.get(c["id"])
                if not x:
                    continue
                im = {"state": "want", "q": x["query"], "by": "auto", "at": now_ms()} if x["picture"] and x["query"] else \
                     {"state": "none", "by": "auto", "at": now_ms()}
                store.put({**c, "image": im})
                out["classified"] += 1
        for c in [c for c in store.cards() if wants_fetch(c)][:FETCH_BATCH]:
            try:
                im = fetch(c, opener)
            except (urllib.error.URLError, OSError, ValueError) as e:
                print("card_media: commons", type(e).__name__, e, file=sys.stderr, flush=True)
                break  # Commons is not answering: try the rest next time
            store.put({**c, "image": im})
            out["fetched" if im.get("state") == "ok" else "missing"] += 1
            time.sleep(1)
    return out


def counts(all_cards):
    live = [c for c in all_cards if not c.get("deleted") and c.get("status", "active") == "active"]
    st = lambda s: sum(1 for c in live if (c.get("image") or {}).get("state") == s)
    return {"withPhoto": st("ok"), "looking": st("want") + sum(1 for c in live if wants_classify(c)), "noPhoto": st("none") + st("missing")}


def watch(store):
    time.sleep(45)
    while True:
        try:
            cs = store.cards()
            if any(wants_classify(c) or wants_fetch(c) for c in cs):
                round_(store)
        except Exception as e:  # one odd answer must never stop the loop
            print("card_media:", type(e).__name__, e, file=sys.stderr, flush=True)
        _kick.wait(EVERY)
        _kick.clear()


if __name__ == "__main__":
    if sys.argv[1:2] == ["once"]:
        import anki
        print(json.dumps(round_(anki._ApiStore())))
    else:
        print(__doc__)
