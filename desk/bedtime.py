#!/usr/bin/env python3
"""Tatsuki's Desk — Bedtime: a gentle word to stop for the day, once the set hour has passed.

server.py runs watch() every 60 seconds, on 4747 only. From bedtime (21:30 by default) until 4:30, while the Mac is
in use (a key or the mouse in the last 5 minutes, or a video playing; screen not locked), it shows one Mac notification with sound, then
one without sound every 15 minutes for as long as the Mac stays in use. A Mac opened later in the night gets its
first one within a minute or two.

- The hour is this Mac's own clock, so it follows the Mac to another country with no setting.
- It only speaks. Nothing is locked, closed or stopped.
- No AI and nothing sent anywhere: it works offline. The words never blame.
- Settings and the small state are in desk/data/notify/ (bedtime.json, bedtime-state.json).

  python3 desk/bedtime.py plan    what it would show right now, as JSON (shows nothing, writes nothing)
  python3 desk/bedtime.py test    one notification now, to see and hear it
"""
import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DESK_DATA") or os.environ.get("DESK_DATA_DIR") or os.path.join(ROOT, "data")
DIR = os.environ.get("NOTIFY_DIR") or os.path.join(DATA, "notify")
UNTIL = 4 * 60 + 30  # the night ends at 4:30, when Tatsuki gets up (2026-11-24)
IN_USE = 5 * 60  # a key or the mouse this recently counts as using the Mac
WATCHING = 60 * 60  # with a video keeping the display awake, this long without touching anything still counts
SLACK = 45  # the watch wakes about once a minute: a repeat is due this many seconds early rather than a minute late
EVERY = (5, 10, 15, 20, 30)
DEFAULTS = {"on": True, "at": 21 * 60 + 30, "every": 15}  # minutes after midnight, minutes between repeats
FIRST = "It's {clock}. A good time to close the Mac and get ready for bed."
LATE = "It's {clock}, {past} past bedtime. A good time to close the Mac."
REPEATS = (
    "{past} past bedtime. What's open will still be here in the morning.",
    "{past} past bedtime. Save your work and call it a day?",
    "{past} past bedtime. You can pick this up where you left off tomorrow.",
    "{past} past bedtime. One last save, then lights out?",
)
LOCK = threading.Lock()


# ---------- is someone at the Mac ----------

def idle_seconds():
    """Seconds since the last key or mouse movement, or None when macOS will not say."""
    try:
        out = subprocess.run(["/usr/sbin/ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
    return int(m.group(1)) / 1e9 if m else None


def screen_locked():
    """True on the lock screen. False when unlocked or when macOS will not say."""
    try:
        out = subprocess.run(["/usr/sbin/ioreg", "-n", "Root", "-d", "1", "-a"], capture_output=True, timeout=10).stdout
        users = plistlib.loads(out).get("IOConsoleUsers") or []
    except Exception:  # no answer, or not a plist
        return False
    return any(u.get("CGSSessionScreenIsLocked") for u in users if isinstance(u, dict))


def display_kept_awake():
    """True while an app holds the display awake: a video is playing, or a call is on."""
    try:
        out = subprocess.run(["/usr/bin/pmset", "-g", "assertions"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    m = re.search(r"PreventUserIdleDisplaySleep\s+(\d+)", out)
    return bool(m and int(m.group(1)))


def in_use(idle, locked, watching=False, limit=None):
    """Someone is at the Mac: not locked, and a key or the mouse within `limit` seconds, or within an hour while a video
    keeps the display awake (watching is using, hands off the keyboard). Unknown idle time counts as in use: the watch
    only runs while the Mac is awake."""
    if locked:
        return False
    if idle is None:
        return True
    return idle < (limit or IN_USE) or (watching and idle < WATCHING)


# ---------- files ----------

def read_json(name, default):
    try:
        with open(os.path.join(DIR, name), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return default


def write_json(name, value):
    os.makedirs(DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DIR, prefix=".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(DIR, name))


def clean_settings(raw, base=None):
    """Only known keys, only sane values. Anything else keeps what was there."""
    out = dict(base or DEFAULTS)
    raw = raw if isinstance(raw, dict) else {}
    if isinstance(raw.get("on"), bool):
        out["on"] = raw["on"]
    at = raw.get("at")
    if isinstance(at, int) and not isinstance(at, bool) and 0 <= at < 24 * 60 and at != UNTIL:
        out["at"] = at
    if raw.get("every") in EVERY and not isinstance(raw.get("every"), bool):
        out["every"] = raw["every"]
    return out


def settings():
    raw = read_json("bedtime.json", {})
    if raw.get("at") == 22 * 60 and not raw.get("v"):  # the old default: bed at 21:30 from 2026-11-24
        raw = {**raw, "at": DEFAULTS["at"], "v": 2}
        try:
            write_json("bedtime.json", raw)
        except OSError:
            pass
    return clean_settings(raw)


# ---------- deciding ----------

def night(dt, at):
    """(the date this night began on, minutes past bedtime), or None outside bedtime-4:30."""
    minute = dt.hour * 60 + dt.minute
    if at > UNTIL:  # the usual case: an evening bedtime, the night runs past midnight
        if minute >= at:
            return dt.strftime("%Y-%m-%d"), minute - at
        if minute < UNTIL:
            return (dt - timedelta(days=1)).strftime("%Y-%m-%d"), minute + 24 * 60 - at
        return None
    return (dt.strftime("%Y-%m-%d"), minute - at) if at <= minute < UNTIL else None  # a bedtime after midnight


def past_text(minutes):
    h, m = divmod(minutes, 60)
    return (f"{h}h {m}m" if m else f"{h}h") if h else f"{m} min"


def plan(now, s, st, using, tz=None):
    """What to show at `now`: (a notification or None, the state after). Pure."""
    dt = datetime.fromtimestamp(now, tz) if tz else datetime.fromtimestamp(now)
    n = night(dt, s["at"]) if s["on"] else None
    if not n or not using:
        return None, st
    key, past = n
    count = st.get("count", 0) if st.get("night") == key else 0
    if count and now - st.get("last", 0) < s["every"] * 60 - SLACK:
        return None, st
    clock = dt.strftime("%-I:%M %p")
    if not count:
        body = (FIRST if past < s["every"] else LATE).format(clock=clock, past=past_text(past))
    else:
        body = REPEATS[(count - 1) % len(REPEATS)].format(past=past_text(past))
    # "now" rings (notify.SOUNDS); any other tier is shown silently
    note = {"id": f"bedtime:{key}:{count}", "kind": "bedtime", "tier": "now" if not count else "quiet", "title": "Bedtime", "body": body}
    return note, {"night": key, "count": count + 1, "last": int(now)}


# ---------- showing ----------

def show_mac(n, sound=True):
    import notify  # the one way the Desk shows a Mac notification: the text goes in as arguments, never into a script
    return notify.show_mac(n, sound)


def tick(now=None, show=show_mac, using=None):
    now = now or time.time()
    try:
        time.tzset()  # the Mac may have moved to another time zone since the server started
    except AttributeError:
        pass
    with LOCK:
        s = settings()
        if not s["on"] or not night(datetime.fromtimestamp(now), s["at"]):
            return None  # daytime: do not even ask macOS about the keyboard
        if using is None:
            using = in_use(idle_seconds(), screen_locked(), display_kept_awake())
        st = read_json("bedtime-state.json", {})
        note, after = plan(now, s, st, using)
        if after != st:
            write_json("bedtime-state.json", after)
    if note:
        show(note, True)
    return note


def watch():
    while True:
        try:
            tick()
        except Exception as e:  # one bad file must not end the watch
            print("bedtime:", type(e).__name__, e, file=sys.stderr, flush=True)
        time.sleep(60)


# ---------- for the page ----------

def view(now=None):
    now = now or time.time()
    s, st = settings(), read_json("bedtime-state.json", {})
    n = night(datetime.fromtimestamp(now), s["at"])
    told = st.get("count", 0) if n and st.get("night") == n[0] else 0
    return {"settings": s, "every": list(EVERY), "night": bool(n), "told": told, "last": st.get("last", 0) if told else 0}


def put_settings(rec):
    with LOCK:
        s = clean_settings(rec, settings())
        write_json("bedtime.json", {**s, "v": 2})
    return 200, view()


def test():
    ok = show_mac({"title": "Bedtime", "body": FIRST.format(clock=datetime.now().strftime("%-I:%M %p")), "tier": "now"}, True)
    return (200, {"ok": True}) if ok else (503, {"error": "could not show"})


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "plan":
        t = time.time()
        idle, locked, watching = idle_seconds(), screen_locked(), display_kept_awake()
        note, _ = plan(t, settings(), read_json("bedtime-state.json", {}), in_use(idle, locked, watching))
        print(json.dumps({"show": note, "idle": idle, "locked": locked, "watching": watching, "settings": settings()}, ensure_ascii=False, indent=1))
    elif cmd == "test":
        print(test())
    else:
        print(__doc__)
