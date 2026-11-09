#!/usr/bin/env python3
"""Puts Desk tasks on Google Calendar, one way: the Desk decides, the calendar follows.

  python3 desk/task_calendar.py plan                         # prints what to create, update and delete
  python3 desk/task_calendar.py done create <taskId> <eventId>
  python3 desk/task_calendar.py done update|delete|missing <taskId>

A not-started task with a due date and time gets one event, 3 days before at the same time.
The hourly scheduled task carries out the plan through the Calendar connector and reports each
step with "done", so a run that stops halfway never makes the same event twice.
Only the title goes on the calendar, not the note. An event deleted by hand stays deleted ("missing").
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from lifeos.lib import paths, tasks  # noqa: E402

MARK = "☑️"  # desk/write_feed.py leaves events starting with this out of the Desk's own calendar
HCM = timezone(timedelta(hours=7))
TZ_NAME = "Asia/Ho_Chi_Minh"
DAYS_BEFORE = 3
MINUTES = 30
ACTIVE = ("notyet", "doing")  # in progress: nothing new is added, but its event stays until it is done


def _state_file():
    return os.path.join(paths.desk_data_dir(), "calendar-sync.json")


def load_state():
    try:
        with open(_state_file(), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_state(state):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(_state_file()), prefix=".calendar-sync.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _state_file())


def has_time(due):
    """A due date without a time is stored as 23:59 in Ho Chi Minh City."""
    if not isinstance(due, (int, float)):
        return False
    at = datetime.fromtimestamp(due / 1000, HCM)
    return (at.hour, at.minute) != (23, 59)


def slot(task):
    start = datetime.fromtimestamp(task["due"] / 1000, HCM) - timedelta(days=DAYS_BEFORE)
    fmt = "%Y-%m-%dT%H:%M:%S"
    return start, start.strftime(fmt), (start + timedelta(minutes=MINUTES)).strftime(fmt)


def event(task):
    _, start, end = slot(task)
    return {"summary": MARK + " " + task["title"], "startTime": start, "endTime": end, "timeZone": TZ_NAME}


def plan(all_tasks, state, now_ms):
    now = datetime.fromtimestamp(now_ms / 1000, HCM)
    by_id = {t["id"]: t for t in all_tasks if not t.get("deleted")}
    out = {"create": [], "update": [], "delete": [], "forget": []}

    def remove(task_id, entry):
        if entry.get("eventId"):
            out["delete"].append({"taskId": task_id, "eventId": entry["eventId"]})
        else:
            out["forget"].append(task_id)

    for task_id, entry in state.items():
        t = by_id.get(task_id)
        if not t or t.get("status") not in ACTIVE or not has_time(t.get("due")):
            remove(task_id, entry)
            continue
        if not entry.get("eventId"):
            continue
        start_at, start, _ = slot(t)
        moved = start != entry.get("start")
        if moved and start_at <= now:
            remove(task_id, entry)
        elif moved or t["title"] != entry.get("title"):
            out["update"].append({"taskId": task_id, "eventId": entry["eventId"], **event(t)})

    for task_id, t in by_id.items():
        if task_id not in state and t.get("status") == "notyet" and has_time(t.get("due")) and slot(t)[0] > now:
            out["create"].append({"taskId": task_id, "description": "Desk task " + task_id, **event(t)})
    return out


def record(state, action, task_id, task=None, event_id=None):
    """The calendar has changed: remember it."""
    if action == "delete":
        state.pop(task_id, None)
        return
    if action == "missing":
        state[task_id] = {**state.get(task_id, {}), "eventId": None}
        return
    if task is None:
        raise KeyError(task_id)
    event_id = event_id if action == "create" else state[task_id]["eventId"]
    state[task_id] = {"eventId": event_id, "start": slot(task)[1], "title": task["title"]}


def main(argv):
    if argv[:1] == ["plan"]:
        state = load_state()
        result = plan(tasks.all_tasks(), state, paths.now_ms())
        for task_id in result.pop("forget"):
            state.pop(task_id, None)
        save_state(state)
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    if len(argv) >= 3 and argv[0] == "done" and argv[1] in ("create", "update", "delete", "missing") and (argv[1] != "create" or len(argv) == 4):
        state = load_state()
        record(state, argv[1], argv[2], tasks.get(argv[2]), argv[3] if len(argv) == 4 else None)
        save_state(state)
        print("ok")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
