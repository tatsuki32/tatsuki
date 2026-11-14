#!/usr/bin/env python3
"""Tatsuki's Desk — go.tany.im short links (the Worker is golinks/ in this repo).

Desk asks the Worker to make a short link for each place a post goes, and reads the click counts back.
The key is in the macOS keychain (tatsuki-desk-go) and never in a file. When the Worker cannot be reached,
Post Studio keeps the original links, so a draft never waits on this.
"""
import json
import os
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.environ.get("DESK_GO_BASE") or "https://go.tany.im"
KEYCHAIN = ("tatsuki-desk-go", "desk")
# which place a Post Studio target counts as
SOURCE = {"ig": "instagram", "threads": "threads", "x": "x", "fb": "facebook", "linkedin": "linkedin", "note": "note",
          "news": "site", "reddit": "reddit", "yt": "youtube", "tiktok": "tiktok"}
_key = None


def key():
    global _key
    if _key is None:
        v = os.environ.get("DESK_GO_KEY")
        if not v:
            try:
                out = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN[0], "-a", KEYCHAIN[1], "-w"],
                                     capture_output=True, text=True, timeout=20)
                v = out.stdout.strip() if out.returncode == 0 else ""
            except (OSError, subprocess.SubprocessError):
                v = ""
        _key = v
    return _key or None


def _call(method, path, body=None, timeout=15):
    k = key()
    if not k:
        raise RuntimeError("The go.tany.im key is not on this Mac")
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"authorization": "Bearer " + k, "content-type": "application/json", "user-agent": "tatsuki-desk"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error")
        except ValueError:
            msg = None
        raise RuntimeError(msg or f"go.tany.im answered {e.code}") from None
    except (OSError, ValueError):
        raise RuntimeError("Could not reach go.tany.im") from None


def source_of(target_id):
    head = str(target_id).split("-")[0]
    return SOURCE.get(head, "other")


def shorten(url, source="other", campaign="", label=""):
    """The short link for one place. Asking again for the same link, place and post gives the same one."""
    return _call("POST", "/api/links", {"dest": url, "source": source, "campaign": campaign, "label": label})["short"]


def for_post(post_id, links, target_ids):
    """{target: {original: short}} for every target of a post. Raises when go.tany.im cannot be used."""
    out = {}
    for tid in target_ids:
        out[tid] = {u: shorten(u, source_of(tid), post_id) for u in links}
    return out


def stats(days=30):
    """The Worker's counts, put together for the Links tab: one row per short link, newest first."""
    d = _call("GET", f"/api/stats?days={int(days)}")
    rows = {l["code"]: {**l, "people": 0, "bots": 0, "last": None, "days": {}, "countries": {}, "from": {}, "devices": {}} for l in d["links"]}
    for p in d["per"]:
        if p["code"] in rows:
            rows[p["code"]].update(people=p["people"] or 0, bots=p["bots"] or 0, last=p["last"])
    for name, field, col in (("byDay", "days", "day"), ("where", "countries", "country"), ("from", "from", "referrer"), ("kind", "devices", "device")):
        for x in d[name]:
            if x["code"] in rows:
                k = x[col]
                if col == "day":  # days since 1970 in UTC, shown as the date in Ho Chi Minh
                    k = (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(days=k)).astimezone(timezone(timedelta(hours=7))).strftime("%Y-%m-%d")
                rows[x["code"]][field][k or "?"] = rows[x["code"]][field].get(k or "?", 0) + x["n"]
    links = sorted(rows.values(), key=lambda r: -r["created"])
    by_source = {}
    for r in links:
        by_source[r["source"]] = by_source.get(r["source"], 0) + r["people"]
    return {"days": d["days"], "links": links, "bySource": by_source, "people": sum(r["people"] for r in links),
            "base": BASE}


def turn_off(code):
    return _call("DELETE", f"/api/links/{code}")


# --- automatic posting (the queue lives on go.tany.im so it is sent even while this Mac sleeps) ---

_accounts = {"at": 0, "value": None}


def accounts(fresh=False):
    """The connected accounts: id (provider:account), provider, handle, problem. Never the tokens. Kept a minute."""
    import time
    if fresh or not _accounts["value"] or time.time() - _accounts["at"] > 60:
        _accounts["value"] = _call("GET", "/api/accounts")["accounts"]
        _accounts["at"] = time.time()
    return _accounts["value"]


def upload(path):
    """One picture to the two-day store Instagram and Threads fetch from. Returns its key."""
    k = key()
    if not k:
        raise RuntimeError("The go.tany.im key is not on this Mac")
    with open(path, "rb") as f:
        data = f.read()
    req = urllib.request.Request(BASE + "/api/media", method="POST", data=data,
                                 headers={"authorization": "Bearer " + k, "content-type": "image/jpeg", "user-agent": "tatsuki-desk"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)["key"]
    except (OSError, ValueError, KeyError):
        raise RuntimeError("Could not send the picture to go.tany.im") from None


def queue_add(rec):
    return _call("POST", "/api/queue", rec)


def queue_list():
    return _call("GET", "/api/queue")["items"]


def queue_do(qid, action, body=None):
    # "now" waits for Meta to prepare the picture, which can take a minute
    return _call("POST", f"/api/queue/{qid}/{action}", body or {}, timeout=120)


if __name__ == "__main__":
    print(json.dumps(stats(), ensure_ascii=False, indent=1))
