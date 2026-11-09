#!/usr/bin/env python3
"""Turns one-to-one WhatsApp chats waiting for a reply into Desk tasks. Only reads the Mac app's store; never sends.

  python3 desk/whatsapp_sync.py scan            # closes answered tasks, adds new ones, prints the chats that need a draft
  python3 desk/whatsapp_sync.py nudges          # open 催促 tasks (Life OS 02): the chats that need a draft
  python3 desk/whatsapp_sync.py save FILE.json  # [{"ref": "<task id>", "topic": "...", "summary": "...", "body": "...", "body_ja": "...", "urgent": false, "urgentWhy": ""}]
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
KINDS = {0: "text", 1: "photo", 2: "video", 3: "voice message", 4: "contact", 5: "location", 7: "text", 8: "file", 15: "sticker"}
RECENT = 12  # messages given to Claude for one draft
NUDGE_LINES = 6  # of the talk so far, shown in a 催促 task
BODY_MAX = 4000
WAIT_WINDOW = 30 * 86400  # how far back an unanswered chat of yours still counts
CLOSERS = re.compile(r"(ありがと(う|うございます|うございました)?|了解(です|しました)?|りょうかい|ok|okay|おっけー?|thanks?( you)?|thx|👍|おやすみ(なさい)?|またね)[!！。.ー〜~\s]*", re.IGNORECASE)
KANA = re.compile(r"[ぁ-んァ-ヶ]")


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
    if not msgs or msgs[0]["me"] or msgs[0]["kind"] in ("sticker", "スタンプ"):
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


def closes_chat(m):
    """Your last word ends the talk, so nobody owes you an answer: a sticker, a thank-you, or too short to ask anything."""
    text = m["text"].strip()
    return m["kind"] != "text" or len(text) < 4 or bool(CLOSERS.fullmatch(text))


def waiting_threads(now=None):
    """One-to-one chats as Life OS 02 threads. lastFrom "them" lets it close one they answered."""
    now = now or int(time.time())
    db = connect()
    try:
        out = []
        for session, jid, name in db.execute(
                "SELECT DISTINCT s.Z_PK, s.ZCONTACTJID, s.ZPARTNERNAME FROM ZWAMESSAGE m JOIN ZWACHATSESSION s ON m.ZCHATSESSION=s.Z_PK"
                " WHERE s.ZSESSIONTYPE=0 AND COALESCE(s.ZREMOVED,0)=0 AND s.ZCONTACTJID NOT LIKE '0@%' AND m.ZMESSAGEDATE>?",
                (now - WAIT_WINDOW - APPLE_EPOCH,)).fetchall():
            msgs = recent(db, session, RECENT)
            if not msgs or (msgs[0]["me"] and closes_chat(msgs[0])):
                continue
            who = clean_name(name) or "(no name)"
            out.append({"threadId": "wa" + hashlib.sha1(jid.encode()).hexdigest()[:16], "source": "whatsapp", "subject": who, "to": [who],
                        "lastFrom": "me" if msgs[0]["me"] else "them", "lang": "ja" if KANA.search(msgs[0]["text"]) else "",
                        "lastAt": datetime.fromtimestamp(msgs[0]["at"], paths.HCMC).isoformat(timespec="seconds")})
        return out
    finally:
        db.close()


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


def title_of(it):
    """"WhatsApp reply: <topic> (<name>)". The topic comes once Claude has read the chat."""
    kind = "WhatsApp nudge" if it.get("nudge") else "WhatsApp reply"
    topic = it.get("topic")
    if topic and re.search(r"[ぁ-んァ-ン一-龥]", topic):  # old chats were read in Japanese: keep their Japanese title
        ja = "WhatsApp催促" if it.get("nudge") else "WhatsApp返信"
        return tasks._clean_title(f"{ja} {topic}について（{it['name']}）")
    return tasks._clean_title(f"{kind}: {topic} ({it['name']})" if topic else f"{kind} ({it['name']})")


def today():
    return datetime.fromtimestamp(time.time(), paths.HCMC).strftime("%Y-%m-%d")


def line(m):
    return m["text"] if m["kind"] == "text" else f"({m['kind']})"


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
            if it.get("nudge"):
                continue  # Life OS 02 closes these when they answer; your own last word is the point
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
            name = clean_name(c["name"]) or "(no name)"
            if not t:
                ms = paths.now_ms()
                tasks._put({"id": tid, "title": title_of({"name": name}), "status": "notyet", "source": "whatsapp",
                            "sourceRef": "wa:" + tid[3:], "due": None, "note": "", "planDate": today(),
                            "createdAt": turn[-1]["at"] * 1000, "deleted": False, "updatedAt": ms})
                created += 1
            last = turn[-1]
            it = items.get(tid, {})
            items[tid] = {**it, "jid": c["jid"], "phone": phone_of(c["jid"]), "name": name, "unknown": is_unknown(name),
                          "lastId": last["id"], "lastAt": last["at"], "kind": last["kind"], "texts": [line(m) for m in turn]}
            if last["kind"] == "text" and it.get("draftFor") != last["id"]:
                drafts.append({"ref": tid, "name": name, "unknown": is_unknown(name),
                               "messages": [{"from": "Me" if m["me"] else name, "text": line(m), "at": m["at"]} for m in msgs[:RECENT][::-1]]})
        save_items(items)
        return {"closed": closed, "created": created, "open": len(items), "drafts": drafts}
    finally:
        db.close()


def nudges():
    """Open 催促 tasks on chats get the same store as replies: the talk so far, and a draft to copy."""
    db = connect()
    try:
        chats = {}
        for session, jid, name in db.execute("SELECT Z_PK, ZCONTACTJID, ZPARTNERNAME FROM ZWACHATSESSION WHERE ZSESSIONTYPE=0 AND COALESCE(ZREMOVED,0)=0"):
            if jid:
                chats["wa" + hashlib.sha1(jid.encode()).hexdigest()[:16]] = (session, jid, clean_name(name) or "(no name)")
        items, drafts = load_items(), []
        for t in tasks._load():
            chat = chats.get(t.get("sourceRef"))
            if t.get("source") != "lifeos-02" or t.get("deleted") or t.get("status") not in tasks.OPEN or not chat:
                continue
            session, jid, name = chat
            msgs = recent(db, session, RECENT)
            if not msgs:
                continue
            it = items.get(t["id"], {})
            talk = msgs[::-1]
            items[t["id"]] = {**it, "nudge": True, "jid": jid, "phone": phone_of(jid), "name": name, "unknown": is_unknown(name), "lastId": msgs[0]["id"],
                              "lastAt": msgs[0]["at"], "kind": "text", "texts": [("Me: " if m["me"] else name + ": ") + line(m) for m in talk if m["kind"] != "other"][-NUDGE_LINES:]}
            if it.get("draftFor") != msgs[0]["id"]:
                drafts.append({"ref": t["id"], "name": name, "unknown": is_unknown(name),
                               "messages": [{"from": "Me" if m["me"] else name, "text": line(m), "at": m["at"]} for m in talk if m["kind"] != "other"]})
        save_items(items)
        return {"open": sum(1 for i in items.values() if i.get("nudge")), "drafts": drafts}
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
        # Desk rings for a reply only when the intake marked it (desk/notify.py); a new draft clears an old mark
        it["urgent"] = row.get("urgent") is True
        it["urgentWhy"] = str(row.get("urgentWhy") or "").strip()[:80] if it["urgent"] else ""
        topic = str(row.get("topic") or "").strip()[:40]
        t = tasks.get(row["ref"]) if topic else None
        if t:
            it["topic"] = topic
            tasks.update(row["ref"], title=title_of(it))
        count += 1
    save_items(items)
    return count


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["scan"]:
        print(json.dumps(scan(), ensure_ascii=False, indent=1))
    elif args == ["nudges"]:
        print(json.dumps(nudges(), ensure_ascii=False, indent=1))
    elif args[:1] == ["save"] and len(args) == 2:
        with open(args[1], encoding="utf-8") as f:
            print(json.dumps({"saved": save(json.load(f))}))
    else:
        print(__doc__)
        sys.exit(2)
