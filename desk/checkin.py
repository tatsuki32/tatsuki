#!/usr/bin/env python3
"""Tatsuki's Desk — ask on this Mac how a task in progress is going, every 10 minutes.

A timer that is running on a task with status "doing" asks 10 minutes after it started, and again 10 minutes after
each answer. The question is a dialog on this Mac with three buttons:
  Done                  the task is marked done and the timer stops
  Back to not started   the task goes back to not started and the timer stops (the time stays logged)
  Still going           nothing changes; the next question comes 10 minutes later
No answer within 60 seconds counts as "Still going". Paused timers are not asked about.

Only the Desk's own files are read and written (desk/data/tasks.json, timelog.json, wins.json, checkin/state.json).
The task's title goes into the dialog as an argument, never into the script.

  python3 desk/checkin.py plan    which timers would be asked about right now (asks nothing, writes nothing)
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DESK_DATA") or os.path.join(ROOT, "data")
DIR = os.path.join(DATA, "checkin")
HCMC = timezone(timedelta(hours=7))  # the day the Desk's wins are keyed by
GAP_MS = 10 * 60 * 1000
GIVE_UP_SECONDS = 60
TICK_SECONDS = 30
BUTTONS = ("Back to not started", "Still going", "Done")
SCRIPT = (
    "on run argv",
    "set r to display dialog (item 1 of argv) with title (item 2 of argv) buttons {\"Back to not started\", \"Still going\", \"Done\"} default button \"Still going\" giveUpAfter " + str(GIVE_UP_SECONDS),
    "if gave up of r then return \"gave up\"",
    "return button returned of r",
    "end run",
)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read_state():
    st = read_json(os.path.join(DIR, "state.json"), {})
    return st if isinstance(st, dict) else {}


def due(now_ms, timelog, tasks, asked):
    """Timers to ask about now: running, not paused, task still in progress, and 10 minutes since the last question
    (or since the start). Returns (timer, task) pairs."""
    by_id = {t.get("id"): t for t in tasks if isinstance(t, dict) and t.get("id")}
    out = []
    for r in timelog:
        if not isinstance(r, dict) or not r.get("id") or r.get("id") == "_calendar":
            continue
        if r.get("end") or r.get("pausedAt") or r.get("deleted"):
            continue
        start = r.get("start")
        if not isinstance(start, (int, float)) or isinstance(start, bool):
            continue
        t = by_id.get(r.get("taskId"))
        if not t or t.get("deleted") or t.get("status") != "doing":
            continue
        last = asked.get(r["id"], start)
        if isinstance(last, (int, float)) and now_ms - last >= GAP_MS:
            out.append((r, t))
    return out


def ask_mac(title, minutes):
    """The dialog. Returns one of BUTTONS, "gave up", or "" if osascript could not run."""
    body = f"How is it going?\n{title}\nRunning for {minutes} min."
    cmd = ["/usr/bin/osascript"]
    for line in SCRIPT:
        cmd += ["-e", line]
    cmd += [body, "Desk"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=GIVE_UP_SECONDS + 30)
    except (OSError, subprocess.SubprocessError):
        return ""
    answer = (out.stdout or "").strip()
    return answer if answer in BUTTONS else ("gave up" if answer == "gave up" else "")


def day_hcmc(now_ms):
    return datetime.fromtimestamp(now_ms / 1000, HCMC).strftime("%Y-%m-%d")


def apply(load, save, lock, timer_id, task_id, answer, now_ms):
    """Write the answer. Reads the files again under the server's lock, so a change made in the page is not lost."""
    with lock:
        tl = load("timelog")
        tasks = load("tasks")
        if answer in ("Done", "Back to not started"):
            for r in tl:
                if r.get("id") == timer_id and not r.get("end"):
                    r["end"] = now_ms
                    r["updatedAt"] = now_ms
        if answer in ("Done", "Back to not started"):
            status = "done" if answer == "Done" else "notyet"
            for t in tasks:
                if t.get("id") == task_id and t.get("status") == "doing":
                    t["status"] = status
                    t["updatedAt"] = now_ms
                    if status == "done":
                        t["doneAt"] = now_ms
            wins = load("wins")
            if answer == "Done":
                title = next((t.get("title") for t in tasks if t.get("id") == task_id), "") or "Task"
                wid = "task-" + str(task_id)
                if not any(w.get("id") == wid for w in wins):
                    wins.append({"id": wid, "kind": "task", "title": title, "date": day_hcmc(now_ms), "at": now_ms})
                    save("wins", wins)
            save("timelog", tl)
            save("tasks", tasks)
        st = read_state()
        st[timer_id] = now_ms
        write_json(os.path.join(DIR, "state.json"), st)


def tick(load, save, lock, now_ms=None, ask=ask_mac):
    """One pass: ask about every due timer, one dialog at a time."""
    now_ms = now_ms or int(time.time() * 1000)
    with lock:
        timelog, tasks, asked = load("timelog"), load("tasks"), read_state()
    todo = due(now_ms, timelog, tasks, asked)
    for r, t in todo:
        # Mark the question before the dialog opens, so a slow answer cannot bring a second one.
        st = read_state()
        st[r["id"]] = now_ms
        write_json(os.path.join(DIR, "state.json"), st)
        minutes = max(0, (now_ms - int(r["start"])) // 60000)
        title = t.get("title") or r.get("title") or "Task"
        answer = ask(title, minutes)
        if answer in BUTTONS:
            apply(load, save, lock, r["id"], t["id"], answer, int(time.time() * 1000))
    return todo


def watch(load, save, lock):
    """The server's background thread. One bad file must not stop the watch."""
    while True:
        try:
            tick(load, save, lock)
        except Exception as e:
            print("checkin:", type(e).__name__, e, file=sys.stderr, flush=True)
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "plan":
        now = int(time.time() * 1000)
        tl = read_json(os.path.join(DATA, "timelog.json"), [])
        tasks = read_json(os.path.join(DATA, "tasks.json"), [])
        rows = [{"timer": r["id"], "task": t.get("title"), "minutes": (now - int(r["start"])) // 60000}
                for r, t in due(now, tl, tasks, read_state())]
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print(__doc__)
