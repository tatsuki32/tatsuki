"""Writes an event to Google Calendar through the owner's Apps Script web app (calendar-webhook.gs).

The web app's URL is the secret. It stays in the keychain:
    security add-generic-password -a webhook -s tatsuki-desk-gcal -w
"""
import json
import os
import subprocess
import urllib.request

from lifeos.lib import paths

KEYCHAIN = ("tatsuki-desk-gcal", "webhook")
TIMEOUT = 20
DEFAULT_MINUTES = 15  # a calendar stays readable when nothing said how long it takes


class CalendarError(Exception):
    """The event did not reach the calendar. The message never holds the URL."""


def url():
    v = os.environ.get("LIFEOS_GCAL_URL")
    if v or paths.is_sandbox():
        return v or None
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN[0], "-a", KEYCHAIN[1], "-w"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def connected():
    return bool(url())


def add(title, at, timed, ref, note="", minutes=0):
    """at is a datetime. Without a clock time it becomes an all-day event. ref keeps a retry from adding it twice."""
    target = url()
    if not target:
        raise CalendarError("not connected")
    body = {"title": title, "ref": ref, "note": note, "start": at.isoformat() if timed else "", "date": at.strftime("%Y-%m-%d"),
            "minutes": minutes or DEFAULT_MINUTES}
    req = urllib.request.Request(target, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "tatsuki-desk-lifeos"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            answer = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        raise CalendarError(type(e).__name__) from None
    if not (isinstance(answer, dict) and answer.get("ok")):
        raise CalendarError("refused")
