#!/usr/bin/env python3
"""Tatsuki's Desk — write data from the scheduled tasks, and import an old online export.

The Mac's desk/data/ is the only source. The scheduled tasks (sync/SCHEDULED_TASKS.md) write here:

  python3 sync.py put COLLECTION DOC_ID FILE.json  -> saves one doc (feeds: docs/<collection>/<id>.json + the page's *.js;
                                                      memos / tasks / ...: through the server when it runs)
  python3 sync.py get COLLECTION DOC_ID            -> prints one doc, or {} when there is none
  python3 sync.py build                            -> rewrites the page's *.js from the docs
  python3 sync.py mail DAY KEY                     -> prints the draft of one mail for /mail-send
  python3 sync.py mail-add DAY FILE.json           -> adds a run's mail to the day's doc; what an earlier run found stays
  python3 sync.py mail-replied DAY KEY             -> you answered in Gmail: closes the item and its task
  python3 sync.py merge REMOTE.json                -> imports an export: {"tasks": [{"id": ..., ...fields}], "days": [...], ...}

Rule for memos / dues / tasks / prompts / notes / habits / lifemap / wins / gear / projects / sections: per id, the record with the larger updatedAt wins;
a missing updatedAt counts as 0; nothing is removed (deleted:true is a record too).
"""
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(ROOT))
from server import FEEDS, LOCK, doc_path, feed_docs, followup_core, lifeos_tools, load, put_doc, rebuild_feed, save, upsert  # noqa: E402

TWO_WAY = ("memos", "dues", "tasks", "prompts", "notes", "habits", "lifemap", "wins", "presents", "gear", "projects", "sections")


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
    for col in FEEDS:
        for d in remote.get(col, []):
            if isinstance(d, dict) and d.get("id"):
                put_doc(col, d["id"], d)
        if col in remote:
            rebuild_feed(col)
    print(json.dumps({"write": write}, ensure_ascii=False, indent=1))


def mail_ref(item):
    return item.get("gmail_url") or item.get("key")


def merge_mail(old, new):
    """Today's doc after one more run. An item keeps its status and draft; the newest text wins."""
    before = {mail_ref(i): i for i in old.get("items") or []}
    items = []
    for i in new.get("items") or []:
        p = before.pop(mail_ref(i), None)
        if p:
            if (p.get("status") or "pending") != "pending":
                i["status"] = p["status"]
            if p.get("draft") and not i.get("draft"):
                i["draft"] = p["draft"]
        items.append(i)
    return {**new, "items": list(before.values()) + items}


def cmd_mail_add(day, path):
    with open(path, encoding="utf-8") as f:
        new = json.load(f)
    old = next((d for d in feed_docs("days") if d.get("id") == day), {})
    put_doc("days", day, merge_mail(old.get("data") or old, new))
    rebuild_feed("days")
    print("ok")


def cmd_mail_replied(day, key):
    from kickoff import due_doc_id
    from lifeos.lib import paths, tasks
    d = next((x for x in feed_docs("days") if x.get("id") == day), None)
    doc = (d.get("data") or d) if d else {}
    item = next((i for i in doc.get("items") or [] if i.get("key") == key), None)
    if not item:
        sys.exit("not found")
    item["status"] = "sent"
    put_doc("days", day, {k: v for k, v in doc.items() if k != "id"})
    rebuild_feed("days")
    uid = "mail:" + (item.get("gmail_url") or f"{day}/{key}")
    tid = "t" + due_doc_id(uid)
    cur = next((t for t in tasks._load() if t["id"] == tid), None)
    if not cur or cur.get("status") in tasks.OPEN:
        ms = paths.now_ms()
        base = cur or {"id": tid, "title": item.get("subject_ja") or item.get("subject") or "（件名なし）", "source": "mail",
                       "sourceRef": uid, "due": None, "note": "", "createdAt": ms, "deleted": False}
        tasks._put({**base, "status": "done", "updatedAt": ms})
    print("ok")


def cmd_build():
    for col in FEEDS:
        rebuild_feed(col)
    print("ok")


def put_two_way(col, doc_id, rec):
    """Through the running server, so its lock covers the write; straight to the file when it is stopped."""
    port = os.environ.get("DESK_PORT", "4747")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/{col}/{urllib.request.quote(doc_id, safe='')}",
        data=json.dumps(rec, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    # no proxy: while websites are blocked the system proxy points at FocusBlock
    try:
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5).read()
    except (urllib.error.URLError, OSError):
        upsert(col, doc_id, {"updatedAt": 0, **rec}, merge=False)


def cmd_put(col, doc_id, path):
    with open(path, encoding="utf-8") as f:
        rec = json.load(f)
    if not isinstance(rec, dict):
        sys.exit("object expected")
    rec.pop("id", None)
    if col in FEEDS:
        put_doc(col, doc_id, rec)
        rebuild_feed(col)
    elif col in TWO_WAY:
        put_two_way(col, doc_id, rec)
    else:
        sys.exit("unknown collection: " + col)
    print("ok")


def cmd_get(col, doc_id):
    if col in FEEDS:
        docs = [d for d in feed_docs(col) if d["id"] == doc_id]
    else:
        docs = [d for d in load(col) if d["id"] == doc_id]
    print(json.dumps(docs[0] if docs else {}, ensure_ascii=False, indent=1))


def cmd_mail(day, key):
    """One mail's draft, for /mail-send. It only reads."""
    try:
        with open(doc_path("days", day), encoding="utf-8") as f:
            items = json.load(f).get("items", [])
    except (OSError, ValueError):
        items = []
    item = next((i for i in items if i.get("key") == key), None)
    if not item or not item.get("draft"):
        sys.exit("draft not found")
    print(json.dumps({k: item.get(k) for k in ("from", "subject", "subject_ja", "action", "draft", "gmail_url")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["merge"] and len(args) == 2:
        cmd_merge(args[1])
    elif args[:1] == ["put"] and len(args) == 4:
        cmd_put(args[1], args[2], args[3])
    elif args[:1] == ["get"] and len(args) == 3:
        cmd_get(args[1], args[2])
    elif args[:1] == ["mail"] and len(args) == 3:
        cmd_mail(args[1], args[2])
    elif args[:1] == ["mail-add"] and len(args) == 3:
        cmd_mail_add(args[1], args[2])
    elif args[:1] == ["mail-replied"] and len(args) == 3:
        cmd_mail_replied(args[1], args[2])
    elif args == ["build"]:
        cmd_build()
    else:
        print(__doc__)
        sys.exit(2)
