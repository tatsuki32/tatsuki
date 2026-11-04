#!/usr/bin/env python3
"""Tatsuki's Desk — Mac server.

Serves desk/index.html at http://localhost:4747 and keeps memos, due dates,
tasks and Claude prompts in desk/data/*.json. Only the Python standard library is used.
POST /api/claude opens Terminal with a Claude Code command (Desk's ⌨️ buttons, macOS only).
/api/focus/* relays to FocusBlock (focusblock/, 127.0.0.1:47800); the password only passes through.

- Listens on 127.0.0.1 only; requests whose Host is not localhost are refused.
- index.html is read from disk on every request, so a `git pull` shows up on reload.
- Read-only data written by the hourly sync (tools, world news, mail, calendar,
  Google tasks, Claude reports) lives in desk/data/*.js and is inlined into the page.
- Nothing is ever hard-deleted: the page sends deleted:true, same as online.
"""
import glob
import json
import os
import secrets
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
PORT = int(os.environ.get("DESK_PORT", "4747"))
COLLECTIONS = ("memos", "dues", "tasks", "prompts", "notes", "habits", "wins")
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
MEMOLINK_LOCK = threading.Lock()
REPO = os.path.dirname(ROOT)
# Desk's ⌨️ buttons may start only these Claude Code commands (POST /api/claude)
CLAUDE_CMDS = ("/morning-kickoff", "/begin", "/recap", "/rsd", "/learn", "/sync", "/knowledge", "/desk-feature", "/followup", "/newtool", "/kickoff", "/where", "/lowenergy")
CLAUDE_ARG_MAX = 6000
FOCUS_URL = "http://127.0.0.1:47800"
# no proxy: while websites are blocked the system proxy points at FocusBlock itself
FOCUS_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


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


def lifeos_tools():
    """Life OS: lifeos/*/tool.json, read fresh every time. A broken file shows up as a problem in the
    list instead of taking the page down."""
    out = []
    for path in sorted(glob.glob(os.path.join(REPO, "lifeos", "[0-9][0-9]-*", "tool.json"))):
        slug = os.path.basename(os.path.dirname(path))
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
            if not isinstance(rec, dict):
                raise ValueError("object expected")
        except (OSError, ValueError):
            rec = {"no": slug[:2], "name": slug, "status": "blocked", "oneline": "tool.json を読めません",
                   "blockers": [{"what": "tool.json", "why": "ファイルの形が壊れています。", "needs": "担当の作業で直す"}]}
        out.append({**rec, "id": slug})
    return out


def followup_core():
    """Life OS 02's rules, or None in a checkout without lifeos/."""
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    try:
        from lifeos.lib.tools import load_tool
        return load_tool("02-followup", "core")
    except (ImportError, OSError):
        return None


def followup_answer(no, rec):
    """POST /api/followups/<no>/answer. Never sends mail: 送る only adds a Desk task."""
    core = followup_core()
    choice = rec.get("choice")
    if core is None or choice not in core.CHOICES or not no.isdigit():
        return 400, {"error": "bad answer"}
    return 200, {"message": core.answer(int(no), choice), "view": core.desk_view()}


def memolink_core():
    """Life OS 11, or None in a checkout without lifeos/."""
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    try:
        from lifeos.lib.tools import load_tool
        return load_tool("11-notes", "core")
    except (ImportError, OSError):
        return None


def memolink_run():
    """POST /api/memolink/run: one pass, never two at once. The answer holds counts only, never a memo's words."""
    core = memolink_core()
    if core is None:
        return 404, {"error": "メモ連携がこの Desk に入っていません"}
    if not MEMOLINK_LOCK.acquire(blocking=False):
        return 409, {"error": "連携しています。終わるまでお待ちください"}
    try:
        core.run()
        return 200, core.desk_view()
    except core.desk_notes.NoteStoreError as e:
        return 500, {"error": str(e)}
    except Exception:
        return 500, {"error": "連携できませんでした。元のメモは変わっていません"}
    finally:
        MEMOLINK_LOCK.release()


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
    los = json.dumps(lifeos_tools(), ensure_ascii=False).replace("</", "<\\/")
    parts.append(f"window.LIFEOS={los};")
    inject = "<script>\n" + "\n".join(parts) + "\n</script>\n"
    i = html.find("<script>")
    return html[:i] + inject + html[i:] if i >= 0 else html + inject


def open_claude(cmd, arg):
    """Open Terminal and start `claude "<cmd> <arg>"` in the repo (macOS only).

    The text never goes into a shell line: it is written to a private temp file that the
    .command script reads and deletes before starting claude, so quotes or `$` in a pasted
    message cannot run anything.
    """
    text = cmd + ("" if not arg else ("" if arg.startswith("\n") else " ") + arg)
    d = tempfile.mkdtemp(prefix="desk-claude-")  # 0700
    msg = os.path.join(d, "prompt.txt")
    script = os.path.join(d, "start.command")
    with open(msg, "w", encoding="utf-8") as f:
        f.write(text)
    with open(script, "w", encoding="utf-8") as f:
        f.write(
            "#!/bin/bash\n"
            'export PATH="$HOME/.local/bin:$HOME/.claude/local:/opt/homebrew/bin:/usr/local/bin:$PATH"\n'
            f"cd {shlex.quote(REPO)} || exit 1\n"
            f'P="$(cat {shlex.quote(msg)})"\n'
            f"rm -rf {shlex.quote(d)}\n"
            'if ! command -v claude >/dev/null; then echo "claude が見つかりません（Claude Code を入れてください）"; read -r; exit 1; fi\n'
            'exec claude "$P"\n'
        )
    os.chmod(script, 0o700)
    subprocess.run(["open", "-a", "Terminal", script], check=True, timeout=10)


def focus_call(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(FOCUS_URL + path, data=data, headers={"Content-Type": "application/json"})
    try:
        try:
            with FOCUS_OPENER.open(req, timeout=10) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)
    except (OSError, ValueError):
        return 502, {"error": "FocusBlock につながりません"}


def focus_state():
    code, st = focus_call("/api/state")
    if code != 200:
        return code, st
    return 200, {"hasPassword": st["hasPassword"], "active": st["active"],
                 "apps": {"mode": st["apps"]["mode"], "count": len(st["apps"]["list"])},
                 "web": {"mode": st["web"]["mode"], "count": len(st["web"]["list"])}}


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
        if path == "/api/focus/state":
            return self.send(*focus_state())
        if path == "/health":
            return self.send(200, {"ok": True})
        if path == "/api/followups":
            core = followup_core()
            return self.send(200, core.desk_view()) if core else self.send(404, {"error": "not found"})
        if path == "/api/memolink":
            core = memolink_core()
            return self.send(200, core.desk_view()) if core else self.send(404, {"error": "not found"})
        self.send(404, {"error": "not found"})

    def write(self, method):
        if not self.allowed():
            return self.send(403, {"error": "forbidden"})
        col, doc_id = self.route()
        try:
            rec = self.body()
        except (ValueError, json.JSONDecodeError):
            return self.send(400, {"error": "bad json"})
        if method == "POST" and urlsplit(self.path).path == "/api/claude":
            return self.claude(rec)
        if method == "POST" and urlsplit(self.path).path in ("/api/focus/start", "/api/focus/stop"):
            return self.focus(urlsplit(self.path).path.rsplit("/", 1)[1], rec)
        if method == "POST" and urlsplit(self.path).path == "/api/memolink/run":
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            return self.send(*memolink_run())
        parts = urlsplit(self.path).path.split("/")
        if method == "POST" and len(parts) == 5 and parts[1:3] == ["api", "followups"] and parts[4] == "answer":
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            return self.send(*followup_answer(parts[3], rec))
        rec.pop("id", None)
        rec.setdefault("updatedAt", int(time.time() * 1000))
        if method == "POST" and col == "memos" and doc_id is None:
            doc_id = "m" + str(int(time.time() * 1000)) + secrets.token_hex(2)
            return self.send(200, upsert(col, doc_id, rec, merge=False))
        if method == "PUT" and col in ("dues", "tasks", "prompts", "notes", "habits", "wins") and doc_id:
            return self.send(200, upsert(col, doc_id, rec, merge=False))
        if method == "PATCH" and col == "memos" and doc_id:
            out = upsert(col, doc_id, rec, merge=True)
            return self.send(200, out) if out else self.send(404, {"error": "no such memo"})
        self.send(405, {"error": "method not allowed"})

    def focus(self, action, rec):
        # same protection as claude(): Host/Origin checked, and JSON from another origin needs a preflight
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send(415, {"error": "json only"})
        keys = ("end", "apps", "web") if action == "start" else ("password",)
        code, out = focus_call("/api/" + action, {k: rec.get(k) for k in keys})
        if code != 200:
            return self.send(code, out)
        return self.send(*focus_state())

    def claude(self, rec):
        # a page on another site cannot get here: allowed() checks Host/Origin, and a JSON body
        # from another origin needs a CORS preflight this server never answers
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send(415, {"error": "json only"})
        cmd, arg = rec.get("cmd"), rec.get("arg") or ""
        if cmd not in CLAUDE_CMDS or not isinstance(arg, str) or len(arg) > CLAUDE_ARG_MAX:
            return self.send(400, {"error": "unknown command"})
        if sys.platform != "darwin":
            return self.send(501, {"error": "mac only"})
        try:
            open_claude(cmd, arg)
        except (OSError, subprocess.SubprocessError):
            return self.send(500, {"error": "could not open Terminal"})
        return self.send(200, {"ok": True})

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
