#!/usr/bin/env python3
"""Tatsuki's Desk: the tabs open in Desk's own window (the tab bar at the top of desk/index.html).

Only the list of tabs and which one is active. A tab is a screen's hash (#tasks, #project/<id>, ...). It is saved as
desk/data/tabs/state.json so the tabs come back after Desk restarts. No text from the screens is kept.

Set DESK_TABS_DIR to keep a test run's file apart from the real one.
"""
import json
import os
import re
import tempfile

DIR = os.environ.get("DESK_TABS_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tabs")
FILE = os.path.join(DIR, "state.json")
MAX = 30
TAB_ID = re.compile(r"^t[a-z0-9]{1,24}$")


def _hash_ok(h):
    return isinstance(h, str) and h.startswith("#") and len(h) <= 200 and not re.search(r"[\x00-\x20\x7f]", h)


def clean(v):
    """Keep only well-formed tabs, no duplicates, at most MAX; the active id must be one of them."""
    v = v if isinstance(v, dict) else {}
    tabs, seen = [], set()
    for t in v.get("tabs") if isinstance(v.get("tabs"), list) else []:
        if not isinstance(t, dict):
            continue
        i, h = t.get("id"), t.get("hash")
        if isinstance(i, str) and TAB_ID.match(i) and i not in seen and _hash_ok(h):
            seen.add(i)
            tabs.append({"id": i, "hash": h})
    tabs = tabs[:MAX]
    act = v.get("active")
    if act not in seen:
        act = tabs[0]["id"] if tabs else None
    return {"tabs": tabs, "active": act}


def read():
    try:
        with open(FILE, encoding="utf-8") as f:
            v = json.load(f)
    except (OSError, ValueError):
        v = {}
    return clean(v)


def write(rec):
    s = clean(rec)
    if not s["tabs"]:
        return {"ok": False, "error": "a tab is needed"}
    os.makedirs(DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DIR, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)
    os.replace(tmp, FILE)
    return {"ok": True, **s}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "show":
        print(json.dumps(read(), ensure_ascii=False, indent=2))
    else:
        print("usage: python3 desk/tabs.py show")
