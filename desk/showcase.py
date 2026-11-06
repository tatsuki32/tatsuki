#!/usr/bin/env python3
"""Tatsuki's Desk — 見せる素材: the portfolio site, its local file and the PR photos on Drive. Look only.

Nothing here edits the site, publishes to Netlify or changes Drive, its files or its sharing. Where to look lives in
desk/data/showcase.json (siteUrl, localFile, driveFolder), never in this public repository.

The photo list comes from Drive's public folder page, which needs no login or key while the folder is shared by link.
Google does not document that page, so a folder it cannot read is reported and the link to Drive stays.
Photos are shown by Drive's own thumbnail address and no copy is kept on this Mac.

  python3 showcase.py     what the page shows
"""
import html
import json
import os
import re
import subprocess
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
CONF = os.environ.get("SHOWCASE_CONF") or os.path.join(ROOT, "data", "showcase.json")
LISTING = "https://drive.google.com/embeddedfolderview?id={}"
THUMB = "https://drive.google.com/thumbnail?id={}&sz=w400"
FILE = "https://drive.google.com/file/d/{}/view"
FOLDER = "https://drive.google.com/drive/folders/{}"
ID = re.compile(r"^[\w-]{10,}$")
FRESH = 600  # a good list is kept this many seconds
RETRY = 60  # after a failure, ask again this soon
MAX_FOLDERS = 30
MAX_DEPTH = 3
TIMEOUT = 15

_lock = threading.Lock()
_cache = {"key": None, "at": 0, "ok": False, "folders": None}


def read_conf():
    try:
        with open(CONF, encoding="utf-8") as f:
            conf = json.load(f)
        return conf if isinstance(conf, dict) else {}
    except (OSError, ValueError):
        return {}


def parse_listing(page):
    """(title, entries) of a public folder page, or None when the page is not a folder list (a login page, say)."""
    if "flip-entries" not in page:
        return None
    title = re.search(r"<title>(.*?)</title>", page, re.S)
    entries = []
    for block in page.split('class="flip-entry"')[1:]:
        fid = re.search(r'id="entry-([\w-]+)"', block)
        name = re.search(r'flip-entry-title">(.*?)</div>', block, re.S)
        if not fid or not ID.match(fid.group(1)):
            continue
        link = re.search(r'<a href="([^"]*)"', block)
        icon = re.search(r"/type/([a-z-]+)/", block)
        if link and "/drive/folders/" in link.group(1):
            kind = "folder"
        else:
            kind = {"image": "photo", "video": "video"}.get(icon.group(1) if icon else "", "file")
        entries.append({"id": fid.group(1), "title": html.unescape(name.group(1)).strip() if name else "", "kind": kind})
    return html.unescape(title.group(1)).strip() if title else "", entries


def fetch_page(folder):
    req = urllib.request.Request(LISTING.format(folder), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def item(entry):
    return {
        "id": entry["id"], "title": entry["title"], "kind": entry["kind"],
        "thumb": THUMB.format(entry["id"]), "url": FILE.format(entry["id"]),
    }


def read_folders(root, fetch=fetch_page):
    """The folder and the ones inside it, outermost first. A folder that cannot be read says so and keeps its link."""
    folders, queue, seen = [], [(root, "", 0)], {root}
    while queue and len(folders) < MAX_FOLDERS:
        fid, known, depth = queue.pop(0)
        row = {"id": fid, "title": known, "url": FOLDER.format(fid), "ok": False, "items": []}
        folders.append(row)
        try:
            got = parse_listing(fetch(fid))
        except (OSError, ValueError):
            got = None
        if got is None:
            continue
        row["ok"] = True
        row["title"] = got[0] or known
        row["items"] = [item(e) for e in got[1] if e["kind"] != "folder"]
        if depth + 1 < MAX_DEPTH:
            for e in got[1]:
                if e["kind"] == "folder" and e["id"] not in seen:
                    seen.add(e["id"])
                    queue.append((e["id"], e["title"], depth + 1))
    return folders


def drive_view(root, now=None, fetch=fetch_page):
    now = time.time() if now is None else now
    with _lock:
        same = _cache["key"] == root and _cache["folders"] is not None
        if not (same and now - _cache["at"] < (FRESH if _cache["ok"] else RETRY)):
            folders = read_folders(root, fetch)
            ok = bool(folders and folders[0]["ok"])
            # a failed read does not wipe a list that was good a moment ago
            if ok or not (same and _cache["ok"]):
                _cache.update(key=root, ok=ok, folders=folders)
            _cache["at"] = now
        return {"url": FOLDER.format(root), "ok": _cache["ok"], "folders": _cache["folders"]}


def view():
    conf = read_conf()
    site = conf.get("siteUrl") if str(conf.get("siteUrl") or "").startswith("https://") else None
    path = str(conf.get("localFile") or "")
    root = str(conf.get("driveFolder") or "")
    return {
        "site": site,
        "local": {"name": os.path.basename(path), "exists": os.path.isfile(path)} if path else None,
        "drive": drive_view(root) if ID.match(root) else None,
    }


def open_local():
    """Opens the configured file and nothing else: the page sends no path."""
    path = str(read_conf().get("localFile") or "")
    if not os.path.isfile(path):
        return False
    try:
        subprocess.run(["/usr/bin/open", path], check=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


if __name__ == "__main__":
    print(json.dumps(view(), ensure_ascii=False, indent=2))
