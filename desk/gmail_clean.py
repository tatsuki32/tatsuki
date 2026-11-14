#!/usr/bin/env python3
"""Tatsuki's Desk — stop unwanted senders in Gmail, one press per sender.

Reads headers only, never a message body, and has no permission to send. Nothing is stopped until the page asks.
  stop   one-click unsubscribe (RFC 8058, only for mail whose signature holds) + a filter that sends new mail to Trash
  spam   moves the sender's mail to Spam; never touches a link in it
  keep   leaves the sender alone and stops suggesting it
The client and refresh token live in the macOS keychain. Unsubscribe URLs carry a personal key, so desk/data/mailclean/
stays out of the backup (backup_data.py) and no URL is ever logged.

  python3 gmail_clean.py auth [client_secret.json]   connect once (newest ~/Downloads/client_secret*.json if not given)
  python3 gmail_clean.py scan                        read the last 90 days
  python3 gmail_clean.py view                        what the page shows
"""
import base64
import glob
import hashlib
import http.server
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from email.utils import parseaddr, parsedate_to_datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("MAILCLEAN_DIR") or os.path.join(ROOT, "data", "mailclean")
SERVICE = "tatsuki-desk-gmail"
SCOPES = "https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/gmail.settings.basic"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCAN_QUERY = "newer_than:90d -in:sent -in:chats (category:promotions OR category:updates OR category:forums OR category:social OR unsubscribe OR 配信停止 OR 配信解除)"
SCAN_MAX = 2000
HEADERS = ["From", "Subject", "List-Unsubscribe", "List-Unsubscribe-Post", "Authentication-Results", "DKIM-Signature"]
SPAM_MAX = 500
# A sender that looks like one of these stays out of the suggestions; stopping it needs a second press.
PROTECT = re.compile(
    r"bank|銀行|信用金庫|証券|securities|sbisec|paypay|wise\.com|paypal|payroll|payslip|給与|給料|明細|invoice|receipt|領収|請求|"
    r"カード|credit|visa|mastercard|super\b|tax|税|保険|insurance|"
    r"security|セキュリティ|sign-?in|ログイン|認証|verif|password|パスワード|alert|"
    r"予約|booking|reservation|itinerary|e-?ticket|航空|airline|airways|virginaustralia|greyhound|"
    r"account|@google\.com|@amazon\.|@apple\.com|@microsoft\.com|facebookmail|netflix|"  # the keys to other services
    r"応募|選考|面接|"
    r"\.go\.jp|\.gov|\.ac\.jp|\.edu", re.I)
LOCK = threading.Lock()
SCAN = {"running": False, "done": 0, "total": 0, "error": ""}
TOKEN = {"value": "", "until": 0}


class NotConnected(Exception):
    pass


class GmailError(Exception):
    def __init__(self, status, reason=""):
        super().__init__(f"{status} {reason}")
        self.status = status


# --- keychain ---
def _key_get(account):
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", account, "-w"],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _key_set(account, value):
    # through `security -i` so the secret never shows in a process list
    line = 'add-generic-password -U -s "%s" -a "%s" -w "%s"\n' % (SERVICE, account, value.replace("\\", "\\\\").replace('"', '\\"'))
    out = subprocess.run(["security", "-i"], input=line, capture_output=True, text=True, timeout=15)
    if out.returncode != 0:
        raise OSError("keychain")


def connected():
    return bool(_key_get("refresh") and _key_get("client"))


# --- connect ---
def find_client_file():
    found = sorted(glob.glob(os.path.expanduser("~/Downloads/client_secret*.json")), key=os.path.getmtime)
    return found[-1] if found else ""


def read_client(path):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    block = raw.get("installed") or raw.get("web") or {}
    if not block.get("client_id") or not block.get("client_secret"):
        raise ValueError("no client id")
    return {"id": block["client_id"], "secret": block["client_secret"]}


def auth(path=None):
    path = path or find_client_file()
    saved = _key_get("client")
    if path:
        client = read_client(path)
    elif saved:
        client = json.loads(saved)  # reconnect with the client already in the keychain
    else:
        print("Connection file not found. Put the JSON file that starts with client_secret in ~/Downloads.")
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
            self.wfile.write("<p>Connection received. You can close this window.</p>".encode())

        def log_message(self, *a):
            pass  # the request line holds the code

    srv = http.server.HTTPServer(("127.0.0.1", 0), Once)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client["id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    print("Opening the Google sign-in page in your browser. If it does not open, visit this URL.\n" + url, flush=True)
    webbrowser.open(url)
    srv.timeout = 300
    deadline = time.time() + 300
    while "code" not in got and time.time() < deadline:
        srv.handle_request()
    srv.server_close()
    if not got.get("code"):
        print("Could not connect: " + (got.get("error") or "timed out"))
        return 1
    data = _form(TOKEN_URL, {"code": got["code"], "client_id": client["id"], "client_secret": client["secret"],
                             "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
    if not data.get("refresh_token"):
        print("No refresh token was received. Please try again.")
        return 1
    _key_set("client", json.dumps(client))
    _key_set("refresh", data["refresh_token"])
    print("Connected." + (" You can delete the file that was read: " + path if path else ""))
    return 0


def _form(url, fields):
    req = urllib.request.Request(url, urllib.parse.urlencode(fields).encode(), {"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (400, 401):
            raise NotConnected() from None
        raise GmailError(e.code) from None


def access_token():
    with LOCK:
        if TOKEN["value"] and time.time() < TOKEN["until"]:
            return TOKEN["value"]
    raw, refresh = _key_get("client"), _key_get("refresh")
    if not raw or not refresh:
        raise NotConnected()
    client = json.loads(raw)
    data = _form(TOKEN_URL, {"client_id": client["id"], "client_secret": client["secret"],
                             "refresh_token": refresh, "grant_type": "refresh_token"})
    with LOCK:
        TOKEN["value"], TOKEN["until"] = data["access_token"], time.time() + int(data.get("expires_in", 3600)) - 60
        return TOKEN["value"]


def gmail(method, path, body=None, tries=6):
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(tries):
        req = urllib.request.Request(API + path, data, method=method, headers={
            "Authorization": "Bearer " + access_token(), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            try:
                reason = json.load(e).get("error", {}).get("message", "")
            except ValueError:
                reason = ""
            if e.code == 401:
                with LOCK:
                    TOKEN["value"] = ""
            # Gmail answers 403 as well as 429 when reading too fast
            limited = e.code == 403 and re.search(r"rate|quota|concurrent", reason, re.I)
            if (e.code in (401, 429, 500, 503) or limited) and attempt < tries - 1:
                time.sleep(2 ** attempt)
                continue
            raise GmailError(e.code, reason) from None
        except urllib.error.URLError:
            if attempt == tries - 1:
                raise GmailError(0, "network") from None
            time.sleep(2 ** attempt)
    raise GmailError(0, "network")


# --- reading ---
def _load(name, default):
    try:
        with open(os.path.join(DIR, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(name, value):
    os.makedirs(DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DIR, prefix=".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(DIR, name))


def sender_id(addr):
    return hashlib.sha1(addr.encode()).hexdigest()[:12]


def trusted(headers):
    """Google's own verdict on this message (the topmost Authentication-Results), and a signature that covers the header."""
    results = next((v for k, v in headers if k.lower() == "authentication-results"), "")
    if not results.lower().startswith("mx.google.com") or "dkim=pass" not in results or "dmarc=pass" not in results:
        return False
    return any(k.lower() == "dkim-signature" and "list-unsubscribe" in re.sub(r"\s", "", v).lower() for k, v in headers)


def parse_unsub(value):
    urls = re.findall(r"<([^<>]+)>", value or "")
    return (next((u for u in urls if u.lower().startswith("https://")), ""),
            next((u for u in urls if u.lower().startswith("mailto:")), ""))


def protected_reason(addr, name, subject, starred):
    if starred:
        return "Has a starred email"
    if PROTECT.search(addr) or PROTECT.search(name) or PROTECT.search(subject or ""):
        return "May be an important message"
    return ""


def digest(messages):
    """Group message metadata by sender. Mail with no unsubscribe header and no Promotions label is not a candidate."""
    out = {}
    for m in sorted(messages, key=lambda x: int(x.get("internalDate") or 0), reverse=True):
        headers = [(h["name"], h["value"]) for h in m.get("payload", {}).get("headers", [])]
        get = lambda n: next((v for k, v in headers if k.lower() == n), "")
        name, addr = parseaddr(get("from"))
        addr = addr.lower()
        labels = m.get("labelIds", [])
        if not addr or "@" not in addr:
            continue
        http_url, mailto = parse_unsub(get("list-unsubscribe"))
        if not (http_url or mailto or "CATEGORY_PROMOTIONS" in labels):
            continue
        item = out.get(addr)
        if not item:
            when = ""
            try:
                when = parsedate_to_datetime(time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime(int(m["internalDate"]) / 1000))).date().isoformat()
            except (KeyError, ValueError, TypeError):
                pass
            item = out[addr] = {"id": sender_id(addr), "addr": addr, "name": name or addr, "count": 0, "last": when,
                                "subject": get("subject")[:120], "url": "", "mailto": False, "oneclick": False,
                                "trusted": False, "starred": False}
        item["count"] += 1
        item["starred"] = item["starred"] or "STARRED" in labels
        if http_url and not item["url"]:  # newest message with a link wins
            item["url"] = http_url
            item["oneclick"] = "one-click" in get("list-unsubscribe-post").lower()
            item["trusted"] = trusted(headers)
        item["mailto"] = item["mailto"] or bool(mailto)
    for item in out.values():
        item["protect"] = protected_reason(item["addr"], item["name"], item["subject"], item["starred"])
    return out


def scan():
    with LOCK:
        if SCAN["running"]:
            return False
        SCAN.update(running=True, done=0, total=0, error="")
    threading.Thread(target=_scan, daemon=True).start()
    return True


def _scan():
    try:
        ids, token = [], ""
        while len(ids) < SCAN_MAX:
            q = {"q": SCAN_QUERY, "maxResults": 500}
            if token:
                q["pageToken"] = token
            page = gmail("GET", "/messages?" + urllib.parse.urlencode(q))
            ids += [m["id"] for m in page.get("messages", [])]
            token = page.get("nextPageToken")
            if not token:
                break
        ids = ids[:SCAN_MAX]
        with LOCK:
            SCAN["total"] = len(ids)
        path = "&".join(["format=metadata"] + ["metadataHeaders=" + h for h in HEADERS])

        def one(mid):
            m = gmail("GET", f"/messages/{mid}?{path}")
            with LOCK:
                SCAN["done"] += 1
            return m

        with ThreadPoolExecutor(4) as pool:
            messages = list(pool.map(one, ids))
        _save("senders.json", {"at": int(time.time() * 1000), "messages": len(ids), "items": digest(messages)})
    except NotConnected:
        with LOCK:
            SCAN["error"] = "connect"
    except (GmailError, OSError) as e:
        with LOCK:
            SCAN["error"] = "Could not read from Gmail. Wait a moment and try again"
        print("mailclean scan:", e, file=sys.stderr, flush=True)
    finally:
        with LOCK:
            SCAN["running"] = False


# --- acting ---
def safe_url(url):
    """https only, and the host must be on the public internet: a mail must not be able to aim this Mac at itself."""
    try:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            return False
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
        return bool(infos) and all(ipaddress.ip_address(i[4][0].split("%")[0]).is_global for i in infos)
    except (ValueError, OSError):
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def one_click(url):
    """RFC 8058 POST. True only for a 2xx; a redirect is not followed and does not count as stopped."""
    if not safe_url(url):
        return False
    req = urllib.request.Request(url, b"List-Unsubscribe=One-Click", method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "TatsukiDesk/1.0"})
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=20) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def create_filter(addr):
    try:
        return gmail("POST", "/settings/filters", {"criteria": {"from": addr},
                                                   "action": {"addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX"]}}).get("id", "")
    except GmailError as e:
        if e.status not in (400, 409):
            raise
        for f in gmail("GET", "/settings/filters").get("filter", []):  # the same filter may already exist
            if f.get("criteria", {}).get("from", "").lower() == addr:
                return f.get("id", "")
        raise


def mark_spam(addr):
    q = urllib.parse.urlencode({"q": f"from:{addr}", "maxResults": SPAM_MAX})
    ids = [m["id"] for m in gmail("GET", "/messages?" + q).get("messages", [])]
    if ids:
        gmail("POST", "/messages/batchModify", {"ids": ids, "addLabelIds": ["SPAM"], "removeLabelIds": ["INBOX"]})
    return len(ids)


def act(sid, action, confirm=False):
    """Returns (status, body)."""
    if action not in ("stop", "spam", "keep"):
        return 400, {"error": "unknown action"}
    item = _load("senders.json", {}).get("items", {})
    item = next((v for v in item.values() if v["id"] == sid), None)
    if not item:
        return 404, {"error": "not found"}
    if action == "stop" and item.get("protect") and not confirm:
        return 409, {"error": "protected", "reason": item["protect"]}
    rec = {"action": action, "at": int(time.time() * 1000)}
    try:
        if action == "stop":
            if item.get("url") and item.get("oneclick") and item.get("trusted"):
                rec["unsub"] = "done" if one_click(item["url"]) else "failed"
            elif item.get("url") and item.get("trusted"):
                rec["unsub"] = "page"
            elif item.get("url") or item.get("mailto"):
                rec["unsub"] = "none"  # a link we do not trust, or a request by mail: nothing is sent
            else:
                rec["unsub"] = "none"
            rec["filterId"] = create_filter(item["addr"])
        elif action == "spam":
            rec["moved"] = mark_spam(item["addr"])
    except NotConnected:
        return 401, {"error": "connect"}
    except GmailError as e:
        return 502, {"error": "gmail", "status": e.status}
    with LOCK:
        decisions = _load("decisions.json", {})
        decisions[sid] = rec
        _save("decisions.json", decisions)
    return 200, view()


def undo(sid):
    with LOCK:
        decisions = _load("decisions.json", {})
    rec = decisions.get(sid)
    if not rec:
        return 404, {"error": "not found"}
    try:
        if rec.get("filterId"):
            gmail("DELETE", "/settings/filters/" + urllib.parse.quote(rec["filterId"], safe=""))
    except NotConnected:
        return 401, {"error": "connect"}
    except GmailError as e:
        if e.status != 404:
            return 502, {"error": "gmail", "status": e.status}
    with LOCK:
        decisions = _load("decisions.json", {})
        decisions.pop(sid, None)
        _save("decisions.json", decisions)
    return 200, view()


# --- the page ---
def view():
    data = _load("senders.json", {})
    decisions = _load("decisions.json", {})
    with LOCK:
        scan_state = dict(SCAN)
    groups = {"candidates": [], "guarded": [], "done": []}
    for item in sorted(data.get("items", {}).values(), key=lambda v: (-v["count"], v["addr"])):
        row = {k: item.get(k) for k in ("id", "addr", "name", "count", "last", "subject", "protect")}
        row["canOneClick"] = bool(item.get("url") and item.get("oneclick") and item.get("trusted"))
        row["doubtful"] = bool(item.get("url")) and not item.get("trusted")
        rec = decisions.get(item["id"])
        if rec:
            row.update(action=rec["action"], unsub=rec.get("unsub", ""), moved=rec.get("moved"), hasFilter=bool(rec.get("filterId")))
            if rec.get("unsub") == "page":  # opened only by a press, and only when the signature held
                row["page"] = item.get("url")
            groups["done"].append(row)
        elif item.get("protect"):
            groups["guarded"].append(row)
        else:
            groups["candidates"].append(row)
    return {"connected": connected(), "scan": scan_state, "at": data.get("at"), "messages": data.get("messages", 0), **groups}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "view"
    if cmd == "auth":
        sys.exit(auth(sys.argv[2] if len(sys.argv) > 2 else None))
    if cmd == "scan":
        scan()
        while SCAN["running"]:
            time.sleep(1)
        print(json.dumps({k: SCAN[k] for k in ("done", "total", "error")}, ensure_ascii=False))
        sys.exit(1 if SCAN["error"] else 0)
    print(json.dumps(view(), ensure_ascii=False, indent=1))
