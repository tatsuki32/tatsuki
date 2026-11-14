#!/usr/bin/env python3
"""Tatsuki's Desk — Post Studio: photos and a short memo become drafts for each social account.

What it does:
- keeps the photos on this Mac (desk/data/posts/, out of the GitHub copy) and cuts them to each platform's size with
  app/postcrop.swift (Vision finds the people, nothing leaves the Mac),
- asks Claude once for every draft (the memo and the photos are the only things sent), then counts the characters
  itself (X counts Japanese double) and asks again only for the drafts that are too long,
- never posts anything. The page copies the text, saves the pictures and opens each site; the person posts.
"""
import base64
import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

import golinks  # go.tany.im short links, one per place a post goes
import lifelog  # the Anthropic key in the keychain and the API settings
import utilities  # downloads go through its one-hour file tokens (the Desk window hands those to the browser)

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DESK_POSTS_DIR") or os.path.join(ROOT, "data", "posts")
EXAMPLE = os.path.join(ROOT, "posts_targets.example.json")
TOOL_SOURCE = os.path.join(ROOT, "app", "postcrop.swift")
TOOL = os.environ.get("DESK_POSTCROP") or os.path.expanduser("~/Library/Application Support/TatsukiDesk/postcrop")
MODEL = "claude-opus-5-5"
PHOTO_MAX = 40 * 1024 * 1024
PHOTOS_PER_POST = 10
API_SIDE = 1568
KINDS = ("post", "story", "article", "news")
NAME = re.compile(r"^[0-9a-f]{16}$")
POST_ID = re.compile(r"^p-\d{8}-[0-9a-f]{6}$")
TARGET_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
URL = re.compile(r"https?://[^\s<>\"'）」』】]+")
# where a link can go: in the text, only as "link in profile" (Instagram captions do not make links), as a link sticker
# added by hand (stories), or nowhere
LINK_MODES = ("inline", "bio", "sticker", "none")
LINKS_PER_POST = 3

_lock = threading.RLock()
_build_lock = threading.Lock()
_running = set()


def dp(*parts):
    return os.path.join(DATA, *parts)


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------- settings ----------

def settings():
    """The person's targets.json, made from the example the first time. Unknown fields are kept as they are."""
    p = dp("targets.json")
    s = _read(p, None)
    if not isinstance(s, dict) or not isinstance(s.get("targets"), list):
        s = _read(EXAMPLE, {"targets": []})
        s.pop("_note", None)
        _write(p, s)
    return s


def _link_defaults():
    return {t.get("id"): t.get("link") for t in _read(EXAMPLE, {}).get("targets", []) if isinstance(t, dict)}


def targets(only_on=False):
    out = []
    defaults = None
    for t in settings().get("targets", []):
        if isinstance(t, dict) and TARGET_ID.match(str(t.get("id", ""))) and t.get("kind") in KINDS:
            if t.get("link") not in LINK_MODES:  # settings made before links were handled
                defaults = defaults if defaults is not None else _link_defaults()
                t = {**t, "link": defaults.get(t["id"]) or ("sticker" if t["kind"] == "story" else "inline")}
            if not only_on or t.get("on"):
                out.append(t)
    return out


def target(tid):
    return next((t for t in targets() if t["id"] == tid), None)


def save_settings(rec):
    """Only the parts the page edits: which targets are on, and each one's style, tags, limit and name."""
    s = settings()
    by = {t.get("id"): t for t in s.get("targets", []) if isinstance(t, dict)}
    for change in rec.get("targets", []) if isinstance(rec.get("targets"), list) else []:
        t = by.get(change.get("id"))
        if not t:
            continue
        if isinstance(change.get("on"), bool):
            t["on"] = change["on"]
        if change.get("link") in LINK_MODES:
            t["link"] = change["link"]
        for key, kind, top in (("style", str, 600), ("name", str, 60), ("fixedTags", str, 200)):
            if isinstance(change.get(key), kind):
                t[key] = change[key].strip()[:top]
        for key, lo, hi in (("tags", 0, 30), ("limit", 20, 20000)):
            if isinstance(change.get(key), int) and lo <= change[key] <= hi:
                t[key] = change[key]
    for lang in ("ja", "en", "ru"):
        for key in ("voice", "signature"):
            v = (rec.get(key) or {}).get(lang) if isinstance(rec.get(key), dict) else None
            if isinstance(v, str):
                s.setdefault(key, {})[lang] = v.strip()[:1500]
    _write(dp("targets.json"), s)
    return 200, view()


# ---------- counting ----------

def x_length(text):
    """X's weighted length (twitter-text v3): most Latin is 1, Japanese, emoji and the rest are 2, a link is 23."""
    n = 0
    rest = re.sub(r"https?://\S+", lambda m: "\x00", text)
    n += 23 * rest.count("\x00")
    for ch in rest.replace("\x00", ""):
        c = ord(ch)
        n += 1 if (c <= 4351 or 8192 <= c <= 8205 or 8208 <= c <= 8223 or 8242 <= c <= 8247) else 2
    return n


def full_text(t, d):
    """What is posted: the text, then the hashtags (the article and the news keep their own layout)."""
    text = (d.get("text") or "").strip()
    tags = [x for x in (d.get("hashtags") or []) if x]
    fixed = [x for x in re.split(r"[\s,]+", t.get("fixedTags") or "") if x.startswith("#")]
    tags = tags + [x for x in fixed if x not in tags]
    if tags and t.get("kind") != "story":
        text += "\n\n" + " ".join(tags)
    return text


def length(t, d):
    s = d.get("overlay", "") if t.get("kind") == "story" else full_text(t, d)
    return x_length(s) if t.get("count") == "x" else len(s)


# ---------- photos ----------

def tool():
    """The Swift helper, built the first time (or again when its source changes). None when this Mac cannot build it."""
    with _build_lock:
        try:
            if os.path.exists(TOOL) and os.path.getmtime(TOOL) >= os.path.getmtime(TOOL_SOURCE):
                return TOOL
            os.makedirs(os.path.dirname(TOOL), exist_ok=True)
            subprocess.run(["swiftc", "-O", TOOL_SOURCE, "-o", TOOL], check=True, capture_output=True, timeout=300)
            return TOOL
        except (OSError, subprocess.SubprocessError):
            return None


def photo_path(name, kind="orig"):
    if not NAME.match(name or ""):
        return None
    p = dp("photos", f"{name}.{'jpg' if kind == 'orig' else 'api.jpg'}")
    return p if os.path.exists(p) else None


def add_photo(stream, size):
    """Takes one uploaded photo. HEIC and others become a JPEG; the same photo twice is kept once."""
    if size <= 0 or size > PHOTO_MAX:
        return 413, {"error": "A photo can be up to 40 MB"}
    data = stream.read(size)
    name = hashlib.sha256(data).hexdigest()[:16]
    final = dp("photos", f"{name}.jpg")
    os.makedirs(dp("photos"), exist_ok=True)
    if not os.path.exists(final):
        with tempfile.TemporaryDirectory() as tmp:
            raw = os.path.join(tmp, "in")
            with open(raw, "wb") as f:
                f.write(data)
            out = os.path.join(tmp, "out.jpg")
            # sips applies the camera rotation and reads HEIC; the copy for Claude is smaller
            r = subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "92", raw, "--out", out], capture_output=True, timeout=120)
            if r.returncode or not os.path.exists(out):
                return 415, {"error": "This file is not a photo this Mac can read"}
            shutil.move(out, final)
        subprocess.run(["sips", "-Z", str(API_SIDE), "-s", "formatOptions", "85", final, "--out", dp("photos", f"{name}.api.jpg")], capture_output=True, timeout=120)
    meta = _read(dp("photos", f"{name}.json"), None)
    if not meta:
        meta = {"x": 0.5, "y": 0.45}
        helper = tool()
        if helper:
            r = subprocess.run([helper, "focus", final], capture_output=True, text=True, timeout=60)
            try:
                meta = {k: v for k, v in json.loads(r.stdout).items() if k in ("w", "h", "x", "y")}
            except ValueError:
                pass
        _write(dp("photos", f"{name}.json"), meta)
    return 200, {"id": name, **meta}


def image(post_id, tid, n):
    """The picture for one target: cut around the photo's focus, at the target's size. Made once per focus and line."""
    post = get(post_id)
    t = target(tid)
    if not post or not t or not (0 <= n < len(post["photos"])):
        return None
    ph = post["photos"][n]
    src = photo_path(ph["id"])
    helper = tool()
    if not src or not helper:
        return None
    d = (post.get("drafts") or {}).get(tid) or {}
    words = (d.get("overlay") or "").strip() if t["kind"] == "story" and n == 0 and post.get("textOn", {}).get(tid, True) else ""
    w, h = t.get("size") or [1080, 1350]
    key = hashlib.sha1(json.dumps([ph["id"], ph.get("x"), ph.get("y"), w, h, words]).encode()).hexdigest()[:12]
    out = dp("out", post_id, f"{tid}-{n}-{key}.jpg")
    if not os.path.exists(out):
        os.makedirs(os.path.dirname(out), exist_ok=True)
        for old in os.listdir(os.path.dirname(out)):
            if old.startswith(f"{tid}-{n}-"):
                os.remove(dp("out", post_id, old))
        args = [helper, "crop", src, out, str(w), str(h), str(ph.get("x", 0.5)), str(ph.get("y", 0.45))] + ([words] if words else [])
        r = subprocess.run(args, capture_output=True, timeout=120)
        if r.returncode or not os.path.exists(out):
            return None
    return out


def save_image(post_id, tid, n):
    p = image(post_id, tid, n)
    t = target(tid)
    if not p or not t:
        return 404, {"error": "The picture could not be made"}
    with open(p, "rb") as f:
        data = f.read()
    w, h = t.get("size") or [0, 0]
    name = f"{datetime.now():%Y-%m-%d} {t['name']} {n + 1} ({w}x{h}).jpg"
    return 200, {"file": utilities._token({"data": data, "name": name, "ctype": "image/jpeg"})}


# ---------- drafts ----------

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["drafts", "namesLeftOut"],
    "properties": {
        "drafts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["target", "title", "text", "overlay", "hashtags"],
            "properties": {"target": {"type": "string"}, "title": {"type": "string"}, "text": {"type": "string"},
                           "overlay": {"type": "string"}, "hashtags": {"type": "array", "items": {"type": "string"}}}}},
        "namesLeftOut": {"type": "array", "items": {"type": "string"}},
    },
}


def system_prompt(s):
    voice = s.get("voice") or {}
    sig = {k: v for k, v in (s.get("signature") or {}).items() if v}
    return "\n".join([
        "You write social media drafts for one person from their own memo and photos. They review and post every draft themselves.",
        "Rules:",
        "- Use only what the memo says and what the photos plainly show. Never add numbers, awards, names of events or claims that are not there.",
        "- Write each draft directly in its language, as the person would write it. Do not translate one draft from another.",
        "- No hype words (revolutionary, amazing, must, guaranteed, 革命的, 圧倒的, 必ず, 絶対). No pressure on the reader.",
        "- Do not write other people's full names. Refer to them by role (a professor, a friend). List any name you left out in namesLeftOut.",
        "- Hashtags go only in the hashtags list, each starting with #, never inside text. Keep within each target's tag count.",
        "- For a story, put one short line in overlay (it is written on the picture) and leave text empty.",
        "- For an article or news item, give a title and write text in Markdown. Do not repeat the title as a heading at the top of text.",
        "- Keep every draft within its limit. For X the limit is weighted: Japanese characters count 2.",
        "The person's voice by language:",
        *[f"- {lang}: {v}" for lang, v in voice.items() if v],
        *(["Signature lines to end posts with, when they fit the target (never in stories):", *[f"- {k}: {v}" for k, v in sig.items()]] if sig else []),
    ])


LINK_RULES = {
    "inline": "Put the link(s) in text exactly as given, once, where they read naturally.",
    "bio": "Write no URL: links in this caption cannot be tapped. If the link matters, say in the target's language that it is in the profile.",
    "sticker": "Write no URL: the person adds a link sticker by hand. The overlay may invite people to tap the link.",
    "none": "Write no URL.",
}


def target_lines(ts, per=None):
    """per: {target: [its links]}. Each place has its own short link, so the line says which one to use."""
    per = per or {}
    def link(t):
        mine = per.get(t["id"]) or []
        if not mine:
            return " Write no URL."
        return f" Link: {LINK_RULES[t.get('link', 'inline')]}" + (f" This target's link(s): {' '.join(mine)}" if t.get("link") == "inline" else "")
    return "\n".join(
        f"- target \"{t['id']}\" ({t['name']}): language {t['lang']}, kind {t['kind']}, limit {t['limit']}"
        f"{' weighted' if t.get('count') == 'x' else ''} characters including hashtags and links, hashtags {t.get('tags', 0)}. {t.get('style', '')}"
        + link(t) for t in ts)


def short_links(post_id, ts):
    """{target: [links to use]}: the go.tany.im ones, made once per post and kept; the originals when that fails."""
    post = get(post_id)
    links = post.get("links") or []
    if not links:
        return {}
    have = post.get("short") or {}
    need = [t["id"] for t in ts if t["id"] not in have or any(u not in have[t["id"]] for u in links)]
    if need:
        try:
            made = golinks.for_post(post_id, links, need)
            with _lock:
                post = get(post_id)
                post.setdefault("short", {}).update(made)
                post["shortError"] = ""
                _save(post)
            have = post["short"]
        except RuntimeError as e:
            with _lock:
                post = get(post_id)
                post["shortError"] = str(e)
                _save(post)
    return {t["id"]: [have.get(t["id"], {}).get(u, u) for u in links] for t in ts}


def fix_links(t, text, links):
    """The links follow the target's rule whatever came back: no made-up URLs, none where they cannot be tapped,
    and every given link once where they can."""
    keep = t.get("link") == "inline"
    text = URL.sub(lambda m: m.group(0) if keep and m.group(0).rstrip(".,!?、。") in links else "", text)
    text = re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", re.sub(r"(?<=\S)[ \t]{2,}", " ", text))).strip()
    if keep:
        missing = [u for u in links if u not in text]
        if missing:
            text = (text + "\n\n" + "\n".join(missing)).strip()
    return text


def call(content, max_tokens=16000):
    key = lifelog.api_key()
    if not key:
        raise RuntimeError("No Anthropic API key is registered on this Mac")
    s = settings()
    body = {"model": s.get("model") or MODEL, "max_tokens": max_tokens, "system": system_prompt(s),
            "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            "messages": [{"role": "user", "content": content}]}
    headers = {"x-api-key": key, "anthropic-version": lifelog.API_VERSION, "content-type": "application/json", "user-agent": "tatsuki-desk"}
    if body["model"].startswith(("claude-opus", "claude-sonnet")):
        body["fallbacks"] = "default"
        headers["anthropic-beta"] = lifelog.FALLBACK_BETA
    req = urllib.request.Request(lifelog.API, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError("Anthropic did not accept the API key") from None
        raise RuntimeError(f"Anthropic returned an error ({e.code}). Try again in a moment") from None
    except (http.client.HTTPException, OSError, ValueError):
        raise RuntimeError("Could not reach Anthropic. Check the connection and try again") from None
    if res.get("stop_reason") == "refusal":
        raise RuntimeError("Claude declined to write these drafts")
    text = next((b.get("text") for b in res.get("content", []) if b.get("type") == "text"), None)
    try:
        out = json.loads(text)
    except (TypeError, ValueError):
        raise RuntimeError("The drafts came back unreadable. Try again") from None
    u = res.get("usage") or {}
    return out, {"input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0)}


def photo_blocks(post):
    blocks = []
    for ph in post["photos"]:
        p = photo_path(ph["id"], "api") or photo_path(ph["id"])
        if p:
            with open(p, "rb") as f:
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(f.read()).decode()}})
    return blocks


def clean_draft(t, d, links=()):
    tags = []
    for x in d.get("hashtags") or []:
        x = "#" + re.sub(r"[\s#]+", "", str(x))
        if len(x) > 1 and x not in tags:
            tags.append(x[:60])
    out = {"title": str(d.get("title") or "").strip()[:200], "text": str(d.get("text") or "").strip(),
           "overlay": str(d.get("overlay") or "").strip()[:200], "hashtags": tags[: t.get("tags", 0)]}
    if t["kind"] == "story":
        out["text"] = ""
    out["text"] = fix_links(t, out["text"], links)
    out["overlay"] = fix_links({"link": "none"}, out["overlay"], links)
    out["length"] = length(t, out)
    return out


def write_drafts(post_id, only=None, hint=""):
    """Runs in the background. Every chosen target in one request; then only the long ones again, twice at most."""
    try:
        post = get(post_id)
        ts = [t for t in (target(x) for x in post["targets"]) if t and (not only or t["id"] == only)]
        when = datetime.fromtimestamp(post["createdAt"] / 1000).strftime("%Y-%m-%d")
        n = len(post["photos"])
        seen = "There is 1 photo above." if n == 1 else f"There are {n} photos above." if n else "There is no photo."
        links = post.get("links") or []
        per = short_links(post_id, ts)
        ask = (f"Date: {when}.\nThe person's memo:\n{post['memo']}\n\n{seen}\n"
               + (f"Link(s) to share (you cannot open them; the memo says what they are): {' '.join(links)}. Each target has its own short version of it, listed on its line; use that one.\n" if links else "")
               + (f"What the person wants changed this time: {hint}\n" if hint else "")
               + "Write one draft for each of these targets:\n" + target_lines(ts, per))
        out, use = call(photo_blocks(post) + [{"type": "text", "text": ask}])
        got = {d.get("target"): d for d in out.get("drafts", []) if isinstance(d, dict)}
        drafts = {t["id"]: clean_draft(t, got.get(t["id"]) or {}, per.get(t["id"], [])) for t in ts}
        for _ in range(2):
            long = [t for t in ts if drafts[t["id"]]["length"] > t["limit"]]
            if not long:
                break
            again = "These drafts are too long. Rewrite each one shorter, keeping the meaning:\n" + "\n".join(
                f"- target \"{t['id']}\": now {drafts[t['id']]['length']}, limit {t['limit']}"
                f"{' weighted (Japanese counts 2)' if t.get('count') == 'x' else ''}.\nCurrent text:\n{full_text(t, drafts[t['id']]) if t['kind'] != 'story' else drafts[t['id']]['overlay']}"
                for t in long) + "\n\nTargets:\n" + target_lines(long, per)
            more, u2 = call([{"type": "text", "text": f"The person's memo:\n{post['memo']}\n\n{again}"}], max_tokens=8000)
            use = {k: use[k] + u2[k] for k in use}
            for d in more.get("drafts", []):
                t = target(d.get("target"))
                if t and t in long:
                    drafts[t["id"]] = clean_draft(t, d, per.get(t["id"], []))
        with _lock:
            post = get(post_id)
            for tid, d in drafts.items():
                post.setdefault("drafts", {})[tid] = {**d, "at": int(time.time() * 1000)}
            post["namesLeftOut"] = sorted(set((post.get("namesLeftOut") or []) + [str(x)[:60] for x in out.get("namesLeftOut", [])]))[:10]
            post["status"], post["error"] = "ready", ""
            post["regen"] = None
            post.setdefault("usage", []).append({**use, "at": int(time.time() * 1000)})
            _save(post)
    except Exception as e:  # anything goes back to the page as a sentence, and the drafts already made stay
        with _lock:
            post = get(post_id)
            if post:
                post["status"] = "ready" if post.get("drafts") else "error"
                post["error"] = str(e) if isinstance(e, RuntimeError) else "Something went wrong while writing. Try again"
                post["regen"] = None
                _save(post)
    finally:
        _running.discard(post_id)


def _start(post_id, only=None, hint=""):
    if post_id in _running:
        return
    _running.add(post_id)
    threading.Thread(target=write_drafts, args=(post_id, only, hint), daemon=True).start()


# ---------- the posts ----------

def _all():
    return _read(dp("posts.json"), {})


def get(post_id):
    return _all().get(post_id) if POST_ID.match(post_id or "") else None


def _save(post):
    with _lock:
        posts = _all()
        post["updatedAt"] = int(time.time() * 1000)
        posts[post["id"]] = post
        _write(dp("posts.json"), posts)


def create(rec):
    memo = str(rec.get("memo") or "").strip()
    photos = [p for p in rec.get("photos") or [] if isinstance(p, str) and photo_path(p)][:PHOTOS_PER_POST]
    on = {t["id"] for t in targets()}
    chosen = [t for t in rec.get("targets") or [] if t in on]
    if not memo and not photos:
        return 400, {"error": "Add a photo or a few words about what you did"}
    if not chosen:
        return 400, {"error": "Choose at least one place to post"}
    if len(memo) > 4000:
        return 413, {"error": "The memo can be up to 4,000 characters"}
    given = rec.get("links") if isinstance(rec.get("links"), list) else [rec.get("links") or ""]
    links = []
    for u in URL.findall(" ".join(str(x) for x in given) + " " + memo):
        u = u.rstrip(".,!?、。")[:500]
        if u not in links:
            links.append(u)
    links = links[:LINKS_PER_POST]
    pid = f"p-{datetime.now():%Y%m%d}-{secrets.token_hex(3)}"
    metas = [{"id": p, **{k: v for k, v in _read(dp("photos", f"{p}.json"), {}).items() if k in ("w", "h", "x", "y")}} for p in photos]
    post = {"id": pid, "createdAt": int(time.time() * 1000), "memo": memo, "photos": metas, "targets": chosen,
            "links": links, "status": "making", "error": "", "drafts": {}, "posted": {}, "namesLeftOut": []}
    _save(post)
    _start(pid)
    return 200, public(post)


def regen(post_id, rec):
    post = get(post_id)
    tid = rec.get("target")
    if not post or tid not in post["targets"]:
        return 404, {"error": "not found"}
    if post_id in _running:
        return 409, {"error": "Still writing. Wait a moment"}
    post["regen"] = tid
    _save(post)
    _start(post_id, tid, str(rec.get("hint") or "")[:300])
    return 200, public(post)


def change(post_id, rec):
    """The page's edits: a draft's words, posted marks, a photo's focus, the story text on or off, or deleting."""
    with _lock:
        post = get(post_id)
        if not post:
            return 404, {"error": "not found"}
        if rec.get("deleted") is True:
            post["deleted"] = True
            shutil.rmtree(dp("out", post_id), ignore_errors=True)
        for tid, d in (rec.get("drafts") or {}).items():
            t = target(tid)
            old = (post.get("drafts") or {}).get(tid)
            if t and old and isinstance(d, dict):
                for k in ("text", "title", "overlay"):
                    if isinstance(d.get(k), str):
                        old[k] = d[k][:20000]
                if isinstance(d.get("hashtags"), list):
                    old["hashtags"] = [str(x)[:60] for x in d["hashtags"]][:30]
                old["edited"] = True
                old["length"] = length(t, old)
        for tid, v in (rec.get("posted") or {}).items():
            if tid in post["targets"]:
                if v:
                    post.setdefault("posted", {})[tid] = int(time.time() * 1000)
                else:
                    post.get("posted", {}).pop(tid, None)
        for tid, v in (rec.get("textOn") or {}).items():
            if tid in post["targets"] and isinstance(v, bool):
                post.setdefault("textOn", {})[tid] = v
        for n, xy in (rec.get("focus") or {}).items():
            try:
                ph = post["photos"][int(n)]
                ph["x"], ph["y"] = min(max(float(xy[0]), 0), 1), min(max(float(xy[1]), 0), 1)
            except (ValueError, IndexError, TypeError, KeyError):
                continue
        _save(post)
    return 200, public(post)


def public(post):
    """What the page needs. The text of the drafts is in it; the pictures come by their own URLs."""
    ts = {t["id"]: t for t in targets()}
    return {**{k: v for k, v in post.items() if k != "usage"}, "running": post["id"] in _running,
            "limits": {tid: ts[tid]["limit"] for tid in post["targets"] if tid in ts}}


def view():
    posts = sorted((p for p in _all().values() if not p.get("deleted")), key=lambda p: -p.get("createdAt", 0))
    return {"targets": targets(), "voice": settings().get("voice", {}), "signature": settings().get("signature", {}),
            "key": bool(lifelog.api_key()), "tool": os.path.exists(TOOL),
            "posts": [{"id": p["id"], "createdAt": p["createdAt"], "memo": p["memo"][:120], "photo": (p["photos"][0]["id"] if p["photos"] else ""),
                       "targets": len(p["targets"]), "posted": len(p.get("posted") or {}), "status": p.get("status")} for p in posts[:40]]}


def copy_html(html, plain):
    """note keeps headings and bold only when the clipboard holds formatted text, so the helper sets both kinds."""
    helper = tool()
    if not helper or not isinstance(html, str) or not isinstance(plain, str) or len(html) > 400000:
        return 400, {"error": "Could not copy"}
    with tempfile.TemporaryDirectory() as tmp:
        a, b = os.path.join(tmp, "a.html"), os.path.join(tmp, "b.txt")
        with open(a, "w", encoding="utf-8") as f:
            f.write(html)
        with open(b, "w", encoding="utf-8") as f:
            f.write(plain)
        r = subprocess.run([helper, "clip-html", a, b], capture_output=True, timeout=30)
    return (200, {"ok": True}) if r.returncode == 0 else (500, {"error": "Could not copy"})


if __name__ == "__main__":
    import sys
    if sys.argv[1:2] == ["count"]:
        print(x_length(sys.argv[2]))
    else:
        print(json.dumps(view(), ensure_ascii=False, indent=1))
