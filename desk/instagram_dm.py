#!/usr/bin/env python3
"""Tatsuki's Desk — Instagram DMs in Replies.

Every 5 minutes (Desk's own server only) this asks go.tany.im for the conversations of the person's own Instagram
accounts (the Worker reads them with Meta's token; Desk never sees it). A conversation whose last message is from the
other person becomes one reply task (source "instagram"), with the messages, a short summary and a reply draft in
their language. Replies shows them at 5:00 and 16:00 like email, WhatsApp and LINE. Send goes out only when the person
presses it, and only within Instagram's 24 hours from their last message. When the last message is the owner's
(sent from Desk or from Instagram), the task is done.

The messages and drafts stay in desk/data/instagram/items.json, out of the GitHub copy.

  python3 instagram_dm.py sync      read once now
"""
import hashlib
import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(ROOT))
sys.path.insert(0, ROOT)

import golinks  # noqa: E402
import line_sync  # noqa: E402  (its ask() is the Claude call with the cost cap)
import lifelog  # noqa: E402
from lifeos.lib import paths, tasks  # noqa: E402

DATA = os.environ.get("DESK_IG_DIR") or os.path.join(ROOT, "data", "instagram")
ITEMS = os.path.join(DATA, "items.json")
STATE = os.path.join(DATA, "state.json")
# the two accounts whose DMs come to Replies, and the language Tatsuki uses there
ACCOUNTS = {"17841412692655599": {"handle": "tatsuki3284", "lang": "en"},
            "17841470988646240": {"handle": "tatsu_languages", "lang": "ru"}}
WINDOW = 24 * 3600 * 1000  # Instagram lets a business reply for 24 hours after the other person's last message
FIRST_LOOKBACK = 3 * 86400 * 1000  # on the very first read, only chats from the last 3 days become tasks
_lock = threading.Lock()

SYSTEM = (
    "You help the owner of an Instagram account answer direct messages. You get one conversation. "
    "Reply as the owner: warm, short, natural, in the same language the other person wrote in. Never invent facts, plans, "
    "prices or promises; if something needs the owner's decision, ask a short question back instead. "
    "summary: one plain English line on what they want. topic: 2 to 5 English words for a task title. "
    "needs_reply: false only for a message that clearly needs no answer (a thank-you emoji, spam, an automated message). "
    "body: the reply to send. body_en: an English translation of body when body is not English, else empty."
)
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["summary", "topic", "needs_reply", "body", "body_en"],
          "properties": {"summary": {"type": "string"}, "topic": {"type": "string"}, "needs_reply": {"type": "boolean"},
                         "body": {"type": "string"}, "body_en": {"type": "string"}}}


def _read(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def view():
    """What the page needs: the items of open tasks (messages, summary, draft, whether Send is still allowed)."""
    items = _read(ITEMS, {})
    now = time.time() * 1000
    return {"items": {k: {**v, "canSend": now - (v.get("theirAt") or 0) < WINDOW} for k, v in items.items()}}


def _draft(conv, lang):
    lines = []
    for m in conv["messages"][-10:]:
        who = "Owner" if m["mine"] else "Them"
        lines.append(f"{who}: {m['text'] or '(a photo, video or sticker)'}")
    text = f"The owner usually writes in {lang} on this account.\nConversation (oldest first):\n" + "\n".join(lines)
    return line_sync.ask([], text, SYSTEM, SCHEMA)


def sync():
    """One read of every account. Returns how many tasks were made or updated."""
    with _lock:
        state = _read(STATE, {})
        items = _read(ITEMS, {})
        saved = {t["id"]: t for t in tasks._load()}
        now = int(time.time() * 1000)
        changed = 0
        for ig, acc in ACCOUNTS.items():
            try:
                v = golinks._call("GET", f"/api/dm/{ig}", timeout=60)
            except RuntimeError as e:
                state.setdefault("errors", {})[ig] = str(e)[:200]
                continue
            state.setdefault("errors", {}).pop(ig, None)
            first = ig not in state.get("seen", {})
            seen = state.setdefault("seen", {}).setdefault(ig, {})
            for conv in v.get("conversations", []):
                msgs = [m for m in conv.get("messages", []) if m.get("at")]
                if not msgs:
                    continue
                last = msgs[-1]
                tid = "tig" + hashlib.sha1(f"{ig}|{conv['id']}".encode()).hexdigest()[:16]
                t = saved.get(tid)
                if last["mine"]:
                    # answered (from Desk or Instagram): close the open task and forget its text
                    if t and t.get("status") in tasks.OPEN:
                        tasks._put({**t, "status": "done", "doneAt": now, "updatedAt": paths.now_ms()})
                        changed += 1
                    items.pop(tid, None)
                    seen[conv["id"]] = last["id"]
                    continue
                if seen.get(conv["id"]) == last["id"] and (tid in items or not t or t.get("status") not in tasks.OPEN):
                    continue  # nothing new since the last read
                if first and now - last["at"] > FIRST_LOOKBACK:
                    seen[conv["id"]] = last["id"]
                    continue
                try:
                    d = _draft(conv, acc["lang"])
                except (lifelog.Stop, lifelog.Refused) as e:
                    state["errors"][ig] = f"Draft: {e}"[:200]
                    continue  # tried again next time
                seen[conv["id"]] = last["id"]
                name = (conv.get("with") or {}).get("username") or "someone"
                topic = " ".join(str(d.get("topic") or "").split()[:6])
                title = tasks._clean_title(f"Instagram reply: @{name} — {topic}" if topic else f"Instagram reply: @{name}")
                if not t or t.get("status") not in tasks.OPEN:
                    t = {"id": tid, "title": title, "status": "notyet", "source": "instagram", "sourceRef": f"ig:{ig}:{conv['id']}",
                         "due": None, "note": "", "planDate": time.strftime("%Y-%m-%d"), "createdAt": now, "deleted": False}
                t = {**t, "title": title, "updatedAt": paths.now_ms()}
                tasks._put(t)
                items[tid] = {"account": ig, "handle": acc["handle"], "conv": conv["id"], "to": conv["with"].get("id", ""), "name": name,
                              "messages": [{"mine": m["mine"], "text": m["text"] or "(a photo, video or sticker)", "at": m["at"]} for m in msgs[-10:]],
                              "theirAt": last["at"], "summary": str(d.get("summary") or "")[:400], "needsReply": bool(d.get("needs_reply")),
                              "body": str(d.get("body") or "")[:1000], "body_en": str(d.get("body_en") or "")[:1000]}
                changed += 1
        # a task you finished by hand takes its text with it
        for tid in list(items):
            t = saved.get(tid) or tasks.get(tid)
            if not t or t.get("deleted") or t.get("status") not in tasks.OPEN:
                items.pop(tid, None)
        state["at"] = now
        _write(ITEMS, items)
        _write(STATE, state)
        return changed


def send(task_id, text):
    """The person pressed Send. Goes out through go.tany.im; the task is done when it arrives."""
    items = _read(ITEMS, {})
    i = items.get(task_id)
    text = str(text or "").strip()
    if not i:
        return 404, {"error": "not found"}
    if not text:
        return 400, {"error": "Write the reply first"}
    if time.time() * 1000 - (i.get("theirAt") or 0) >= WINDOW:
        return 400, {"error": "More than 24 hours have passed since their last message. Reply in Instagram"}
    try:
        r = golinks._call("POST", f"/api/dm/{i['account']}/send", {"to": i["to"], "text": text}, timeout=60)
    except RuntimeError as e:
        return 502, {"error": str(e)}
    t = tasks.get(task_id)
    if t:
        note = (t.get("note") or "").rstrip()
        tasks._put({**t, "status": "done", "doneAt": int(time.time() * 1000), "updatedAt": paths.now_ms(),
                    "note": (note + "\n\n" if note else "") + "Sent on Instagram: " + text[:500]})
    with _lock:
        items = _read(ITEMS, {})
        items.pop(task_id, None)
        _write(ITEMS, items)
    return 200, {"ok": True, "id": r.get("id", "")}


def watch():
    while True:
        try:
            sync()
        except Exception:
            pass
        time.sleep(300)


if __name__ == "__main__":
    print(sync() if (sys.argv[1:] or ["sync"])[0] == "sync" else json.dumps(view(), ensure_ascii=False)[:2000])
