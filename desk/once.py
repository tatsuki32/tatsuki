#!/usr/bin/env python3
"""Tatsuki's Desk — one-time changes to the Mac's data, run once by server.py at start (4747 only).

Each step runs at most once: desk/data/once.json keeps the names of the steps already done.
Used when Tatsuki asks for a change to data that lives only on the Mac (cloud sessions cannot reach it).

  python3 desk/once.py plan    what the waiting steps would change (changes nothing)
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import replywin  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DESK_DATA_DIR") or os.path.join(ROOT, "data")
DAY = 86400 * 1000


def _read(name, default):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return default


def _write(name, value):
    os.makedirs(DATA, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DATA, prefix=".once", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(DATA, name))


def old_nudges(tasks, now_ms, days=14):
    """Open follow-up (催促) tasks made more than `days` days ago. Tatsuki's choice on 2026-11-24: drop them all."""
    out = []
    for t in tasks:
        if t.get("deleted") or t.get("status") not in ("notyet", "doing"):
            continue
        if replywin.reply_kind(t) != "nudge":
            continue
        made = t.get("createdAt") or t.get("updatedAt") or now_ms
        if isinstance(made, (int, float)) and now_ms - made > days * DAY:
            out.append(t)
    return out


def drop_old_nudges(tasks, now_ms):
    ids = {t["id"] for t in old_nudges(tasks, now_ms)}
    for t in tasks:
        if t["id"] in ids:
            t.update(status="dropped", droppedAt=now_ms, updatedAt=now_ms,
                     skip={**(t.get("skip") or {}), "why": "Follow-up older than 14 days. Dropped in one go.",
                           "by": "me", "at": now_ms})
    return len(ids)


def add_helpfeel_task(tasks, now_ms):
    """Tatsuki asked on 2026-11-24 for a task for today: answer Helpfeel (pick a new meeting time himself)."""
    tid = "t-once-helpfeel-20261124"
    if any(t.get("id") == tid for t in tasks):
        return 0
    tasks.append({"id": tid, "title": "Reply to Takahashi (Helpfeel): book a new meeting time",
                  "status": "notyet", "source": "manual", "sourceRef": "", "due": None,
                  "note": "Use the scheduling link in his Nov 16 email (Gmail: Helpfeel 髙橋).",
                  "planDate": time.strftime("%Y-%m-%d"), "createdAt": now_ms, "updatedAt": now_ms,
                  "deleted": False})
    return 1


STEPS = [("2026-11-24-drop-old-nudges", "tasks", drop_old_nudges),
         ("2026-11-24-helpfeel-task", "tasks", add_helpfeel_task)]


def run(load, save, lock, plan=False):
    """load(col) / save(col, items) / lock are server.py's. Returns {step: count}."""
    done = _read("once.json", {})
    out = {}
    now = int(time.time() * 1000)
    for name, col, fn in STEPS:
        if done.get(name):
            continue
        with lock:
            items = load(col)
            if plan:
                out[name] = len(old_nudges(items, now)) if fn is drop_old_nudges else 1
                continue
            n = fn(items, now)
            if n:
                save(col, items)
        done[name] = {"at": now, "changed": n}
        out[name] = n
    if not plan and out:
        _write("once.json", done)
    return out


if __name__ == "__main__" and sys.argv[1:] == ["plan"]:
    import threading
    def _load(col):
        return _read(col + ".json", [])
    print(json.dumps(run(_load, None, threading.Lock(), plan=True)))
