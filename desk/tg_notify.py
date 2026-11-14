#!/usr/bin/env python3
"""Tatsuki's Desk — tasks and events to Telegram, one message each, from a bot that only notifies.

What is sent (each can be turned off on the Notifications screen):
  event10    an event starts in 10 minutes                   any hour
  added      a task was added                                4:00-17:00
  done       a task was finished                             4:00-17:00
  deleted    a task was deleted                              4:00-17:00
  due1       due tomorrow           once a day               4:00-17:00
  due0       due today              once a day               4:00-17:00
  overdue    past its due date      once a day, with days    4:00-17:00
  stuck      in progress, untouched for 3 days, once a day   4:00-17:00
  doing      each task in progress, once a day               4:00-17:00
  reply      a reply is waiting (Replies), once per task, only while Replies is open (5:00 / 16:00)
Hours are this Mac's clock, so they follow the Mac to another country. What happens outside 4:00-17:00 waits and
is sent from 4:00, one message each. Task messages stop at 30 a day; then one message says how many more there were.

A message holds a title, a date, an event name and time, and a Desk link. Never the text of a mail, a reply draft
or a note. Nothing is sent anywhere but api.telegram.org.

Events come from Google Calendar's secret iCal address(es) in the keychain (service tatsuki-desk-gcal-ics, one or
more URLs separated by spaces), read every 5 minutes. Without it, from desk/data/docs/calendar/upcoming.json.
The bot token is in the keychain (service tatsuki-desk-telegram-notify). The chat is the owner's chat of the other
Desk bots (Telegram gives the same id with every bot), unless `connect` set one.

  python3 desk/tg_notify.py plan      what would be sent now, as JSON (sends nothing, writes nothing)
  python3 desk/tg_notify.py test      one test message
  python3 desk/tg_notify.py connect   after you sent /start to the new bot: remember that chat
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DESK_DATA") or os.path.join(ROOT, "data")
DIR = os.environ.get("TGNOTIFY_DIR") or os.path.join(DATA, "tgnotify")
HCMC = timezone(timedelta(hours=7))  # date-only dues are stored in Ho Chi Minh time
API = "https://api.telegram.org"
LINK = "http://localhost:4747/#task/"
TOKEN_SERVICE = "tatsuki-desk-telegram-notify"
ICS_SERVICE = "tatsuki-desk-gcal-ics"
MIN, HOUR, DAY = 60, 3600, 86400
OPEN = ("notyet", "doing")
EVENT_BEFORE = 10 * MIN
STUCK = 3 * DAY
CAP = 30  # task messages a day
PER_TICK = 25  # Telegram allows about 30 a second; this keeps a morning burst calm
FAIL_LINE = 3
ICS_EVERY = 5 * MIN
KEEP_SENT = 14 * DAY
KEEP_LOG = 7 * DAY
KINDS = (
    ("event10", "10 minutes before an event (any hour)"),
    ("added", "Task added"),
    ("done", "Task done"),
    ("deleted", "Task deleted"),
    ("due1", "Due tomorrow"),
    ("due0", "Due today"),
    ("overdue", "Past due (once a day)"),
    ("stuck", "In progress but untouched for 3 days"),
    ("doing", "Each task in progress (once a day)"),
    ("reply", "Reply waiting (when Replies opens)"),
)
DEFAULTS = {"on": {k: True for k, _ in KINDS}, "from": 4 * 60, "to": 17 * 60, "chat": ""}
LOCK = threading.Lock()
_ics = {"at": 0, "events": [], "error": ""}


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


def clean_settings(raw, base=None):
    out = json.loads(json.dumps(base or DEFAULTS))
    raw = raw if isinstance(raw, dict) else {}
    for k in out["on"]:
        if isinstance((raw.get("on") or {}).get(k), bool):
            out["on"][k] = raw["on"][k]
    minute = lambda v: isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 24 * 60
    for k in ("from", "to"):
        if minute(raw.get(k)):
            out[k] = raw[k]
    if isinstance(raw.get("chat"), str) and re.fullmatch(r"-?\d{1,20}|", raw["chat"]):
        out["chat"] = raw["chat"]
    return out


def settings():
    return clean_settings(read_json(os.path.join(DIR, "settings.json"), {}))


def blank_state():
    return {"seen": None, "sent": {}, "queue": [], "count": {}, "over": {}, "fail": 0, "lastOk": 0, "lastError": ""}


def read_state():
    st = read_json(os.path.join(DIR, "state.json"), {})
    return {**blank_state(), **(st if isinstance(st, dict) else {})}


# ---------- keys ----------

def keychain(service):
    if os.environ.get("TGNOTIFY_NO_KEYCHAIN"):
        return None
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def token():
    return os.environ.get("TGNOTIFY_TOKEN") or keychain(TOKEN_SERVICE)


def chat_id(s=None):
    s = s or settings()
    if s.get("chat"):
        return s["chat"]
    saved = read_json(os.path.join(DATA, "lifeos", "secrets", "telegram.json"), {})
    return os.environ.get("LIFEOS_TELEGRAM_CHAT_ID") or (saved.get("chat_id") if isinstance(saved, dict) else None) or None


def call(tok, method, **params):
    data = urllib.parse.urlencode(params).encode("utf-8")
    try:
        with urllib.request.urlopen(f"{API}/bot{tok}/{method}", data=data, timeout=20) as r:
            v = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            v = json.loads(e.read().decode("utf-8"))
        except (OSError, ValueError):
            raise RuntimeError(f"{method}: HTTP {e.code}") from None
    except (OSError, ValueError) as e:
        raise RuntimeError(f"{method}: {type(e).__name__}") from None  # never the URL: it holds the token
    if not v.get("ok"):
        raise RuntimeError(f"{method}: {v.get('description') or 'failed'}")
    return v.get("result")


def send_telegram(text, s=None):
    tok, chat = token(), chat_id(s)
    if not tok:
        raise RuntimeError("No bot token in the keychain")
    if not chat:
        raise RuntimeError("No chat yet: send /start to the bot, then press Connect")
    call(tok, "sendMessage", chat_id=chat, text=text[:4000], disable_web_page_preview="true")
    return True


# ---------- helpers ----------

def local(ts):
    return datetime.fromtimestamp(ts)


def minutes_of(dt):
    return dt.hour * 60 + dt.minute


def in_hours(now, s):
    m, a, b = minutes_of(local(now)), s["from"], s["to"]
    return a <= m < b if a <= b else (m >= a or m < b)


def title_of(t):
    c = t.get("clear") or {}
    text = (c.get("title") if c.get("title") and not c.get("titleOff") else t.get("title")) or "(untitled)"
    text = " ".join(str(text).split())
    return text if len(text) <= 200 else text[:199] + "…"


def due_date(t):
    """The due as a date, or None. Dues are milliseconds; a date-only due sits at noon or 23:59 in Ho Chi Minh."""
    due = t.get("due")
    if not isinstance(due, (int, float)) or isinstance(due, bool) or due <= 0:
        return None
    return datetime.fromtimestamp(due / 1000, HCMC).date()


def is_reply(t):
    try:
        import replywin
        return replywin.is_reply(t)
    except Exception:
        return False


def reply_open(now):
    try:
        import replywin
        return replywin.is_open(now, data_dir=DATA)
    except Exception:
        return True


def plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def task_msg(kind, t, today):
    title, link = title_of(t), LINK + urllib.parse.quote(str(t["id"]))
    d = due_date(t)
    due = f"\nDue: {d.isoformat()}" if d else ""
    head = {
        "added": "🆕 Task added",
        "done": "✅ Done",
        "deleted": "🗑️ Deleted",
        "due1": "📅 Due tomorrow",
        "due0": "⏰ Due today",
        "doing": "▶️ In progress",
        "reply": "💬 Reply needed",
    }.get(kind)
    if kind == "overdue":
        head = f"🚨 Overdue {plural((today - d).days, 'day')}"
    elif kind == "stuck":
        idle = max(3, int((time.time() - (t.get("updatedAt") or 0) / 1000) // DAY))
        head = f"⏸️ In progress, untouched for {plural(idle, 'day')}"
    if kind == "deleted":
        return f"{head}: {title}"  # the link would open nothing
    return f"{head}: {title}{due}\n{link}"


# ---------- events ----------

def unfold(text):
    return re.sub(r"\r?\n[ \t]", "", text).splitlines()


def parse_dt(value, params):
    """(datetime aware, all_day) from an iCal value."""
    v = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", v):
        return datetime.strptime(v[:8], "%Y%m%d").replace(tzinfo=HCMC), True
    dt = datetime.strptime(v.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if v.endswith("Z"):
        return dt.replace(tzinfo=timezone.utc), False
    tz = None
    if params.get("TZID") and ZoneInfo:
        try:
            tz = ZoneInfo(params["TZID"])
        except Exception:
            tz = None
    return (dt.replace(tzinfo=tz) if tz else dt.astimezone()), False


def ics_events(text):
    """VEVENTs as dicts: uid, summary, start, end-less, all_day, rrule, exdates, rid, cancelled."""
    out, cur = [], None
    for line in unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {"exdates": set()}
        elif line == "END:VEVENT":
            if cur and cur.get("start"):
                out.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            head, value = line.split(":", 1)
            name, *ps = head.split(";")
            params = dict(p.split("=", 1) for p in ps if "=" in p)
            try:
                if name == "UID":
                    cur["uid"] = value
                elif name == "SUMMARY":
                    cur["summary"] = value.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ")
                elif name == "DTSTART":
                    cur["start"], cur["all_day"] = parse_dt(value, params)
                elif name == "RRULE":
                    cur["rrule"] = dict(p.split("=", 1) for p in value.split(";") if "=" in p)
                elif name == "EXDATE":
                    for v in value.split(","):
                        cur["exdates"].add(int(parse_dt(v, params)[0].timestamp()))
                elif name == "RECURRENCE-ID":
                    cur["rid"] = int(parse_dt(value, params)[0].timestamp())
                elif name == "STATUS":
                    cur["cancelled"] = value.strip().upper() == "CANCELLED"
            except ValueError:
                continue
    return out


WD = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def add_months(dt, n):
    y, m = divmod(dt.month - 1 + n, 12)
    try:
        return dt.replace(year=dt.year + y, month=m + 1)
    except ValueError:
        return None  # the 31st in a short month: skipped, as Google does


def nth_weekday(year, month, spec, like):
    """'2TU' or '-1FR' in that month, at the time of `like`."""
    m = re.fullmatch(r"([+-]?\d)?([A-Z]{2})", spec)
    if not m or m.group(2) not in WD:
        return None
    n, wd = int(m.group(1) or 1), WD.index(m.group(2))
    first = like.replace(year=year, month=month, day=1)
    days = [first + timedelta(days=i) for i in range(31) if (first + timedelta(days=i)).month == month]
    days = [d for d in days if d.weekday() == wd]
    try:
        return days[n - 1] if n > 0 else days[n]
    except IndexError:
        return None


def occurrences(ev, lo, hi):
    """Start times (epoch) in [lo, hi). Covers what Google writes: DAILY / WEEKLY with BYDAY / MONTHLY / YEARLY,
    INTERVAL, COUNT, UNTIL. Anything stranger falls back to the first start only."""
    start = ev["start"]
    r = ev.get("rrule")
    if not r:
        ts = start.timestamp()
        return [int(ts)] if lo <= ts < hi else []
    freq, step = r.get("FREQ"), max(1, int(r.get("INTERVAL") or 1))
    count = int(r["COUNT"]) if (r.get("COUNT") or "").isdigit() else None
    until = None
    if r.get("UNTIL"):
        try:
            until = parse_dt(r["UNTIL"], {})[0].timestamp()
        except ValueError:
            until = None
    bydays = [d for d in (r.get("BYDAY") or "").split(",") if d]
    out, n, i = [], 0, 0
    while i < 20000:
        if freq == "DAILY":
            cands = [start + timedelta(days=i * step)]
        elif freq == "WEEKLY":
            week = start + timedelta(weeks=i * step)
            monday = week - timedelta(days=week.weekday())
            days = [WD.index(d[-2:]) for d in bydays if d[-2:] in WD] or [start.weekday()]
            cands = sorted(monday + timedelta(days=d) for d in set(days))
            cands = [c for c in cands if c >= start]
        elif freq == "MONTHLY":
            base = add_months(start.replace(day=1), i * step)
            if bydays:
                cands = sorted(c for c in (nth_weekday(base.year, base.month, d, start) for d in bydays) if c and c >= start)
            else:
                c = add_months(start, i * step)
                cands = [c] if c else []
        elif freq == "YEARLY":
            try:
                cands = [start.replace(year=start.year + i * step)]
            except ValueError:
                cands = []
        else:
            ts = start.timestamp()
            return [int(ts)] if lo <= ts < hi else []
        i += 1
        stop = False
        for c in cands:
            ts = c.timestamp()
            if until is not None and ts > until:
                stop = True
                break
            n += 1
            if count is not None and n > count:
                stop = True
                break
            if ts >= hi:
                stop = True
                break
            if ts >= lo and int(ts) not in ev["exdates"]:
                out.append(int(ts))
        if stop:
            break
    return out


def expand(evs, lo, hi):
    """[{id, title, start}] of timed events starting in [lo, hi)."""
    moved = {}
    for e in evs:
        if e.get("rid") is not None and e.get("uid"):
            moved.setdefault(e["uid"], {})[e["rid"]] = e
    out = []
    for e in evs:
        if e.get("all_day") or e.get("cancelled"):
            continue
        if e.get("rid") is not None:
            ts = e["start"].timestamp()
            if lo <= ts < hi:
                out.append({"id": f"{e.get('uid')}:{e['rid']}", "title": e.get("summary") or "Event", "start": int(ts)})
            continue
        for ts in occurrences(e, lo, hi):
            if ts in moved.get(e.get("uid"), {}):
                continue  # this one was changed; the changed copy counts
            out.append({"id": f"{e.get('uid')}:{ts}", "title": e.get("summary") or "Event", "start": ts})
    return out


def fetch_ics(now):
    urls = (keychain(ICS_SERVICE) or "").split()
    if not urls:
        return None
    if now - _ics["at"] < ICS_EVERY:
        return _ics["events"]
    evs, err = [], ""
    for u in urls:
        if not u.startswith("https://"):
            continue
        try:
            with urllib.request.urlopen(u, timeout=20) as r:
                evs += ics_events(r.read().decode("utf-8", "replace"))
        except (OSError, ValueError) as e:
            err = f"Could not read the calendar ({type(e).__name__})"  # the URL itself is a secret
    _ics.update(at=now, events=evs if evs or not _ics["events"] else _ics["events"], error=err)
    return _ics["events"]


def upcoming(now):
    """Timed events starting from now to 1 hour ahead."""
    lo, hi = now - 5 * MIN, now + HOUR
    evs = fetch_ics(now)
    if evs is not None:
        out = expand(evs, lo, hi)
    else:
        cal = read_json(os.path.join(DATA, "docs", "calendar", "upcoming.json"), {}) or {}
        out = []
        for e in cal.get("events") or []:
            if not isinstance(e, dict) or e.get("allDay") or "T" not in str(e.get("start") or ""):
                continue
            try:
                ts = datetime.fromisoformat(e["start"]).timestamp()
            except ValueError:
                continue
            if lo <= ts < hi:
                out.append({"id": str(e.get("id")), "title": e.get("title") or "Event", "start": int(ts)})
    return [e for e in out if not str(e["title"]).startswith("☑️")]


# ---------- what to send ----------

def load_tasks():
    tasks = read_json(os.path.join(DATA, "tasks.json"), []) or []
    return [t for t in tasks if isinstance(t, dict) and t.get("id") and t.get("by") != "claude"]


def plan(now, tasks, events, s, st, reply_is_open=True):
    """(messages to send now, new state). A message: {key, kind, text, event?}. Pure: sends nothing."""
    st = json.loads(json.dumps(st))
    on, today = s["on"], local(now).date()
    day = today.isoformat()
    sent, fresh = st["sent"], []

    def add(key, kind, text, event=False):
        if key in sent or not on.get(kind, True):
            return
        sent[key] = int(now)
        (fresh if event else st["queue"]).append({"key": key, "kind": kind, "text": text, "at": int(now), "event": event})

    # events: any hour, never late
    for e in events:
        left = e["start"] - now
        if 0 < left <= EVENT_BEFORE:
            at = local(e["start"]).strftime("%-H:%M")
            add(f"event10:{e['id']}:{e['start']}", "event10", f"🗓️ In {max(1, round(left / 60))} min: {e['title']}\nStarts {at}", True)

    # changes: compared with what was seen a minute ago
    seen = st.get("seen")
    if not tasks and seen:
        tasks_ok = False  # the file could not be read: do not take every task for deleted, or for new next time
    else:
        tasks_ok = True
    now_seen = {}
    for t in tasks:
        status = "deleted" if t.get("deleted") else t.get("status")
        now_seen[t["id"]] = status
        if seen is None:
            if is_reply(t):  # replies already waiting at setup are not told; only the ones that come in after
                sent[f"reply:{t['id']}"] = int(now)
            continue  # first run: remember everything, tell nothing
        if is_reply(t):
            continue
        before = seen.get(t["id"])
        if before is None and status in OPEN:
            add(f"added:{t['id']}", "added", task_msg("added", t, today))
        elif before is not None and before != status:
            if status == "done":
                add(f"done:{t['id']}:{day}", "done", task_msg("done", t, today))
            elif status == "deleted":
                add(f"deleted:{t['id']}", "deleted", task_msg("deleted", t, today))
    if tasks_ok:
        st["seen"] = now_seen

    # once a day, from the start of the hours
    if in_hours(now, s):
        board = [t for t in tasks if not t.get("deleted") and t.get("status") in OPEN and not is_reply(t)]
        over, due0, due1, stuck, doing = [], [], [], [], []
        for t in board:
            d = due_date(t)
            if d and d < today:
                over.append((d, t))
            elif d == today:
                due0.append(t)
            elif d == today + timedelta(days=1):
                due1.append(t)
            if t.get("status") == "doing":
                idle = now - (t.get("updatedAt") or 0) / 1000
                (stuck if idle >= STUCK else doing).append(t)
        for d, t in sorted(over, key=lambda x: x[0]):
            add(f"overdue:{t['id']}:{day}", "overdue", task_msg("overdue", t, today))
        for t in due0:
            add(f"due0:{t['id']}:{day}", "due0", task_msg("due0", t, today))
        for t in due1:
            add(f"due1:{t['id']}:{day}", "due1", task_msg("due1", t, today))
        for t in stuck:
            add(f"stuck:{t['id']}:{day}", "stuck", task_msg("stuck", t, today))
        for t in doing:
            add(f"doing:{t['id']}:{day}", "doing", task_msg("doing", t, today))
        if reply_is_open:
            for t in tasks:
                if is_reply(t) and not t.get("deleted") and t.get("status") in OPEN:
                    add(f"reply:{t['id']}", "reply", task_msg("reply", t, today))

    # what goes out now: events first, then the queue up to the day's limit; one line says how many were left out
    out = list(fresh)
    if in_hours(now, s):
        keep = []
        for m in st["queue"]:
            if not on.get(m["kind"], True):
                continue  # turned off since it was queued
            if len(out) >= PER_TICK:
                keep.append(m)
            elif st["count"].get(day, 0) < CAP:
                st["count"][day] = st["count"].get(day, 0) + 1
                out.append(m)
            else:
                st["over"][day] = st["over"].get(day, 0) + 1
        st["queue"] = keep
        if st["over"].get(day) and f"over:{day}" not in sent and not keep:
            sent[f"over:{day}"] = int(now)
            out.append({"key": f"over:{day}", "kind": "over", "at": int(now), "event": False,
                        "text": f"➕ {plural(st['over'][day], 'more task notification')} today were not sent (limit {CAP} a day). See Desk:\nhttp://localhost:4747/#tasks"})
    st["sent"] = {k: v for k, v in sent.items() if now - v < KEEP_SENT or k.startswith("added:") or k.startswith("deleted:") or k.startswith("reply:")}
    st["count"] = {k: v for k, v in st["count"].items() if k >= (today - timedelta(days=2)).isoformat()}
    st["over"] = {k: v for k, v in st["over"].items() if k >= (today - timedelta(days=2)).isoformat()}
    return out, st


def prune_once_keys(st, tasks):
    """added / deleted / reply keys live as long as the task does."""
    ids = {t["id"] for t in tasks}
    st["sent"] = {k: v for k, v in st["sent"].items()
                  if not k.startswith(("added:", "deleted:", "reply:")) or k.split(":", 2)[1] in ids}
    return st


# ---------- running ----------

def tick(now=None, send=None):
    now = now or time.time()
    try:
        time.tzset()
    except AttributeError:
        pass
    send = send or send_telegram
    with LOCK:
        s = settings()
        if not token() and send is send_telegram:
            return []  # not set up yet: nothing is queued, so setting it up later does not flood the chat
        tasks = load_tasks()
        st = read_state()
        try:
            events = upcoming(now) if s["on"].get("event10") else []
        except Exception:
            events = []
        out, st = plan(now, tasks, events, s, st, reply_open(now))
        st = prune_once_keys(st, tasks)
        done, log = [], []
        for i, m in enumerate(out):
            try:
                send(m["text"], s)
            except Exception as e:
                st["fail"] = st.get("fail", 0) + 1
                st["lastError"] = str(e)[:160]
                # not sent: task messages go back to the front of the queue; an event is planned again next
                # minute if it has not started yet; the "more" line is worked out again
                day = local(now).date().isoformat()
                back = [x for x in out[i:] if not x.get("event") and x["kind"] != "over"]
                st["count"][day] = max(0, st["count"].get(day, 0) - len(back))
                st["queue"] = back + st["queue"]
                for x in out[i:]:
                    if x.get("event") or x["kind"] == "over":
                        st["sent"].pop(x["key"], None)
                break
            done.append(m)
            st["fail"], st["lastOk"], st["lastError"] = 0, int(now), ""
            log.append({"at": int(now), "kind": m["kind"], "text": m["text"].split("\n")[0][:120]})
            time.sleep(0.05)
        write_json("state.json", st)
        if log:
            old = [e for e in read_json(os.path.join(DIR, "log.json"), []) if isinstance(e, dict) and now - e.get("at", 0) < KEEP_LOG]
            write_json("log.json", old + log)
    return done


def watch():
    while True:
        try:
            tick()
        except Exception as e:  # one bad file must not end the watch
            print("tg_notify:", type(e).__name__, e, file=sys.stderr, flush=True)
        time.sleep(60)


# ---------- for the page ----------

def view(now=None):
    now = now or time.time()
    s, st = settings(), read_state()
    has_token, chat = bool(token()), chat_id(s)
    problem = ""
    if not has_token:
        problem = "Not connected: no bot token yet."
    elif not chat:
        problem = "Send /start to the bot in Telegram, then press Connect."
    elif st.get("fail", 0) >= FAIL_LINE:
        problem = f"Telegram messages are failing: {st.get('lastError') or 'unknown error'}"
    ics = bool(keychain(ICS_SERVICE))
    log = [e for e in read_json(os.path.join(DIR, "log.json"), []) if isinstance(e, dict) and now - e.get("at", 0) < KEEP_LOG]
    return {"settings": s, "connected": has_token and bool(chat), "problem": problem,
            "calendar": "Google Calendar (direct)" if ics else "Desk calendar import",
            "calendarError": _ics.get("error") if ics else "",
            "waiting": len(st.get("queue") or []), "inHours": in_hours(now, s),
            "kinds": [{"id": k, "label": label} for k, label in KINDS],
            "log": sorted(log, key=lambda e: -e["at"])[:50]}


def put_settings(rec):
    with LOCK:
        s = clean_settings(rec, settings())
        write_json("settings.json", s)
    return 200, view()


def test():
    try:
        send_telegram("🔔 Test from Desk. Task and event notifications will look like this:\n🚨 Overdue 2 days: Example task\nDue: "
                      + (date.today() - timedelta(days=2)).isoformat() + "\n" + LINK + "example")
        return 200, {"ok": True}
    except Exception as e:
        return 503, {"error": str(e)[:160]}


def connect():
    """Remembers the chat that last wrote to the bot. If the Desk bots already know the owner's chat, only that one."""
    tok = token()
    if not tok:
        return 400, {"error": "No bot token in the keychain"}
    try:
        ups = call(tok, "getUpdates", timeout=0) or []
    except Exception as e:
        return 503, {"error": str(e)[:160]}
    chats = [str(u["message"]["chat"]["id"]) for u in ups if (u.get("message") or {}).get("chat", {}).get("type") == "private"]
    if not chats:
        return 404, {"error": "No message yet. Send /start to the bot in Telegram, then try again."}
    known = chat_id({"chat": ""})
    pick = known if known and known in chats else (None if known else chats[-1])
    if not pick:
        return 409, {"error": "The message came from a different Telegram account than the other Desk bots use."}
    put_settings({"chat": pick})
    return 200, view()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "plan":
        t = time.time()
        tasks = load_tasks()
        out, _ = plan(t, tasks, upcoming(t), settings(), read_state(), reply_open(t))
        print(json.dumps([m["text"] for m in out], ensure_ascii=False, indent=1))
    elif cmd == "test":
        print(test())
    elif cmd == "connect":
        code, v = connect()
        print(code, v.get("error") or ("connected" if v.get("connected") else v.get("problem")))
    else:
        print(__doc__)
