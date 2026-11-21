"""Words: every word looked up (Translate, Reader, searches) and every word put in Anki, per language.

Nothing new is the source of truth: the counts and word lists are worked out each time from `lookups` and `cards`.
Small files in desk/data/words/ (only in the private data backup, like the rest of desk/data):
  anki.json     Anki's "learned" words (interval 21 days or more), read while Anki is open, kept for when it is closed
  hidden.json   words taken off the public pages ("<lang>:<word>")
  publish.json  whether publishing started, and per language the page title, the hash of the last lines sent, when
Public pages: Rhizome Box "<Language> words" (PUT /_/api/desk/words with the key in the keychain item
tatsuki-desk-rhizomebox-words). Only words of 1-3 words with a meaning, no proper nouns, no Japanese, and only
examples Claude wrote: a sentence from a book, a Google Translate text or an imported deck never goes out.
"""
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request


ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("DESK_WORDS_DIR") or os.path.join(ROOT, "data", "words")
KEYCHAIN = "tatsuki-desk-rhizomebox-words"
NATIVE = "ja"
LEARNED_IVL = 21          # days: Anki calls a card "mature" from here
MAX_WORDS = 3
MAX_LEN = 40
PUBLISH_EVERY = 30 * 60
ANKI_EVERY = 10 * 60
_EXTRA = {"eo": "Esperanto", "tok": "Toki Pona", "la": "Latin", "fi": "Finnish", "el": "Greek", "he": "Hebrew", "fa": "Persian",
          "ms": "Malay", "tl": "Tagalog", "cs": "Czech", "hu": "Hungarian", "ro": "Romanian", "bg": "Bulgarian", "da": "Danish",
          "no": "Norwegian", "ka": "Georgian", "hy": "Armenian"}
_names = {}


def names():
    """Language names. anki, cards and translate are loaded only when used: their tests set their own folders first."""
    if not _names:
        import cards
        import translate
        _names.update({**cards.LANG_NAMES, **translate.LANG_NAMES, **_EXTRA})
    return _names


# decks that came before the Desk: a word in the deck's name says the language
DECK_HINTS = [("esperanto", "eo"), ("german", "de"), ("toki", "tok"), ("russian", "ru"), ("serbian", "sr"),
              ("vietnam", "vi"), ("english", "en"), ("vocabulary", "en"), ("英", "en")]
_lock = threading.Lock()
_memo = {"at": 0, "val": None}


def now_ms():
    return int(time.time() * 1000)


def _read(name, default):
    try:
        with open(os.path.join(DIR, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(name, data):
    os.makedirs(DIR, exist_ok=True)
    p = os.path.join(DIR, name)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(p + ".tmp", p)


def norm(s):
    return " ".join(str(s or "").split()).lower()


def plain(s):
    s = re.sub(r"\[sound:[^\]]*\]", "", str(s or ""))
    return norm(re.sub(r"<[^>]+>", " ", s).replace("&nbsp;", " "))


def guess_lang(text):
    """Only for searches with no language: the script says enough."""
    if re.search(r"[぀-ヿ]", text):
        return NATIVE
    if re.search(r"[Ѐ-ӿ]", text):
        return "ru"
    if re.search(r"[가-힯]", text):
        return "ko"
    if re.search(r"[一-鿿]", text):
        return "zh"
    if re.search(r"[A-Za-z]", text):
        return "en"
    return ""


def base(lang):
    return (lang or "").split("-")[0].lower()


def is_word(text):
    t = " ".join(str(text or "").split())
    return bool(t) and len(t) <= MAX_LEN and len(t.split()) <= MAX_WORDS


def deck_lang(deck):
    if deck.startswith("Desk::"):
        name = deck.split("::")[1]
        return next((k for k, v in names().items() if v == name and "-" not in k), "")
    low = deck.lower()
    return next((code for hint, code in DECK_HINTS if hint in low), "")


def local_day(ms):
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d") if ms else ""


# ---------- the word book ----------

def _entry(book, lang, term):
    k = lang + ":" + norm(term)
    e = book.get(k)
    if not e:
        e = book[k] = {"key": k, "lang": lang, "term": " ".join(str(term).split()), "meaning": {"ja": "", "en": ""}, "pos": "",
                       "example": None, "context": "", "first": 0, "looked": 0, "via": [], "inAnki": False, "ankiAt": 0,
                       "imported": False, "cardId": "", "learned": False}
    return e


def _meet(e, at):
    if at and (not e["first"] or at < e["first"]):
        e["first"] = at


def build(lookups, all_cards, snap=None, hidden=()):
    """Every word, by '<lang>:<word>'. Japanese words looked up for their translation count in the other language."""
    book = {}
    for lk in lookups:
        if lk.get("deleted"):
            continue
        text, res = lk.get("text") or "", lk.get("result") or ""
        frm = base(lk.get("from") or (lk.get("key") or "").split(":")[0]) or guess_lang(text)
        to = base(lk.get("to"))
        info = lk.get("info") or {}
        if frm == NATIVE:
            if not to or to == NATIVE or not is_word(res):
                continue
            e = _entry(book, to, res)
            e["meaning"]["ja"] = e["meaning"]["ja"] or text
        else:
            if not frm or not is_word(text):
                continue
            e = _entry(book, frm, text)
            if res and to == "ja":
                e["meaning"]["ja"] = e["meaning"]["ja"] or res
            elif res and to == "en":
                e["meaning"]["en"] = e["meaning"]["en"] or res
            e["meaning"]["en"] = e["meaning"]["en"] or info.get("gloss", "")
            ex = (info.get("examples") or [None])[0]
            if isinstance(ex, dict) and ex.get("text") and not e["example"]:
                e["example"] = {"text": ex["text"], "translation": ex.get("translation", ""), "by": "claude"}
        e["pos"] = e["pos"] or info.get("pos", "")
        e["looked"] += 1
        via = lk.get("via") or "desk"
        if via not in e["via"]:
            e["via"].append(via)
        _meet(e, lk.get("at") or 0)
    for c in all_cards:
        if c.get("deleted") or c.get("status", "active") != "active" or not c.get("term"):
            continue
        lang = base(c.get("lang"))
        if not lang or lang == NATIVE:
            continue
        e = _entry(book, lang, c["term"])
        m = c.get("meaning") or {}
        e["meaning"]["ja"] = e["meaning"]["ja"] or m.get("ja", "")
        e["meaning"]["en"] = e["meaning"]["en"] or m.get("en", "")
        e["pos"] = e["pos"] or c.get("pos", "")
        kind = (c.get("source") or {}).get("kind", "")
        e["cardId"] = c.get("id", "")
        if kind in ("anki", "sheet"):
            e["imported"] = True  # their example is someone else's sentence (a deck) or the person's own: never public
        elif c.get("example") and not e["example"]:
            e["example"] = {"text": c["example"], "translation": c.get("exampleTranslation", ""), "by": "claude"}
        if c.get("context") and not e["context"]:
            e["context"] = c["context"]
        a = c.get("anki") or {}
        if kind == "anki" or a.get("state") in ("sent", "exists"):
            e["inAnki"] = True
            if kind not in ("anki", "sheet"):
                e["ankiAt"] = a.get("at") or c.get("createdAt") or 0
        if kind in ("book", "translate") and not e["looked"]:
            e["looked"] = max(1, int(c.get("lookups") or 1))
            if kind not in e["via"]:
                e["via"].append("reader" if kind == "book" else "desk")
        _meet(e, c.get("createdAt") or 0)
    learned = set((snap or {}).get("words") or [])
    hid = set(hidden)
    for k, e in book.items():
        e["learned"] = k in learned
        e["hidden"] = k in hid
    return book


def _week_start(today):
    return today - dt.timedelta(days=today.weekday())


def summary(book, snap=None, days=30, today=None):
    today = today or dt.date.today()
    wk, mo = _week_start(today).isoformat(), today.replace(day=1).isoformat()
    langs = {}
    per_day = {(today - dt.timedelta(days=i)).isoformat(): {"looked": 0, "anki": 0} for i in range(days)}
    for e in book.values():
        L = langs.setdefault(e["lang"], {"lang": e["lang"], "name": names().get(e["lang"], e["lang"]), "looked": 0, "inAnki": 0,
                                          "learned": 0, "week": 0, "month": 0})
        fd, ad = local_day(e["first"]), local_day(e["ankiAt"])
        if e["looked"]:
            L["looked"] += 1
            if fd in per_day:
                per_day[fd]["looked"] += 1
        if e["inAnki"]:
            L["inAnki"] += 1
            if ad in per_day:
                per_day[ad]["anki"] += 1
        new = max(fd if e["looked"] else "", ad)
        if new >= wk:
            L["week"] += 1
        if new >= mo:
            L["month"] += 1
    for lang, n in ((snap or {}).get("byLang") or {}).items():
        if lang in langs:
            langs[lang]["learned"] = n
        elif lang and lang != NATIVE:
            langs[lang] = {"lang": lang, "name": names().get(lang, lang), "looked": 0, "inAnki": 0, "learned": n, "week": 0, "month": 0}
    rows = sorted(langs.values(), key=lambda r: (-(r["inAnki"] + r["looked"] + r["learned"]), r["name"]))
    tot = {k: sum(r[k] for r in rows) for k in ("looked", "inAnki", "learned", "week", "month")}
    return {"totals": tot, "langs": rows, "days": [{"date": d, **v} for d, v in sorted(per_day.items())]}


def word_list(book, lang):
    rows = [e for e in book.values() if e["lang"] == lang]
    rows.sort(key=lambda e: -(max(e["first"], e["ankiAt"]) or 0))
    return [{**e, "public": public_ok(e), "onPage": on_page(e)} for e in rows]


# ---------- Anki: what has been learned ----------

def anki_refresh():
    """Read Anki's mature cards (interval >= 21 days). Only reads. Raises anki.Closed when Anki is not open."""
    import anki
    ids = anki.call("findCards", timeout=10, query=f"prop:ivl>={LEARNED_IVL}") or []
    by_note, words = {}, set()
    for i in range(0, len(ids), 300):
        for c in anki.call("cardsInfo", timeout=20, cards=ids[i:i + 300]) or []:
            lang = deck_lang(c.get("deckName") or "")
            flds = c.get("fields") or {}
            first = min(flds.values(), key=lambda f: f.get("order", 0)).get("value", "") if flds else ""
            if c.get("note") in by_note:
                continue
            by_note[c.get("note")] = lang
            if lang and first:
                words.add(lang + ":" + plain(first))
    by_lang = {}
    for lang in by_note.values():
        by_lang[lang or "other"] = by_lang.get(lang or "other", 0) + 1
    snap = {"at": now_ms(), "byLang": by_lang, "words": sorted(words), "total": len(by_note)}
    _write("anki.json", snap)
    _memo["at"] = 0
    return snap


# ---------- the public pages ----------

def public_ok(e):
    """Words seen only in Google searches stay on the Desk: a search can be a name or anything else private."""
    pos = (e.get("pos") or "").lower()
    meaning = e["meaning"]["ja"] or e["meaning"]["en"]
    if e.get("via") == ["search"] and not e.get("inAnki"):
        return False
    if "%" in e["term"] or norm(meaning) == norm(e["term"]):
        return False
    return (not e.get("hidden") and e["lang"] != NATIVE and e["lang"] in names() and is_word(e["term"])
            and bool(e["meaning"]["ja"] or e["meaning"]["en"]) and "proper" not in pos)


def _safe(s, n=160):
    """Plain text in Scrapbox notation: no links, tags or markup from a word or a meaning."""
    s = " ".join(str(s or "").split())[:n]
    return s.replace("[", "(").replace("]", ")").replace("#", "").replace("`", "'")


def page_title(lang):
    return names().get(lang, lang) + " words"


def on_page(e):
    """A word goes to the public page once Anki shows it 21 days apart or more (learned), and it is safe to show."""
    return bool(e.get("learned")) and public_ok(e)


def _item(e):
    """The words of one entry as they go on the page. Kept as sent: a word once on the page does not change."""
    meaning = e["meaning"]["ja"] or e["meaning"]["en"]
    it = {"k": e["key"], "t": _safe(e["term"], 60), "m": _safe(meaning)}
    ex = e.get("example")
    if ex and ex.get("by") == "claude" and not e["imported"]:
        it["ex"] = _safe(ex["text"], 200) + (f" — {_safe(ex.get('translation'))}" if ex.get("translation") else "")
    return it


def _plan(book, lang, page, today=None):
    """What the page holds: the words already sent (as they were sent, minus hidden ones), then the newly learned ones
    under today's date. Returns (items, new)."""
    hid = {k for k, e in book.items() if e.get("hidden")}
    items = [it for it in (page or {}).get("items", []) if it["k"] not in hid]
    have = {it["k"] for it in items}
    day = (today or dt.date.today()).isoformat()
    rows = sorted((e for e in book.values() if e["lang"] == lang and on_page(e) and e["key"] not in have),
                  key=lambda e: e["term"].lower())
    new = [{**_item(e), "day": day} for e in rows]
    return items + new, new


def page_lines(items):
    out, day = [], None
    for it in items:
        if it.get("day") != day:
            if out:
                out.append("")
            day = it.get("day")
            out.append(f"[* Added {day}]")
        out.append(f" {it['t']} — {it['m']}")
        if it.get("ex"):
            out.append(f"  {it['ex']}")
    return out


def page_title(lang):
    return names().get(lang, lang) + " words"


def page_head(lang):
    return [f"Words I have learned in [{names().get(lang, lang)}]. My Desk adds new ones at the bottom as I learn them."]


def _state():
    st = _read("publish.json", {})
    st.setdefault("langs", {})
    return st


def preview(book, lang=None):
    """Per language: how many learned words are on the page, how many are new, and the page as it would be."""
    st = _state()
    langs = sorted({e["lang"] for e in book.values() if e["lang"] != NATIVE and e["lang"] in names()}, key=lambda l: names().get(l, l))
    out = []
    for l in langs:
        if lang and l != lang:
            continue
        pg = st["langs"].get(l) or {}
        items, new = _plan(book, l, pg)
        out.append({"lang": l, "name": names().get(l, l), "title": page_title(l), "on": bool(pg.get("on")), "url": pg.get("url", ""),
                    "count": len(items), "sent": len(items) - len(new), "new": len(new), "at": pg.get("at", 0),
                    "lines": page_lines(items) if lang else None})
    return out


def key():
    try:
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN, "-w"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _put(site, k, body, opener=None):
    req = urllib.request.Request(site + "/_/api/desk/words", data=json.dumps(body).encode(), method="PUT",
                                 headers={"Authorization": "Bearer " + k, "X-RB": "1", "Content-Type": "application/json",
                                          "User-Agent": "tatsuki-desk-words"})
    with (opener or urllib.request.urlopen)(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def publish(book, only=None, k=None, site=None, opener=None, today=None):
    """Send the pages of the languages that are on, when something changed: new learned words go at the bottom.
    A language just turned on is made public; one just turned off is made private (its words stay on it)."""
    with _lock:
        st = _state()
        todo = [l for l, pg in st["langs"].items() if (pg.get("on") or pg.get("setPub") is False) and (not only or l == only)]
        if not todo:
            return st
        k = k or key()
        if not k:
            st["error"] = "No key yet. Run bash desk/words_setup.sh once."
            _write("publish.json", st)
            return st
        if not site:
            import rhizomebox_backup
            site = rhizomebox_backup.site()
        st["error"] = ""
        for l in todo:
            pg = st["langs"][l]
            items, new = _plan(book, l, pg, today)
            body = {"title": page_title(l), "head": page_head(l), "lines": page_lines(items)}
            h = hashlib.sha256(json.dumps(body, ensure_ascii=False).encode()).hexdigest()[:16]  # the words only
            if "setPub" in pg:
                body["setPub"] = pg["setPub"]
            if pg.get("hash") == h and "setPub" not in pg:
                continue
            if not items and not pg.get("id"):
                continue  # nothing learned yet: no empty page
            try:
                out = _put(site, k, body, opener)
            except (urllib.error.URLError, OSError, ValueError) as e:
                st["error"] = f"Could not reach Rhizome Box ({getattr(e, 'code', '') or type(e).__name__}). It tries again in 30 minutes."
                break
            pg.update(items=items, hash=h, at=now_ms(), title=page_title(l), url=site + "/" + page_title(l).replace(" ", "_"))
            if new:
                pg.setdefault("log", []).append({"at": now_ms(), "n": len(new)})
                pg["log"] = pg["log"][-50:]
            pg.pop("setPub", None)
            if (out.get("page") or {}).get("id"):
                pg["id"] = out["page"]["id"]
        st["at"] = now_ms()
        _write("publish.json", st)
        _memo["at"] = 0
        return st


def set_lang(lang, on):
    """Turn one language's public page on (made public at the next send) or off (made private; its words stay)."""
    if lang not in names() or lang == NATIVE:
        return False
    st = _state()
    pg = st["langs"].setdefault(lang, {})
    pg["on"] = bool(on)
    if on or pg.get("id"):
        pg["setPub"] = bool(on)
    _write("publish.json", st)
    _memo["at"] = 0
    return True


def set_hidden(rec):
    k = str(rec.get("key") or "")
    if ":" not in k or len(k) > 120:
        return 400, {"error": "key"}
    hid = set(_read("hidden.json", []))
    (hid.add if rec.get("hidden", True) else hid.discard)(k)
    _write("hidden.json", sorted(hid))
    _memo["at"] = 0
    return 200, {"key": k, "hidden": k in hid}


# ---------- the server's side ----------

def book_from(load):
    return build(load("lookups"), load("cards"), _read("anki.json", {}), _read("hidden.json", []))


def state(load):
    """GET /api/words: the counts (kept 60 seconds)."""
    if _memo["val"] and time.time() - _memo["at"] < 60:
        return _memo["val"]
    snap = _read("anki.json", {})
    book = book_from(load)
    out = summary(book, snap)
    st = _state()
    out["anki"] = {"at": snap.get("at", 0), "total": snap.get("total", 0)}
    out["publish"] = {"langs": preview(book), "error": st.get("error", ""), "at": st.get("at", 0), "hasKey": bool(key()),
                      "pages": {l: {"url": pg.get("url", ""), "title": pg.get("title", "")} for l, pg in st["langs"].items() if pg.get("on") and pg.get("url")}}
    _memo.update(at=time.time(), val=out)
    return out


def reader_lookup(upsert, term, lang, info):
    """A word pressed in the Reader becomes a lookup (one per word and day), like a word typed in Translate."""
    term = " ".join(str(term or "").split())
    lang = base(lang)
    if not term or not lang or not isinstance(info, dict):
        return None
    import translate
    k = translate.key_of(term, lang)
    day = dt.date.today().isoformat()
    lid = "rd" + hashlib.sha1((k + "|" + day).encode()).hexdigest()[:15]
    to = "ja" if info.get("meaning_ja") else "en"
    ex = [{"text": info["example"], "translation": ""}] if info.get("example") else []
    rec = {"text": term, "from": lang, "to": to, "result": info.get("meaning_ja") or info.get("meaning_en") or "", "at": now_ms(),
           "via": "reader", "key": k, "word": True, "lemma": info.get("lemma") or term, "engine": "claude",
           "info": {"lemma": info.get("lemma") or term, "pos": info.get("pos", ""), "gloss": info.get("meaning_en", ""), "examples": ex}}
    upsert("lookups", lid, rec, False)
    _memo["at"] = 0
    return lid


def watch(load):
    """Every 10 minutes: Anki's learned words while Anki is open. Every 30: the public pages, once started."""
    import anki
    time.sleep(60)
    last_pub = 0
    while True:
        try:
            anki_refresh()
        except anki.Closed:
            pass
        except Exception as e:  # never stop the loop
            print("words: anki", type(e).__name__, e, flush=True)
        if time.time() - last_pub >= PUBLISH_EVERY:
            last_pub = time.time()
            try:
                publish(book_from(load))
            except Exception as e:
                print("words: publish", type(e).__name__, e, flush=True)
        time.sleep(ANKI_EVERY)
