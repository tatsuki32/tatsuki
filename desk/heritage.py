#!/usr/bin/env python3
"""毎日世界遺産: three new countries a day, in the order of desk/heritage_countries.json.

  python3 desk/heritage.py next                 # today's countries; {"done": ...} when today's issue exists or the round is over
  python3 desk/heritage.py search "Himeji Castle"  # file titles on Wikimedia Commons to choose from
  python3 desk/heritage.py photo "File:X.jpg"   # saves one Wikimedia Commons photo and prints its record (author and license come from Commons)
  python3 desk/heritage.py save FILE.json       # checks an issue and saves it as docs/heritage/<date>.json

A missed day is not made up: the next run writes that day's three countries only.
"""
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import server  # noqa: E402

PER_DAY = 3
SITES_MAX = 3
PHOTOS_MAX = 3
KINDS = ("cultural", "natural", "mixed")
FREE_LICENSE = re.compile(r"^(cc0|cc[ -]by([ -]sa)?([ -]\d(\.\d)?.*)?|public domain|pd\b.*)$", re.I)
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
COMMONS_HOSTS = ("upload.wikimedia.org", "thumb.wikimedia.org")
USER_AGENT = "TatsukiDesk/1.0 (personal dashboard; heritage photos)"
PHOTO_TYPES = {"image/jpeg": "jpg", "image/png": "png"}
PHOTO_MAX_BYTES = 8 * 1024 * 1024


def today():
    return datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%Y-%m-%d")


def countries():
    with open(os.path.join(ROOT, "heritage_countries.json"), encoding="utf-8") as f:
        return [{"code": c, "name": n, "region": r} for c, n, r in json.load(f)]


def issues():
    return [d for d in server.feed_docs("heritage") if not d.get("deleted")]


def next_countries(date=None):
    """What the issue of this date must hold, or a reason there is nothing to write."""
    date = date or today()
    docs = issues()
    if any(d["id"] == date for d in docs):
        return {"done": "today", "date": date}
    seen = {c.get("code") for d in docs for c in d.get("countries", [])}
    rest = [c for c in countries() if c["code"] not in seen]
    if not rest:
        return {"done": "round", "date": date}
    return {"date": date, "countries": rest[:PER_DAY], "read": len(seen), "total": len(rest) + len(seen)}


def photos_dir():
    return os.path.join(server.DATA, "heritage", "photos")


def _plain(text):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", str(text or ""))).split())


def _get(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    if host != "commons.wikimedia.org" and host not in COMMONS_HOSTS:
        raise ValueError("Not a Commons URL")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for wait in (20, 60, None):  # Commons answers 429 when asked too fast
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read(PHOTO_MAX_BYTES + 1), r.headers.get_content_type()
        except urllib.error.HTTPError as e:
            if e.code != 429 or wait is None:
                raise
            time.sleep(wait)


def photo(title, width=1200):
    """Saves one photo. The author and license are read from Commons, so a wrong credit cannot be typed in."""
    title = title if title.startswith("File:") else "File:" + title
    query = urllib.parse.urlencode({"action": "query", "format": "json", "titles": title, "prop": "imageinfo", "iiprop": "url|mime|extmetadata", "iiurlwidth": width})
    data, _ = _get(COMMONS_API + "?" + query)
    pages = json.loads(data)["query"]["pages"]
    info = (next(iter(pages.values())).get("imageinfo") or [None])[0]
    if not info:
        raise ValueError("Not a file on Commons: " + title)
    meta = info.get("extmetadata") or {}
    field = lambda k: _plain((meta.get(k) or {}).get("value"))
    author, license_name = field("Artist"), field("LicenseShortName")
    if not author:
        raise ValueError("Author unknown: " + title)
    if not FREE_LICENSE.match(license_name):
        raise ValueError(f"License not usable ({license_name or 'unknown'}): {title}")
    body, ctype = _get(info.get("thumburl") or info["url"])
    if ctype not in PHOTO_TYPES or len(body) > PHOTO_MAX_BYTES:
        raise ValueError("Only JPEG or PNG up to 8 MB: " + title)
    name = hashlib.sha256(title.encode("utf-8")).hexdigest()[:16] + "." + PHOTO_TYPES[ctype]
    os.makedirs(photos_dir(), exist_ok=True)
    server.write_bytes_atomic(os.path.join(photos_dir(), name), body)
    return {"file": name, "author": author[:120], "license": license_name, "page": info["descriptionurl"]}


def search(words, limit=12):
    """File titles on Commons for the task to choose from. photo() still checks each one."""
    query = urllib.parse.urlencode({"action": "query", "format": "json", "list": "search", "srnamespace": 6, "srlimit": limit, "srsearch": words + " filetype:bitmap"})
    data, _ = _get(COMMONS_API + "?" + query)
    return [x["title"] for x in json.loads(data)["query"]["search"]]


def _https(url):
    return isinstance(url, str) and url.startswith("https://")


def _check_photos(items, where, errors):
    if not isinstance(items, list) or len(items) > PHOTOS_MAX:
        errors.append(f"{where}: up to {PHOTOS_MAX} photos")
        return
    for p in items:
        p = p if isinstance(p, dict) else {}
        ok_file = server.HERITAGE_PHOTO.match(str(p.get("file") or "")) and os.path.exists(os.path.join(photos_dir(), p["file"]))
        if not ok_file:
            errors.append(f"{where}: photo file is missing")
        if not p.get("author") or not FREE_LICENSE.match(str(p.get("license") or "")):
            errors.append(f"{where}: photo author or license is missing")
        if not str(p.get("page") or "").startswith("https://commons.wikimedia.org/"):
            errors.append(f"{where}: photo Commons page is missing")


def check(doc, expected):
    """Every problem in an issue, in English. An empty list means it can be saved."""
    errors = []
    items = doc.get("countries") if isinstance(doc.get("countries"), list) else []
    codes = [c.get("code") for c in items if isinstance(c, dict)]
    want = [c["code"] for c in expected]
    if codes != want:
        errors.append(f"Wrong countries (expected: {', '.join(want)})")
    for c in items:
        c = c if isinstance(c, dict) else {}
        where = c.get("name") or c.get("code") or "Country"
        for key, label in (("name", "name"), ("capital", "capital"), ("about", "description")):
            if not str(c.get(key) or "").strip():
                errors.append(f"{where}: {label} is missing")
        sources = c.get("sources") if isinstance(c.get("sources"), list) else []
        if not sources or not all(isinstance(s, dict) and s.get("name") and _https(s.get("url")) for s in sources):
            errors.append(f"{where}: sources are missing")
        _check_photos(c.get("photos") or [], where, errors)
        sites = c.get("sites") if isinstance(c.get("sites"), list) else []
        if len(sites) > SITES_MAX:
            errors.append(f"{where}: up to {SITES_MAX} sites")
        if not sites and not str(c.get("noSites") or "").strip():
            errors.append(f"{where}: needs sites, or the reason there are none (noSites)")
        for s in sites:
            s = s if isinstance(s, dict) else {}
            name = f"{where} {s.get('name') or 'site'}"
            if not s.get("name") or not s.get("why"):
                errors.append(f"{name}: name or reason for listing is missing")
            if s.get("kind") not in KINDS:
                errors.append(f"{name}: kind must be {' / '.join(KINDS)}")
            if not isinstance(s.get("year"), int):
                errors.append(f"{name}: year listed is missing")
            if not _https(s.get("url")):
                errors.append(f"{name}: source is missing")
            _check_photos(s.get("photos") or [], name, errors)
        if not c.get("photos") and not any(s.get("photos") for s in sites if isinstance(s, dict)):
            errors.append(f"{where}: no photos at all")
    return errors


def save(doc, date=None):
    date = date or today()
    plan = next_countries(date)
    if plan.get("done"):
        return {"done": plan["done"], "date": date}
    errors = check(doc, plan["countries"])
    if errors:
        return {"errors": errors}
    regions = {c["code"]: c["region"] for c in plan["countries"]}
    items = [{**c, "region": regions[c["code"]]} for c in doc["countries"]]
    rec = {"date": date, "countries": items, "updatedAt": int(time.time() * 1000)}
    with server.LOCK:
        server.put_doc("heritage", date, rec)
        server.rebuild_feed("heritage")
    return {"saved": date, "countries": [c["name"] for c in items]}


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "next":
        out = next_countries()
    elif cmd in ("photo", "search") and len(argv) == 2:
        try:
            out = photo(argv[1]) if cmd == "photo" else {"files": search(argv[1])}
        except (ValueError, OSError, KeyError) as e:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
            return 1
    elif cmd == "save" and len(argv) == 2:
        with open(argv[1], encoding="utf-8") as f:
            out = save(json.load(f))
    else:
        print(__doc__)
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 1 if out.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
