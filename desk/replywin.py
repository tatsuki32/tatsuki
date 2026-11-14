#!/usr/bin/env python3
"""The reply window: which tasks are replies, and whether the Replies screen is open right now.

Replies (email, WhatsApp, LINE, and the nudges that chase an answer) never sit on the task board.
They show in "💬 Replies", which opens at 5:00 and 16:00 on the Mac's own clock, and stays open until
"Replies done" is pressed. While it is closed, no title, sender, count-by-name or notification leaks out.

  python3 desk/replywin.py          the window now, as JSON

The state is desk/data/replywin.json, doc `state`: {"closedAt": <ms>}. Python standard library only.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
SLOTS = (5, 16)  # hours on the Mac's clock
REPLY_SOURCES = {"mail", "whatsapp", "line", "lifeos-02"}  # lifeos-02: nudges that chase an answer
REPLY_TITLE = re.compile(r"^\s*(?:Email|WhatsApp|LINE)\s+(?:reply|nudge)\b|^\s*(?:メール|WhatsApp|LINE)\s*(?:返信|催促)", re.I)


def reply_kind(t):
    """'mail' | 'whatsapp' | 'line' | 'nudge' for a reply-type task, else None. Ignores asTask."""
    if not isinstance(t, dict):
        return None
    src, title = t.get("source") or "", str(t.get("title") or "")
    if src == "lifeos-02" or re.match(r"^\s*(?:Email nudge|WhatsApp nudge|メール催促|WhatsApp催促)", title, re.I):
        return "nudge"
    if src in ("mail", "whatsapp", "line"):
        return src
    if REPLY_TITLE.match(title):
        return "mail" if re.match(r"^\s*(?:Email|メール)", title, re.I) else "whatsapp" if re.match(r"^\s*WhatsApp", title, re.I) else "line"
    return None


def is_reply(t):
    """Lives in Replies (not on the board) unless you moved it there yourself with "Make it a task"."""
    return bool(reply_kind(t)) and not t.get("asTask")


def last_slot(now=None):
    """The latest opening time (epoch seconds) at or before `now`, on the Mac's local clock."""
    dt = datetime.fromtimestamp(now if now is not None else time.time())
    for day in (0, 1):
        base = (dt - timedelta(days=day)).replace(minute=0, second=0, microsecond=0)
        for h in sorted(SLOTS, reverse=True):
            s = base.replace(hour=h)
            if s <= dt:
                return s.timestamp()
    return (dt - timedelta(days=1)).replace(hour=SLOTS[-1], minute=0, second=0, microsecond=0).timestamp()


def next_slot(now=None):
    dt = datetime.fromtimestamp(now if now is not None else time.time())
    for day in (0, 1):
        base = (dt + timedelta(days=day)).replace(minute=0, second=0, microsecond=0)
        for h in SLOTS:
            s = base.replace(hour=h)
            if s > dt:
                return s.timestamp()
    return dt.timestamp() + 86400


def read_state(data_dir=DATA):
    try:
        with open(os.path.join(data_dir, "replywin.json"), encoding="utf-8") as f:
            docs = json.load(f)
    except (OSError, ValueError):
        return {}
    for d in docs if isinstance(docs, list) else []:
        if isinstance(d, dict) and d.get("id") == "state":
            return d
    return {}


def is_open(now=None, state=None, data_dir=DATA):
    """Open unless "Replies done" was pressed after the latest 5:00 / 16:00."""
    now = now if now is not None else time.time()
    st = state if state is not None else read_state(data_dir)
    closed = st.get("closedAt")
    if not isinstance(closed, (int, float)) or isinstance(closed, bool):
        return True
    return closed / 1000 < last_slot(now)


def summary(now=None, data_dir=DATA):
    now = now if now is not None else time.time()
    return {"open": is_open(now, data_dir=data_dir), "lastSlot": int(last_slot(now) * 1000), "nextSlot": int(next_slot(now) * 1000)}


if __name__ == "__main__":
    print(json.dumps(summary(), indent=1))
    sys.exit(0)
