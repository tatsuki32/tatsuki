#!/usr/bin/env python3
"""Turns raw connector output into the Desk's feeds, keeping only the fields the Desk shows.

  python3 desk/write_feed.py calendar raw.json   # raw = Google Calendar list_events result (JSON)
  python3 desk/write_feed.py gtasks   raw.json   # raw = the "Tasks" sheet values: [[header...], [row...], ...]

Saves docs/calendar/upcoming.json or docs/gtasks/current.json and rebuilds the page's data.
Event descriptions and attendees are dropped on purpose: they hold phone numbers and other private notes.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from server import put_doc, rebuild_feed  # noqa: E402


def calendar(raw):
    out = []
    for e in raw.get("events", raw.get("items", [])):
        if e.get("status") == "cancelled":
            continue
        s, en = e.get("start", {}), e.get("end", {})
        all_day = "date" in s and "dateTime" not in s
        out.append({
            "id": e.get("id", ""),
            "title": e.get("summary") or "(タイトルなし)",
            "start": (s.get("date") or "")[:10] if all_day else s.get("dateTime", ""),
            "end": (en.get("date") or "")[:10] if all_day else en.get("dateTime", ""),
            "allDay": all_day,
            "location": e.get("location", ""),
            "url": e.get("htmlLink", ""),
        })
    return {"events": out}


def gtasks(raw):
    values = raw.get("values", raw) if isinstance(raw, dict) else raw
    if not values:
        return {"tasks": []}
    head = [str(h) for h in values[0]]
    rows = [dict(zip(head, [str(v) for v in r])) for r in values[1:]]
    keep = ("id", "listId", "list", "title", "notes", "due", "status", "completed", "updated")
    return {"tasks": [{k: r.get(k, "") for k in keep} for r in rows if r.get("id") and r.get("title")]}


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("calendar", "gtasks"):
        print(__doc__)
        sys.exit(2)
    with open(sys.argv[2], encoding="utf-8") as f:
        data = json.load(f)
    kind = sys.argv[1]
    doc = {"calendar": calendar, "gtasks": gtasks}[kind](data)
    doc["generated_at"] = time.strftime("%b %-d, %-I:%M %p")
    doc["fetchedAt"] = int(time.time() * 1000)
    doc_id = {"calendar": "upcoming", "gtasks": "current"}[kind]
    put_doc(kind, doc_id, doc)
    rebuild_feed(kind)
    print("ok")
