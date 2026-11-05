#!/usr/bin/env python3
"""Makes vague Desk tasks easier to start: a clear title, the first five minutes, steps, sources, wording to ask a friend.

  python3 desk/task_clarify.py pending [--limit 30] [--id TASK_ID]   # prints the tasks to work on, most urgent first
  python3 desk/task_clarify.py apply                                 # reads a JSON list from stdin

The nightly scheduled task (desk-task-clarify) and /clarify write here. A result only fills "clear":
the person's title, note, due date, period, time and status are never touched, and their answers stay.
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from lifeos.lib import tasks  # noqa: E402

HCM = timezone(timedelta(hours=7))
OPEN = ("notyet", "doing")
SKIP_SOURCES = ("mail", "daily", "lifeos-19")  # these already say what to do, and carry their own flow (lifeos-19: a new one every day)
DEFAULT_LIMIT = 30
TITLE_MAX, FIRST_MAX, LINE_MAX, ASK_MAX = 80, 120, 200, 500
MAX_STEPS, MAX_SOURCES, MAX_OPEN_QUESTIONS = 10, 5, 2
ASK_KINDS = ("teach", "together", "help")


def when(t):
    if t.get("planDate"):
        return t["planDate"]
    if t.get("due") is not None:
        return datetime.fromtimestamp(t["due"] / 1000, HCM).date().isoformat()
    return None


def rank(t, today):
    """Answers first (the person is waiting), then today, this week, and the oldest of the rest."""
    w = when(t)
    week_end = (today + timedelta(days=6 - today.weekday())).isoformat()
    urgency = 0 if (t.get("clear") or {}).get("redo") else 1 if w and w <= today.isoformat() else 2 if w and w <= week_end else 3
    return (urgency, t.get("createdAt") or 0)


def eligible(t):
    return not t.get("deleted") and t.get("status") in OPEN and t.get("source") not in SKIP_SOURCES


def pending(items, today, limit=DEFAULT_LIMIT, only=None):
    if only:
        picked = [t for t in items if t.get("id") == only and eligible(t)]  # the button: done again on request
    else:
        picked = sorted((t for t in items if eligible(t) and (not t.get("clear") or t["clear"].get("redo"))), key=lambda t: rank(t, today))[:limit]
    return [{"id": t["id"], "title": t.get("title", ""), "note": t.get("note") or "", "when": when(t),
             "questions": (t.get("clear") or {}).get("questions") or []} for t in picked]


def text(value, limit):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def merged_questions(old, new):
    out = list(old)
    asked = {q.get("q") for q in old}
    still_open = sum(q.get("state") == "open" for q in old)
    for raw in new if isinstance(new, list) else []:
        q = text(raw, LINE_MAX)
        if q and q not in asked and still_open < MAX_OPEN_QUESTIONS:  # an answered or unknown question is never asked twice
            out.append({"id": f"q{len(out) + 1}", "q": q, "a": None, "state": "open"})
            asked.add(q)
            still_open += 1
    return out


def clean(row, old, now_ms):
    """The checked "clear" record, or None when there is nothing to show."""
    title, first = text(row.get("title"), TITLE_MAX), text(row.get("first"), FIRST_MAX)
    questions = merged_questions(old.get("questions") or [], row.get("questions"))
    if not title or not (first or any(q["state"] == "open" for q in questions)):
        return None
    steps = [text(s, LINE_MAX) for s in row.get("steps") or []] if isinstance(row.get("steps"), list) else []
    sources = []
    for s in row.get("sources") if isinstance(row.get("sources"), list) else []:
        url = s.get("url") if isinstance(s, dict) else None
        if isinstance(url, str) and url.startswith("https://") and " " not in url:
            sources.append({"label": text(s.get("label"), LINE_MAX) or url, "url": url})
    raw_asks = row.get("asks") if isinstance(row.get("asks"), dict) else {}
    asks = {k: raw_asks[k].strip()[:ASK_MAX] for k in ASK_KINDS if isinstance(raw_asks.get(k), str) and raw_asks[k].strip()}
    out = {"title": title, "first": first, "steps": [s for s in steps if s][:MAX_STEPS], "done": text(row.get("done"), LINE_MAX),
           "sources": sources[:MAX_SOURCES], "asks": asks, "questions": questions, "at": now_ms, "redo": False}
    if old.get("titleOff"):
        out["titleOff"] = True
    return out


def apply_rows(items, rows, now_ms):
    by_id = {t["id"]: t for t in items if eligible(t)}
    out = []
    for row in rows:
        t = by_id.get(row.get("id")) if isinstance(row, dict) else None
        rec = clean(row, t.get("clear") or {}, now_ms) if t else None
        if rec:
            out.append({**t, "clear": rec})  # updatedAt stays: this is not something the person did
    return out


def main(argv):
    today = datetime.now(HCM).date()
    if argv[:1] == ["pending"]:
        limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else DEFAULT_LIMIT
        only = argv[argv.index("--id") + 1] if "--id" in argv else None
        print(json.dumps(pending(tasks._load(), today, limit, only), ensure_ascii=False, indent=1))
    elif argv[:1] == ["apply"]:
        rows = json.load(sys.stdin)
        if not isinstance(rows, list):
            sys.exit("list expected")
        saved = apply_rows(tasks._load(), rows, int(time.time() * 1000))
        for rec in saved:
            tasks._put(rec)
        print(f"整えた {len(saved)}件" + (f" / 飛ばした {len(rows) - len(saved)}件" if len(rows) != len(saved) else ""))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
