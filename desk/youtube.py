#!/usr/bin/env python3
"""Tatsuki's Desk — Post Studio's YouTube uploads, straight from this Mac (no copy on go.tany.im).

Connecting uses the Google Cloud OAuth client already set up for Inbox Cleanup (keychain tatsuki-desk-gmail), with the
youtube.upload and youtube.readonly scopes only. The refresh token is kept in the keychain (tatsuki-desk-youtube).
A video for later is uploaded at once as private with publishAt, so YouTube itself makes it public at that time and
this Mac does not need to be awake then.

  python3 youtube.py auth      connect (opens Google's page)
  python3 youtube.py status    which channel is connected
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timezone

SERVICE = "tatsuki-desk-youtube"
GMAIL_SERVICE = "tatsuki-desk-gmail"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
CHUNK = 16 * 1024 * 1024  # a multiple of 256 KB, as resumable uploads require
_cache = {"at": 0, "channel": None, "token": None, "token_at": 0}


def _kc_get(service, account):
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", service, "-a", account, "-w"], capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _kc_set(service, account, value):
    subprocess.run(["security", "add-generic-password", "-U", "-s", service, "-a", account, "-w", value], capture_output=True, timeout=20, check=True)


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
            self.wfile.write("<p>YouTube is connected to Post Studio. You can close this window.</p>".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Once)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    webbrowser.open(AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client["id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}))
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
    _cache.update(at=0, channel=None, token=None)
    return ""


def _token():
    if _cache["token"] and time.time() - _cache["token_at"] < 3000:
        return _cache["token"]
    client, refresh = _client(), _kc_get(SERVICE, "refresh")
    if not client or not refresh:
        raise RuntimeError("YouTube is not connected. Connect it in Post Studio's Settings")
    try:
        tok = _form(TOKEN_URL, {"client_id": client["id"], "client_secret": client["secret"], "refresh_token": refresh,
                                "grant_type": "refresh_token"}).get("access_token", "")
    except urllib.error.HTTPError:
        raise RuntimeError("The YouTube connection has expired. Connect it again in Settings") from None
    except OSError:
        raise RuntimeError("Could not reach Google") from None
    _cache.update(token=tok, token_at=time.time())
    return tok


def channel(fresh=False):
    """{id, title} of the connected channel, or None. Kept 10 minutes."""
    if not _kc_get(SERVICE, "refresh"):
        return None
    if not fresh and _cache["channel"] and time.time() - _cache["at"] < 600:
        return _cache["channel"]
    try:
        req = urllib.request.Request(f"{API}/channels?part=snippet&mine=true", headers={"Authorization": "Bearer " + _token()})
        with urllib.request.urlopen(req, timeout=20) as r:
            items = json.load(r).get("items") or []
    except (RuntimeError, OSError, ValueError):
        return _cache["channel"]
    _cache["channel"] = {"id": items[0]["id"], "title": items[0]["snippet"]["title"]} if items else None
    _cache["at"] = time.time()
    return _cache["channel"]


def upload(path, title, description, tags=(), publish_at=None):
    """Uploads one video. publish_at (ms) in the future: private until YouTube makes it public then. Returns the video id."""
    status = {"privacyStatus": "public", "selfDeclaredMadeForKids": False}
    if publish_at and publish_at > time.time() * 1000 + 120000:
        status = {"privacyStatus": "private", "selfDeclaredMadeForKids": False,
                  "publishAt": datetime.fromtimestamp(publish_at / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")}
    meta = {"snippet": {"title": title[:100] or "Video", "description": description[:5000],
                        "tags": [t.lstrip("#") for t in tags][:15], "categoryId": "22"}, "status": status}
    size = os.path.getsize(path)
    req = urllib.request.Request(f"https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                                 data=json.dumps(meta).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + _token(), "Content-Type": "application/json; charset=UTF-8",
                                          "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            where = r.headers["Location"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(_why(e)) from None
    sent = 0
    with open(path, "rb") as f:
        while sent < size:
            chunk = f.read(CHUNK)
            end = sent + len(chunk) - 1
            put = urllib.request.Request(where, data=chunk, method="PUT",
                                         headers={"Authorization": "Bearer " + _token(), "Content-Length": str(len(chunk)),
                                                  "Content-Range": f"bytes {sent}-{end}/{size}"})
            try:
                with urllib.request.urlopen(put, timeout=600) as r:
                    return json.load(r)["id"]  # the last part answers with the video
            except urllib.error.HTTPError as e:
                if e.code != 308:  # 308: "go on" with the next part
                    raise RuntimeError(_why(e)) from None
            sent = end + 1
    raise RuntimeError("YouTube did not confirm the upload")


def _why(e):
    try:
        err = json.load(e).get("error", {})
        reason = (err.get("errors") or [{}])[0].get("reason", "")
        if reason == "quotaExceeded":
            return "YouTube's daily upload limit for this app is used up. Try tomorrow"
        if reason in ("accessNotConfigured", "SERVICE_DISABLED") or "has not been used" in err.get("message", ""):
            return "Turn on the YouTube Data API v3 in the Google Cloud project, then try again"
        return "YouTube said: " + err.get("message", str(e.code))[:200]
    except ValueError:
        return f"YouTube answered {e.code}"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "auth":
        print(auth() or "Connected.")
    else:
        print(json.dumps(channel(fresh=True), ensure_ascii=False))
