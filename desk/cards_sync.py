"""Keeps the flashcards in step with the Cards app on the phone (cards/, Cloudflare Pages).

server.py runs watch() every 2 minutes, on 4747 only. Each round: read cards + cardlog from this Desk's own API, send them to the
app's /api/sync (which merges them with the phone's: the newer card wins, a day's review entries are joined), and write back
every doc that came back different. A card typed on the phone arrives with its meaning missing; cards.watch fills it next round.

Only cards travel: a word, its meaning, the one sentence it was met in. A book's text never leaves the Mac.
The passphrase is in the macOS keychain (service "tatsuki-desk-cards"), never in a file. No passphrase: nothing happens.

  python3 desk/cards_sync.py once     one round now, prints counts
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

PORT = int(os.environ.get("DESK_PORT", "4747"))
LOCAL = f"http://127.0.0.1:{PORT}/api/"
APP = os.environ.get("CARDS_APP_URL", "https://tatsuki-cards.pages.dev/")
CLOUD = APP + "api/sync"
KEYCHAIN = "tatsuki-desk-cards"
EVERY = 120
COLS = ("cards", "cardlog")


def key():
    try:
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN, "-w"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def local_get(col):
    with urllib.request.urlopen(LOCAL + col, timeout=10) as r:
        v = json.load(r)
    return [x for x in v if isinstance(x, dict) and x.get("id")] if isinstance(v, list) else []


def local_put(col, doc):
    body = {k: v for k, v in doc.items() if k != "id"}
    req = urllib.request.Request(LOCAL + col + "/" + urllib.request.quote(doc["id"], safe=""), data=json.dumps(body, ensure_ascii=False).encode(),
                                 method="PUT", headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{PORT}"})
    urllib.request.urlopen(req, timeout=10).read()


def once():
    k = key()
    if not k:
        return {"skipped": "no passphrase in the keychain"}
    mine = {col: local_get(col) for col in COLS}
    req = urllib.request.Request(CLOUD, data=json.dumps(mine, ensure_ascii=False).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-sync-key": k, "User-Agent": "tatsuki-desk-cards-sync"})
    with urllib.request.urlopen(req, timeout=30) as r:
        merged = json.load(r)
    wrote = 0
    for col in COLS:
        have = {d["id"]: d for d in mine[col]}
        for d in merged.get(col) or []:
            if not isinstance(d, dict) or not d.get("id"):
                continue
            old = have.get(d["id"])
            if old is None or json.dumps(old, sort_keys=True) != json.dumps(d, sort_keys=True):
                local_put(col, d)
                wrote += 1
    return {"sent": sum(len(v) for v in mine.values()), "wrote": wrote}


def watch():
    time.sleep(40)  # let the server start answering first
    while True:
        try:
            once()
        except Exception as e:  # offline or a bad answer: try again next round
            print("cards_sync:", type(e).__name__, e, file=sys.stderr, flush=True)
        time.sleep(EVERY)


if __name__ == "__main__":
    if (sys.argv[1:] or ["once"])[0] == "once":
        print(json.dumps(once()))
    else:
        print(__doc__)
