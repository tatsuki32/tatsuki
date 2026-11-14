#!/usr/bin/env python3
"""Time you measured, as events on the "Desk Log" calendar. The nightly desk-time-calendar task calls this.

  python3 desk/time_calendar.py pending      # finished records of 5 min or more from the last 7 days, not on the calendar yet (JSON)
  python3 desk/time_calendar.py mark         # stdin [{"id": "<timelog id>", "eventId": "<calendar event id>"}]
  python3 desk/time_calendar.py status ok|missing   # whether the "Desk Log" calendar was found (the Desk shows one line when missing)

Times are wall times on the Mac's own clock (the country he is in). Only the title, the type, the minutes, the estimate and
where it was measured go to the calendar: no notes, no message text. Marking an id twice keeps the first event, so a second
run of the same night adds nothing. Python standard library only.
"""
import json
import os
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "desk"))
from lifeos.lib import tasks  # noqa: E402
from task_skip import _load_col, _put_col  # noqa: E402

MIN_MS = 5 * 60000
LOOKBACK_MS = 7 * 86400000
CALENDAR = "Desk Log"
STATUS_ID = "_calendar"


def minutes(r, now_ms=None):
    end = r.get("end") or now_ms or int(time.time() * 1000)
    paused = (r.get("pausedMs") or 0) + ((end - r["pausedAt"]) if r.get("pausedAt") and not r.get("end") else 0)
    return max(0, end - r["start"] - paused) / 60000


def wall(ms):
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%dT%H:%M:%S")


def is_record(r):
    return isinstance(r, dict) and r.get("id") != STATUS_ID and not r.get("deleted") and isinstance(r.get("start"), (int, float))


def pending(records, task_list, now_ms):
    by_id = {t["id"]: t for t in task_list}
    out = []
    for r in records:
        if not is_record(r) or not r.get("end") or r.get("calEventId") or now_ms - r["end"] > LOOKBACK_MS:
            continue
        if minutes(r) * 60000 < MIN_MS:
            continue
        t = by_id.get(r.get("taskId")) or {}
        est = t.get("estMin") or t.get("estAi")
        title = ((t.get("clear") or {}).get("title") if (t.get("clear") or {}).get("title") and not (t.get("clear") or {}).get("titleOff") else None) or t.get("title") or r.get("title") or "Task"
        mins = round(minutes(r))
        desc = [f"Type: {t.get('cat') or r.get('cat') or '—'}", f"Measured: {mins} min",
                f"Estimate: {est} min" if est else "Estimate: —", f"Measured in: {'Chrome' if r.get('via') == 'chrome' else 'Desk'}"]
        out.append({"id": r["id"], "summary": "⏱ " + " ".join(str(title).split())[:120], "start": wall(r["start"]), "end": wall(r["end"]),
                    "description": "\n".join(desc)})
    return sorted(out, key=lambda x: x["start"])


def mark(records, rows):
    by_id = {r["id"]: r for r in records if is_record(r)}
    out = []
    for row in rows if isinstance(rows, list) else []:
        r = by_id.get(row.get("id")) if isinstance(row, dict) else None
        ev = row.get("eventId") if isinstance(row, dict) else None
        if r and isinstance(ev, str) and ev and not r.get("calEventId"):
            out.append({**r, "calEventId": ev[:200]})
    return out


def main(argv):
    now_ms = int(time.time() * 1000)
    if argv[:1] == ["pending"]:
        print(json.dumps(pending(_load_col("timelog"), tasks._load(), now_ms), ensure_ascii=False, indent=1))
    elif argv[:1] == ["mark"]:
        recs = mark(_load_col("timelog"), json.load(sys.stdin))
        for r in recs:
            _put_col("timelog", r)
        print(f"Marked {len(recs)}")
    elif argv[:1] == ["status"] and argv[1:2] in (["ok"], ["missing"]):
        _put_col("timelog", {"id": STATUS_ID, "kind": "status", "calendar": argv[1], "at": now_ms})
        print(argv[1])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
