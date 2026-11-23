"""Tatsuki's Desk — an alarm 3 minutes before each Google Calendar event, on this Mac.

At 3 minutes before a timed event: a Mac notification with sound, an alarm sound played several times, the voice
saying the event title, and a dialog that stays on screen until it is closed (or the event starts). Any hour of the day,
not held by notification pauses or focus sessions: an event is something to be at.

Events come from tg_notify.upcoming(): Google Calendar's secret iCal address in the keychain (service
tatsuki-desk-gcal-ics), or the Desk calendar import when that is not set. All-day events and ☑️ events are skipped.
Each event start rings once (desk/data/notify/alarm.json). Text goes to osascript and say as arguments, never into a script.

  python3 desk/event_alarm.py plan    what would ring in the next hour
  python3 desk/event_alarm.py test    ring once now
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime

import tg_notify

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DESK_DATA") or os.path.join(ROOT, "data")
STATE = os.path.join(os.environ.get("NOTIFY_DIR") or os.path.join(DATA, "notify"), "alarm.json")
BEFORE = 3 * 60
TICK = 20
RINGS = 4
SOUND = "/System/Library/Sounds/Glass.aiff"
KEEP = 14 * 86400
LOCK = threading.Lock()

ALERT = ("on run argv",
         "display alert (item 1 of argv) message (item 2 of argv) as critical buttons {\"OK\"} default button \"OK\" giving up after (item 3 of argv as integer)",
         "end run")
NOTE = ("on run argv",
        "display notification (item 2 of argv) with title \"Desk\" subtitle (item 1 of argv) sound name \"Glass\"",
        "end run")


def read_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(STATE))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, STATE)


def due(now, events, sent):
    """Events starting within the next 3 minutes that have not rung. Pure."""
    out = []
    for e in events:
        key = f"{e.get('id')}:{int(e['start'])}"
        if key not in sent and 0 < e["start"] - now <= BEFORE:
            out.append({**e, "key": key})
    return out


def _osa(lines, args, wait=False):
    cmd = ["/usr/bin/osascript"]
    for line in lines:
        cmd += ["-e", line]
    cmd += ["--"] + [str(a) for a in args]  # "--": an event title that starts with "-e" must not be run as a script
    try:
        if wait:
            return subprocess.run(cmd, capture_output=True, timeout=15).returncode == 0
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def ring(e, now=None):
    """Notification, then the dialog, then sound and voice (in this thread; call it from a thread of its own)."""
    now = now or time.time()
    left = max(1, round((e["start"] - now) / 60))
    when = datetime.fromtimestamp(e["start"]).strftime("%-I:%M %p")
    title = str(e.get("title") or "Event")[:120]
    head = f"In {left} min: {title}"
    _osa(NOTE, [head, f"Starts at {when}"], wait=True)
    _osa(ALERT, [head, f"Starts at {when}.", max(30, int(e["start"] - now))])
    for _ in range(RINGS):
        try:
            subprocess.run(["/usr/bin/afplay", SOUND], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            break
    try:
        subprocess.run(["/usr/bin/say", f"{title}, in {left} minutes"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def tick(now=None, ringer=None):
    now = now or time.time()
    try:
        time.tzset()  # the Mac may have moved to another time zone
    except AttributeError:
        pass
    events = tg_notify.upcoming(now)
    with LOCK:
        st = read_state()
        sent = {k: v for k, v in (st.get("sent") or {}).items() if now - v < KEEP}
        hits = due(now, events, sent)
        for e in hits:
            sent[e["key"]] = int(now)
        if hits or sent != st.get("sent"):
            write_state({"sent": sent})
    for e in hits:
        threading.Thread(target=ringer or ring, args=(e,), daemon=True).start()
    return hits


def watch():
    while True:
        try:
            tick()
        except Exception as ex:  # one bad calendar read must not end the watch
            print("event_alarm:", type(ex).__name__, ex, file=sys.stderr, flush=True)
        time.sleep(TICK)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "plan":
        t = time.time()
        evs = tg_notify.upcoming(t)
        src = "Google Calendar (direct)" if tg_notify.keychain(tg_notify.ICS_SERVICE) else "Desk calendar import"
        print(json.dumps({"source": src, "error": tg_notify._ics.get("error"),
                          "next hour": [{"title": e["title"], "rings at": datetime.fromtimestamp(e["start"] - BEFORE).strftime("%H:%M")}
                                        for e in sorted(evs, key=lambda e: e["start"]) if e["start"] > t]},
                         ensure_ascii=False, indent=1))
    elif cmd == "test":
        ring({"id": "test", "title": "Test event", "start": time.time() + BEFORE})
    else:
        print(__doc__)
