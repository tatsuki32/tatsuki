#!/usr/bin/env python3
"""Asks Google for read-only access to Calendar and Gmail, and prints the refresh token to stdout.

    python3 scripts/google_auth.py client_secret.json | npx wrangler secret put GOOGLE_REFRESH_TOKEN

Pipe it straight into wrangler, so the token is never shown or written to a file. Messages go to stderr.
Standard library only. The browser comes back to this Mac on a local port.
"""
import base64
import hashlib
import http.server
import json
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser

SCOPES = "https://www.googleapis.com/auth/calendar.readonly https://www.googleapis.com/auth/gmail.readonly"
AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
PORT = 8765


def main():
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    with open(sys.argv[1], encoding="utf-8") as f:
        raw = json.load(f)
    client = raw.get("installed") or raw.get("web") or raw
    redirect = f"http://127.0.0.1:{PORT}/"
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    url = AUTH + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state:
                got["code"] = q.get("code", [None])[0]
                got["error"] = q.get("error", [None])[0]
            self.send_response(200)
            self.send_header("content-type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Terminal に戻ってください。".encode())

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    print("ブラウザで Google の許可画面を開きます。開かないときは次の URL を開いてください。\n" + url, file=sys.stderr)
    webbrowser.open(url)
    while "code" not in got:
        server.handle_request()
    if not got.get("code"):
        print(f"許可されませんでした：{got.get('error')}", file=sys.stderr)
        return 1
    body = urllib.parse.urlencode({
        "client_id": client["client_id"], "client_secret": client.get("client_secret", ""), "code": got["code"],
        "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect,
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body)) as res:
        token = json.load(res).get("refresh_token")
    if not token:
        print("refresh token を受け取れませんでした。", file=sys.stderr)
        return 1
    print(token)
    print("受け取りました。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
