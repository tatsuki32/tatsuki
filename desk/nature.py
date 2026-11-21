"""Nature videos for Study Focus (#study): real footage of forests, mountains, rivers and green places.

Only fetched when the button on the screen is pressed. Sources:
- Wikimedia Commons: free licenses only (CC0, CC BY, CC BY-SA, public domain). The author and license are
  read from Commons by this code, so a wrong credit cannot be typed in. A file without an author is skipped.
- Pexels (only when a key is in the keychain, service tatsuki-desk-pexels): the Pexels license, credited by name.
Files go to data/videos/ (out of the private backup: they are large) and are played through /videos/<name>.
The list is data/videos/nature.json. A video removed on the screen is never fetched again.

  python3 desk/nature.py fetch     # fetch now, in this terminal
  python3 desk/nature.py status
  python3 desk/nature.py add <file> [title]   # a video already on this Mac (kept here only, never sent anywhere)
"""
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("NATURE_DIR") or os.path.join(ROOT, "data", "videos")
LIST = os.path.join(DIR, "nature.json")
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
PEXELS_API = "https://api.pexels.com/videos/search"
HOSTS = ("commons.wikimedia.org", "upload.wikimedia.org", "api.pexels.com", "videos.pexels.com")
USER_AGENT = "TatsukiDesk/1.0 (personal dashboard; nature videos for a focus timer)"
FREE_LICENSE = re.compile(r"^(cc0|cc[ -]by([ -]sa)?([ -]\d(\.\d)?.*)?|public domain|pd\b.*)$", re.I)
QUERIES = ["forest stream", "waterfall forest", "mountain forest", "moss forest", "rainforest", "green valley",
           "mountain lake", "forest sunlight", "river forest", "alpine meadow"]
PEXELS_QUERIES = ["green forest", "forest stream", "waterfall", "misty mountains", "moss", "rainforest", "mountain valley", "sunlight forest"]
AVOID = re.compile(r"\b(city|street|traffic|car|road|people|person|man|woman|protest|interview|lecture|game|drone crash|fire|smoke|war)\b", re.I)
WANT = 10                     # how many videos to keep
MAX_ONE = 350 * 1024 ** 2     # one file
MAX_ALL = 2500 * 1024 ** 2    # all nature videos together
STATE = {"running": False, "done": 0, "want": 0, "error": "", "at": 0, "current": ""}
_lock = threading.Lock()


def _plain(text):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", str(text or ""))).split())


def load():
    try:
        with open(LIST, encoding="utf-8") as f:
            d = json.load(f)
        return {"videos": d.get("videos") or [], "hidden": d.get("hidden") or []}
    except (OSError, ValueError):
        return {"videos": [], "hidden": []}


def save(d):
    os.makedirs(DIR, exist_ok=True)
    tmp = LIST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, LIST)


def pexels_key():
    try:
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", "tatsuki-desk-pexels", "-w"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _open(url, headers=None, timeout=30):
    host = urllib.parse.urlsplit(url).hostname or ""
    if host not in HOSTS:
        raise ValueError("Not an allowed host: " + host)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    for wait in (20, 60, None):  # Commons answers 429 when asked too fast
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code != 429 or wait is None:
                raise
            time.sleep(wait)


def _json(url, headers=None):
    with _open(url, headers) as r:
        return json.load(r)


def commons_candidates(query, limit=20):
    """Commons videos for one search: HD, at least 20 seconds, a free license and a known author."""
    q = urllib.parse.urlencode({"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
                                "gsrsearch": query + " filetype:video", "gsrlimit": limit, "prop": "videoinfo",
                                "viprop": "url|size|mime|extmetadata|derivatives"})
    pages = (_json(COMMONS_API + "?" + q).get("query") or {}).get("pages") or {}
    out = []
    for p in pages.values():
        info = (p.get("videoinfo") or [None])[0]
        title = p.get("title", "")
        if not info or AVOID.search(title):
            continue
        meta = info.get("extmetadata") or {}
        field = lambda k: _plain((meta.get(k) or {}).get("value"))
        author, lic = field("Artist"), field("LicenseShortName")
        if not author or not FREE_LICENSE.match(lic) or float(info.get("duration") or 0) < 20 or (info.get("height") or 0) < 720:
            continue
        ders = info.get("derivatives") or []
        pick = None
        for key in ("1080p.mp4", "1080p.vp9.webm", "720p.mp4", "720p.vp9.webm", "1080p.webm", "720p.webm"):
            pick = next((d for d in ders if (d.get("transcodekey") or "").endswith(key)), None)
            if pick:
                break
        if not pick:
            continue
        out.append({"id": "c:" + title, "src": pick["src"], "ext": "mp4" if pick["src"].endswith(".mp4") else "webm",
                    "title": re.sub(r"^File:|\.(webm|ogv|mp4|mpg|mpeg)$", "", title, flags=re.I).replace("_", " ")[:120],
                    "author": author[:120], "license": lic, "page": info.get("descriptionurl", ""), "source": "Wikimedia Commons",
                    "w": info.get("width"), "h": info.get("height"), "dur": round(float(info.get("duration") or 0))})
    return sorted(out, key=lambda x: -(x["h"] or 0))


def pexels_candidates(key, query, limit=15):
    q = urllib.parse.urlencode({"query": query, "orientation": "landscape", "size": "large", "per_page": limit})
    out = []
    for v in _json(PEXELS_API + "?" + q, {"Authorization": key}).get("videos") or []:
        files = [f for f in v.get("video_files") or [] if f.get("file_type") == "video/mp4" and (f.get("height") or 0) >= 720]
        if not files or (v.get("duration") or 0) < 15:
            continue
        f = min(files, key=lambda f: abs((f.get("height") or 0) - 1080))
        user = (v.get("user") or {}).get("name") or ""
        if not user:
            continue
        out.append({"id": "p:%s" % v["id"], "src": f["link"], "ext": "mp4", "title": query.title(), "author": user[:120],
                    "license": "Pexels License", "page": v.get("url", ""), "source": "Pexels",
                    "w": f.get("width"), "h": f.get("height"), "dur": v.get("duration")})
    return out


def _download(c):
    name = hashlib.sha256(c["id"].encode("utf-8")).hexdigest()[:32] + "." + c["ext"]
    path = os.path.join(DIR, name)
    if os.path.exists(path):
        return name, os.path.getsize(path)
    tmp, n = path + ".part", 0
    os.makedirs(DIR, exist_ok=True)
    try:
        with _open(c["src"], timeout=60) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                n += len(chunk)
                if n > MAX_ONE:
                    raise ValueError("too large")
                f.write(chunk)
        with open(tmp, "rb") as f:
            head = f.read(12)
        if not (head[4:8] == b"ftyp" or head.startswith(b"\x1a\x45\xdf\xa3")):
            raise ValueError("not a video")
        os.replace(tmp, path)
        return name, n
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def fetch(want=WANT, log=print):
    d = load()
    have = {v["id"] for v in d["videos"]}
    skip = have | set(d["hidden"])
    total = sum(v.get("size", 0) for v in d["videos"])
    need = max(0, want - len(d["videos"]))
    STATE.update(want=need, done=0, error="")
    cands = []
    key = pexels_key()
    if key:
        for q in PEXELS_QUERIES:
            try:
                cands += pexels_candidates(key, q)[:2]
            except (OSError, ValueError) as e:
                log("pexels:", q, e)
    for q in QUERIES:
        try:
            cands += commons_candidates(q)[:2]
        except (OSError, ValueError) as e:
            log("commons:", q, e)
        time.sleep(1)
    seen = set()
    for c in cands:
        if STATE["done"] >= need or total >= MAX_ALL:
            break
        if c["id"] in skip or c["id"] in seen:
            continue
        seen.add(c["id"])
        STATE["current"] = c["title"]
        try:
            name, size = _download(c)
        except (OSError, ValueError) as e:
            log("skip:", c["title"], e)
            continue
        c = {k: v for k, v in c.items() if k != "src"}
        d["videos"].append({**c, "file": name, "size": size, "at": int(time.time() * 1000)})
        total += size
        STATE["done"] += 1
        save(d)
        log("saved:", c["title"], round(size / 1e6), "MB")
    STATE["current"] = ""
    if not d["videos"]:
        STATE["error"] = "No videos could be fetched. Check the internet and try again."
    return d


def start():
    """Fetches in the background (the screen polls view())."""
    with _lock:
        if STATE["running"]:
            return
        STATE.update(running=True, at=int(time.time() * 1000))

    def run():
        try:
            fetch(log=lambda *a: None)
        except Exception as e:  # noqa: BLE001 - shown on the screen, the server keeps running
            STATE["error"] = "Fetching stopped: %s" % e.__class__.__name__
        finally:
            STATE["running"] = False
    threading.Thread(target=run, daemon=True).start()


def hide(vid):
    d = load()
    keep = []
    for v in d["videos"]:
        if v["id"] == vid:
            try:
                os.remove(os.path.join(DIR, v["file"]))
            except OSError:
                pass
        else:
            keep.append(v)
    d["videos"] = keep
    if vid not in d["hidden"]:
        d["hidden"].append(vid)
    save(d)
    return view()


def add_local(path, title="", source="Saved on this Mac", license="Personal use only", page="", sound=True):
    """A video the owner saved: copied next to the others, played with its own sound if it has one."""
    path = os.path.abspath(os.path.expanduser(path))
    with open(path, "rb") as f:
        head = f.read(12)
    ext = "mp4" if head[4:8] == b"ftyp" else "webm" if head.startswith(b"\x1a\x45\xdf\xa3") else ""
    if not ext:
        raise ValueError("not an mp4 or webm video")
    st = os.stat(path)
    vid = "l:%s:%d" % (os.path.basename(path), st.st_size)
    d = load()
    if any(v["id"] == vid for v in d["videos"]):
        return view()
    name = hashlib.sha256(vid.encode("utf-8")).hexdigest()[:32] + "." + ext
    os.makedirs(DIR, exist_ok=True)
    tmp = os.path.join(DIR, name + ".part")
    shutil.copyfile(path, tmp)
    os.replace(tmp, os.path.join(DIR, name))
    if not title:
        title = re.sub(r"_(\d{3,4}p)$", "", os.path.splitext(os.path.basename(path))[0]).replace(" _ ", " · ")
    d["hidden"] = [h for h in d["hidden"] if h != vid]
    d["videos"].insert(0, {"id": vid, "file": name, "title": title[:120], "author": "", "license": license, "page": page,
                           "source": source, "size": st.st_size, "sound": bool(sound), "local": True, "at": int(time.time() * 1000)})
    save(d)
    return view()


def view():
    d = load()
    vids = [{k: v.get(k) for k in ("id", "file", "title", "author", "license", "page", "source", "dur", "sound", "local")}
            for v in d["videos"] if os.path.exists(os.path.join(DIR, v.get("file") or "-"))]
    return {"videos": vids, "state": dict(STATE), "pexels": bool(pexels_key()),
            "size": sum(v.get("size", 0) for v in d["videos"])}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "fetch":
        fetch()
    elif cmd == "add" and len(sys.argv) > 2:
        add_local(sys.argv[2], " ".join(sys.argv[3:]))
    print(json.dumps(view(), ensure_ascii=False, indent=1))
