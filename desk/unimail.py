#!/usr/bin/env python3
"""Tatsuki's Desk — the university Gmail in Replies, next to the personal Gmail.

The personal Gmail is read by the Claude Gmail connector, which holds one account. The university account is read here,
straight from the Gmail API, by the scheduled task desk-morning-mail (4:30 and 15:30):

  python3 unimail.py auth                 connect once (Google's page in the browser; pick the university account)
  python3 unimail.py status               connected? which account? (JSON)
  python3 unimail.py pending [DAYS]       inbox threads (default 14 days) that may need an answer, as JSON on stdout
  python3 unimail.py draft FILE.json      makes a reply draft in the university Gmail; prints {"id","view_url"}
  python3 unimail.py replied THREAD...    {"<thread id>": true} when his own message is the newest in the thread

Permissions: gmail.readonly and gmail.compose (drafts). Nothing here sends, deletes or changes labels.
The OAuth client is the one of the mail cleanup ("tatsuki-desk-gmail" in the keychain); the refresh token lives in the
keychain as "tatsuki-desk-unimail". Mail text is printed for the scheduled task only and never written to a file:
desk/data/unimail/ holds the account address and the last run, and stays out of the backup (backup_data.py).
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
import webbrowser
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("UNIMAIL_DIR") or os.path.join(ROOT, "data", "unimail")
CLIENT_SERVICE = "tatsuki-desk-gmail"
SERVICE = "tatsuki-desk-unimail"
SCOPES = "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.compose"
API = os.environ.get("UNIMAIL_API") or "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
ACCOUNT = "zen"  # the mark on the Desk's items and tasks
QUERY = "in:inbox newer_than:{days}d -category:promotions -category:social -in:chats"
MAX_THREADS = 60
TEXT_MAX = 3000  # per message handed to the scheduled task
BULK_FROM = re.compile(r"no-?reply|do-?not-?reply|noreply|mailer-daemon|postmaster|notification|notify|"
                       r"forms-receipts|calendar-notification|@docs\.google\.com|@classroom\.google\.com", re.I)
LOCK = threading.Lock()
TOKEN = {"value": "", "until": 0}
CONNECT = {"running": False, "url": "", "error": ""}


class NotConnected(Exception):
    pass


class MailError(Exception):
    pass


# --- keychain ---
def _kc_get(service, account):
    if os.environ.get("UNIMAIL_FAKE"):
        return os.environ.get("UNIMAIL_FAKE_" + account.upper(), "")
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
    return json.loads(raw) if raw else None


# --- small state (the account address and the last run; never mail text) ---
def _load():
    try:
        with open(os.path.join(DIR, "state.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(rec):
    os.makedirs(DIR, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DIR, prefix=".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(DIR, "state.json"))


def status():
    st = _load()
    return {"connected": bool(_kc_get(SERVICE, "refresh")), "client": bool(_client()), "email": st.get("email", ""),
            "lastRun": st.get("lastRun", 0), "error": CONNECT["error"] or st.get("error", ""),
            "connecting": CONNECT["running"], "url": CONNECT["url"] if CONNECT["running"] else ""}


# --- connect ---
def _form(url, fields):
    req = urllib.request.Request(url, urllib.parse.urlencode(fields).encode(), {"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (400, 401):
            raise NotConnected("The university Gmail needs to be connected again") from None
        raise MailError(f"Google returned an error ({e.code})") from None
    except OSError:
        raise MailError("Couldn't reach Google. Check the internet connection") from None


def connect(open_browser=True):
    """Opens Google's consent page in the browser and waits there for 5 minutes in a thread. The owner picks the
    university account and presses Allow. Returns the URL right away."""
    client = _client()
    if not client:
        raise MailError("The Google connection file is missing. Connect the mail cleanup once first (python3 desk/gmail_clean.py auth)")
    with LOCK:
        if CONNECT["running"]:
            return {"url": CONNECT["url"]}
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
                self.wfile.write("<p>The university Gmail is connected to the Desk. You can close this window.</p>".encode())

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
            deadline = time.time() + 300
            while "code" not in got and time.time() < deadline:
                srv.handle_request()
            if not got.get("code"):
                err = got.get("error") or ""
                raise MailError("Google refused the permission (the university may block outside apps)" if err == "access_denied"
                                else (err or "The sign-in was not finished in 5 minutes"))
            data = _form(TOKEN_URL, {"code": got["code"], "client_id": client["id"], "client_secret": client["secret"],
                                     "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
            if not data.get("refresh_token"):
                raise MailError("Google sent no lasting permission. Please try again")
            _kc_set(SERVICE, "refresh", data["refresh_token"])
            with LOCK:
                TOKEN.update(value=data.get("access_token", ""), until=time.time() + int(data.get("expires_in", 3600)) - 60)
            email = gmail("GET", "/profile").get("emailAddress", "")
            _save({**_load(), "email": email, "error": "", "connectedAt": int(time.time() * 1000)})
        except (MailError, NotConnected, OSError) as e:
            CONNECT["error"] = str(e)
        finally:
            srv.server_close()
            CONNECT.update(running=False, url="")

    th = threading.Thread(target=wait, daemon=True)
    th.start()
    if open_browser:
        webbrowser.open(url)
    return {"url": url, "thread": th}


def _token():
    with LOCK:
        if TOKEN["value"] and time.time() < TOKEN["until"]:
            return TOKEN["value"]
    client, refresh = _client(), _kc_get(SERVICE, "refresh")
    if not client or not refresh:
        raise NotConnected("Connect the university Gmail first (python3 desk/unimail.py auth)")
    data = _form(TOKEN_URL, {"client_id": client["id"], "client_secret": client["secret"],
                             "refresh_token": refresh, "grant_type": "refresh_token"})
    with LOCK:
        TOKEN.update(value=data["access_token"], until=time.time() + int(data.get("expires_in", 3600)) - 60)
        return TOKEN["value"]


def gmail(method, path, body=None, tries=4):
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(tries):
        req = urllib.request.Request(API + path, data, method=method, headers={
            "Authorization": "Bearer " + _token(), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 401:
                with LOCK:
                    TOKEN.update(value="", until=0)
                if attempt < tries - 1:
                    continue
                raise NotConnected("The university Gmail needs to be connected again") from None
            if e.code in (429, 500, 503) and attempt < tries - 1:
                time.sleep(2 ** attempt)
                continue
            raise MailError(f"Gmail returned an error ({e.code})") from None
        except urllib.error.URLError:
            if attempt == tries - 1:
                raise MailError("Couldn't reach Gmail. Check the internet connection") from None
            time.sleep(2 ** attempt)
    raise MailError("Couldn't reach Gmail")


# --- reading ---
def _headers(msg):
    return {h["name"].lower(): h["value"] for h in (msg.get("payload") or {}).get("headers") or []}


def _text(part):
    """The plain text of a message (text/plain first, else text/html with the tags taken out)."""
    plain, html = [], []

    def walk(p):
        mt, data = p.get("mimeType", ""), (p.get("body") or {}).get("data")
        if data and mt == "text/plain":
            plain.append(base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace"))
        elif data and mt == "text/html":
            html.append(base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace"))
        for c in p.get("parts") or []:
            walk(c)
    walk(part or {})
    if plain:
        return "\n".join(plain)
    raw = "\n".join(html)
    raw = re.sub(r"(?is)<(script|style).*?</\1>", "", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", raw)
    return re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", "", raw)).strip()


def _quote_cut(text):
    """Drops the quoted earlier mail under 'On … wrote:' / '2026年11月19日(木) … :' so only the new part is passed on."""
    lines = text.splitlines()
    for n, line in enumerate(lines):
        s = line.strip()
        if s.startswith(">") or re.match(r"^On .{4,120} wrote:$", s) or re.match(r"^\d{4}年\d{1,2}月\d{1,2}日.{0,60}[:：]$", s) \
                or re.match(r"^-{2,}\s*(Original Message|元のメッセージ)", s, re.I):
            return "\n".join(lines[:n]).strip()
    return text.strip()


def is_bulk(h):
    """A newsletter, notice or automated mail: no person is waiting for an answer."""
    sender = parseaddr(h.get("from", ""))[1]
    if BULK_FROM.search(sender):
        return True
    if h.get("list-unsubscribe") or h.get("list-id"):
        return True
    if (h.get("precedence") or "").lower() in ("bulk", "list", "junk"):
        return True
    auto = (h.get("auto-submitted") or "no").lower()
    return auto != "no"


def is_invite(part):
    """A calendar invitation or reply (a text/calendar part): answered in the calendar, not by mail."""
    if (part or {}).get("mimeType", "").startswith("text/calendar"):
        return True
    return any(is_invite(c) for c in (part or {}).get("parts") or [])


def _iso(date_header):
    try:
        return parsedate_to_datetime(date_header).isoformat()
    except (TypeError, ValueError):
        return ""


def thread_url(email, thread_id):
    return f"https://mail.google.com/mail/?authuser={urllib.parse.quote(email)}#all/{thread_id}"


def pending(days=14):
    """Threads where someone else wrote last and a person (not a mailer) may be waiting. Prints what the scheduled task
    needs to decide and draft; the Desk itself keeps none of the text."""
    me = (_load().get("email") or gmail("GET", "/profile").get("emailAddress", "")).lower()
    q = urllib.parse.quote(QUERY.format(days=int(days)))
    found = gmail("GET", f"/threads?q={q}&maxResults={MAX_THREADS}").get("threads") or []
    out = []
    for t in found:
        th = gmail("GET", f"/threads/{t['id']}?format=full")
        msgs = th.get("messages") or []
        if not msgs:
            continue
        last = msgs[-1]
        h = _headers(last)
        sender = parseaddr(h.get("from", ""))[1].lower()
        if sender == me or "DRAFT" in (last.get("labelIds") or []) or is_bulk(h) or is_invite(last.get("payload")):
            continue
        cc = [a for _, a in getaddresses([h.get("to", ""), h.get("cc", "")]) if a and a.lower() not in (me, sender)]
        out.append({
            "account": ACCOUNT, "thread": t["id"], "gmail_url": thread_url(me, t["id"]),
            "from": h.get("from", ""), "replyTo": h.get("reply-to", "") or h.get("from", ""), "cc": cc,
            "subject": h.get("subject", ""), "receivedAt": _iso(h.get("date", "")),
            "messageId": h.get("message-id", ""), "references": h.get("references", ""),
            "messages": [{"from": _headers(m).get("from", ""), "date": _iso(_headers(m).get("date", "")),
                          "text": _quote_cut(_text(m.get("payload")))[:TEXT_MAX]} for m in msgs[-3:]],
        })
    _save({**_load(), "email": me, "lastRun": int(time.time() * 1000), "error": ""})
    return {"account": ACCOUNT, "email": me, "items": out}


def replied(thread_ids):
    """{"<thread>": true} when the newest message in the thread is his own (he answered in the university Gmail)."""
    me = (_load().get("email") or gmail("GET", "/profile").get("emailAddress", "")).lower()
    res = {}
    for tid in thread_ids:
        tid = tid.rsplit("/", 1)[-1]  # a gmail_url works too
        try:
            msgs = gmail("GET", f"/threads/{tid}?format=metadata&metadataHeaders=From").get("messages") or []
        except MailError:
            continue
        msgs = [m for m in msgs if "DRAFT" not in (m.get("labelIds") or [])]
        res[tid] = bool(msgs) and parseaddr(_headers(msgs[-1]).get("from", ""))[1].lower() == me
    return res


def build_draft(spec, me):
    """The MIME of a reply in the same thread. spec: thread, to (list or str), cc?, subject, body, messageId?, references?"""
    msg = EmailMessage()
    to = spec.get("to") or []
    msg["To"] = ", ".join(to) if isinstance(to, list) else str(to)
    if spec.get("cc"):
        msg["Cc"] = ", ".join(spec["cc"]) if isinstance(spec["cc"], list) else str(spec["cc"])
    msg["From"] = me
    subj = spec.get("subject") or ""
    msg["Subject"] = subj if re.match(r"(?i)^re[:：]", subj) else "Re: " + subj
    if spec.get("messageId"):
        msg["In-Reply-To"] = spec["messageId"]
        msg["References"] = ((spec.get("references") or "") + " " + spec["messageId"]).strip()
    msg.set_content(spec.get("body") or "")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def draft(spec):
    if not spec.get("thread") or not spec.get("to") or not (spec.get("body") or "").strip():
        raise MailError("thread, to and body are needed")
    me = _load().get("email") or gmail("GET", "/profile").get("emailAddress", "")
    res = gmail("POST", "/drafts", {"message": {"threadId": spec["thread"], "raw": build_draft(spec, me)}})
    mid = (res.get("message") or {}).get("id", "")
    return {"id": res.get("id", ""), "view_url": f"https://mail.google.com/mail/?authuser={urllib.parse.quote(me)}#drafts?compose={mid}"}


def main(argv):
    cmd = argv[0] if argv else ""
    try:
        if cmd == "auth":
            res = connect()
            print("Opening Google's page in the browser. Pick the university account and press Allow.\n" + res["url"], flush=True)
            res["thread"].join(310)
            st = status()
            print("Connected: " + st["email"] if st["connected"] and not CONNECT["error"] else "Could not connect: " + (CONNECT["error"] or "unknown"))
            return 0 if st["connected"] and not CONNECT["error"] else 1
        if cmd == "status":
            print(json.dumps(status(), ensure_ascii=False))
            return 0
        if cmd == "pending":
            print(json.dumps(pending(int(argv[1]) if len(argv) > 1 else 14), ensure_ascii=False, indent=1))
            return 0
        if cmd == "draft" and len(argv) == 2:
            with open(argv[1], encoding="utf-8") as f:
                print(json.dumps(draft(json.load(f)), ensure_ascii=False))
            return 0
        if cmd == "replied" and len(argv) > 1:
            print(json.dumps(replied(argv[1:]), ensure_ascii=False))
            return 0
    except (NotConnected, MailError) as e:
        if cmd == "pending":
            _save({**_load(), "error": str(e)})
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 2
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
