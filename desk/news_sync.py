"""Brings "Make it a task" from Sift Daily (https://sift.tany.im) into the Desk's tasks.

server.py runs watch() every 5 minutes, on 4747 only. Each round: ask Sift Daily for articles marked as a task that the Desk
hasn't taken yet, add each as a Desk task (source "news", title "Read: <title>", the link in the note), then tell Sift Daily
it was taken. An article already in the Desk (same link in sourceRef) is not added twice.
Nothing from the Desk is sent to Sift Daily.

The key is in the macOS keychain (service "tatsuki-desk-sift"; the same value as the Worker's SIFT_KEY). No key: nothing happens.

  python3 desk/news_sync.py once     one round now, prints what happened
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lifeos.lib import tasks  # noqa: E402

APP = os.environ.get("SIFT_URL", "https://sift.tany.im")
KEYCHAIN = "tatsuki-desk-sift"
EVERY = 300
UA = "tatsuki-desk-sift-sync"


def key():
    try:
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN, "-w"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _call(path, k, method="GET"):
    req = urllib.request.Request(APP + path, method=method, data=b"" if method == "POST" else None,
                                 headers={"x-sift-key": k, "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def once():
    k = key()
    if not k:
        return {"skipped": "no key in the keychain"}
    waiting = _call("/api/desk/tasks", k)
    have = {t.get("sourceRef") for t in tasks.all_tasks(include_deleted=True) if t.get("source") == "news"}
    added = 0
    for it in waiting:
        url = it.get("url") or ""
        if url and url not in have:
            note = "\n".join(x for x in [it.get("line") or it.get("descr") or "", f"From: {it.get('site') or ''}", url] if x)
            tasks.add("Read: " + (it.get("title") or url), note=note, source="news", source_ref=url)
            have.add(url)
            added += 1
        _call(f"/api/desk/tasks/{it['id']}/sent", k, method="POST")
    return {"waiting": len(waiting), "added": added}


def watch():
    while True:
        try:
            once()
        except Exception as e:  # keep the loop alive; the next round tries again
            print("news_sync:", e, file=sys.stderr)
        time.sleep(EVERY)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        print(json.dumps(once(), ensure_ascii=False))
    else:
        print(__doc__)
