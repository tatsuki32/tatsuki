#!/usr/bin/env python3
"""Tatsuki's Desk — merge the Mac's data with the online desk (Artifact DB).

Used by the hourly sync task (see sync/HOURLY_SYNC.md). Claude reads the online
collections into one JSON file, runs this script, and writes back what it prints.

  python3 sync.py merge REMOTE.json   -> updates data/*.json and data/*.js,
                                         prints {"write": {collection: [docs]}}
                                         = docs the online desk must `set` (id kept)
  python3 sync.py code-status         -> prints {"changed": bool, "hash": ...}
                                         (index.html differs from the last publish)
  python3 sync.py mark-published      -> remembers the current index.html as published

REMOTE.json: {"memos": [{"id": ..., ...fields}], "dues": [...], "tasks": [...], "prompts": [...], "notes": [...], "habits": [...],
              "tools": [...], "editions": [...], "days": [...], "reports": [...], "watch": [...],
              "calendar": [...], "gtasks": [...], "lifeos": [...]}  (each doc = its id plus its fields)

Rule for memos / dues / tasks / prompts / notes: per id, the record with the larger updatedAt wins;
a missing updatedAt counts as 0; nothing is removed (deleted:true is a record too).
lifeos flows Mac -> online only: lifeos/*/tool.json in this repo is the source (Life OS view).
followups flows Mac -> online too, except the answers left on its docs, which are applied here first.
Everything else flows online -> Mac only.
"""
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, ROOT)
from server import LOCK, followup_core, lifeos_tools, load, save  # noqa: E402

TWO_WAY = ("memos", "dues", "tasks", "prompts", "notes", "habits")


def ts(x):
    try:
        return float(x.get("updatedAt") or 0)
    except (TypeError, ValueError):
        return 0.0


def merge(local, remote):
    """Returns (merged list for the Mac, docs the online desk needs)."""
    loc = {x["id"]: x for x in local if x.get("id")}
    rem = {x["id"]: x for x in remote if isinstance(x, dict) and x.get("id")}
    out, push = [], []
    for i in list(loc) + [i for i in rem if i not in loc]:
        a, b = loc.get(i), rem.get(i)
        if a and (not b or ts(a) > ts(b)):
            out.append(a)
            if a != b:
                push.append(a)
        else:
            out.append(b)
    return out, push


def lifeos_push(remote):
    """Docs the online desk needs so its lifeos collection matches this repo (one way)."""
    local = {d["id"]: d for d in lifeos_tools()}
    online = {d["id"]: d for d in remote if isinstance(d, dict) and d.get("id")}
    push = [d for i, d in local.items() if online.get(i) != d]
    if local:  # an old checkout without lifeos/ must not hide what is online
        push += [{"id": i, "deleted": True} for i, d in online.items() if i not in local and not d.get("deleted")]
    return push


def followups_push(remote):
    """Life OS 02: answers from the phone go in, the current list goes out. A failure must not stop the sync."""
    core = followup_core()
    if core is None:
        return []
    try:
        return core.sync_online(remote)
    except Exception as e:
        print("followups: " + type(e).__name__, file=sys.stderr)
        return []


def write_js(name, var, value):
    text = json.dumps(value, ensure_ascii=False).replace("</", "<\\/")
    tmp = os.path.join(DATA, "." + name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(f"window.{var}={text};\n")
    os.replace(tmp, os.path.join(DATA, name))


def by_date_desc(docs, key="date"):
    return sorted(docs, key=lambda d: str(d.get(key) or d.get("id") or ""), reverse=True)


def one(docs, doc_id):
    for d in docs:
        if d.get("id") == doc_id:
            return {k: v for k, v in d.items() if k != "id"}
    return None


def cmd_merge(remote_path):
    with open(remote_path, encoding="utf-8") as f:
        remote = json.load(f)
    os.makedirs(DATA, exist_ok=True)
    write = {}
    with LOCK:
        for col in TWO_WAY:
            if col not in remote:
                continue  # never treat a collection that was not read as empty
            merged, push = merge(load(col), remote[col])
            save(col, merged)
            if push:
                write[col] = push
    if "lifeos" in remote:
        push = lifeos_push(remote["lifeos"])
        if push:
            write["lifeos"] = push
    if "followups" in remote:
        push = followups_push(remote["followups"])
        if push:
            write["followups"] = push
    strip = lambda d: {k: v for k, v in d.items() if k != "id"}
    if "tools" in remote:
        write_js("tools.js", "TOOLS", [d for d in remote["tools"] if not d.get("deleted")])
    if "editions" in remote:
        write_js("world.js", "WORLD", [{"id": d["id"], "data": strip(d)} for d in by_date_desc(remote["editions"])[:60]])
    if "days" in remote:
        write_js("mail.js", "MAIL", [{"id": d["id"], "data": strip(d)} for d in by_date_desc(remote["days"])[:30]])
    if "reports" in remote:
        write_js("reports.js", "REPORTS", by_date_desc(remote["reports"])[:30])
    if "watch" in remote:
        write_js("watch.js", "WATCH", [d for d in by_date_desc(remote["watch"])[:30] if not d.get("deleted")])
    if "calendar" in remote:
        write_js("calendar.js", "CAL", one(remote["calendar"], "upcoming"))
    if "gtasks" in remote:
        write_js("gtasks.js", "GTASKS", one(remote["gtasks"], "current"))
    print(json.dumps({"write": write}, ensure_ascii=False, indent=1))


def index_hash():
    with open(os.path.join(ROOT, "index.html"), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def published_path():
    return os.path.join(DATA, ".published")


def cmd_code_status():
    h = index_hash()
    try:
        with open(published_path()) as f:
            last = f.read().strip()
    except FileNotFoundError:
        last = ""
    print(json.dumps({"changed": h != last, "hash": h}))


def cmd_mark_published():
    os.makedirs(DATA, exist_ok=True)
    with open(published_path(), "w") as f:
        f.write(index_hash())
    print("ok")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["merge"] and len(args) == 2:
        cmd_merge(args[1])
    elif args == ["code-status"]:
        cmd_code_status()
    elif args == ["mark-published"]:
        cmd_mark_published()
    else:
        print(__doc__)
        sys.exit(2)
