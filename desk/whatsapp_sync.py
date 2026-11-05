#!/usr/bin/env python3
"""Turns one-to-one WhatsApp chats waiting for a reply into Desk tasks. Only reads the Mac app's store; never sends.

  python3 desk/whatsapp_sync.py scan            # closes answered tasks, adds new ones, prints the chats that need a draft
  python3 desk/whatsapp_sync.py save FILE.json  # [{"ref": "<task id>", "summary": "...", "body": "...", "body_ja": "..."}]
                                                # or {"ref": "<task id>", "noreply": true}: nothing to answer, the task is put away

A task is one turn of a chat: the messages since your last one. Replying closes it; their next message makes a new one.
Message text and drafts stay in desk/data/whatsapp/ (out of the private backup too); the task holds the name only.
The store is not a public format: message type numbers were read from this Mac's data, so `scan` stops if the tables change.
"""
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from lifeos.lib import paths, tasks  # noqa: E402

STORE = "~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite"
APPLE_EPOCH = 978307200  # the store counts seconds from 2001-01-01
WINDOW = 48 * 3600  # older than this is not a new message, also when a run was missed
SYSTEM = (6, 10)  # group and security notices
KINDS = {0: "text", 1: "画像", 2: "動画", 3: "音声", 4: "連絡先", 5: "位置情報", 7: "text", 8: "ファイル", 15: "スタンプ"}
RECENT = 12  # messages given to Claude for one draft
BODY_MAX = 4000


def store_path():
    return os.path.expanduser(os.environ.get("WHATSAPP_STORE") or STORE)


def _items_file():
    return os.path.join(paths.desk_data_dir(), "whatsapp", "items.json")


def load_items():
    try:
        with open(_items_file(), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_items(items):
    path = _items_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".items.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def connect():
    return sqlite3.connect("file:" + store_path().replace("?", "%3F") + "?mode=ro", uri=True, timeout=5)


def chats_with_news(db, since):
    """One-to-one chats where they wrote after `since`. A group or a channel has another session type."""
    return [dict(zip(("session", "jid", "name"), r)) for r in db.execute(
        "SELECT DISTINCT s.Z_PK, s.ZCONTACTJID, s.ZPARTNERNAME FROM ZWAMESSAGE m JOIN ZWACHATSESSION s ON m.ZCHATSESSION=s.Z_PK"
        " WHERE s.ZSESSIONTYPE=0 AND COALESCE(s.ZREMOVED,0)=0 AND s.ZCONTACTJID NOT LIKE '0@%' AND m.ZISFROMME=0 AND m.ZMESSAGEDATE>?",
        (since - APPLE_EPOCH,))]


def session_of(db, jid):
    r = db.execute("SELECT Z_PK FROM ZWACHATSESSION WHERE ZCONTACTJID=? AND ZSESSIONTYPE=0", (jid,)).fetchone()
    return r[0] if r else None


def recent(db, session, limit=40):
    """Newest first. Their message types we do not know (calls, reactions) do not count; anything of yours does."""
    out = []
    for me, kind, text, at, sid in db.execute(
            "SELECT ZISFROMME, ZMESSAGETYPE, ZTEXT, ZMESSAGEDATE, ZSTANZAID FROM ZWAMESSAGE WHERE ZCHATSESSION=?"
            " AND ZMESSAGETYPE NOT IN (6,10) ORDER BY ZMESSAGEDATE DESC LIMIT ?", (session, limit)):
        if me or kind in KINDS:
            out.append({"me": bool(me), "kind": KINDS.get(kind, "other"), "text": text or "", "at": int(at + APPLE_EPOCH), "id": sid or ""})
    return out


def waiting_turn(msgs):
    """Their messages since your last one, oldest first; [] when you spoke last or their last word is a sticker."""
    if not msgs or msgs[0]["me"] or msgs[0]["kind"] == "スタンプ":
        return []
    turn = []
    for m in msgs:
        if m["me"]:
            break
        turn.append(m)
    return turn[::-1]


def answered(msgs, since):
    """You wrote after their message, also when they have written again since."""
    return any(m["me"] and m["at"] >= since for m in msgs)


def task_id(jid, first_id):
    return "twa" + hashlib.sha1(f"{jid}|{first_id}".encode()).hexdigest()[:16]


def clean_name(name):
    return re.sub(r"[‎‏‪-‮]", "", name or "").strip()


def is_unknown(name):
    """WhatsApp shows the number itself for someone not in your contacts."""
    return bool(re.fullmatch(r"\+?[\d\s()-]+", name))


def phone_of(jid):
    m = re.fullmatch(r"(\d+)@s\.whatsapp\.net", jid or "")
    return m.group(1) if m else ""


def today():
    return datetime.fromtimestamp(time.time(), paths.HCMC).strftime("%Y-%m-%d")


def line(m):
    return m["text"] if m["kind"] == "text" else f"（{m['kind']}）"


def scan(now=None):
    now = now or int(time.time())
    db = connect()
    try:
        items, saved = load_items(), {t["id"]: t for t in tasks._load()}
        closed = created = 0
        # 1. you answered: the task is done
        for tid, it in list(items.items()):
            t = saved.get(tid)
            if not t or t.get("deleted") or t.get("status") not in tasks.OPEN:
                del items[tid]  # finished by hand: the text has no further use
                continue
            session = session_of(db, it["jid"])
            if session and answered(recent(db, session), it["lastAt"]):
                rec = {**t, "status": "done", "updatedAt": paths.now_ms()}
                tasks._put(rec)
                saved[tid] = rec
                del items[tid]
                closed += 1
        # 2. they wrote: one task per turn
        drafts = []
        for c in chats_with_news(db, now - WINDOW):
            msgs = recent(db, c["session"])
            turn = waiting_turn(msgs)
            if not turn or turn[-1]["at"] <= now - WINDOW:
                continue
            tid = task_id(c["jid"], turn[0]["id"])
            t = saved.get(tid)
            if t and (t.get("deleted") or t.get("status") not in tasks.OPEN):
                continue  # you closed this turn yourself
            name = clean_name(c["name"]) or "（名前なし）"
            if not t:
                ms = paths.now_ms()
                tasks._put({"id": tid, "title": tasks._clean_title("連絡返信：" + name), "status": "notyet", "source": "whatsapp",
                            "sourceRef": "wa:" + tid[3:], "due": None, "note": "", "planDate": today(),
                            "createdAt": turn[-1]["at"] * 1000, "deleted": False, "updatedAt": ms})
                created += 1
            last = turn[-1]
            it = items.get(tid, {})
            items[tid] = {**it, "jid": c["jid"], "phone": phone_of(c["jid"]), "name": name, "unknown": is_unknown(name),
                          "lastId": last["id"], "lastAt": last["at"], "kind": last["kind"], "texts": [line(m) for m in turn]}
            if last["kind"] == "text" and it.get("draftFor") != last["id"]:
                drafts.append({"ref": tid, "name": name, "unknown": is_unknown(name),
                               "messages": [{"from": "自分" if m["me"] else name, "text": line(m), "at": m["at"]} for m in msgs[:RECENT][::-1]]})
        save_items(items)
        return {"closed": closed, "created": created, "open": len(items), "drafts": drafts}
    finally:
        db.close()


def save(rows):
    items = load_items()
    count = 0
    for row in rows:
        it = items.get(row.get("ref"))
        if it and row.get("noreply") is True:
            t = tasks.get(row["ref"])
            if t:
                tasks._put({**t, "status": "archived", "updatedAt": paths.now_ms()})
            del items[row["ref"]]
            count += 1
            continue
        body = str(row.get("body") or "").strip()[:BODY_MAX]
        if not it or not body:
            continue
        it.update(summary=str(row.get("summary") or "").strip()[:400], body=body,
                  body_ja=str(row.get("body_ja") or "").strip()[:BODY_MAX], draftFor=it.get("lastId"))
        count += 1
    save_items(items)
    return count


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["scan"]:
        print(json.dumps(scan(), ensure_ascii=False, indent=1))
    elif args[:1] == ["save"] and len(args) == 2:
        with open(args[1], encoding="utf-8") as f:
            print(json.dumps({"saved": save(json.load(f))}))
    else:
        print(__doc__)
        sys.exit(2)
