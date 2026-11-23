"""Keeps a copy of Rhizome Box (https://rhizomebox.tany.im) on this Mac, once a day.

server.py runs watch() on 4747 only. About once a day it asks Rhizome Box for every page (public and private, with
their earlier titles and settings) and writes desk/data/rhizomebox/pages.json, then downloads any file it does not have yet
into desk/data/rhizomebox/files/. The private backup (backup_data.py) takes pages.json; the files stay on this Mac only
(GitHub refuses files over 100 MB). A file deleted in Rhizome Box stays here. Nothing is sent to Rhizome Box except the
request itself.

usage() is the number the Desk shows on the Rhizome Box tile: how much of the 9 GB is used (numbers only, 10 minutes).

The key is in the macOS keychain (service "tatsuki-desk-rhizomebox"; the same value as the Worker's BACKUP_KEY).
No key: nothing happens. The address is https://rhizomebox.tany.im unless desk/data/rhizomebox/config.json says
{"url": "..."} (used while the site is on its test address).

  python3 desk/rhizomebox_backup.py once     one copy now, prints what happened
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

DATA = os.environ.get("DESK_DATA") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DIR = os.path.join(DATA, "rhizomebox")
KEYCHAIN = "tatsuki-desk-rhizomebox"
EVERY = 6 * 3600          # look every 6 hours
FRESH = 20 * 3600         # copy when the last one is older than this
UA = "tatsuki-desk-rhizomebox-backup"
ROOM = 10 * 10 ** 9       # leave at least this much free on the Mac


def key():
    try:
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN, "-w"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def site():
    try:
        with open(os.path.join(DIR, "config.json"), encoding="utf-8") as f:
            u = json.load(f).get("url", "")
            if u.startswith("https://"):
                return u.rstrip("/")
    except (OSError, ValueError):
        pass
    return "https://rhizomebox.tany.im"


def _get(url, k, timeout=60):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + k, "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _download(url, k, path, size):
    """Straight to disk (files can be 500 MB); a file that arrives short is thrown away."""
    tmp = path + ".tmp"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + k, "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        if size and os.path.getsize(tmp) != size:
            raise OSError("short file")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


_usage = {"at": 0.0, "data": None}


def usage():
    """{used, cap, count, at}, or {} when there is no key or the site has not answered yet."""
    if time.time() - _usage["at"] > 600:
        _usage["at"] = time.time()
        k = key()
        if k:
            try:
                d = json.loads(_get(site() + "/_/api/storage", k, timeout=8))
                _usage["data"] = {n: int(d[n]) for n in ("used", "cap", "count")}
                _usage["data"]["at"] = int(time.time())
            except (OSError, ValueError, KeyError, TypeError):
                pass
    return _usage["data"] or {}


def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def once():
    k = key()
    if not k:
        return {"skipped": "no key in the keychain"}
    base = site()
    raw = _get(base + "/_/api/export", k)
    data = json.loads(raw)
    os.makedirs(os.path.join(DIR, "files"), exist_ok=True)
    _write(os.path.join(DIR, "pages.json"), json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))
    got = skipped = 0
    for f in data.get("files", []):
        name = f.get("name", "")
        if not re.fullmatch(r"[\w.-]+", name):
            continue
        path = os.path.join(DIR, "files", name)
        if os.path.exists(path):
            continue
        size = int(f.get("size") or 0)
        if shutil.disk_usage(DIR).free - size < ROOM:
            skipped += 1
            continue
        try:
            _download(base + "/files/" + name, k, path, size)
            got += 1
        except OSError:
            pass
    _write(os.path.join(DIR, "last.json"), json.dumps({"at": int(time.time()), "pages": len(data.get("pages", [])), "files": got, "skipped": skipped}).encode())
    return {"pages": len(data.get("pages", [])), "new_files": got, "skipped_no_room": skipped}


def last():
    try:
        with open(os.path.join(DIR, "last.json"), encoding="utf-8") as f:
            return json.load(f).get("at", 0)
    except (OSError, ValueError):
        return 0


def watch():
    time.sleep(90)
    while True:
        if time.time() - last() > FRESH:
            try:
                once()
            except Exception as e:  # keep the Desk server running whatever happens here
                sys.stderr.write("rhizomebox backup: %s\n" % e)
        time.sleep(EVERY)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        print(json.dumps(once(), ensure_ascii=False))
    else:
        print(__doc__)
