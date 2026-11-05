#!/usr/bin/env python3
"""Claude's guesses for Desk tasks: how long each one takes, and when it could happen.

  python3 desk/task_estimate.py pending    # prints the open tasks that need a guess
  python3 desk/task_estimate.py apply      # reads [{"id": "...", "minutes": 15, "plan": "week"}] from stdin

The daily scheduled task (desk-task-estimate) reads the list and answers with apply.
A guess only fills estAi and planAi. The person's own choices (estMin, planDate) are never touched,
and planAi moves a task only after the person presses it on the page.
Periods match tPeriods in desk/index.html: the plan is kept as the last day of the period.
"""
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from lifeos.lib import tasks  # noqa: E402

HCM = timezone(timedelta(hours=7))
OPEN = ("notyet", "doing")
MAX_MINUTES = 60 * 24  # a day's worth of work is the largest guess that means anything
NOTE_MAX = 200


def month_end(y, m, ahead=0):
    m0 = m - 1 + ahead + 1  # the first of the following month, minus a day
    return date(y + m0 // 12, m0 % 12 + 1, 1) - timedelta(days=1)


def plan_date(key, today):
    """The last day of a period, or None for a key that is not one."""
    if key == "today":
        return today.isoformat()
    if key == "week":
        return (today + timedelta(days=6 - today.weekday())).isoformat()
    if key in ("month", "next", "next2"):
        return month_end(today.year, today.month, ("month", "next", "next2").index(key)).isoformat()
    if key == "later":
        return "9999-12-31"
    if isinstance(key, str) and len(key) == 5 and key[0] == "y" and key[1:].isdigit() and int(key[1:]) >= today.year:
        return f"{key[1:]}-12-31"
    return None


def needs_time(t):
    return t.get("estMin") is None and t.get("estAi") is None


def needs_plan(t, today):
    if t.get("planDate") or t.get("due") is not None:
        return False
    return not t.get("planAi") or t["planAi"] < today.isoformat()  # an old guess has run out: ask again


def pending(items, today):
    out = []
    for t in items:
        if t.get("deleted") or t.get("status") not in OPEN:
            continue
        time_, plan = needs_time(t), needs_plan(t, today)
        if time_ or plan:
            out.append({"id": t["id"], "title": t.get("title", ""), "note": (t.get("note") or "")[:NOTE_MAX],
                        "needs": [n for n, on in (("minutes", time_), ("plan", plan)) if on]})
    return out


def apply_rows(items, rows, today):
    """The records to save. Anything unknown, finished or out of range is skipped."""
    by_id = {t["id"]: t for t in items if not t.get("deleted") and t.get("status") in OPEN}
    out = []
    for row in rows:
        t = by_id.get(row.get("id")) if isinstance(row, dict) else None
        if not t:
            continue
        change = {}
        minutes = row.get("minutes")
        if needs_time(t) and isinstance(minutes, int) and not isinstance(minutes, bool) and 0 < minutes <= MAX_MINUTES:
            change["estAi"] = minutes
        plan = plan_date(row.get("plan"), today)
        if plan and needs_plan(t, today):
            change["planAi"] = plan
        if change:
            out.append({**t, **change})  # updatedAt stays: a guess is not something the person did
    return out


def main(argv):
    today = datetime.now(HCM).date()
    if argv[:1] == ["pending"]:
        print(json.dumps(pending(tasks._load(), today), ensure_ascii=False, indent=1))
    elif argv[:1] == ["apply"]:
        rows = json.load(sys.stdin)
        if not isinstance(rows, list):
            sys.exit("list expected")
        saved = apply_rows(tasks._load(), rows, today)
        for rec in saved:
            tasks._put(rec)
        print(f"見積もり {len(saved)}件")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
