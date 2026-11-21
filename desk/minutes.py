#!/usr/bin/env python3
"""Tatsuki's Desk — minutes (議事録).

A minute is one meeting's notes. Recordings made in Transcribe become minutes by themselves: when a
recording's text is ready (status done or partial), this module reads it from the transcriber on 127.0.0.1:8791,
asks Claude once for a summary (English, the same rule as the rest of Desk), and saves the minute here.
A minute can be linked to an event on the Calendar view. Writing the link into that Google Calendar event
is a separate, explicit action, and it needs one-time permission to write events (python3 desk/minutes.py auth).

Files: desk/data/minutes/<id>.json (private; backup_data.py ignores the folder).
Text is sent to Claude only for the summary. The Google write sends the link to the minute, never its text.

Notes added to Transcribe as text (pasted, Word, PDF, Google Docs; 2026-11-21) become minutes too, without any call to
Claude: the minute shows the notes, and once "Make minutes" is pressed (here or in Transcribe) the transcriber's
minutes (Sonnet 5.5) replace the "not made yet" part on the next pass, unless the minute's text was edited here.

  python3 minutes.py sync     make minutes for ready recordings that do not have one yet
  python3 minutes.py auth     allow Desk to write the minute link into Google Calendar events
"""
import base64
import hashlib
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
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("MINUTES_DIR") or os.path.join(ROOT, "data", "minutes")
CAL_DOC = os.environ.get("MINUTES_CAL_DOC") or os.path.join(ROOT, "data", "docs", "calendar", "upcoming.json")
TRANSCRIBE = os.environ.get("TRANSCRIBE_URL") or "http://127.0.0.1:8791"
LINK_BASE = os.environ.get("MINUTES_LINK_BASE") or "http://localhost:4747"
MODEL = "claude-haiku-5-5"
TRANSCRIPT_MAX = 60000  # characters sent to Claude; a longer text is cut and says so
PASTE_MAX = 20000       # characters kept in a minute made without a summary
LINE_PREFIX = "Minutes (Desk): "
SERVICE = "tatsuki-desk-gcal-write"
GMAIL_SERVICE = "tatsuki-desk-gmail"  # the same Google Cloud OAuth client as Inbox Cleanup
SCOPES = "https://www.googleapis.com/auth/calendar.events"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CAL_API = "https://www.googleapis.com/calendar/v3"
STATUSES = ("draft", "ready")
ID_RE = re.compile(r"^[a-f0-9]{32}$")
LOCK = threading.Lock()
_token = {"value": None, "at": 0}


class MinutesError(Exception):
    """A message that is safe to show on the page: it never carries the text of a minute or a key."""


# ---------- storage ----------

def _path(mid):
    return os.path.join(DIR, mid + ".json")


def _read(mid):
    if not ID_RE.match(mid or ""):
        return None
    try:
        with open(_path(mid), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(rec):
    os.makedirs(DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DIR, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path(rec["id"]))


def _all(deleted=False):
    out = []
    if not os.path.isdir(DIR):
        return out
    for name in os.listdir(DIR):
        if name.endswith(".json") and not name.startswith("."):
            rec = _read(name[:-5])
            if rec and (deleted or not rec.get("deleted")):
                out.append(rec)
    return out


def _text(v, n):
    return str(v or "").strip()[:n]


def _event(v):
    """A link to a calendar event. Only the fields the page shows and the write needs are kept."""
    if not v:
        return None
    if not isinstance(v, dict):
        raise MinutesError("The event link is not valid.")
    return {"id": _text(v.get("id"), 300), "uid": _text(v.get("uid"), 300), "title": _text(v.get("title"), 200),
            "start": _text(v.get("start"), 40), "end": _text(v.get("end"), 40), "allDay": bool(v.get("allDay"))}


def view():
    with LOCK:
        rows = sorted(_all(), key=lambda r: r.get("at") or r.get("created") or 0, reverse=True)
    return {"minutes": rows, "transcriber": _transcriber_up()}


def get(mid):
    with LOCK:
        rec = _read(mid)
    if not rec or rec.get("deleted"):
        raise MinutesError("Not found")
    return rec


def create(rec):
    rec = rec or {}
    now = time.time()
    mid = uuid.uuid4().hex
    out = {"id": mid, "title": _text(rec.get("title"), 200) or "Untitled meeting", "body": "",
           "status": "draft", "place": _text(rec.get("place"), 200), "at": _at(rec.get("at"), now),
           "source": {"kind": "manual"}, "event": None, "calendar": None, "created": now, "updated": now}
    with LOCK:
        _write(out)
    return out


def put(mid, rec):
    rec = rec or {}
    with LOCK:
        cur = _read(mid)
        if not cur or cur.get("deleted"):
            raise MinutesError("Not found")
        if "title" in rec:
            cur["title"] = _text(rec.get("title"), 200) or "Untitled meeting"
        if "body" in rec:
            cur["body"] = str(rec.get("body") or "")[:200000]
        if "place" in rec:
            cur["place"] = _text(rec.get("place"), 200)
        if "status" in rec:
            if rec.get("status") not in STATUSES:
                raise MinutesError("Status must be draft or ready.")
            cur["status"] = rec["status"]
        if "at" in rec:
            cur["at"] = _at(rec.get("at"), cur.get("at") or cur["created"])
        if "event" in rec:
            new = _event(rec.get("event"))
            old = cur.get("event") or {}
            if (old.get("id") or None) != (new or {}).get("id"):
                cur["calendar"] = None  # a different event: the old link in Google is not this one any more
            cur["event"] = new
        cur["updated"] = time.time()
        _write(cur)
    return cur


def remove(mid):
    with LOCK:
        cur = _read(mid)
        if not cur:
            raise MinutesError("Not found")
        # the text is erased now; the id stays so a late sync cannot make it again
        cur = {"id": cur["id"], "deleted": True, "source": cur.get("source"), "created": cur.get("created"),
               "updated": time.time(), "title": "", "body": "", "event": None, "calendar": None}
        _write(cur)
    return {"ok": True}


def _at(v, default):
    try:
        return float(v) if v not in (None, "") else float(default)
    except (TypeError, ValueError):
        return float(default)


# ---------- the transcriber (read only) ----------

def _transcriber_up():
    try:
        _http_json(f"{TRANSCRIBE}/api/status")
        return True
    except (OSError, ValueError):
        return False


def _http_json(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.load(r)


def _http_text(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def sync():
    """Makes a minute for each ready recording that has none. Returns how many were made."""
    try:
        sessions = _http_json(f"{TRANSCRIBE}/api/sessions")
    except (OSError, ValueError):
        return 0  # the transcriber is not running; nothing is lost, the next pass tries again
    with LOCK:
        # deleted minutes count too, so a recording a person removed is never made into a minute again
        known = {r.get("source", {}).get("sid") for r in _all(deleted=True) if (r.get("source") or {}).get("kind") == "transcribe"}
    made = 0
    for s in sessions:
        sid = s.get("id") or ""
        if s.get("kind") == "text" and ID_RE.match(sid):
            made += _sync_notes(s, sid in known)
            known.add(sid)
            continue
        if s.get("status") not in ("done", "partial") or sid in known or not ID_RE.match(sid):
            continue
        try:
            text = _http_text(f"{TRANSCRIBE}/api/sessions/{sid}/transcript.txt?ts=1")
        except (OSError, ValueError):
            continue
        summary = summarize(text, s.get("title") or "") if text.strip() else None
        now = time.time()
        rec = {"id": uuid.uuid4().hex,
               "title": _text(s.get("title"), 200) or _text((summary or {}).get("title"), 200) or "Untitled meeting",
               "body": build_body(summary, text), "status": "draft", "place": "",
               "at": float(s.get("created") or now), "source": {"kind": "transcribe", "sid": sid},
               "event": None, "calendar": None, "created": now, "updated": now}
        with LOCK:
            _write(rec)
        known.add(sid)
        made += 1
    return made


def _by_sid(sid):
    for r in _all(deleted=True):
        src = r.get("source") or {}
        if src.get("kind") == "transcribe" and src.get("sid") == sid:
            return r
    return None


def _noon(met, default):
    try:
        return datetime.strptime(met + " 12:00", "%Y-%m-%d %H:%M").timestamp()
    except (TypeError, ValueError):
        return float(default)


def _stamp(m):
    return max(float((m or {}).get("at") or 0), float((m or {}).get("edited") or 0))


def _sync_notes(s, known):
    """A text note from Transcribe. Returns 1 when a new minute was made. No call to Claude happens here."""
    sid = s["id"]
    if known:
        with LOCK:
            rec = _read((_by_sid(sid) or {}).get("id", ""))
        src = (rec or {}).get("source") or {}
        # made before notes were known here: an empty "transcript" entry. Rebuilt once, if nobody touched it.
        old = rec and not rec.get("deleted") and src.get("from") != "notes"
        if old and rec.get("updated") == rec.get("created") and not rec.get("event") and rec.get("body", "").rstrip().endswith("## Transcript"):
            try:
                full = _http_json(f"{TRANSCRIBE}/api/sessions/{sid}")
            except (OSError, ValueError):
                return 0
            m, now = full.get("minutes"), time.time()
            with LOCK:
                cur = _read(rec["id"])
                cur["body"] = notes_body(m, full.get("text") or "")
                cur["at"] = _noon(full.get("met"), full.get("created") or cur.get("at") or now)
                cur["source"] = {"kind": "transcribe", "sid": sid, "from": "notes", "minutesAt": _stamp(m), "fp": _fp(cur["body"])}
                cur["updated"] = cur["created"]  # still counts as untouched
                _write(cur)
            return 0
        if old:
            return 0
        # refresh only when the transcriber has newer minutes and the text here is still the one this pass wrote
        # ("Make minutes" pressed here sets force, so the new minutes replace an edited text too)
        edited = _fp(rec.get("body") if rec else "") != src.get("fp")
        if not rec or rec.get("deleted") or not s.get("has_minutes") or (edited and not src.get("force")):
            return 0
    try:
        full = _http_json(f"{TRANSCRIBE}/api/sessions/{sid}")
    except (OSError, ValueError):
        return 0
    m = full.get("minutes")
    now = time.time()
    if known:
        if _stamp(m) <= src.get("minutesAt", 0):
            return 0
        with LOCK:
            rec = _read(rec["id"])
            rec["body"] = notes_body(m, full.get("text") or "")
            rec["source"] = {**{k: v for k, v in src.items() if k != "force"}, "minutesAt": _stamp(m), "fp": _fp(rec["body"])}
            rec["updated"] = now
            _write(rec)
        return 0
    body = notes_body(m, full.get("text") or "")
    rec = {"id": uuid.uuid4().hex, "title": _text(full.get("title"), 200) or "Untitled meeting",
           "body": body, "status": "draft", "place": "",
           "at": _noon(full.get("met"), full.get("created") or now),
           "source": {"kind": "transcribe", "sid": sid, "from": "notes", "minutesAt": _stamp(m), "fp": _fp(body)},
           "event": None, "calendar": None, "created": now, "updated": now}
    with LOCK:
        _write(rec)
    return 1


def notes_body(m, text):
    """Markdown for a note from Transcribe: its minutes when there are some, then the notes themselves."""
    parts = []
    if m:
        if m.get("summary"):
            parts.append("## Summary\n" + "\n".join("- " + x for x in m["summary"]))
        if m.get("decisions"):
            parts.append("## Decisions\n" + "\n".join("- " + x for x in m["decisions"]))
        if m.get("actions"):
            rows = []
            for a in m["actions"]:
                extra = " — " + a["who"] if a.get("who") else ""
                extra += f" (due {a['due']})" if a.get("due") else ""
                rows.append(f"- [ ] {a['what']}{extra}")
            parts.append("## Next steps\n" + "\n".join(rows))
        if m.get("next"):
            parts.append("## For next time\n" + "\n".join("- " + x for x in m["next"]))
    else:
        parts.append("## Summary\nNot made yet. Press \"Make minutes\" to get a summary, decisions and next steps.")
    body = text.strip()
    more = "\n..." if len(body) > PASTE_MAX else ""
    parts.append("## Original notes\n" + body[:PASTE_MAX] + more)
    return "\n\n".join(parts)


def make_from_notes(mid):
    """Asks Transcribe to make minutes for the note behind this minute (it sends the text to Claude, once)."""
    rec = get(mid)
    src = rec.get("source") or {}
    if src.get("kind") != "transcribe" or not src.get("sid"):
        raise MinutesError("This minute did not come from Transcribe.")
    req = urllib.request.Request(f"{TRANSCRIBE}/api/sessions/{src['sid']}/minutes", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error") or ""
        except (ValueError, OSError):
            msg = ""
        raise MinutesError(msg or f"Transcribe answered {e.code}.") from None
    except (OSError, ValueError):
        raise MinutesError("Transcribe is not running. Open the Transcribe view once, then try again.") from None
    with LOCK:  # pressing it means the new minutes may replace an edited text
        cur = _read(mid)
        cur["source"] = {**(cur.get("source") or {}), "force": True}
        _write(cur)
    return {"ok": True}


def _fp(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def watch():
    """Run by the Desk server on 4747: a check every minute. Errors are printed without any text of a minute."""
    while True:
        try:
            sync()
        except Exception as e:  # keep the loop alive; the message names no content
            print(f"[minutes] sync failed: {type(e).__name__}", flush=True)
        time.sleep(60)


# ---------- the summary (Claude) ----------

def _anthropic_key():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    if os.environ.get("MINUTES_DIR"):
        return None  # tests never touch the keychain
    sys.path.insert(0, ROOT)
    try:
        import lifelog
        return lifelog.api_key()
    except Exception:
        return None


PROMPT = """You write meeting minutes for Tatsuki's Desk. The transcript below was made from a recording.
Speakers' names appear as "Name: words" when known. Write everything in English. Keep names and terms as they were said.
Do not invent facts. If something is not in the transcript, leave it out. Give no advice.

Return only one JSON object with these keys:
- "title": a short title, at most 8 words
- "summary": 3 to 6 short bullets of what was discussed
- "decisions": bullets of what was decided (may be empty)
- "todos": a list of {"text": "...", "owner": "...", "due": "YYYY-MM-DD or empty"} for things someone agreed to do (may be empty)
- "topics": a list of {"heading": "...", "points": ["..."]} for the main topics, at most 5

Transcript:
"""


def summarize(text, title=""):
    """Claude's summary as a dict, or None when there is no key, no network, or no usable answer."""
    if os.environ.get("MINUTES_FAKE") == "1":
        return sanitize({"title": title, "summary": ["Test summary"], "decisions": [], "todos": [
            {"text": "Check the test result", "owner": "", "due": ""}], "topics": []})
    key = _anthropic_key()
    if not key:
        return None
    body = text.strip()
    if len(body) > TRANSCRIPT_MAX:
        body = body[:TRANSCRIPT_MAX] + "\n[The transcript is longer; the rest is not included.]"
    payload = {"model": MODEL, "max_tokens": 3000,
               "messages": [{"role": "user", "content": PROMPT + body}]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(payload).encode("utf-8"),
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.load(r)
        out = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        parsed = json.loads(out[out.find("{"): out.rfind("}") + 1])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return sanitize(parsed)


def sanitize(j):
    if not isinstance(j, dict):
        return None

    def lines(v, n=12):
        return [_text(x, 400) for x in (v if isinstance(v, list) else []) if _text(x, 400)][:n]

    todos = []
    for t in (j.get("todos") if isinstance(j.get("todos"), list) else [])[:20]:
        if isinstance(t, dict) and _text(t.get("text"), 300):
            todos.append({"text": _text(t.get("text"), 300), "owner": _text(t.get("owner"), 80),
                          "due": _text(t.get("due"), 10)})
    topics = []
    for t in (j.get("topics") if isinstance(j.get("topics"), list) else [])[:5]:
        if isinstance(t, dict) and _text(t.get("heading"), 120):
            topics.append({"heading": _text(t.get("heading"), 120), "points": lines(t.get("points"), 8)})
    return {"title": _text(j.get("title"), 200), "summary": lines(j.get("summary")),
            "decisions": lines(j.get("decisions")), "todos": todos, "topics": topics}


def build_body(s, text):
    """The minute's text in the simple markdown the page draws: ## headings, bullets and - [ ] checkboxes."""
    if not s:
        body = text.strip()
        more = "\n..." if len(body) > PASTE_MAX else ""
        return ("## Summary\nNot made yet. Claude writes the summary when it can read the transcript.\n\n"
                "## Transcript\n" + body[:PASTE_MAX] + more)
    parts = []
    if s["summary"]:
        parts.append("## Summary\n" + "\n".join("- " + x for x in s["summary"]))
    if s["decisions"]:
        parts.append("## Decisions\n" + "\n".join("- " + x for x in s["decisions"]))
    if s["todos"]:
        rows = []
        for t in s["todos"]:
            extra = " — " + t["owner"] if t["owner"] else ""
            extra += f" (due {t['due']})" if t["due"] else ""
            rows.append(f"- [ ] {t['text']}{extra}")
        parts.append("## Next steps\n" + "\n".join(rows))
    for t in s["topics"]:
        parts.append(f"## {t['heading']}\n" + "\n".join("- " + p for p in t["points"]))
    return "\n\n".join(parts) if parts else "## Summary\nNo summary came out of this transcript."


# ---------- the Google Calendar link (write, explicit) ----------

def _kc_get(service, account):
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", service, "-a", account, "-w"],
                           capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _kc_set(service, account, value):
    subprocess.run(["security", "add-generic-password", "-U", "-s", service, "-a", account, "-w", value],
                   capture_output=True, timeout=20, check=True)


def _client():
    raw = _kc_get(SERVICE, "client") or _kc_get(GMAIL_SERVICE, "client")
    return json.loads(raw) if raw else None


def _form(url, fields):
    req = urllib.request.Request(url, urllib.parse.urlencode(fields).encode(), {"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def auth():
    """Opens Google's page in the default browser and waits (10 minutes) for the person to allow it."""
    client = _client()
    if not client:
        return "No Google Cloud OAuth client on this Mac. Connect Inbox Cleanup first."
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
            self.wfile.write("<p>Desk can now add minute links to Google Calendar. You can close this window.</p>".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Once)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    webbrowser.open(AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client["id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256"}))
    deadline = time.time() + 600
    srv.timeout = 600
    while "code" not in got and time.time() < deadline:
        srv.handle_request()
    srv.server_close()
    if not got.get("code"):
        return "Not connected: " + (got.get("error") or "timed out")
    data = _form(TOKEN_URL, {"code": got["code"], "client_id": client["id"], "client_secret": client["secret"],
                             "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
    if not data.get("refresh_token"):
        return "No refresh token came back. Try again."
    _kc_set(SERVICE, "client", json.dumps(client))
    _kc_set(SERVICE, "refresh", data["refresh_token"])
    _token.update(value=None, at=0)
    return ""


def _access():
    if _token["value"] and time.time() - _token["at"] < 3000:
        return _token["value"]
    client, refresh = _client(), _kc_get(SERVICE, "refresh")
    if not client or not refresh:
        raise MinutesError("Google Calendar writing is not allowed yet. Run: python3 desk/minutes.py auth")
    try:
        data = _form(TOKEN_URL, {"client_id": client["id"], "client_secret": client["secret"],
                                 "refresh_token": refresh, "grant_type": "refresh_token"})
    except (OSError, ValueError):
        raise MinutesError("Google Calendar writing could not be checked. Try again, or run: python3 desk/minutes.py auth")
    _token.update(value=data["access_token"], at=time.time())
    return _token["value"]


def _api(method, url, body=None):
    req = urllib.request.Request(url, json.dumps(body).encode("utf-8") if body is not None else None, method=method,
                                 headers={"Authorization": "Bearer " + _access(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise MinutesError(f"Google Calendar answered {e.code}. The minute link was not written.")
    except OSError:
        raise MinutesError("Google Calendar could not be reached.")


def _start_ms(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _pick(items, start):
    """The Google event for this occurrence: the one whose start matches, else the first."""
    want = _start_ms(start)
    if want is None:
        return items[0]

    def gap(it):
        s = it.get("start", {})
        ms = _start_ms(s.get("dateTime") or s.get("date"))
        return abs(ms - want) if ms is not None else float("inf")
    best = min(items, key=gap)
    return best


def write_calendar(mid):
    """Writes the minute's link into the description of its Google Calendar event, keeping the rest of it."""
    rec = get(mid)
    ev = rec.get("event")
    if not ev or not ev.get("uid"):
        raise MinutesError("Link the minute to a calendar event first.")
    q = urllib.parse.urlencode({"iCalUID": ev["uid"], "maxResults": 50, "showDeleted": "false"})
    items = _api("GET", f"{CAL_API}/calendars/primary/events?{q}").get("items", [])
    if not items:
        raise MinutesError("That event was not found in the primary Google Calendar.")
    target = _pick(items, ev.get("start"))
    desc = target.get("description") or ""
    kept = "\n".join(line for line in desc.splitlines() if not line.startswith(LINE_PREFIX)).rstrip()
    new = (kept + "\n\n" if kept else "") + LINE_PREFIX + f"{LINK_BASE}/#minutes/{mid}"
    _api("PATCH", f"{CAL_API}/calendars/primary/events/{urllib.parse.quote(target['id'])}", {"description": new})
    with LOCK:
        cur = _read(mid)
        cur["calendar"] = {"googleId": target["id"], "at": time.time(), "ok": True, "error": ""}
        cur["updated"] = time.time()
        _write(cur)
    return cur


# ---------- command line ----------

if __name__ == "__main__":
    if sys.argv[1:] == ["sync"]:
        print(f"made {sync()} minute(s)")
    elif sys.argv[1:] == ["auth"]:
        print(auth() or "Connected. Desk can add minute links to Google Calendar.")
    else:
        print(json.dumps(view(), ensure_ascii=False, indent=2))
