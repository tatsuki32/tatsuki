#!/usr/bin/env python3
"""Tatsuki's Desk — Mac notifications with sound, kept few.

Three tiers. "now" rings on the spot: a reply marked urgent, 30 minutes before an event, 30 minutes before a
task's timed due. "heads" shows without sound: 6 hours before an event. "digest" is one notification three
times a day for everything else. Nothing shows or rings outside the ring hours of this Mac's own clock
(8:00-17:00 by default), so a change of country needs no setting. The same thing is never told twice.

Reads the files already in desk/data/ and sends nothing anywhere. A notification carries a title and a name,
never the text of a message. What was shown or held back stays 7 days in desk/data/notify/ (names inside, so
it is kept out of the GitHub copy).

  python3 desk/notify.py plan    what would be shown right now, as JSON (shows nothing, writes nothing)
  python3 desk/notify.py test    one test notification
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DESK_DATA") or os.path.join(ROOT, "data")
DIR = os.environ.get("NOTIFY_DIR") or os.path.join(DATA, "notify")
HCMC = timezone(timedelta(hours=7))  # the day the Desk files are keyed by
FOCUS_URL = "http://127.0.0.1:47800/api/state"
OPEN = ("notyet", "doing")
MIN = 60
HOUR = 3600
DAY = 86400
KEEP_LOG = 7 * DAY
KEEP_SENT = 14 * DAY
LATE = 30 * MIN  # a digest missed by more than this (the Mac slept) is dropped
URGENT_CAP = 5  # reply notifications a day
URGENT_TOGETHER = 3  # more than this at once becomes one notification
NUDGE_GAP = 7 * DAY  # the same task is not brought up again sooner
STALE_DOING = 3 * DAY
TRIAGE_OVER = 10
OVERUSE = 1.5  # an app this many times its 7-day average is mentioned
OVERUSE_MIN = 30 * MIN
CALENDAR_OLD = 6 * HOUR
SOUNDS = {"now": "Glass", "digest": "Tink"}
SLOTS = ("morning", "noon", "evening")
SLOT_TITLES = {"morning": "Morning digest", "noon": "Midday digest", "evening": "Afternoon digest"}

# id, label on the settings screen, tier
KINDS = (
    ("reply", "Messages that need a reply now", "now"),
    ("event30", "30 minutes before an event", "now"),
    ("task30", "30 minutes before a task is due", "now"),
    ("event6h", "6 hours before an event", "heads"),
    ("morning", "Morning digest", "digest"),
    ("noon", "Midday digest", "digest"),
    ("evening", "Afternoon digest", "digest"),
    ("overdue", "Tasks past their due date", "line"),
    ("meal", "Meal log", "line"),
    ("habit", "Habits and routines", "line"),
    ("progress", "Finished and in-progress tasks", "line"),
    ("triage", "Unsorted tasks", "line"),
    ("screen", "Screen time", "line"),
    ("move", "Exercise", "line"),
    ("nudge", "Gentle reminder (one a day)", "line"),
)
DEFAULTS = {
    "on": {k: True for k, _, _ in KINDS},
    "ringFrom": 8 * 60,  # minutes after midnight, this Mac's clock
    "ringTo": 17 * 60,
    "digests": {"morning": 8 * 60, "noon": 12 * 60 + 30, "evening": 16 * 60 + 30},
    "sound": True,
    "focusOnlyNow": True,
    "pauseUntil": 0,
}
LOCK = threading.Lock()


# ---------- files ----------

def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(name, value):
    os.makedirs(DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DIR, prefix=".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(DIR, name))


def settings():
    return clean_settings(read_json(os.path.join(DIR, "settings.json"), {}))


def clean_settings(raw, base=None):
    """Only known keys, only sane values. Anything else falls back to what was there."""
    out = json.loads(json.dumps(base or DEFAULTS))
    raw = raw if isinstance(raw, dict) else {}
    for k in out["on"]:
        if isinstance((raw.get("on") or {}).get(k), bool):
            out["on"][k] = raw["on"][k]
    minute = lambda v: isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 24 * 60
    for k in ("ringFrom", "ringTo"):
        if minute(raw.get(k)):
            out[k] = raw[k]
    for k in SLOTS:
        if minute((raw.get("digests") or {}).get(k)):
            out["digests"][k] = raw["digests"][k]
    for k in ("sound", "focusOnlyNow"):
        if isinstance(raw.get(k), bool):
            out[k] = raw[k]
    if isinstance(raw.get("pauseUntil"), (int, float)) and not isinstance(raw.get("pauseUntil"), bool):
        out["pauseUntil"] = int(raw["pauseUntil"])
    return out


def blank_state():
    return {"sent": {}, "carry": [], "digests": {}, "urgent": {}, "nudged": {}, "waiting": {}}


def read_state():
    st = read_json(os.path.join(DIR, "state.json"), {})
    return {**blank_state(), **(st if isinstance(st, dict) else {})}


def load_data(now):
    """Everything the rules look at, read from the Desk files. A file that cannot be read is simply empty."""
    j = lambda *p, d=None: read_json(os.path.join(DATA, *p), d)
    days = [datetime.fromtimestamp(now - i * DAY, HCMC).strftime("%Y-%m-%d") for i in (0, 1)]
    mail = []
    for d in days:
        mail += [i for i in (j("docs", "days", d + ".json", d={}) or {}).get("items") or [] if isinstance(i, dict)]
    cal = j("docs", "calendar", "upcoming.json", d={}) or {}
    wa = j("whatsapp", "items.json", d={})
    body = j("body", "records.json", d={})
    try:
        import kickoff
        top = kickoff.candidates()["tasks"][:3]
    except Exception:  # the order of today's three is a nicety; a broken file must not stop the rest
        top = []
    return {
        "tasks": [t for t in j("tasks.json", d=[]) or [] if isinstance(t, dict) and t.get("id") and not t.get("deleted")],
        "events": [e for e in cal.get("events") or [] if isinstance(e, dict)],
        "calendarAt": cal.get("fetchedAt"),
        "mail": mail,
        "whatsapp": wa if isinstance(wa, dict) else {},
        "habits": [h for h in j("habits.json", d=[]) or [] if isinstance(h, dict) and not h.get("deleted")],
        "routines": [r for r in j("routines.json", d=[]) or [] if isinstance(r, dict) and not r.get("deleted")],
        "routinelog": [r for r in j("routinelog.json", d=[]) or [] if isinstance(r, dict)],
        "body": [r for r in (body.values() if isinstance(body, dict) else []) if isinstance(r, dict)],
        "screen": j("screentime", "days.json", d={}) or {},
        "appNames": j("screentime", "names.json", d={}) or {},
        "top3": [{"id": t.get("id"), "title": t.get("title", ""), "status": t.get("status")} for t in top],
    }


# ---------- small helpers ----------

def local(ts, tz=None):
    """This Mac's clock (tz None), so the ring hours follow the Mac wherever it is."""
    return datetime.fromtimestamp(ts, tz) if tz else datetime.fromtimestamp(ts)


def minutes_of(dt):
    return dt.hour * 60 + dt.minute


def hhmm(ts, tz):
    return local(ts, tz).strftime("%-H:%M")


def start_ts(text):
    try:
        return datetime.fromisoformat(text).timestamp() if "T" in text else None
    except (TypeError, ValueError):
        return None


def task_title(t):
    c = t.get("clear") or {}
    return (c.get("title") if c.get("title") and not c.get("titleOff") else t.get("title")) or "(untitled)"


def timed_due(t):
    """Seconds, or None. A date-only due is stored as 23:59 in Ho Chi Minh and gets no reminder."""
    due = t.get("due")
    if not isinstance(due, (int, float)) or isinstance(due, bool):
        return None
    d = datetime.fromtimestamp(due / 1000, HCMC)
    return None if (d.hour, d.minute) == (23, 59) else due / 1000


def sender(text):
    """The name only: no address in a notification."""
    return re.sub(r"\s*<[^>]*>", "", str(text or "")).strip().strip('"')[:30]


def short(text, n=40):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def dur(sec):
    h, m = int(sec) // 3600, int(sec) % 3600 // 60
    return f"{h}h {m}m" if h else f"{m}m"


def in_ring(minute, s):
    a, b = s["ringFrom"], s["ringTo"]
    return a <= minute < b if a <= b else (minute >= a or minute < b)


# ---------- what could be told ----------

def instant(now, data, s, tz=None):
    """The things tied to a moment: each has an id, so it is told once."""
    out = []
    for e in data["events"]:
        st = None if e.get("allDay") else start_ts(e.get("start"))
        if st is None or not e.get("id"):
            continue
        left = st - now
        line = f"{hhmm(st, tz)} {short(e.get('title') or 'Event')}"
        if 0 < left <= 30 * MIN:
            out.append({"id": f"event30:{e['id']}:{int(st)}", "kind": "event30", "tier": "now",
                        "title": f"Event in {max(1, round(left / 60))} min", "body": line, "link": "#calendar"})
        elif 5 * HOUR < left <= 6 * HOUR:
            out.append({"id": f"event6h:{e['id']}:{int(st)}", "kind": "event6h", "tier": "heads",
                        "title": "Today's event", "body": line + " (in about 6 hours)", "link": "#calendar"})
    for t in data["tasks"]:
        due = timed_due(t) if t.get("status") in OPEN else None
        if due is not None and 0 < due - now <= 30 * MIN:
            out.append({"id": f"task30:{t['id']}:{int(due)}", "kind": "task30", "tier": "now",
                        "title": f"Task due in {max(1, round((due - now) / 60))} min",
                        "body": f"{hhmm(due, tz)} {short(task_title(t))}", "link": "#tasks", "task": t["id"]})
    return out + urgent(data)


def urgent(data):
    """Replies the intake marked `urgent`, still unanswered. The intake decides; nothing here raises a message."""
    tasks = {t.get("sourceRef"): t for t in data["tasks"] if t.get("sourceRef")}
    by_id = {t["id"]: t for t in data["tasks"]}
    out = []
    for m in data["mail"]:
        if m.get("urgent") is not True or not m.get("key") or (m.get("status") or "pending") != "pending":
            continue
        t = tasks.get("mail:" + str(m.get("gmail_url")))
        if t and t.get("status") not in OPEN:
            continue
        who = sender(m.get("from"))
        out.append({"id": f"reply:mail:{m['key']}", "kind": "reply", "tier": "now", "title": "Email that needs a reply now",
                    "body": short(m.get("topic") or m.get("subject_ja") or m.get("subject") or "Email") + (f" ({who})" if who else ""),
                    "why": short(m.get("urgentWhy"), 60), "link": "#tasks", "task": t and t["id"]})
    for tid, w in data["whatsapp"].items():
        if not isinstance(w, dict) or w.get("urgent") is not True:
            continue
        t = by_id.get(tid)
        if t and t.get("status") not in OPEN:
            continue
        out.append({"id": f"reply:wa:{tid}:{w.get('lastId') or w.get('lastAt')}", "kind": "reply", "tier": "now",
                    "title": "WhatsApp message that needs a reply now",
                    "body": short(w.get("topic") or "Message") + (f" ({short(w.get('name'), 20)})" if w.get("name") else ""),
                    "why": short(w.get("urgentWhy"), 60), "link": "#tasks", "task": tid})
    return out


def done_today(data, now, tz):
    day = local(now, tz).date()
    return [t for t in data["tasks"] if t.get("status") == "done" and isinstance(t.get("updatedAt"), (int, float))
            and local(t["updatedAt"] / 1000, tz).date() == day]


def morning_lines(now, data, s, st, tz):
    lines, on = [], s["on"]
    day = local(now, tz).date()
    for item in st["carry"]:  # urgent replies that came in the quiet hours
        lines.append(("reply", f"Reply: {item['body']}"))
    evs = sorted(((start_ts(e.get("start")), e) for e in data["events"] if not e.get("allDay") and start_ts(e.get("start"))), key=lambda x: x[0])
    evs = [(t, e) for t, e in evs if t > now and local(t, tz).date() == day]
    if evs:
        lines.append(("event6h", "Today's events: " + ", ".join(f"{hhmm(t, tz)} {short(e.get('title'), 16)}" for t, e in evs[:3])
                      + (f" and {len(evs) - 3} more" if len(evs) > 3 else "")))
    dues = sorted(((timed_due(t), t) for t in data["tasks"] if t.get("status") in OPEN and timed_due(t)), key=lambda x: x[0])
    dues = [(d, t) for d, t in dues if d > now and local(d, tz).date() == day]
    if dues:
        lines.append(("task30", "Due today: " + ", ".join(f"{hhmm(d, tz)} {short(task_title(t), 16)}" for d, t in dues[:3])))
    if on["overdue"]:
        late = [t for t in data["tasks"] if t.get("status") == "notyet" and isinstance(t.get("due"), (int, float))
                and 0 < now - t["due"] / 1000 <= 3 * DAY and f"overdue:{t['id']}:{int(t['due'])}" not in st["sent"]]
        if late:
            lines.append(("overdue", f"Past due, not started: {short(task_title(late[0]), 20)}" + (f" and {len(late) - 1} more" if len(late) > 1 else ""),
                          [f"overdue:{t['id']}:{int(t['due'])}" for t in late]))
    return lines


def noon_lines(now, data, s, st, tz):
    lines, on = [], s["on"]
    today = datetime.fromtimestamp(now, HCMC).strftime("%Y-%m-%d")
    if on["meal"] and not any(r.get("date") == today and str(r.get("kind", "")).startswith("meal") for r in data["body"]):
        lines.append(("meal", "No meals logged today. A photo after you eat is enough."))
    if on["habit"]:
        left = [h.get("name") or "" for h in data["habits"] if today not in (h.get("done") or [])]
        if left:
            lines.append(("habit", "Habits still open: " + ", ".join(short(n, 10) for n in left[:3]) + (f" and {len(left) - 3} more" if len(left) > 3 else "")))
        items = [r for r in data["routines"] if r.get("when") == "morning"]
        marks = (next((r for r in data["routinelog"] if r.get("id") == today), {}).get("morning")) or {}
        rest = sum(1 for r in items if not marks.get(r.get("id")))
        if items and rest:
            lines.append(("habit", f"Morning routine: {rest} left"))
    if on["progress"]:
        lines += progress(now, data, tz)
    if on["triage"]:
        loose = [t for t in data["tasks"] if t.get("status") == "notyet" and not t.get("planDate") and not t.get("due")]
        key = "triage:" + local(now, tz).strftime("%G-%V")
        if len(loose) > TRIAGE_OVER and key not in st["sent"]:
            lines.append(("triage", f"{len(loose)} unsorted tasks. Sort them in batches of 10.", [key]))
    return lines


def progress(now, data, tz):
    done = done_today(data, now, tz)
    doing = [t for t in data["tasks"] if t.get("status") == "doing"]
    out = []
    if done:
        out.append(("progress", f"Finished today: {len(done)} tasks ({short(task_title(done[-1]), 16)} and others)" if len(done) > 1
                    else f"Finished today: {short(task_title(done[0]), 24)}"))
    if doing:
        out.append(("progress", f"In progress: {short(task_title(doing[0]), 20)}" + (f" and {len(doing) - 1} more" if len(doing) > 1 else "")))
    return out


def screen_lines(now, data):
    days = data["screen"]
    today = datetime.fromtimestamp(now, HCMC).strftime("%Y-%m-%d")
    apps = ((days.get(today) or {}).get("mac") or {}).get("apps") or {}
    if not apps:
        return []
    name = lambda b: data["appNames"].get(b) or b.rsplit(".", 1)[-1]
    top = max(apps, key=apps.get)
    out = [("screen", f"Screen time today: {dur(sum(apps.values()))} (most in {name(top)}, {dur(apps[top])})")]
    past = [datetime.fromtimestamp(now - i * DAY, HCMC).strftime("%Y-%m-%d") for i in range(1, 8)]
    past = [((days.get(d) or {}).get("mac") or {}).get("apps") for d in past]
    past = [p for p in past if p]
    if len(past) >= 3:  # an average of fewer days says little
        over = []
        for b, sec in apps.items():
            avg = sum(p.get(b, 0) for p in past) / len(past)
            if sec >= OVERUSE_MIN and avg > 0 and sec > avg * OVERUSE:
                over.append((sec - avg, b))
        for extra, b in sorted(over, reverse=True)[:2]:
            out.append(("screen", f"{name(b)} ran {dur(extra)} longer than usual."))
    return out


def nudge(now, data, st, tz):
    """One task a day, never the same one within a week. Today's three first, then what has sat in 'doing'."""
    fresh = lambda tid: now - st["nudged"].get(tid, 0) >= NUDGE_GAP
    by_id = {t["id"]: t for t in data["tasks"]}
    day = local(now, tz).date()
    if local(now, tz).hour >= 15 and data["top3"] and not done_today(data, now, tz):
        touched = any(t.get("status") == "doing" or (isinstance((by_id.get(t["id"]) or {}).get("updatedAt"), (int, float))
                      and local(by_id[t["id"]]["updatedAt"] / 1000, tz).date() == day) for t in data["top3"])
        first = data["top3"][0]
        if not touched and first.get("id") and fresh(first["id"]):
            title = task_title(by_id.get(first["id"]) or first)
            return first["id"], f"First of today's three: {short(title, 24)}. Open it for five minutes?"
    stale = sorted((t for t in data["tasks"] if t.get("status") == "doing" and isinstance(t.get("updatedAt"), (int, float))
                    and now - t["updatedAt"] / 1000 >= STALE_DOING and fresh(t["id"])), key=lambda t: t["updatedAt"])
    if stale:
        return stale[0]["id"], f"Still in progress: {short(task_title(stale[0]), 24)}. You can pick up where you left off."
    return None, None


def evening_lines(now, data, s, st, tz):
    lines, on = [], s["on"]
    if on["screen"]:
        lines += screen_lines(now, data)
    if on["move"]:
        since = datetime.fromtimestamp(now - 3 * DAY, HCMC).strftime("%Y-%m-%d")
        if not any(r.get("kind") in ("steps", "workout") and str(r.get("date", "")) > since for r in data["body"]):
            lines.append(("move", "No steps or workouts logged for 3 days. A short walk counts."))
    if on["progress"]:
        lines += [p for p in progress(now, data, tz) if p[1].startswith(("Finished today", "今日終わった"))]
    if on["nudge"]:
        tid, text = nudge(now, data, st, tz)
        if tid:
            lines.append(("nudge", text, [], tid))
    return lines


BUILDERS = {"morning": morning_lines, "noon": noon_lines, "evening": evening_lines}


# ---------- deciding ----------

def plan(now, data, s, st, tz=None, focus=False):
    """What to show at `now`. Returns (notifications to show, entries for the log, the state after). Pure."""
    st = json.loads(json.dumps({**blank_state(), **st}))
    dt = local(now, tz)
    minute, day = minutes_of(dt), dt.strftime("%Y-%m-%d")
    ring, paused = in_ring(minute, s), now < s["pauseUntil"]
    show, log = [], []
    entry = lambda n, shown, why="": {**{k: n.get(k) for k in ("id", "kind", "tier", "title", "body", "link", "task", "why", "lines") if n.get(k)},
                                      "at": int(now), "shown": shown, "held": why}

    fresh = [n for n in instant(now, data, s, tz) if n["id"] not in st["sent"] and s["on"].get(n["kind"])]
    replies = [n for n in fresh if n["kind"] == "reply"]
    for n in [n for n in fresh if n["kind"] != "reply"]:
        if focus and s["focusOnlyNow"] and n["tier"] != "now" and ring and not paused:
            continue  # waits for the focus session to end, while its hour lasts
        st["sent"][n["id"]] = int(now)
        why = "paused" if paused else "" if ring else "quiet"
        log.append(entry(n, not why, why))
        if not why:
            show.append(n)
    if replies:
        used = st["urgent"].get(day, 0)
        for n in replies:
            st["sent"][n["id"]] = int(now)
        if paused or not ring:
            for n in replies:
                log.append(entry(n, False, "paused" if paused else "quiet"))
                st["carry"].append({"id": n["id"], "body": n["body"]})
        elif used >= URGENT_CAP:
            log += [entry(n, False, "cap") for n in replies]
        elif len(replies) > URGENT_TOGETHER:
            one = {"id": "reply:many:" + str(int(now)), "kind": "reply", "tier": "now", "title": f"{len(replies)} messages need a reply now",
                   "body": ", ".join(n["body"] for n in replies[:2]) + " and more", "link": "#tasks", "lines": [n["body"] for n in replies]}
            show.append(one)
            log.append(entry(one, True))
            st["urgent"][day] = used + 1
        else:
            for n in replies:
                ok = st["urgent"].get(day, 0) < URGENT_CAP
                log.append(entry(n, ok, "" if ok else "cap"))
                if ok:
                    show.append(n)
                    st["urgent"][day] = st["urgent"].get(day, 0) + 1

    told = st["digests"].setdefault(day, [])
    for slot in SLOTS:
        at = s["digests"][slot]
        if slot in told or not s["on"][slot] or minute < at:
            continue
        waiting = st["waiting"].get(slot) == day
        if minute - at > LATE // 60 and not waiting:
            told.append(slot)  # the Mac slept through it
            continue
        if paused or not ring:
            told.append(slot)
            continue
        if focus and s["focusOnlyNow"]:
            st["waiting"][slot] = day
            continue
        told.append(slot)
        st["waiting"].pop(slot, None)
        lines = BUILDERS[slot](now, data, s, st, tz)
        if slot == "morning":
            st["carry"] = []
        if not lines:
            continue
        for line in lines:
            for sid in (line[2] if len(line) > 2 else []):
                st["sent"][sid] = int(now)
            if len(line) > 3:
                st["nudged"][line[3]] = int(now)
        texts = [line[1] for line in lines]
        n = {"id": f"digest:{slot}:{day}", "kind": slot, "tier": "digest", "title": SLOT_TITLES[slot],
             "body": "\n".join(texts[:2]) + (f"\n{len(texts) - 2} more in Desk notices" if len(texts) > 2 else ""),
             "lines": texts, "link": "#notices"}
        show.append(n)
        log.append(entry(n, True))

    st["sent"] = {k: v for k, v in st["sent"].items() if now - v < KEEP_SENT}
    st["nudged"] = {k: v for k, v in st["nudged"].items() if now - v < 2 * NUDGE_GAP}
    for key in ("digests", "urgent"):
        st[key] = {k: v for k, v in st[key].items() if k >= local(now - 2 * DAY, tz).strftime("%Y-%m-%d")}
    st["waiting"] = {k: v for k, v in st["waiting"].items() if v == day}
    return show, log, st


# ---------- showing ----------

SCRIPT = ("on run argv", "if (item 4 of argv) is \"\" then",
          "display notification (item 1 of argv) with title (item 2 of argv) subtitle (item 3 of argv)", "else",
          "display notification (item 1 of argv) with title (item 2 of argv) subtitle (item 3 of argv) sound name (item 4 of argv)",
          "end if", "end run")


def show_mac(n, sound=True):
    """The text goes in as arguments, never into the script, so a title cannot run anything."""
    cmd = ["/usr/bin/osascript"]
    for line in SCRIPT:
        cmd += ["-e", line]
    cmd += [n.get("body") or "", "Desk", n.get("title") or "", SOUNDS.get(n.get("tier"), "") if sound else ""]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def focus_on():
    try:
        with urllib.request.urlopen(FOCUS_URL, timeout=1) as r:
            return bool((json.load(r).get("active") or {}).get("active"))
    except Exception:  # FocusBlock not installed or not answering: no session
        return False


def tick(now=None, show=show_mac):
    now = now or time.time()
    try:
        time.tzset()  # the Mac may have moved to another time zone since the server started
    except AttributeError:
        pass
    with LOCK:
        s, st = settings(), read_state()
        shown, log, after = plan(now, load_data(now), s, st, focus=focus_on() if s["focusOnlyNow"] else False)
        if after != st:
            write_json("state.json", after)
        if log:
            old = [e for e in read_json(os.path.join(DIR, "log.json"), []) if isinstance(e, dict) and now - e.get("at", 0) < KEEP_LOG]
            write_json("log.json", old + log)
    for n in shown:
        show(n, s["sound"])
    return shown


def watch():
    while True:
        try:
            tick()
        except Exception as e:  # one bad file must not end the watch
            print("notify:", type(e).__name__, e, file=sys.stderr, flush=True)
        time.sleep(60)


# ---------- for the page ----------

def view(now=None):
    now = now or time.time()
    s = settings()
    log = [e for e in read_json(os.path.join(DIR, "log.json"), []) if isinstance(e, dict) and now - e.get("at", 0) < KEEP_LOG]
    cal = (read_json(os.path.join(DATA, "docs", "calendar", "upcoming.json"), {}) or {}).get("fetchedAt")
    problem = ""
    if isinstance(cal, (int, float)) and now - cal / 1000 > CALENDAR_OLD:
        problem = f"Calendar import has been paused for {int((now - cal / 1000) // HOUR)} hours. Event notifications are using older data."
    try:
        time.tzset()
    except AttributeError:
        pass
    return {"settings": s, "log": sorted(log, key=lambda e: -e.get("at", 0)), "problem": problem,
            "kinds": [{"id": k, "label": label, "tier": tier} for k, label, tier in KINDS],
            "ringing": in_ring(minutes_of(local(now)), s) and now >= s["pauseUntil"], "now": int(now),
            "clock": local(now).strftime("%-H:%M")}


def put_settings(rec):
    with LOCK:
        s = clean_settings(rec, settings())
        write_json("settings.json", s)
    return 200, view()


def pause(rec, now=None):
    """{"minutes": 60}, {"today": true} until midnight of this Mac's clock, or {"minutes": 0} to resume."""
    now = now or time.time()
    rec = rec if isinstance(rec, dict) else {}
    if rec.get("today") is True:
        until = int((local(now).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp())
    elif isinstance(rec.get("minutes"), int) and not isinstance(rec.get("minutes"), bool) and 0 <= rec["minutes"] <= 24 * 60:
        until = int(now + rec["minutes"] * 60) if rec["minutes"] else 0
    else:
        return 400, {"error": "minutes or today"}
    return put_settings({"pauseUntil": until})


def test():
    ok = show_mac({"title": "Test notification", "body": "This is how notifications look and sound.", "tier": "now"}, True)
    return (200, {"ok": True}) if ok else (503, {"error": "could not show"})


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "plan":
        t = time.time()
        shown, log, _ = plan(t, load_data(t), settings(), read_state(), focus=focus_on())
        print(json.dumps({"show": shown, "held": [e for e in log if not e["shown"]]}, ensure_ascii=False, indent=1))
    elif cmd == "test":
        print(test())
    else:
        print(__doc__)
