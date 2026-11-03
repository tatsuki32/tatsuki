#!/usr/bin/env python3
"""Tatsuki's Desk — Mac server.

Serves desk/index.html at http://localhost:4747 and keeps memos, due dates,
tasks and Claude prompts in desk/data/*.json. Only the Python standard library is used.

- Listens on 127.0.0.1 only; requests whose Host is not localhost are refused.
- index.html is read from disk on every request, so a `git pull` shows up on reload.
- Read-only data written by the hourly sync (tools, world news, mail, calendar,
  Google tasks, Claude reports) lives in desk/data/*.js and is inlined into the page.
- Nothing is ever hard-deleted: the page sends deleted:true, same as online.
"""
import json
import os
import secrets
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
PORT = int(os.environ.get("DESK_PORT", "4747"))
COLLECTIONS = ("memos", "dues", "tasks", "prompts", "notes", "habits")
# file in data/, global the page reads, value when the file is missing
DATA_JS = [
    ("tools.js", "TOOLS", "[]"),
    ("world.js", "WORLD", "[]"),
    ("mail.js", "MAIL", "[]"),
    ("calendar.js", "CAL", "null"),
    ("gtasks.js", "GTASKS", "null"),
    ("reports.js", "REPORTS", "[]"),
    ("watch.js", "WATCH", "[]"),
]
LOCK = threading.Lock()


def path_of(col):
    return os.path.join(DATA, col + ".json")


def load(col):
    try:
        with open(path_of(col), encoding="utf-8") as f:
            v = json.load(f)
    except FileNotFoundError:
        return []
    return [x for x in v if isinstance(x, dict) and x.get("id")] if isinstance(v, list) else []


def save(col, items):
    os.makedirs(DATA, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DATA, prefix="." + col, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path_of(col))


def upsert(col, doc_id, rec, merge):
    with LOCK:
        items = load(col)
        for i, x in enumerate(items):
            if x["id"] == doc_id:
                items[i] = {**x, **rec, "id": doc_id} if merge else {**rec, "id": doc_id}
                break
        else:
            if merge:
                return None
            items.append({**rec, "id": doc_id})
        save(col, items)
        return next(x for x in items if x["id"] == doc_id)


def page():
    with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
        html = f.read()
    parts = []
    for name, var, default in DATA_JS:
        try:
            with open(os.path.join(DATA, name), encoding="utf-8") as f:
                parts.append(f.read().replace("</script", "<\\/script"))
        except FileNotFoundError:
            parts.append(f"window.{var}={default};")
    try:  # what I was doing: read memory/ fresh on every page load (Desk's 🧭 button)
        import kickoff
        mem = json.dumps(kickoff.memory_doc(), ensure_ascii=False).replace("</", "<\\/")
    except Exception:
        mem = "null"
    parts.append(f"window.MEMORY={mem};")
    inject = "<script>\n" + "\n".join(parts) + "\n</script>\n"
    i = html.find("<script>")
    return html[:i] + inject + html[i:] if i >= 0 else html + inject


class Handler(BaseHTTPRequestHandler):
    server_version = "TatsukiDesk/2"

    def log_message(self, fmt, *args):
        if os.environ.get("DESK_LOG"):
            super().log_message(fmt, *args)

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        if not isinstance(body, (bytes, str)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def allowed(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("localhost", "127.0.0.1"):
            return False
        origin = self.headers.get("Origin")
        if self.command != "GET" and origin:
            return urlsplit(origin).hostname in ("localhost", "127.0.0.1")
        return True

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        v = json.loads(self.rfile.read(n) or b"{}") if n else {}
        if not isinstance(v, dict):
            raise ValueError("object expected")
        return v

    def route(self):
        p = urlsplit(self.path).path.rstrip("/").split("/")[1:]  # ['api','memos','id']
        if len(p) >= 2 and p[0] == "api" and p[1] in COLLECTIONS:
            return p[1], (unquote(p[2]) if len(p) > 2 else None)
        return None, None

    def do_GET(self):
        if not self.allowed():
            return self.send(403, {"error": "forbidden"})
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            return self.send(200, page(), "text/html; charset=utf-8")
        col, doc_id = self.route()
        if col and doc_id is None:
            with LOCK:
                return self.send(200, load(col))
        if path == "/health":
            return self.send(200, {"ok": True})
        self.send(404, {"error": "not found"})

    def write(self, method):
        if not self.allowed():
            return self.send(403, {"error": "forbidden"})
        col, doc_id = self.route()
        try:
            rec = self.body()
        except (ValueError, json.JSONDecodeError):
            return self.send(400, {"error": "bad json"})
        rec.pop("id", None)
        rec.setdefault("updatedAt", int(time.time() * 1000))
        if method == "POST" and col == "memos" and doc_id is None:
            doc_id = "m" + str(int(time.time() * 1000)) + secrets.token_hex(2)
            return self.send(200, upsert(col, doc_id, rec, merge=False))
        if method == "PUT" and col in ("dues", "tasks", "prompts", "notes", "habits") and doc_id:
            return self.send(200, upsert(col, doc_id, rec, merge=False))
        if method == "PATCH" and col == "memos" and doc_id:
            out = upsert(col, doc_id, rec, merge=True)
            return self.send(200, out) if out else self.send(404, {"error": "no such memo"})
        self.send(405, {"error": "method not allowed"})

    def do_POST(self):
        self.write("POST")

    def do_PUT(self):
        self.write("PUT")

    def do_PATCH(self):
        self.write("PATCH")


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Tatsuki's Desk: http://localhost:{PORT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
