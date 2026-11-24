#!/usr/bin/env python3
"""The Desk's Calendar, read straight from Google Calendar's secret iCal address (no Claude, no connector).

The address(es) live in the keychain (service tatsuki-desk-gcal-ics, one or more https URLs separated by spaces), the same
ones tg_notify.py uses. The server (4747 only) reads them every 5 minutes and writes docs/calendar/upcoming.json,
in the same shape write_feed.py makes: today and the next 7 days, title, start, end, all-day, location.
No description and no guests are kept: from them only two marks come out, `meet` (there is a Meet, Zoom, Teams or Webex link)
and `declined` (the calendar's own address said no), for Transcribe's auto-recording. Without an address nothing is written and the last calendar stays.
If one address fails, the last good calendar stays and the reason is kept in memory only (the URL is a secret).

  python3 desk/gcal_ics.py once     read now and write the Desk's calendar
  python3 desk/gcal_ics.py status   is an address saved, when it was last read, and the last error
"""
import json
import re
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta

import tg_notify as ics  # unfold, parse_dt, occurrences, keychain

EVERY = 5 * 60
DAYS = 8  # today + the next 7 days, like the old hourly import
_state = {"at": 0, "ok": 0, "error": "", "count": 0}


GUEST = re.compile(r"mailto:([^\s;,:]+@[^\s;,:]+)\s*$", re.I)
# a link to join, not a link about the service (a help page in an invitation is not a meeting)
MEET = re.compile(r"https?://(?:[\w-]+\.)*(?:meet\.google\.com/[a-z]{3}-|meet\.google\.com/lookup/|zoom\.us/(?:j|my|w|s)/|teams\.microsoft\.com/(?:l/meetup-join|meet/)"
                  r"|teams\.live\.com/meet/|webex\.com/(?:meet/|[\w./-]*j\.php))", re.I)
MEET_FIELDS = ("LOCATION", "DESCRIPTION", "X-GOOGLE-CONFERENCE", "X-GOOGLE-HANGOUT", "URL")


def parse(text, guests=False):
    """VEVENTs with what the calendar shows: uid, summary, start, end, all_day, location, rrule, exdates, rid, cancelled.
    guests=True (minutes_mail only, in memory) adds the invited addresses; the Desk's calendar never keeps them."""
    out, cur, owner = [], None, ""
    for line in ics.unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {"exdates": set()}
        elif line == "END:VEVENT":
            if cur and cur.get("start"):
                out.append(cur)
            cur = None
        elif cur is None and line.upper().startswith("X-WR-CALNAME:"):
            owner = line.split(":", 1)[1].strip().lower()  # a main Google calendar is named after its address
        elif cur is not None and ":" in line:
            head, value = line.split(":", 1)
            name, *ps = head.split(";")
            if name in MEET_FIELDS and MEET.search(value):
                cur["meet"] = True
            if name == "ATTENDEE" and "@" in owner and "PARTSTAT=DECLINED" in re.sub(r'"[^"]*"', "", line).upper():
                g = GUEST.search(line)
                if g and g.group(1).lower() == owner:
                    cur["declined"] = True
            params = dict(p.split("=", 1) for p in ps if "=" in p)
            text_value = value.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ").replace("\\\\", "\\")
            if guests and name in ("ATTENDEE", "ORGANIZER"):
                g = GUEST.search(line)
                if g:
                    up = re.sub(r'"[^"]*"', "", line[:g.start()]).upper()  # a name in quotes cannot pretend to be an answer
                    st = re.search(r"PARTSTAT=([A-Z-]+)", up)
                    cur.setdefault("guests", []).append({"email": g.group(1).lower(), "status": "ORGANIZER" if name == "ORGANIZER" else st.group(1) if st else "",
                                                         "room": bool(re.search(r"CUTYPE=(RESOURCE|ROOM|GROUP)", up))})
                continue
            try:
                if name == "UID":
                    cur["uid"] = value
                elif name == "SUMMARY":
                    cur["summary"] = text_value
                elif name == "LOCATION":
                    cur["location"] = text_value
                elif name == "DTSTART":
                    cur["start"], cur["all_day"] = ics.parse_dt(value, params)
                elif name == "DTEND":
                    cur["end"] = ics.parse_dt(value, params)[0]
                elif name == "RRULE":
                    cur["rrule"] = dict(p.split("=", 1) for p in value.split(";") if "=" in p)
                elif name == "EXDATE":
                    for v in value.split(","):
                        cur["exdates"].add(int(ics.parse_dt(v, params)[0].timestamp()))
                elif name == "RECURRENCE-ID":
                    cur["rid"] = int(ics.parse_dt(value, params)[0].timestamp())
                elif name == "STATUS":
                    cur["cancelled"] = value.strip().upper() == "CANCELLED"
            except ValueError:
                continue
    return out


def _row(e, ts):
    row = _plain_row(e, ts)
    return {**row, "guests": e["guests"]} if e.get("guests") else row


def _plain_row(e, ts):
    start = datetime.fromtimestamp(ts, e["start"].tzinfo)
    length = (e["end"] - e["start"]) if e.get("end") else timedelta(0)
    end = start + length
    uid = e.get("uid") or "event"
    if e.get("all_day"):
        s, en = start.date().isoformat(), end.date().isoformat()
        return {"id": f"{uid}_{s}", "title": e.get("summary") or "(No title)", "start": s, "end": en or s,
                "allDay": True, "location": e.get("location", ""), "url": ""}
    s, en = start.astimezone(), end.astimezone()  # the Mac's own time zone, like the rest of the Desk
    marks = {k: True for k in ("meet", "declined") if e.get(k)}
    return {"id": f"{uid}_{int(ts)}", "title": e.get("summary") or "(No title)", "start": s.isoformat(timespec="seconds"),
            "end": en.isoformat(timespec="seconds"), "allDay": False, "location": e.get("location", ""), "url": "", **marks}


def events(evs, lo, hi):
    """Rows for the Desk's calendar: events that overlap [lo, hi) (epoch seconds), sorted by start."""
    moved = {}
    for e in evs:
        if e.get("rid") is not None and e.get("uid"):
            moved.setdefault(e["uid"], set()).add(e["rid"])
    out = []
    for e in evs:
        if e.get("cancelled") or (e.get("summary") or "").startswith("☑️"):  # ☑️: Desk's own tasks (task_calendar.py)
            continue
        length = (e["end"] - e["start"]).total_seconds() if e.get("end") else 0
        if e.get("rid") is not None:
            ts = e["start"].timestamp()
            if ts < hi and ts + max(length, 1) > lo:
                out.append(_row(e, ts))
            continue
        for ts in ics.occurrences(e, lo - max(length, 0), hi):  # an event that began before lo can still be on today
            if ts + max(length, 1) <= lo or ts in moved.get(e.get("uid"), ()):
                continue
            out.append(_row(e, ts))
    out.sort(key=lambda r: (r["start"][:10], not r["allDay"], r["start"]))
    return out


def window(now=None):
    now = now or time.time()
    day = datetime.fromtimestamp(now).astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return day.timestamp(), (day + timedelta(days=DAYS)).timestamp()


def urls():
    return [u for u in (ics.keychain(ics.ICS_SERVICE) or "").split() if u.startswith("https://")]


def once(write):
    """Read every saved address; write(doc) only when all of them were read. Returns the number of events or None."""
    found = urls()
    _state["at"] = time.time()
    if not found:
        _state["error"] = "No iCal address in the keychain"
        return None
    evs = []
    for u in found:
        try:
            with urllib.request.urlopen(u, timeout=30) as r:
                evs += parse(r.read().decode("utf-8", "replace"))
        except (OSError, ValueError) as e:
            _state["error"] = f"Could not read the calendar ({type(e).__name__})"  # never the URL: it is the secret
            return None
    lo, hi = window()
    rows = events(evs, lo, hi)
    write({"events": rows, "generated_at": time.strftime("%b %-d, %-I:%M %p"), "fetchedAt": int(time.time() * 1000),
           "via": "ical"})
    _state.update(ok=time.time(), error="", count=len(rows))
    return len(rows)


def status():
    return {"saved": bool(urls()), "lastTry": int(_state["at"]), "lastOk": int(_state["ok"]),
            "events": _state["count"], "error": _state["error"]}


def watch(write):
    while True:
        try:
            once(write)
        except Exception as e:  # noqa: BLE001 — one bad feed must not stop the thread
            _state["error"] = f"Calendar import failed ({type(e).__name__})"
        time.sleep(EVERY)


def _write_with_server(doc):
    from server import put_doc, rebuild_feed
    put_doc("calendar", "upcoming", doc)
    rebuild_feed("calendar")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "once":
        n = once(_write_with_server)
        print(json.dumps({"events": n, **status()}, ensure_ascii=False))
        sys.exit(0 if n is not None else 1)
    elif cmd == "status":
        print(json.dumps(status(), ensure_ascii=False))
    else:
        print(__doc__)
        sys.exit(2)
