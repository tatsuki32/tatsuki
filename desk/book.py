#!/usr/bin/env python3
"""Tatsuki's Desk — Bookings: the rules and the list behind tany.im/book.

The booking page itself runs in the tany.im Worker (desk/data/tany/site/book.js), so it works while this Mac sleeps.
Desk is where the rules are written (which hours, how long, how many a day, private links) and where the bookings are read.
Guests' names and addresses stay in the Worker's D1 and in desk/data/book/ on this Mac (out of the GitHub copy).

  python3 desk/book.py auth [client_secret.json]   connect Google Calendar once (events + free/busy + the calendar list);
                                                   the permission is sent to the Worker sealed, nothing stays here
  python3 desk/book.py pull                        read the bookings and the Google status from the Worker
  python3 desk/book.py push-settings               send the rules on this Mac to the Worker
  python3 desk/book.py status                      what the page shows, as JSON
  python3 desk/book.py times [days]                the open times as text (what "Copy open times" copies)

The Worker's passphrase is in the keychain as tatsuki-desk-book (the Worker has it as BOOK_KEY).
"""
import base64
import glob
import hashlib
import http.server
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("DESK_BOOK_DIR") or os.path.join(ROOT, "data", "book")
WORKER = os.environ.get("DESK_BOOK_WORKER") or "https://tany.im"
PUBLIC = "https://tany.im"  # the address guests see, whatever the Worker is asked through
KEYCHAIN = "tatsuki-desk-book"
GOOGLE_SERVICES = ("tatsuki-desk-gmail", "tatsuki-desk-gsc", "tatsuki-desk-youtube")  # OAuth clients already on this Mac
SCOPES = "https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/calendar.freebusy https://www.googleapis.com/auth/calendar.calendarlist.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
PULL_EVERY = 300
LANGS = ("en", "ja", "ru", "vi")
_lock = threading.Lock()
_auth = {"running": False, "error": "", "doneAt": 0, "url": ""}  # url: the consent page while a connection is in progress


# ---------- files ----------

def _read(name, default):
    try:
        with open(os.path.join(DIR, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(name, data):
    os.makedirs(DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DIR, prefix=".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(DIR, name))


def mac_tz():
    """The IANA name of this Mac's time zone, or UTC."""
    try:
        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return os.environ.get("TZ") or "UTC"


# ---------- settings (the Worker cleans them again; this keeps the copy on the Mac sane) ----------

DEFAULTS = {
    "ownerName": "Tatsuki", "title": "{purpose} · {name}", "tz": "Asia/Ho_Chi_Minh",
    "hours": {str(i): [["09:00", "18:00"]] if 1 <= i <= 5 else [] for i in range(7)},
    "special": {}, "buffer": 15, "daysAhead": 14, "noticeHours": 12, "maxPerDay": 3, "step": 30,
    "purposes": [
        {"id": "first", "min": 30, "name": {"en": "First meeting", "ja": "はじめまして", "ru": "Знакомство", "vi": "Lần đầu gặp"}},
        {"id": "advice", "min": 45, "name": {"en": "Need advice", "ja": "相談したい", "ru": "Нужен совет", "vi": "Cần tư vấn"}},
        {"id": "decision", "min": 45, "name": {"en": "Need a decision", "ja": "決めたいことがある", "ru": "Нужно решение", "vi": "Cần quyết định"}},
        {"id": "chat", "min": 30, "name": {"en": "Just chat", "ja": "ざっくばらんに", "ru": "Просто поболтать", "vi": "Trò chuyện thoải mái"}},
    ],
    "checkCals": ["primary"], "writeCal": "primary", "meet": True, "replyTo": "", "contact": "", "updatedAt": 0,
}


def _hm(v):
    try:
        h, m = str(v).split(":")
        h, m = int(h), int(m)
        return h * 60 + m if 0 <= h < 24 and 0 <= m < 60 else None
    except (ValueError, AttributeError):
        return None


def _ranges(v):
    out = []
    for r in v if isinstance(v, list) else []:
        if isinstance(r, list) and len(r) == 2 and _hm(r[0]) is not None and _hm(r[1]) is not None and _hm(r[0]) != _hm(r[1]):
            out.append([str(r[0]), str(r[1])])
    return out[:4]


def _int(v, lo, hi, d):
    return v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else d


def clean_settings(raw, base=None):
    raw = raw if isinstance(raw, dict) else {}
    s = json.loads(json.dumps(base or DEFAULTS))
    if isinstance(raw.get("ownerName"), str) and raw["ownerName"].strip():
        s["ownerName"] = raw["ownerName"].strip()[:60]
    if isinstance(raw.get("title"), str) and raw["title"].strip():
        s["title"] = raw["title"].strip()[:120]
    if isinstance(raw.get("tz"), str) and raw["tz"]:
        s["tz"] = raw["tz"][:64]
    if isinstance(raw.get("hours"), dict):
        for i in range(7):
            if str(i) in raw["hours"] or i in raw["hours"]:
                s["hours"][str(i)] = _ranges(raw["hours"].get(str(i), raw["hours"].get(i)))
    if isinstance(raw.get("special"), dict):
        s["special"] = {k: _ranges(v) for k, v in list(raw["special"].items())[:200] if isinstance(k, str) and len(k) == 10 and k[4] == "-" and k[7] == "-"}
    s["buffer"] = _int(raw.get("buffer"), 0, 120, s["buffer"])
    s["daysAhead"] = _int(raw.get("daysAhead"), 1, 90, s["daysAhead"])
    s["noticeHours"] = _int(raw.get("noticeHours"), 0, 240, s["noticeHours"])
    s["maxPerDay"] = _int(raw.get("maxPerDay"), 1, 20, s["maxPerDay"])
    if raw.get("step") in (15, 30, 60):
        s["step"] = raw["step"]
    if isinstance(raw.get("purposes"), list):
        out, seen = [], set()
        for p in raw["purposes"]:
            if not isinstance(p, dict):
                continue
            pid = "".join(c for c in str(p.get("id") or "") if c.isalnum() or c in "-_")[:24]
            name = {l: str((p.get("name") or {}).get(l) or "").strip()[:60] for l in LANGS} if isinstance(p.get("name"), dict) else {}
            if not pid or pid in seen or not (name.get("en") or name.get("ja")):
                continue
            seen.add(pid)
            out.append({"id": pid, "min": _int(p.get("min"), 5, 240, 30), "name": name})
        if out:
            s["purposes"] = out[:8]
    if isinstance(raw.get("checkCals"), list):
        c = [x for x in raw["checkCals"] if isinstance(x, str) and 0 < len(x) < 200][:10]
        if c:
            s["checkCals"] = c
    if isinstance(raw.get("writeCal"), str) and 0 < len(raw["writeCal"]) < 200:
        s["writeCal"] = raw["writeCal"]
    if isinstance(raw.get("meet"), bool):
        s["meet"] = raw["meet"]
    if isinstance(raw.get("replyTo"), str):
        s["replyTo"] = raw["replyTo"].strip()[:200] if "@" in raw["replyTo"] else ""
    if isinstance(raw.get("contact"), str):
        s["contact"] = raw["contact"].strip()[:200]
    if isinstance(raw.get("updatedAt"), (int, float)) and not isinstance(raw.get("updatedAt"), bool):
        s["updatedAt"] = int(raw["updatedAt"])
    return s


def settings():
    return clean_settings(_read("settings.json", {}))


def purpose_name(s, pid, lang="en"):
    for p in s.get("purposes") or []:
        if p.get("id") == pid:
            return p["name"].get(lang) or p["name"].get("en") or p["name"].get("ja") or pid
    return pid


# ---------- the Worker ----------

def key():
    v = os.environ.get("DESK_BOOK_KEY")
    if v is not None:
        return v
    try:
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN, "-w"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def worker(method, path, body=None, timeout=30, auth=True):
    headers = {"Content-Type": "application/json", "User-Agent": "TatsukiDesk"}
    if auth:
        k = key()
        if not k:
            raise RuntimeError("Bookings aren't connected yet (keychain: tatsuki-desk-book). Run bash desk/book_setup.sh")
        headers["Authorization"] = "Bearer " + k
    req = urllib.request.Request(WORKER + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error")
        except (ValueError, AttributeError):
            msg = ""
        raise RuntimeError(msg or f"tany.im answered {e.code}") from None
    except (OSError, ValueError):
        raise RuntimeError("Could not reach tany.im. Check the connection and try again") from None


def _state():
    return _read("state.json", {"lastPull": 0, "error": "", "google": {}, "mail": None})


def pull():
    """Bookings, private links and the Google status from the Worker. The rules: the newer side wins (Desk's edits are pushed)."""
    st = _state()
    try:
        remote = worker("GET", "/book/api/desk/state")
    except RuntimeError as e:
        st["error"] = str(e)
        _write("state.json", st)
        return st
    with _lock:
        s = settings()
        rs = remote.get("settings") or {}
        if s.get("updatedAt", 0) > (rs.get("updatedAt") or 0) and os.path.exists(os.path.join(DIR, "settings.json")):
            try:
                rs = worker("PUT", "/book/api/desk/settings", s)
            except RuntimeError as e:
                st["error"] = str(e)
        if rs:
            s = clean_settings(rs)
            _write("settings.json", s)
        rows = []
        for b in remote.get("bookings") or []:
            if isinstance(b, dict) and b.get("id"):
                b["purposeName"] = purpose_name(s, b.get("purpose"))
                rows.append(b)
        _write("bookings.json", rows)
        links = _read("links.json", {})
        if not isinstance(links, dict):
            links = {}
        keep = {}
        for l in remote.get("links") or []:
            if isinstance(l, dict) and l.get("id"):
                local = links.get(l["id"]) or {}
                keep[l["id"]] = {**l, "key": local.get("key", ""), "url": local.get("url", "")}
        _write("links.json", keep)
        # open times picked by hand on tany.im/book/admin: when on, the weekly hours here are not what guests see
        pk = remote.get("picked") if isinstance(remote.get("picked"), dict) else {}
        now = int(time.time() * 1000)
        ahead = [w for w in pk.get("windows") or [] if isinstance(w, dict) and isinstance(w.get("e"), (int, float)) and w["e"] > now]
        st.update(lastPull=now, error="", google=remote.get("google") or {}, mail=remote.get("mail"), picked={"on": pk.get("on") is True, "ahead": len(ahead)})
        _write("state.json", st)
    return st


def push_settings():
    s = settings()
    out = worker("PUT", "/book/api/desk/settings", s)
    with _lock:
        _write("settings.json", clean_settings(out))
    return out


def save_settings(rec):
    """From the page: the rules change here first, then go to the Worker right away."""
    with _lock:
        s = clean_settings(rec, settings())
        s["updatedAt"] = int(time.time() * 1000)
        _write("settings.json", s)
    try:
        push_settings()
    except RuntimeError as e:
        return 200, {**view(), "warning": "Saved on this Mac. Not on tany.im yet: " + str(e)}
    return 200, view()


# ---------- what the page shows ----------

def view():
    st = _state()
    s = settings()
    links = _read("links.json", {})
    k = bool(key())
    return {
        "settings": s, "bookings": _read("bookings.json", []), "links": sorted((links if isinstance(links, dict) else {}).values(), key=lambda l: -(l.get("created") or 0)),
        "connected": k, "google": st.get("google") or {}, "mail": st.get("mail"), "lastPull": st.get("lastPull") or 0, "error": st.get("error") or "",
        "picked": st.get("picked") or {"on": False, "ahead": 0},
        "shareUrl": PUBLIC + "/book", "macTz": mac_tz(), "auth": dict(_auth), "now": int(time.time() * 1000),
    }


def home():
    """One line for Home: the next booking, or the problem that stops bookings."""
    st, s = _state(), settings()
    g = st.get("google") or {}
    problem = ""
    if key() and st.get("lastPull"):
        if not g.get("connected"):
            problem = "Bookings: Google Calendar is not connected yet, so tany.im/book is paused"
        elif g.get("ok") is False:
            problem = "Bookings: Google Calendar stopped answering, so tany.im/book is paused"
        elif st.get("mail") is False:
            problem = "Bookings: confirmation emails are off (RESEND_KEY or BOOK_FROM is missing on tany.im)"
    now = time.time() * 1000
    nxt = [b for b in _read("bookings.json", []) if b.get("status") == "booked" and (b.get("end") or 0) > now]
    nxt.sort(key=lambda b: b.get("start") or 0)
    n = nxt[0] if nxt else None
    return {"problem": problem, "next": {"start": n["start"], "end": n["end"], "name": n.get("name"), "purpose": n.get("purposeName") or purpose_name(s, n.get("purpose"))} if n else None, "count": len(nxt)}


def open_times(days=7, tz=None):
    """The open times as text, in this Mac's time zone (or tz), from the public slots answer."""
    try:
        d = worker("GET", "/book/api/slots", auth=False)
    except RuntimeError as e:
        return {"error": str(e)}
    if d.get("down"):
        return {"error": "Booking is paused on tany.im right now"}
    name = tz or mac_tz()
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(name)
    except Exception:
        zone, name = timezone.utc, "UTC"
    days = max(1, min(int(days or 7), 60))
    now = datetime.now(zone)
    until = (now + timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    by_day = {}
    for f in d.get("free") or []:
        s_, e_ = datetime.fromtimestamp(f["s"] / 1000, zone), datetime.fromtimestamp(f["e"] / 1000, zone)
        cur = s_
        while cur < e_ and cur < until:
            day_end = (cur + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = min(e_, day_end)
            if (end - cur).total_seconds() >= 15 * 60:
                by_day.setdefault(cur.date(), []).append((cur, end))
            cur = end
    off = now.utcoffset() or timedelta(0)
    sign, mins = ("+" if off >= timedelta(0) else "-"), abs(int(off.total_seconds()) // 60)
    gmt = f"GMT{sign}{mins // 60}" + (f":{mins % 60:02d}" if mins % 60 else "")
    lines = [f"{d.get('ownerName') or 'My'} open times ({name.split('/')[-1].replace('_', ' ')} {gmt}) · book at {PUBLIC}/book"]
    for day in sorted(by_day):
        hm = lambda x: "24:00" if x.date() != day else x.strftime("%H:%M")
        lines.append(f"{day.strftime('%a, %b %-d')}  " + ", ".join(f"{a.strftime('%H:%M')}–{hm(b)}" for a, b in by_day[day]))
    if len(lines) == 1:
        lines.append("No open times in the next %d days." % days)
    return {"text": "\n".join(lines), "tz": name, "days": days}


# ---------- actions ----------

def cancel(bid):
    out = worker("POST", f"/book/api/desk/bookings/{urllib.parse.quote(str(bid))}/cancel", {})
    pull()
    return out


def make_link(rec):
    rec = rec if isinstance(rec, dict) else {}
    windows = []
    for w in rec.get("windows") or []:
        if isinstance(w, dict) and isinstance(w.get("s"), (int, float)) and isinstance(w.get("e"), (int, float)) and w["e"] > w["s"]:
            windows.append({"s": int(w["s"]), "e": int(w["e"])})
    body = {"note": str(rec.get("note") or "")[:120], "windows": windows[:50], "expires": int(rec["expires"]) if isinstance(rec.get("expires"), (int, float)) and rec["expires"] > 0 else None}
    out = worker("POST", "/book/api/desk/links", body)
    with _lock:
        links = _read("links.json", {})
        if not isinstance(links, dict):
            links = {}
        links[out["id"]] = {**out, "off": False, "used": 0}
        _write("links.json", links)
    return out


def link_off(lid):
    out = worker("POST", f"/book/api/desk/links/{urllib.parse.quote(str(lid))}/off", {})
    with _lock:
        links = _read("links.json", {})
        if isinstance(links, dict) and lid in links:
            links[lid]["off"] = True
            _write("links.json", links)
    return out


def calendars():
    return worker("GET", "/book/api/desk/calendars")


# ---------- Google Calendar permission (obtained here, kept only in the Worker, sealed) ----------

def _kc_get(service, account):
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", service, "-a", account, "-w"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _client(path=None):
    if path:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        block = raw.get("installed") or raw.get("web") or {}
        if block.get("client_id") and block.get("client_secret"):
            return {"id": block["client_id"], "secret": block["client_secret"]}
        raise ValueError("no client id in " + path)
    for svc in GOOGLE_SERVICES:
        raw = _kc_get(svc, "client")
        if raw:
            try:
                return json.loads(raw)
            except ValueError:
                pass
    found = sorted(glob.glob(os.path.expanduser("~/Downloads/client_secret*.json")), key=os.path.getmtime)
    return _client(found[-1]) if found else None


def _form(url, fields):
    req = urllib.request.Request(url, urllib.parse.urlencode(fields).encode(), {"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def auth(path=None, log=print):
    """Opens Google's consent page, trades the code for a refresh token and hands it to the Worker. Returns 0 when connected."""
    client = _client(path)
    if not client:
        log("No Google Cloud OAuth client found. Connect Inbox Cleanup first (python3 desk/gmail_clean.py auth) or pass the client_secret JSON.")
        return 1
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
            self.wfile.write("<p>Google Calendar connected for tany.im/book. You can close this window.</p>".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Once)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client["id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
    log("Opening the Google sign-in page. If it does not open, visit this URL.\n" + url)
    _auth["url"] = url
    webbrowser.open(url)
    deadline = time.time() + 600
    srv.timeout = 600
    while "code" not in got and time.time() < deadline:
        srv.handle_request()
    srv.server_close()
    if not got.get("code"):
        log("Could not connect: " + (got.get("error") or "timed out"))
        return 1
    try:
        data = _form(TOKEN_URL, {"code": got["code"], "client_id": client["id"], "client_secret": client["secret"],
                                 "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
    except (OSError, ValueError) as e:
        log("Google did not give a token: " + str(e))
        return 1
    if not data.get("refresh_token"):
        log("No refresh token came back. Please try again.")
        return 1
    worker("POST", "/book/api/desk/google", {"client": client, "refresh": data["refresh_token"]}, timeout=60)
    log("Connected. The permission now lives on tany.im, sealed; nothing was kept on this Mac.")
    pull()
    return 0


def auth_in_background():
    """From the page: the browser opens on this Mac; the page polls `auth` in view()."""
    if _auth["running"]:
        return 200, {"ok": True, "running": True}

    def run():
        _auth.update(running=True, error="")
        msgs = []
        try:
            rc = auth(log=msgs.append)
            _auth["error"] = "" if rc == 0 else (msgs[-1] if msgs else "Could not connect")
        except Exception as e:  # the thread must end with a message, whatever happened
            _auth["error"] = str(e)
        _auth.update(running=False, doneAt=int(time.time() * 1000), url="")

    threading.Thread(target=run, daemon=True).start()
    return 200, {"ok": True, "running": True}


# ---------- requests from server.py ----------

def handle(method, parts, rec):
    """parts: the path after /api/book/. Returns (status, body)."""
    try:
        if method == "GET" and not parts:
            return 200, view()
        if method == "GET" and parts == ["home"]:
            return 200, home()
        if method == "GET" and parts == ["times"]:
            return 200, open_times(rec.get("days") if isinstance(rec, dict) else 7, rec.get("tz") if isinstance(rec, dict) else None)
        if method == "GET" and parts == ["calendars"]:
            return 200, calendars()
        if method == "PUT" and parts == ["settings"]:
            return save_settings(rec)
        if method == "POST" and parts == ["refresh"]:
            pull()
            return 200, view()
        if method == "POST" and parts == ["google"]:
            return auth_in_background()
        if method == "POST" and parts == ["links"]:
            return 200, make_link(rec)
        if method == "POST" and len(parts) == 3 and parts[0] == "links" and parts[2] == "off":
            link_off(parts[1])
            return 200, view()
        if method == "POST" and len(parts) == 2 and parts[1] == "cancel":
            cancel(parts[0])
            return 200, view()
    except RuntimeError as e:
        return 502, {"error": str(e)}
    return 404, {"error": "not found"}


def watch():
    """Every 5 minutes: new bookings land in bookings.json, where notify.py finds them."""
    while True:
        try:
            if key():
                pull()
        except Exception as e:  # a bad answer must not end the watch
            print("book:", type(e).__name__, e, file=sys.stderr, flush=True)
        time.sleep(PULL_EVERY)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "auth":
        sys.exit(auth(sys.argv[2] if len(sys.argv) > 2 else None))
    elif cmd == "pull":
        print(json.dumps(pull(), ensure_ascii=False, indent=1))
    elif cmd == "push-settings":
        print(json.dumps(push_settings(), ensure_ascii=False, indent=1))
    elif cmd == "status":
        print(json.dumps(view(), ensure_ascii=False, indent=1))
    elif cmd == "times":
        print(open_times(int(sys.argv[2]) if len(sys.argv) > 2 else 7).get("text") or open_times().get("error"))
    else:
        print(__doc__)
