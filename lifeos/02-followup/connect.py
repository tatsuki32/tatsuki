"""Where 02 touches the outside. Swap a function here to change a connection; core.py stays.

Python has no Gmail login here, so mail goes through a Claude session that has the Gmail
connector (MORNING_RUN.md): it saves a snapshot and creates the drafts this tool queues.
There is deliberately no function that sends mail.
"""
import json
import os
import time

from lifeos.bot import telegram
from lifeos.lib import paths, tasks, tools

SLUG = "02-followup"


def _desk_module():
    """desk/kickoff.py already reads the synced calendar and mail: use it, pointed at the Desk data."""
    mod = tools.load_file(os.path.join(paths.REPO, "desk"), "kickoff")
    mod.DATA = paths.desk_data_dir()
    return mod


def _age_hours(name):
    p = os.path.join(paths.desk_data_dir(), name)
    return (time.time() - os.path.getmtime(p)) / 3600 if os.path.exists(p) else None


def read_waiting(day):
    """Today's snapshot of sent threads, or None if there is none for this day."""
    try:
        with open(paths.data_path("tools", SLUG, "snapshot.json"), encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, ValueError):
        return None
    return snap if isinstance(snap, dict) and snap.get("day") == day else None


def read_calendar(day):
    """Today's events from the Desk's calendar sync: {"events": [...], "ageHours": n}, or None."""
    age = _age_hours("calendar.js")
    if age is None:
        return None
    return {"events": _desk_module().events_today(paths.now_ms()), "ageHours": age}


def read_needs_reply(day):
    """Mail the Morning Mail check marked as ours to answer, minus what the Desk has closed."""
    age = _age_hours("mail.js")
    if age is None:
        return None
    mod = _desk_module()
    stored = {t["id"]: t for t in tasks.all_tasks()}
    dues = {d["id"]: d for d in mod.read_json("dues.json", []) if isinstance(d, dict) and d.get("id")}
    return {"items": [{"title": x["title"], "from": x["from"]} for x in mod.mail_tasks(dues, stored)], "ageHours": age}


def read_dms(day):
    """DMs to answer: [{"title", "from"}]. Empty until the owner decides which services count."""
    return []


def make_draft(request):
    """Creates a draft and returns {"id", "viewUrl"}, or None to leave it queued for MORNING_RUN.md."""
    return None


def can_ask():
    token, chat = telegram.config()
    return bool(token and chat) and not paths.is_sandbox()


def ask(text):
    """Puts a question to the owner. False means it waits in logs/outbox.jsonl."""
    return telegram.notify(text, telegram.SECRETARY)
