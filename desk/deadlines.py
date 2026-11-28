#!/usr/bin/env python3
"""Tatsuki's Desk — deadlines found in mail, put on a Google calendar of their own ("Deadlines").

The scheduled task desk-morning-mail (4:30 and 15:30) reads the personal Gmail (connector) and the university Gmail
(desk/unimail.py recent), and hands what it found to this file. The code, not Claude, decides about duplicates, writes
the calendar and tells him in one mail:

  python3 deadlines.py auth             connect once (Google's page in the browser; it makes the "Deadlines" calendar)
  python3 deadlines.py status           JSON
  python3 deadlines.py add FILE.json    a list of found deadlines (shape below); writes the calendar, mails once, prints JSON
  python3 deadlines.py sync             retries what is waiting, notices events he deleted in Google Calendar

One found deadline: {"kind": "report|exam|paperwork|work|doby", "title": "History report", "due": "2026-12-04" or
"2026-12-04T23:59" or with an offset ("+09:00"), "account": "personal|zen", "messageId", "thread", "gmail_url",
"subject", "from" (a name), "summary" (one or two short lines)}. "doby" is a date Claude chose for something with no
deadline in the mail (an answer or a task to do soon); those are all-day and marked as suggested.

Google: the only permission is calendar.app.created — the calendars this app made and their events, nothing else in
his calendars. The OAuth client is the mail cleanup's ("tatsuki-desk-gmail"); the refresh token lives in the keychain as
"tatsuki-desk-deadlines". The mail is put into his own inbox with Gmail's messages.import (the mail cleanup's
gmail.modify): there is no recipient, nothing is sent. desk/data/deadlines/ keeps titles, dates, subjects and sender
names (no mail text) and stays out of the backup.
DEADLINES_FAKE=1: no Google at all (calendar and mail go to fake_*.json in the folder) — for tests and test servers.
"""
import base64
import hashlib
import html
import http.server
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("DEADLINES_DIR") or os.path.join(os.environ.get("DESK_DATA_DIR") or os.path.join(ROOT, "data"), "deadlines")
CLIENT_SERVICE = "tatsuki-desk-gmail"
SERVICE = "tatsuki-desk-deadlines"
SCOPES = "https://www.googleapis.com/auth/calendar.app.created"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CAL_API = "https://www.googleapis.com/calendar/v3"
CAL_NAME = "Deadlines"
LINK = (os.environ.get("DEADLINES_LINK_BASE") or "http://localhost:4747") + "/#deadlines"
KINDS = {"report": ("Due", "Report"), "exam": ("Exam", "Exam"), "paperwork": ("Deadline", "Paperwork"),
         "work": ("Due", "Work"), "doby": ("Do by", "Do by")}
SOFT_MAX = 5          # suggested dates per run
SOFT_DAYS = 7         # a suggested date is at most this far ahead
FAR_DAYS = 400        # further than this is a misread
TIMED_MIN = 30        # a timed deadline is a 30-minute event that ends at the deadline
EVERY = 1800          # the server's check: retry waiting ones, notice deletions
LOCK = threading.RLock()
TOKEN = {"value": "", "until": 0}
CONNECT = {"running": False, "url": "", "error": ""}


class NotConnected(Exception):
    pass


class DeadlineError(Exception):
    """Safe to show on the page: never mail text or a key."""


def fake():
    return bool(os.environ.get("DEADLINES_FAKE"))


# --- files ---
def _path(name):
    return os.path.join(DIR, name)


def _read(name, default):
    try:
        with open(_path(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(name, data):
    os.makedirs(DIR, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DIR, prefix=".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path(name))


def items():
    return _read("items.json", {})


def settings():
    s = {"soft": True, "calendarId": "", "email": "", "lastRun": 0, "error": "", "pendingMail": []}
    s.update(_read("settings.json", {}))
    return s


def _save_settings(**kw):
    with LOCK:
        s = settings()
        s.update(kw)
        _write("settings.json", s)
        return s


# --- time ---
def mac_tz():
    name = os.environ.get("DEADLINES_TZ") or ""
    if not name:
        try:
            link = os.readlink("/etc/localtime")
            name = link.split("zoneinfo/", 1)[1] if "zoneinfo/" in link else ""
        except OSError:
            name = ""
    name = name or "Asia/Ho_Chi_Minh"
    try:
        return name, ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return "Asia/Ho_Chi_Minh", ZoneInfo("Asia/Ho_Chi_Minh")


def parse_due(v):
    """(datetime with tz, all_day). Raises ValueError for anything else."""
    v = str(v or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        d = date.fromisoformat(v)
        return datetime(d.year, d.month, d.day, tzinfo=mac_tz()[1]), True
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:?\d{2})?", v):
        raise ValueError("due")
    v = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", v.replace("Z", "+00:00").replace(" ", "T"))
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=mac_tz()[1])
    return dt.astimezone(mac_tz()[1]), False


def _now():
    return datetime.now(mac_tz()[1])


# --- keychain ---
def _kc_get(service, account):
    if fake():
        return os.environ.get("DEADLINES_FAKE_" + account.upper(), "fake" if account == "refresh" else "")
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", service, "-a", account, "-w"],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _kc_set(service, account, value):
    # through `security -i` so the secret never shows in a process list
    line = 'add-generic-password -U -s "%s" -a "%s" -w "%s"\n' % (service, account, value.replace("\\", "\\\\").replace('"', '\\"'))
    out = subprocess.run(["security", "-i"], input=line, capture_output=True, text=True, timeout=15)
    if out.returncode != 0:
        raise OSError("keychain")


def _client():
    raw = _kc_get(CLIENT_SERVICE, "client")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def _form(url, fields):
    req = urllib.request.Request(url, urllib.parse.urlencode(fields).encode(), {"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (400, 401):
            raise NotConnected("Google Calendar needs to be connected again") from None
        raise DeadlineError(f"Google returned an error ({e.code})") from None
    except OSError:
        raise DeadlineError("Couldn't reach Google. Check the internet connection") from None


def _token():
    with LOCK:
        if TOKEN["value"] and time.time() < TOKEN["until"]:
            return TOKEN["value"]
    client, refresh = _client(), _kc_get(SERVICE, "refresh")
    if not client or not refresh:
        raise NotConnected("Google Calendar is not connected yet")
    data = _form(TOKEN_URL, {"client_id": client["id"], "client_secret": client["secret"],
                             "refresh_token": refresh, "grant_type": "refresh_token"})
    with LOCK:
        TOKEN.update(value=data["access_token"], until=time.time() + int(data.get("expires_in", 3600)) - 60)
        return TOKEN["value"]


# --- the calendar (real or fake) ---
def _fake_cal():
    return _read("fake_calendar.json", {"calendars": {}, "events": {}})


def cal(method, path, body=None):
    """Google Calendar, or the fake one. Returns the JSON; a missing event comes back as None."""
    if fake():
        with LOCK:
            fc = _fake_cal()
            if method == "POST" and path == "/calendars":
                cid = "fake-" + uuid.uuid4().hex[:8] + "@group.calendar.google.com"
                fc["calendars"][cid] = body
                _write("fake_calendar.json", fc)
                return {"id": cid, **body}
            m = re.fullmatch(r"/calendars/([^/]+)/events(?:/([^/?]+))?", path.split("?")[0])
            if not m or urllib.parse.unquote(m.group(1)) not in fc["calendars"]:
                return None
            eid = m.group(2)
            if method == "POST":
                eid = uuid.uuid4().hex
                fc["events"][eid] = {**body, "id": eid, "htmlLink": "https://calendar.google.com/event?eid=" + eid}
            elif eid not in fc["events"]:
                return None
            elif method == "PATCH":
                fc["events"][eid].update(body)
            elif method == "DELETE":
                del fc["events"][eid]
                _write("fake_calendar.json", fc)
                return {}
            _write("fake_calendar.json", fc)
            return fc["events"][eid]
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(3):
        req = urllib.request.Request(CAL_API + path, data, method=method, headers={
            "Authorization": "Bearer " + _token(), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return None
            if e.code == 401:
                with LOCK:
                    TOKEN.update(value="", until=0)
                if attempt < 2:
                    continue
                raise NotConnected("Google Calendar needs to be connected again") from None
            if e.code == 403:
                raise NotConnected("Google Calendar refused (connect it again)") from None
            if e.code in (429, 500, 503) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise DeadlineError(f"Google Calendar returned an error ({e.code})") from None
        except OSError:
            if attempt == 2:
                raise DeadlineError("Couldn't reach Google Calendar") from None
            time.sleep(2 ** attempt)
    raise DeadlineError("Couldn't reach Google Calendar")


def ensure_calendar():
    """The "Deadlines" calendar's id; makes it when there is none (or he deleted it)."""
    s = settings()
    cid = s.get("calendarId")
    if cid and cal("GET", f"/calendars/{urllib.parse.quote(cid)}/events?maxResults=1") is not None:
        return cid
    tzname = mac_tz()[0]
    made = cal("POST", "/calendars", {"summary": CAL_NAME, "description": "Deadlines found in mail by Tatsuki's Desk.", "timeZone": tzname})
    if not made or not made.get("id"):
        raise DeadlineError("Couldn't make the Deadlines calendar")
    _save_settings(calendarId=made["id"])
    return made["id"]


# --- connect ---
def connect(open_browser=True):
    """Opens Google's consent page and waits for 5 minutes in a thread. Returns the URL right away."""
    if fake():
        _save_settings(error="")
        ensure_calendar()
        return {"url": "", "thread": None}
    client = _client()
    if not client:
        raise DeadlineError("The Google connection file is missing. Connect the mail cleanup once first (python3 desk/gmail_clean.py auth)")
    with LOCK:
        if CONNECT["running"]:
            return {"url": CONNECT["url"], "thread": None}
        state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        got = {}

        class Once(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                if q.get("state", [""])[0] == state:
                    got["code"] = q.get("code", [""])[0]
                    got["error"] = q.get("error", [""])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write("<p>The Desk can now put deadlines on your Deadlines calendar. You can close this window.</p>".encode())

            def log_message(self, *a):
                pass  # the request line holds the code

        srv = http.server.HTTPServer(("127.0.0.1", 0), Once)
        redirect = f"http://127.0.0.1:{srv.server_port}"
        url = AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": client["id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
            "access_type": "offline", "prompt": "consent select_account", "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256"})
        CONNECT.update(running=True, url=url, error="")

    def wait():
        try:
            srv.timeout = 5
            end = time.time() + 300
            while "code" not in got and time.time() < end:
                srv.handle_request()
            if not got.get("code"):
                raise DeadlineError(got.get("error") or "The sign-in was not finished in 5 minutes")
            data = _form(TOKEN_URL, {"code": got["code"], "client_id": client["id"], "client_secret": client["secret"],
                                     "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
            if not data.get("refresh_token"):
                raise DeadlineError("Google sent no lasting permission. Please try again")
            _kc_set(SERVICE, "refresh", data["refresh_token"])
            with LOCK:
                TOKEN.update(value=data.get("access_token", ""), until=time.time() + int(data.get("expires_in", 3600)) - 60)
            ensure_calendar()
            _save_settings(error="", connectedAt=int(time.time() * 1000))
            run()  # what waited for the connection goes on the calendar now
        except (DeadlineError, NotConnected, OSError) as e:
            CONNECT["error"] = str(e)
        finally:
            srv.server_close()
            CONNECT.update(running=False, url="")

    th = threading.Thread(target=wait, daemon=True)
    th.start()
    if open_browser:
        webbrowser.open(url)
    return {"url": url, "thread": th}


def connected():
    return bool(_kc_get(SERVICE, "refresh") and _client()) or fake()


# --- deciding ---
def norm(s):
    s = re.sub(r"[\s　]+", " ", str(s or "").lower())
    return re.sub(r"[^\w ]", "", s).strip()


def _grams(s):
    s = norm(s).replace(" ", "")
    return {s[i:i + 2] for i in range(max(1, len(s) - 1))} if s else set()


def similar(a, b):
    """Same assignment? Works for Japanese too (character pairs, not words)."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    if re.findall(r"\d+", na) != re.findall(r"\d+", nb):  # "Report 2" is not "Report 3"
        return False
    if na == nb or (min(len(na), len(nb)) >= 4 and (na in nb or nb in na)):
        return True
    ga, gb = _grams(na), _grams(nb)
    return len(ga & gb) / max(1, len(ga | gb)) >= 0.6


def key_of(f):
    src = f.get("messageId") or f.get("thread") or f.get("gmail_url") or ""
    return hashlib.sha256(f"{f.get('account', '')}|{src}|{norm(f.get('title'))}".encode()).hexdigest()[:20]


def clean(f, now=None):
    """The found deadline as the Desk keeps it, or (None, reason)."""
    now = now or _now()
    if not isinstance(f, dict):
        return None, "not an object"
    kind = str(f.get("kind") or "").lower()
    if kind not in KINDS:
        return None, "kind"
    title = re.sub(r"\s+", " ", str(f.get("title") or "")).strip()[:80]
    if not title:
        return None, "title"
    try:
        due, all_day = parse_due(f.get("due"))
    except ValueError:
        return None, "due"
    if kind == "doby":
        all_day = True
    end = due + timedelta(days=1) if all_day else due
    if end <= now:
        return None, "past"
    if due - now > timedelta(days=SOFT_DAYS + 1 if kind == "doby" else FAR_DAYS):
        return None, "too far"
    account = "zen" if f.get("account") == "zen" else "personal"
    rec = {"kind": kind, "title": title, "due": due.date().isoformat() if all_day else due.isoformat(timespec="minutes"),
           "allDay": all_day, "soft": kind == "doby", "account": account,
           "messageId": str(f.get("messageId") or "")[:300], "thread": str(f.get("thread") or "")[:100],
           "gmail_url": str(f.get("gmail_url") or "")[:400] if str(f.get("gmail_url") or "").startswith("https://mail.google.com/") else "",
           "subject": re.sub(r"\s+", " ", str(f.get("subject") or "")).strip()[:160],
           "from": re.sub(r"\s+", " ", re.sub(r"<[^>]*>", "", str(f.get("from") or ""))).strip()[:80],
           "summary": re.sub(r"\s+", " ", str(f.get("summary") or "")).strip()[:240]}
    rec["key"] = key_of({**f, "account": account, "title": title})
    return rec, ""


def _due_dt(rec):
    return parse_due(rec["due"])[0]


def match(rec, all_items):
    """An item already kept for the same thing: same key, or the same account and a similar title within two weeks."""
    for it in all_items.values():
        if it.get("key") == rec["key"] or rec["key"] in (it.get("aliases") or []):
            return it
    for it in all_items.values():
        if it.get("account") != rec["account"] or it.get("soft") != rec["soft"] or not similar(it.get("title"), rec["title"]):
            continue
        same_thread = rec["thread"] and it.get("thread") == rec["thread"]
        if same_thread or abs(_due_dt(it) - _due_dt(rec)) <= timedelta(days=14):
            return it
    return None


def add(found):
    """Keeps what is new, moves what changed, never brings back what he removed. Then writes the calendar and mails."""
    now = _now()
    s = settings()
    report = {"added": [], "moved": [], "same": 0, "skipped": [], "waiting": 0}
    soft = 0
    with LOCK:
        all_items = items()
        for f in found if isinstance(found, list) else []:
            rec, why = clean(f, now)
            if not rec:
                report["skipped"].append(why)
                continue
            if rec["soft"]:
                if not s.get("soft", True):
                    report["skipped"].append("suggested dates are off")
                    continue
                if soft >= SOFT_MAX:
                    report["skipped"].append("too many suggested dates")
                    continue
                soft += 1
            old = match(rec, all_items)
            if old:
                if rec["key"] != old["key"] and rec["key"] not in (old.get("aliases") or []):
                    old.setdefault("aliases", []).append(rec["key"])
                if old.get("state") == "removed" or old.get("fixed") or old["due"] == rec["due"]:
                    report["same"] += 1
                    continue
                old.update(due=rec["due"], allDay=rec["allDay"], moveFrom=old.get("moveFrom") or old["due"],
                           write="move", notified=False, changedAt=time.time())
                for k in ("summary", "subject", "gmail_url", "messageId", "thread", "from"):
                    if rec[k]:
                        old[k] = rec[k]
                report["moved"].append(old["id"])
                continue
            rec.update(id=uuid.uuid4().hex[:12], state="waiting", write="create", notified=False,
                       addedAt=time.time(), eventId="", htmlLink="", error="")
            all_items[rec["id"]] = rec
            report["added"].append(rec["id"])
        _write("items.json", all_items)
    out = run()
    report["waiting"] = out["waiting"]
    report["mailed"] = out["mailed"]
    report["error"] = out["error"]
    return report


# --- writing the calendar ---
def event_body(it):
    prefix, _ = KINDS[it["kind"]]
    lines = []
    if it.get("summary"):
        lines.append(it["summary"])
    if it.get("soft"):
        lines.append("Suggested date: the mail gave no deadline.")
    src = "university Gmail" if it.get("account") == "zen" else "Gmail"
    if it.get("subject") or it.get("from"):
        lines.append(f"From {src}: " + " — ".join(x for x in (it.get("from"), it.get("subject")) if x))
    if it.get("gmail_url"):
        lines.append("Mail: " + it["gmail_url"])
    lines.append("Added by Tatsuki's Desk · " + LINK)
    body = {"summary": f"{prefix}: {it['title']}", "description": "\n".join(lines), "transparency": "transparent",
            "extendedProperties": {"private": {"deskDeadline": it["id"]}}}
    due = _due_dt(it)
    tzname = mac_tz()[0]
    if it["allDay"]:
        d = due.date()
        body["start"] = {"date": d.isoformat()}
        body["end"] = {"date": (d + timedelta(days=1)).isoformat()}
        body["reminders"] = {"useDefault": False, "overrides": [{"method": "popup", "minutes": 15 * 60}]}  # 9:00 the day before
    else:
        body["start"] = {"dateTime": (due - timedelta(minutes=TIMED_MIN)).isoformat(), "timeZone": tzname}
        body["end"] = {"dateTime": due.isoformat(), "timeZone": tzname}
        body["reminders"] = {"useDefault": False, "overrides": [{"method": "popup", "minutes": 24 * 60}, {"method": "popup", "minutes": 180}]}
    return body


def _write_one(it, cid):
    path = f"/calendars/{urllib.parse.quote(cid)}/events"
    if it.get("eventId") and it.get("write") == "move":
        got = cal("PATCH", f"{path}/{urllib.parse.quote(it['eventId'])}", event_body(it))
        if got is None:  # he deleted it in Google Calendar: that counts as removed
            it.update(state="removed", removedBy="calendar", removedAt=time.time(), write="")
            return
    else:
        got = cal("POST", path, event_body(it))
    if got is None:
        raise DeadlineError("The Deadlines calendar was not found")
    it.update(state="added", eventId=got.get("id", ""), htmlLink=got.get("htmlLink", ""), write="", error="", writtenAt=time.time())


def run():
    """Writes what is waiting (when connected), notices deletions, then mails about what is new. Never raises."""
    out = {"waiting": 0, "mailed": 0, "error": ""}
    now = _now()
    with LOCK:
        all_items = items()
        todo = [it for it in all_items.values() if it.get("write") and it.get("state") != "removed"]
        if todo or any(it.get("state") == "added" for it in all_items.values()):
            try:
                if not connected():
                    raise NotConnected("Google Calendar is not connected yet")
                cid = ensure_calendar()
                for it in todo:
                    end = _due_dt(it) + (timedelta(days=1) if it["allDay"] else timedelta())
                    if end <= now and it.get("write") == "create":
                        it.update(state="expired", write="")
                        continue
                    try:
                        _write_one(it, cid)
                    except DeadlineError as e:
                        it["error"] = str(e)
                _notice_deleted(all_items, cid, now)
                _save_settings(error="", lastRun=int(time.time() * 1000))
            except (NotConnected, DeadlineError) as e:
                out["error"] = str(e)
                _save_settings(error=str(e) if settings().get("calendarId") else "", lastRun=int(time.time() * 1000))
            _write("items.json", all_items)
        out["waiting"] = sum(1 for it in all_items.values() if it.get("write") and it.get("state") != "removed")
    try:
        out["mailed"] = mail()
    except DeadlineError as e:
        out["error"] = out["error"] or str(e)
    return out


def _notice_deleted(all_items, cid, now):
    """An event he deleted in Google Calendar is removed here too (and never comes back). Only upcoming ones are looked at."""
    for it in all_items.values():
        if it.get("state") != "added" or not it.get("eventId") or _due_dt(it) + timedelta(days=1) < now:
            continue
        got = cal("GET", f"/calendars/{urllib.parse.quote(cid)}/events/{urllib.parse.quote(it['eventId'])}")
        if got is None or got.get("status") == "cancelled":
            it.update(state="removed", removedBy="calendar", removedAt=time.time())


# --- the mail ---
def when_text(it):
    due = _due_dt(it)
    day = due.strftime("%a %b ") + str(due.day)
    return day if it["allDay"] else day + due.strftime(", %H:%M")


def compose(new, moved):
    n = len(new) + len(moved)
    if moved and not new:
        subject = f"Deadlines: {len(moved)} moved on your calendar"
    else:
        subject = f"Added to your calendar: {len(new)} deadline{'s' if len(new) != 1 else ''}" + (f", {len(moved)} moved" if moved else "")
    text, rows = [], []
    e = html.escape
    for label, group in (("New", new), ("Moved", moved)):
        if not group:
            continue
        text.append(label)
        rows.append(f'<h3 style="font-size:14px;margin:16px 0 4px">{label}</h3>')
        for it in sorted(group, key=_due_dt):
            prefix = KINDS[it["kind"]][0]
            src = "university Gmail" if it.get("account") == "zen" else "Gmail"
            was = f" (was {when_text({**it, 'due': it['moveFrom']})})" if it in moved and it.get("moveFrom") else ""
            frm = " — ".join(x for x in (it.get("from"), it.get("subject")) if x)
            text.append(f"- {prefix}: {it['title']} — {when_text(it)}{was}" + (f"\n  From {src}: {frm}" if frm else "")
                        + (f"\n  Calendar: {it['htmlLink']}" if it.get("htmlLink") else "") + (f"\n  Mail: {it['gmail_url']}" if it.get("gmail_url") else ""))
            links = " · ".join(x for x in ((f'<a href="{e(it["htmlLink"])}">Calendar</a>' if it.get("htmlLink") else ""),
                                            (f'<a href="{e(it["gmail_url"])}">Mail</a>' if it.get("gmail_url") else "")) if x)
            rows.append(f'<p style="margin:6px 0"><b>{e(prefix)}: {e(it["title"])}</b> — {e(when_text(it))}{e(was)}'
                        + (f'<br><span style="color:#5c5d66">From {e(src)}: {e(frm)}</span>' if frm else "")
                        + (f"<br>{links}" if links else "") + "</p>")
    text.append(f"\nWrong one? Remove it on the Desk (it won't come back): {LINK}")
    page = (f'<div style="font-family:-apple-system,sans-serif;font-size:14px;line-height:1.5;color:#1c1d21">'
            f'<p>The Desk put {n} thing{"s" if n != 1 else ""} from your mail on the Deadlines calendar.</p>{"".join(rows)}'
            f'<p style="margin:16px 0 0;font-size:13px;color:#5c5d66">Wrong one? <a href="{e(LINK)}">Remove it on the Desk</a> (it won\'t come back).</p></div>')
    return subject, "\n".join(text), page


def _gmail(method, path, body=None, tries=2):
    import gmail_clean
    try:
        return gmail_clean.gmail(method, path, body, tries=tries)
    except gmail_clean.NotConnected:
        raise DeadlineError("Gmail is not connected. Connect it once in Inbox Cleanup") from None
    except (gmail_clean.GmailError, OSError):
        raise DeadlineError("Couldn't reach Gmail. The mail will be tried again") from None


def mail():
    """One mail for everything written since the last mail. A lost answer never makes a second one (Message-ID first)."""
    with LOCK:
        all_items = items()
        due = [it for it in all_items.values() if it.get("state") == "added" and not it.get("notified") and not it.get("write")]
        if not due:
            return 0
        new = [it for it in due if not it.get("moveFrom")]
        moved = [it for it in due if it.get("moveFrom")]
        s = settings()
        tag = s.get("mailTag") or uuid.uuid4().hex[:16]
        _save_settings(mailTag=tag)
    message_id = f"<deadlines-{tag}@tatsuki-desk.invalid>"
    subject, text, page = compose(new, moved)
    if fake():
        sent = _read("fake_mail.json", [])
        sent.append({"subject": subject, "text": text, "messageId": message_id})
        _write("fake_mail.json", sent)
    else:
        found = _gmail("GET", "/messages?maxResults=1&q=" + urllib.parse.quote("rfc822msgid:" + message_id.strip("<>")))
        if not found.get("messages"):
            me = str(_gmail("GET", "/profile").get("emailAddress") or "")
            if "@" not in me:
                raise DeadlineError("Couldn't read which Gmail is connected")
            msg = MIMEMultipart("alternative")
            msg["From"] = formataddr(("Tatsuki's Desk", me))
            msg["To"] = me
            msg["Subject"] = subject
            msg["Date"] = formatdate(localtime=True)
            msg["Message-ID"] = message_id
            msg["Auto-Submitted"] = "auto-generated"
            msg.attach(MIMEText(text, "plain", "utf-8"))
            msg.attach(MIMEText(page, "html", "utf-8"))
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            _gmail("POST", "/messages/import?neverMarkSpam=true&internalDateSource=receivedTime",
                   {"raw": raw, "labelIds": ["INBOX", "UNREAD"]}, tries=1)
    with LOCK:
        all_items = items()
        for it in due:
            cur = all_items.get(it["id"])
            if cur:
                cur.update(notified=True, notifiedAt=time.time())
                cur.pop("moveFrom", None)
        _write("items.json", all_items)
        _save_settings(mailTag="")
    return len(due)


# --- the page ---
def view():
    s = settings()
    now = _now()
    rows = []
    for it in items().values():
        if it.get("state") == "expired":
            continue
        rows.append({k: it.get(k) for k in ("id", "kind", "title", "due", "allDay", "soft", "account", "from", "subject",
                                            "summary", "gmail_url", "htmlLink", "state", "error", "fixed", "addedAt", "removedBy")}
                    | {"past": _due_dt(it) + (timedelta(days=1) if it["allDay"] else timedelta()) <= now,
                       "waiting": bool(it.get("write")) and it.get("state") != "removed"})
    rows.sort(key=lambda r: r["due"])
    return {"connected": connected(), "calendar": bool(s.get("calendarId")), "soft": s.get("soft", True),
            "error": CONNECT["error"] or s.get("error", ""), "connecting": CONNECT["running"],
            "lastRun": s.get("lastRun", 0), "items": rows, "fake": fake()}


def remove(iid):
    with LOCK:
        all_items = items()
        it = all_items.get(iid)
        if not it:
            raise DeadlineError("Not found")
        if it.get("eventId") and it.get("state") == "added":
            cid = settings().get("calendarId")
            if cid:
                cal("DELETE", f"/calendars/{urllib.parse.quote(cid)}/events/{urllib.parse.quote(it['eventId'])}")
        it.update(state="removed", removedBy="me", removedAt=time.time(), write="")
        _write("items.json", all_items)
        return it


def edit(iid, rec):
    """He fixes the date or the title. A fixed one is never moved again by later mails."""
    with LOCK:
        all_items = items()
        it = all_items.get(iid)
        if not it or it.get("state") == "removed":
            raise DeadlineError("Not found")
        if "due" in rec:
            try:
                due, all_day = parse_due(rec["due"])
            except ValueError:
                raise DeadlineError("Use a date like 2026-12-04 or 2026-12-04T23:59") from None
            it.update(due=due.date().isoformat() if all_day else due.isoformat(timespec="minutes"), allDay=all_day)
        if rec.get("title"):
            it["title"] = re.sub(r"\s+", " ", str(rec["title"])).strip()[:80]
        it.update(fixed=True, write="move" if it.get("eventId") else "create", notified=True)
        _write("items.json", all_items)
    run()
    return items().get(iid)


def put_settings(rec):
    kw = {}
    if "soft" in rec:
        kw["soft"] = bool(rec["soft"])
    return _save_settings(**kw)


def watch():
    while True:
        time.sleep(EVERY)
        try:
            if connected() and settings().get("calendarId"):
                run()
        except Exception as e:  # noqa: BLE001 — never stop the server's thread
            _save_settings(error=f"Deadlines check failed ({type(e).__name__})")


def handle(method, parts, body):
    """/api/deadlines… for server.py. Returns (status, json)."""
    try:
        if method == "GET" and not parts:
            return 200, view()
        if method == "POST" and parts == ["connect"]:
            connect()
            return 200, view()
        if method == "PUT" and parts == ["settings"]:
            put_settings(body or {})
            return 200, view()
        if method == "POST" and len(parts) == 2 and parts[1] == "remove":
            remove(parts[0])
            return 200, view()
        if method == "PUT" and len(parts) == 1:
            edit(parts[0], body or {})
            return 200, view()
    except NotConnected as e:
        return 400, {"error": str(e)}
    except DeadlineError as e:
        return 400, {"error": str(e)}
    return 404, {"error": "not found"}


def main(argv):
    cmd = argv[0] if argv else ""
    try:
        if cmd == "auth":
            res = connect()
            if res.get("url"):
                print("Opening Google's page in the browser. Pick your personal Google account and press Allow.\n" + res["url"], flush=True)
            if res.get("thread"):
                res["thread"].join(310)
            ok = connected() and settings().get("calendarId") and not CONNECT["error"]
            print("Connected: the Deadlines calendar is ready" if ok else "Could not connect: " + (CONNECT["error"] or "unknown"))
            return 0 if ok else 1
        if cmd == "status":
            v = view()
            print(json.dumps({k: v[k] for k in ("connected", "calendar", "soft", "error", "lastRun")} | {"items": len(v["items"])}, ensure_ascii=False))
            return 0
        if cmd == "add" and len(argv) == 2:
            with open(argv[1], encoding="utf-8") as f:
                print(json.dumps(add(json.load(f)), ensure_ascii=False))
            return 0
        if cmd == "sync":
            print(json.dumps(run(), ensure_ascii=False))
            return 0
    except (OSError, ValueError) as e:
        print(json.dumps({"error": type(e).__name__}))
        return 2
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.path.insert(0, ROOT)
    sys.exit(main(sys.argv[1:]))
