#!/usr/bin/env python3
"""VINEPRO ボード — 自前サーバー版。

claude.ai の Artifact で動いていた index.html を、そのままスタッフ向けに配信する。
- メールアドレスとパスワードでログイン（オーナーがアカウントを発行する）
- 権限: owner（管理者・1人）/ editor（編集できる）/ viewer（見るだけ）。停止中のアカウントはログイン不可
- 最終ログイン・最終アクセス・オンライン中かどうか
- 編集履歴（誰が・いつ・何を）と、オーナーによる「この変更を元に戻す」
- 同時編集: Server-Sent Events で変更とカーソルを全員に配る

Python 標準ライブラリだけで動く（自動翻訳だけは `pip install anthropic` と ANTHROPIC_API_KEY が必要）。

使い方:
  python3 server.py adduser --email you@example.com --name たっちゃん --role owner   # 最初に1回
  python3 server.py                     # http://localhost:4848
  python3 server.py import data/all.json  # 既存データの取り込み（上書き）
"""
import argparse
import getpass
import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import sqlite3
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("VB_DATA_DIR") or os.path.join(HERE, "var")
DB_PATH = os.path.join(DATA_DIR, "board.db")
PORT = int(os.environ.get("PORT") or os.environ.get("VB_PORT") or 4848)
HOST = os.environ.get("VB_HOST", "0.0.0.0")
COLS = ("boards", "items", "edges", "tasks", "strokes")
ROLES = ("owner", "editor", "viewer")
SESSION_SEC = 30 * 86400
COALESCE_SEC = 90          # 同じ人が同じ要素を続けて直したら、履歴は1行にまとめる
COOKIE = "vb_session"
MODEL = os.environ.get("VB_MODEL", "claude-opus-5-5")
COLORS = ["#2B55C9", "#C2410C", "#15803D", "#9333EA", "#B91C1C", "#0E7490", "#A16207", "#BE185D", "#4D7C0F", "#1D4ED8"]


def now():
    return time.time()


# ================= storage =================
class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users(
                  id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                  role TEXT NOT NULL, pw TEXT NOT NULL, color TEXT, disabled INTEGER DEFAULT 0,
                  created_at REAL, last_login_at REAL, last_seen_at REAL);
                CREATE TABLE IF NOT EXISTS sessions(
                  token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                  created_at REAL, expires_at REAL, ip TEXT, ua TEXT);
                CREATE TABLE IF NOT EXISTS docs(
                  col TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
                  updated_at REAL, updated_by TEXT, PRIMARY KEY(col, id));
                CREATE TABLE IF NOT EXISTS history(
                  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, last_at REAL,
                  user_id TEXT, user_name TEXT, col TEXT, doc_id TEXT, action TEXT,
                  board TEXT, label TEXT, fields TEXT, before TEXT, after TEXT, reverted_by INTEGER);
                CREATE INDEX IF NOT EXISTS history_doc ON history(col, doc_id, id);
                CREATE INDEX IF NOT EXISTS history_at ON history(at);
                CREATE TABLE IF NOT EXISTS logins(
                  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, at REAL, ip TEXT, ua TEXT);
                """
            )

    def q(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def one(self, sql, args=()):
        r = self.q(sql, args)
        return r[0] if r else None

    def x(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args)


S = None  # Store


# ================= passwords & sessions =================
def hash_pw(pw):
    salt = secrets.token_bytes(16)
    it = 240000
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, it)
    return f"pbkdf2_sha256${it}${salt.hex()}${dk.hex()}"


def check_pw(pw, stored):
    try:
        _, it, salt, dk = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(got.hex(), dk)
    except Exception:
        return False


def tok_hash(t):
    return hashlib.sha256(t.encode()).hexdigest()


def temp_password():
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(12))


def norm_email(e):
    return (e or "").strip().lower()


def valid_email(e):
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e or ""))


def create_user(email, name, role, pw):
    email = norm_email(email)
    if not valid_email(email):
        raise ValueError("bad_email")
    if role not in ROLES:
        raise ValueError("bad_role")
    if len(pw) < 8:
        raise ValueError("short_password")
    if S.one("SELECT 1 FROM users WHERE email=?", (email,)):
        raise ValueError("email_taken")
    n = S.one("SELECT COUNT(*) c FROM users")["c"]
    uid = "u_" + secrets.token_hex(6)
    S.x("INSERT INTO users(id,email,name,role,pw,color,created_at) VALUES(?,?,?,?,?,?,?)",
        (uid, email, (name or email.split("@")[0]).strip()[:40], role, hash_pw(pw), COLORS[n % len(COLORS)], now()))
    return uid


def user_public(u, online=None):
    d = {"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"], "color": u["color"],
         "disabled": bool(u["disabled"]), "createdAt": u["created_at"], "lastLoginAt": u["last_login_at"],
         "lastSeenAt": u["last_seen_at"]}
    if online is not None:
        d["online"] = online
    return d


# ================= realtime hub =================
class Hub:
    def __init__(self):
        self.lock = threading.Lock()
        self.clients = {}  # peer -> {user, q, presence}
        self.seq = 0

    def next_seq(self):
        with self.lock:
            self.seq += 1
            return self.seq

    def add(self, peer, uid):
        with self.lock:
            old = self.clients.get(peer)
            if old:
                old["q"].put(None)
            c = {"user": uid, "q": queue.Queue(maxsize=2000), "presence": {}}
            self.clients[peer] = c
        self.peers_changed()
        return c

    def remove(self, peer, c):
        with self.lock:
            if self.clients.get(peer) is c:
                del self.clients[peer]
            else:
                return
        self.peers_changed()

    def send(self, event, data, only_user=None):
        msg = (event, json.dumps(data, ensure_ascii=False))
        with self.lock:
            targets = list(self.clients.values())
        for c in targets:
            if only_user and c["user"] != only_user:
                continue
            try:
                c["q"].put_nowait(msg)
            except queue.Full:
                pass  # 読めていない接続。ブラウザ側は再接続時に全件を取り直す

    def peers(self):
        with self.lock:
            return [{"peer": p, "by": c["user"], "presence": c["presence"]} for p, c in self.clients.items()]

    def peers_changed(self):
        self.send("peers", {"peers": self.peers()})

    def set_presence(self, peer, uid, presence):
        with self.lock:
            c = self.clients.get(peer)
            if not c or c["user"] != uid:
                return False
            p = dict(c["presence"])
            p.update(presence)
            c["presence"] = p
        self.send("presence", {"peer": peer, "presence": p})
        return True

    def online_users(self):
        with self.lock:
            out = {}
            for c in self.clients.values():
                out[c["user"]] = out.get(c["user"], 0) + 1
            return out

    def kick(self, uid):
        """そのユーザーの画面に「権限が変わった」と伝えて接続を切る。"""
        with self.lock:
            targets = [c for c in self.clients.values() if c["user"] == uid]
        for c in targets:
            try:
                c["q"].put_nowait(("auth", "{}"))
            except queue.Full:
                pass
            c["q"].put(None)


HUB = Hub()


# ================= documents & history =================
def deep_merge(a, b):
    o = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(o.get(k), dict):
            o[k] = deep_merge(o[k], v)
        else:
            o[k] = v
    return o


def get_doc(col, did):
    r = S.one("SELECT data FROM docs WHERE col=? AND id=?", (col, did))
    return json.loads(r["data"]) if r else None


def doc_label(col, d):
    if not d:
        return ""
    tr = d.get("tr") or {}
    ja = tr.get("ja") or {}
    for f in ("title", "name", "label", "body", "note"):
        v = d.get(f) or ja.get(f)
        if isinstance(v, str) and v.strip():
            v = " ".join(v.split())
            return v[:60]
    return ""


IGNORED = {"by", "at"}


def changed_fields(before, after):
    keys = set((before or {}).keys()) | set((after or {}).keys())
    return sorted(k for k in keys if k not in IGNORED and (before or {}).get(k) != (after or {}).get(k))


def record(user, col, did, action, before, after):
    t = now()
    fields = changed_fields(before, after)
    board = (after or before or {}).get("board") or (did if col == "boards" else "")
    label = doc_label(col, after) or doc_label(col, before)
    last = S.one("SELECT * FROM history WHERE col=? AND doc_id=? ORDER BY id DESC LIMIT 1", (col, did))
    if (last and action == "update" and last["user_id"] == user["id"] and last["action"] in ("create", "update")
            and t - last["last_at"] < COALESCE_SEC and not last["reverted_by"]):
        merged = sorted(set(json.loads(last["fields"] or "[]")) | set(fields))
        S.x("UPDATE history SET last_at=?, after=?, fields=?, label=? WHERE id=?",
            (t, json.dumps(after, ensure_ascii=False), json.dumps(merged), label, last["id"]))
        return last["id"]
    cur = S.x("INSERT INTO history(at,last_at,user_id,user_name,col,doc_id,action,board,label,fields,before,after) "
              "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
              (t, t, user["id"], user["name"], col, did, action, board, label, json.dumps(fields),
               json.dumps(before, ensure_ascii=False) if before is not None else None,
               json.dumps(after, ensure_ascii=False) if after is not None else None))
    return cur.lastrowid


def write_doc(user, col, did, mode, data, origin=None, action=None):
    """mode: set / update / delete。履歴を取り、全員に配る。"""
    with S.lock:
        before = get_doc(col, did)
        if mode == "update":
            if before is None:
                raise LookupError("not_found")
            after = deep_merge(before, data)
        elif mode == "set":
            after = data
        else:
            after = None
            if before is None:
                return None
        if after is None:
            S.x("DELETE FROM docs WHERE col=? AND id=?", (col, did))
        else:
            S.x("INSERT INTO docs(col,id,data,updated_at,updated_by) VALUES(?,?,?,?,?) "
                "ON CONFLICT(col,id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                (col, did, json.dumps(after, ensure_ascii=False), now(), user["id"]))
        act = action or ("delete" if after is None else ("create" if before is None else "update"))
        hid = None
        if before != after:
            hid = record(user, col, did, act, before, after)
        seq = HUB.next_seq()
    HUB.send("doc", {"seq": seq, "col": col, "id": did, "type": "delete" if after is None else "set",
                     "data": after, "origin": origin})
    return {"seq": seq, "history": hid}


def import_all(all_data, user=None):
    with S.lock:
        S.x("BEGIN")
        try:
            S.x("DELETE FROM docs")
            n = 0
            for col in COLS:
                for d in all_data.get(col) or []:
                    d = dict(d)
                    did = str(d.pop("id"))
                    S.x("INSERT INTO docs(col,id,data,updated_at,updated_by) VALUES(?,?,?,?,?)",
                        (col, did, json.dumps(d, ensure_ascii=False), now(), user["id"] if user else None))
                    n += 1
            if user:
                t = now()
                S.x("INSERT INTO history(at,last_at,user_id,user_name,col,doc_id,action,board,label,fields) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (t, t, user["id"], user["name"], "*", "*", "import", "", f"{n}", "[]"))
            S.x("COMMIT")
        except Exception:
            S.x("ROLLBACK")
            raise
    return n


def export_all():
    out = {c: [] for c in COLS}
    for r in S.q("SELECT col,id,data FROM docs ORDER BY col,id"):
        if r["col"] in out:
            d = {"id": r["id"]}
            d.update(json.loads(r["data"]))
            out[r["col"]].append(d)
    return out


# ================= translation (optional) =================
_anthropic = None


def translate_ready():
    global _anthropic
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    if _anthropic is None:
        try:
            import anthropic  # noqa
            _anthropic = anthropic.Anthropic()
        except Exception:
            _anthropic = False
    return bool(_anthropic)


def run_sample(prompt):
    resp = _anthropic.beta.messages.create(
        model=MODEL,
        max_tokens=4000,
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},
        messages=[{"role": "user", "content": prompt}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("refusal")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("invalid_json")
    return json.loads(m.group(0))


# ================= login rate limit =================
FAILS = {}
FAILS_LOCK = threading.Lock()


def too_many(key):
    with FAILS_LOCK:
        ts = [t for t in FAILS.get(key, []) if now() - t < 900]
        FAILS[key] = ts
        return len(ts) >= 10


def add_fail(key):
    with FAILS_LOCK:
        FAILS.setdefault(key, []).append(now())


# ================= HTTP =================
def read_file(*p):
    with open(os.path.join(HERE, *p), encoding="utf-8") as f:
        return f.read()


def board_page():
    head = ('<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            '<meta name="robots" content="noindex">'
            '<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 16 16%22%3E%3Crect width=%2216%22 height=%2216%22 rx=%223%22 fill=%22%232B55C9%22/%3E%3C/svg%3E">'
            '<style>body{margin:0;font:14px system-ui,sans-serif}[hidden]{display:none!important}</style>'
            '<script src="/static/shim.js"></script></head><body>')
    return head + read_file("index.html") + "</body></html>"


class H(BaseHTTPRequestHandler):
    server_version = "VineproBoard/1.0"
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("VB_QUIET"):
            return
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # ---- helpers ----
    def ip(self):
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def secure(self):
        return os.environ.get("VB_SECURE") == "1" or self.headers.get("X-Forwarded-Proto") == "https"

    def send(self, code, body=b"", ctype="application/json; charset=utf-8", headers=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, code, obj, headers=None):
        self.send(code, json.dumps(obj, ensure_ascii=False), headers=headers)

    def err(self, code, ecode, msg=""):
        self.json(code, {"error": ecode, "message": msg})

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 20 * 1024 * 1024:
            raise ValueError("too_large")
        raw = self.rfile.read(n) if n else b""
        return json.loads(raw or b"{}")

    def redirect(self, to):
        self.send(302, "", "text/plain", {"Location": to})

    def session_user(self):
        c = SimpleCookie(self.headers.get("Cookie") or "")
        if COOKIE not in c:
            return None
        th = tok_hash(c[COOKIE].value)
        r = S.one("SELECT u.* , s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?", (th,))
        if not r or r["expires_at"] < now() or r["disabled"]:
            return None
        t = now()
        if not r["last_seen_at"] or t - r["last_seen_at"] > 30:
            S.x("UPDATE users SET last_seen_at=? WHERE id=?", (t, r["id"]))
        return r

    def cookie_header(self, token, max_age):
        parts = [f"{COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={max_age}"]
        if self.secure():
            parts.append("Secure")
        return "; ".join(parts)

    def csrf_ok(self):
        # 書き込み系は独自ヘッダー必須（他サイトのフォームからは送れない）
        return self.headers.get("X-VB") == "1"

    # ---- routing ----
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        qs = parse_qs(u.query)
        try:
            if p == "/healthz":
                return self.send(200, "ok", "text/plain")
            if p.startswith("/static/"):
                return self.static(p[len("/static/"):])
            if p == "/login":
                return self.send(200, read_file("static", "login.html"), "text/html; charset=utf-8")
            user = self.session_user()
            if p in ("/", "/index.html"):
                if not user:
                    return self.redirect("/login")
                return self.send(200, board_page(), "text/html; charset=utf-8")
            if p == "/admin":
                if not user:
                    return self.redirect("/login?next=/admin")
                return self.send(200, read_file("static", "admin.html"), "text/html; charset=utf-8")
            if not p.startswith("/api/"):
                return self.send(404, "not found", "text/plain")
            if not user:
                return self.err(401, "unauthenticated")
            return self.api_get(user, p, qs)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        self.mutate("POST")

    def do_PUT(self):
        self.mutate("PUT")

    def do_PATCH(self):
        self.mutate("PATCH")

    def do_DELETE(self):
        self.mutate("DELETE")

    def mutate(self, method):
        p = urlparse(self.path).path
        try:
            if not self.csrf_ok():
                return self.err(403, "csrf")
            try:
                data = self.body()
            except Exception:
                return self.err(400, "bad_json")
            if p == "/api/login" and method == "POST":
                return self.login(data)
            if p == "/api/logout" and method == "POST":
                c = SimpleCookie(self.headers.get("Cookie") or "")
                if COOKIE in c:
                    S.x("DELETE FROM sessions WHERE token=?", (tok_hash(c[COOKIE].value),))
                return self.json(200, {"ok": True}, {"Set-Cookie": self.cookie_header("", 0)})
            user = self.session_user()
            if not user:
                return self.err(401, "unauthenticated")
            return self.api_write(user, method, p, data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def static(self, name):
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", name):
            return self.send(404, "not found", "text/plain")
        path = os.path.join(HERE, "static", name)
        if not os.path.isfile(path):
            return self.send(404, "not found", "text/plain")
        ext = name.rsplit(".", 1)[-1]
        ctype = {"js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8",
                 "html": "text/html; charset=utf-8", "svg": "image/svg+xml"}.get(ext, "application/octet-stream")
        with open(path, "rb") as f:
            self.send(200, f.read(), ctype)

    # ---- auth ----
    def login(self, data):
        email = norm_email(data.get("email"))
        pw = data.get("password") or ""
        key_ip, key_em = "ip:" + self.ip(), "em:" + email
        if too_many(key_ip) or too_many(key_em):
            return self.err(429, "too_many", "")
        u = S.one("SELECT * FROM users WHERE email=?", (email,))
        if not u or not check_pw(pw, u["pw"]):
            add_fail(key_ip)
            add_fail(key_em)
            return self.err(401, "bad_login")
        if u["disabled"]:
            return self.err(403, "disabled")
        token = secrets.token_urlsafe(32)
        t = now()
        ua = (self.headers.get("User-Agent") or "")[:200]
        S.x("INSERT INTO sessions(token,user_id,created_at,expires_at,ip,ua) VALUES(?,?,?,?,?,?)",
            (tok_hash(token), u["id"], t, t + SESSION_SEC, self.ip(), ua))
        S.x("UPDATE users SET last_login_at=?, last_seen_at=? WHERE id=?", (t, t, u["id"]))
        S.x("INSERT INTO logins(user_id,at,ip,ua) VALUES(?,?,?,?)", (u["id"], t, self.ip(), ua))
        S.x("DELETE FROM sessions WHERE expires_at<?", (t,))
        return self.json(200, {"ok": True}, {"Set-Cookie": self.cookie_header(token, SESSION_SEC)})

    # ---- GET API ----
    def api_get(self, user, p, qs):
        if p == "/api/me":
            return self.json(200, {**user_public(user), "canEdit": user["role"] in ("owner", "editor"),
                                   "isOwner": user["role"] == "owner", "translate": translate_ready()})
        if p == "/api/profiles":
            rows = S.q("SELECT id,name,color FROM users")
            return self.json(200, {r["id"]: {"id": r["id"], "name": r["name"], "color": r["color"]} for r in rows})
        m = re.fullmatch(r"/api/db/([a-z]+)", p)
        if m:
            col = m.group(1)
            if col not in COLS:
                return self.err(404, "not_found")
            with S.lock:
                seq = HUB.seq
                rows = S.q("SELECT id,data FROM docs WHERE col=?", (col,))
            return self.send(200, '{"seq":%d,"docs":[%s]}' % (seq, ",".join(
                '{"id":%s,"data":%s}' % (json.dumps(r["id"], ensure_ascii=False), r["data"]) for r in rows)))
        if p == "/api/stream":
            return self.stream(user, (qs.get("peer") or [""])[0])
        if p == "/api/history":
            return self.history(user, qs)
        m = re.fullmatch(r"/api/history/(\d+)", p)
        if m:
            r = S.one("SELECT * FROM history WHERE id=?", (int(m.group(1)),))
            if not r:
                return self.err(404, "not_found")
            d = dict(r)
            d["fields"] = json.loads(d["fields"] or "[]")
            d["before"] = json.loads(d["before"]) if d["before"] else None
            d["after"] = json.loads(d["after"]) if d["after"] else None
            return self.json(200, d)
        if user["role"] != "owner":
            return self.err(403, "forbidden")
        if p == "/api/admin/users":
            on = HUB.online_users()
            rows = S.q("SELECT * FROM users ORDER BY (role='owner') DESC, disabled, created_at")
            out = []
            for r in rows:
                d = user_public(r, online=on.get(r["id"], 0) > 0)
                d["sessions"] = S.one("SELECT COUNT(*) c FROM sessions WHERE user_id=? AND expires_at>?", (r["id"], now()))["c"]
                d["edits"] = S.one("SELECT COUNT(*) c FROM history WHERE user_id=?", (r["id"],))["c"]
                out.append(d)
            return self.json(200, {"users": out})
        if p == "/api/admin/logins":
            rows = S.q("SELECT l.*, u.name, u.email FROM logins l LEFT JOIN users u ON u.id=l.user_id ORDER BY l.id DESC LIMIT 200")
            return self.json(200, {"logins": [dict(r) for r in rows]})
        if p == "/api/admin/export":
            body = json.dumps(export_all(), ensure_ascii=False, indent=1)
            fn = time.strftime("vinepro-board-%Y%m%d-%H%M.json")
            return self.send(200, body, headers={"Content-Disposition": f'attachment; filename="{fn}"'})
        return self.err(404, "not_found")

    def history(self, user, qs):
        limit = min(int((qs.get("limit") or ["100"])[0]), 500)
        before = (qs.get("before") or [None])[0]
        where, args = [], []
        if before:
            where.append("id<?")
            args.append(int(before))
        for k, colname in (("user", "user_id"), ("board", "board"), ("col", "col"), ("doc", "doc_id")):
            v = (qs.get(k) or [None])[0]
            if v:
                where.append(f"{colname}=?")
                args.append(v)
        sql = ("SELECT id,at,last_at,user_id,user_name,col,doc_id,action,board,label,fields,reverted_by,"
               "json_extract(coalesce(after,before),'$.kind') AS kind FROM history")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = []
        for r in S.q(sql, args):
            d = dict(r)
            d["fields"] = json.loads(d["fields"] or "[]")
            rows.append(d)
        return self.json(200, {"history": rows})

    def stream(self, user, peer):
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", peer or ""):
            return self.err(400, "bad_peer")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        c = HUB.add(peer, user["id"])
        uid = user["id"]
        role = user["role"]
        try:
            self.wfile.write(("retry: 3000\nevent: hello\ndata: %s\n\n" % json.dumps(
                {"seq": HUB.seq, "peers": HUB.peers()})).encode())
            self.wfile.flush()
            last_check = now()
            while True:
                try:
                    msg = c["q"].get(timeout=15)
                except queue.Empty:
                    msg = ("ping", "{}")
                if msg is None:
                    break
                ev, data = msg
                self.wfile.write(f"event: {ev}\ndata: {data}\n\n".encode())
                self.wfile.flush()
                if ev == "auth":
                    break
                if now() - last_check > 20:
                    last_check = now()
                    r = S.one("SELECT role,disabled FROM users WHERE id=?", (uid,))
                    if not r or r["disabled"] or r["role"] != role:
                        self.wfile.write(b"event: auth\ndata: {}\n\n")
                        self.wfile.flush()
                        break
                    S.x("UPDATE users SET last_seen_at=? WHERE id=?", (now(), uid))
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            S.x("UPDATE users SET last_seen_at=? WHERE id=?", (now(), uid))
            HUB.remove(peer, c)

    # ---- write API ----
    def api_write(self, user, method, p, data):
        is_owner = user["role"] == "owner"
        can_edit = user["role"] in ("owner", "editor")
        m = re.fullmatch(r"/api/db/([a-z]+)/([^/]+)", p)
        if m:
            col, did = m.group(1), unquote(m.group(2))
            if col not in COLS or len(did) > 120:
                return self.err(404, "not_found")
            if not can_edit:
                return self.err(403, "invalid_argument", "read_only")
            mode = {"PUT": "set", "PATCH": "update", "DELETE": "delete"}.get(method)
            if not mode:
                return self.err(405, "method")
            payload = data.get("data") if mode != "delete" else None
            if mode != "delete" and not isinstance(payload, dict):
                return self.err(400, "bad_data")
            try:
                r = write_doc(user, col, did, mode, payload, origin=data.get("origin"))
            except LookupError:
                return self.err(404, "not_found")
            return self.json(200, {"ok": True, **(r or {})})
        if p == "/api/presence" and method == "POST":
            pres = data.get("presence")
            if not isinstance(pres, dict) or len(json.dumps(pres)) > 2000:
                return self.err(400, "bad_presence")
            HUB.set_presence(str(data.get("peer")), user["id"], pres)
            return self.json(200, {"ok": True})
        if p == "/api/sample" and method == "POST":
            if not can_edit:
                return self.err(403, "invalid_argument")
            if not translate_ready():
                return self.err(501, "sampling_disabled")
            prompt = data.get("prompt") or ""
            if not isinstance(prompt, str) or len(prompt) > 20000:
                return self.err(400, "bad_prompt")
            try:
                return self.json(200, {"result": run_sample(prompt)})
            except Exception as e:
                sys.stderr.write(f"translate failed: {e!r}\n")
                return self.err(502, "sample_failed")
        if p == "/api/me/password" and method == "POST":
            if not check_pw(data.get("current") or "", user["pw"]):
                return self.err(400, "bad_current")
            new = data.get("new") or ""
            if len(new) < 8:
                return self.err(400, "short_password")
            S.x("UPDATE users SET pw=? WHERE id=?", (hash_pw(new), user["id"]))
            # ほかの端末のログインは切る（今の端末は残す）
            c = SimpleCookie(self.headers.get("Cookie") or "")
            S.x("DELETE FROM sessions WHERE user_id=? AND token<>?", (user["id"], tok_hash(c[COOKIE].value)))
            return self.json(200, {"ok": True})
        m = re.fullmatch(r"/api/history/(\d+)/revert", p)
        if m and method == "POST":
            if not is_owner:
                return self.err(403, "forbidden")
            h = S.one("SELECT * FROM history WHERE id=?", (int(m.group(1)),))
            if not h or h["col"] not in COLS:
                return self.err(404, "not_found")
            before = json.loads(h["before"]) if h["before"] else None
            r = write_doc(user, h["col"], h["doc_id"], "delete" if before is None else "set", before, action="revert")
            S.x("UPDATE history SET reverted_by=? WHERE id=?", ((r or {}).get("history") or -1, h["id"]))
            return self.json(200, {"ok": True})
        # ---- owner only ----
        if not is_owner:
            return self.err(403, "forbidden")
        if p == "/api/admin/users" and method == "POST":
            pw = data.get("password") or temp_password()
            try:
                uid = create_user(data.get("email"), data.get("name"), data.get("role") or "editor", pw)
            except ValueError as e:
                return self.err(400, str(e))
            if data.get("role") == "owner":
                S.x("UPDATE users SET role='editor' WHERE id=?", (uid,))
            return self.json(200, {"ok": True, "id": uid, "password": pw})
        m = re.fullmatch(r"/api/admin/users/([A-Za-z0-9_]+)(/[a-z]+)?", p)
        if m:
            uid, sub = m.group(1), m.group(2)
            target = S.one("SELECT * FROM users WHERE id=?", (uid,))
            if not target:
                return self.err(404, "not_found")
            self_target = uid == user["id"]
            if sub == "/password" and method == "POST":
                pw = temp_password()
                S.x("UPDATE users SET pw=? WHERE id=?", (hash_pw(pw), uid))
                S.x("DELETE FROM sessions WHERE user_id=?", (uid,))
                if not self_target:
                    HUB.kick(uid)
                return self.json(200, {"ok": True, "password": pw})
            if sub == "/logout" and method == "POST":
                if self_target:
                    return self.err(400, "self")
                S.x("DELETE FROM sessions WHERE user_id=?", (uid,))
                HUB.kick(uid)
                return self.json(200, {"ok": True})
            if sub is None and method == "PATCH":
                if self_target and ("role" in data or "disabled" in data):
                    return self.err(400, "self")
                if "role" in data:
                    if data["role"] not in ("editor", "viewer"):
                        return self.err(400, "bad_role")
                    S.x("UPDATE users SET role=? WHERE id=?", (data["role"], uid))
                if "disabled" in data:
                    S.x("UPDATE users SET disabled=? WHERE id=?", (1 if data["disabled"] else 0, uid))
                    if data["disabled"]:
                        S.x("DELETE FROM sessions WHERE user_id=?", (uid,))
                if "name" in data and str(data["name"]).strip():
                    S.x("UPDATE users SET name=? WHERE id=?", (str(data["name"]).strip()[:40], uid))
                if "email" in data:
                    em = norm_email(data["email"])
                    if not valid_email(em):
                        return self.err(400, "bad_email")
                    if S.one("SELECT 1 FROM users WHERE email=? AND id<>?", (em, uid)):
                        return self.err(400, "email_taken")
                    S.x("UPDATE users SET email=? WHERE id=?", (em, uid))
                if ("role" in data or data.get("disabled")) and not self_target:
                    HUB.kick(uid)
                return self.json(200, {"ok": True})
            if sub is None and method == "DELETE":
                if self_target or target["role"] == "owner":
                    return self.err(400, "self")
                HUB.kick(uid)
                S.x("DELETE FROM users WHERE id=?", (uid,))
                return self.json(200, {"ok": True})
        if p == "/api/admin/import" and method == "POST":
            if not isinstance(data, dict) or not any(isinstance(data.get(c), list) for c in COLS):
                return self.err(400, "bad_data")
            n = import_all(data, user)
            HUB.send("reload", {})
            return self.json(200, {"ok": True, "count": n})
        return self.err(404, "not_found")


# ================= CLI =================
def cmd_serve(_args):
    if not S.one("SELECT 1 FROM users WHERE role='owner'"):
        em, pw = os.environ.get("VB_OWNER_EMAIL"), os.environ.get("VB_OWNER_PASSWORD")
        if em and pw:
            create_user(em, os.environ.get("VB_OWNER_NAME") or "Owner", "owner", pw)
            print(f"オーナーを作成しました: {em}")
        else:
            print("まだオーナーがいません。先に次を実行してください:\n"
                  "  python3 server.py adduser --email あなたのメール --name 名前 --role owner")
            sys.exit(1)
    if not S.one("SELECT 1 FROM docs LIMIT 1"):
        for seed in (os.path.join(DATA_DIR, "seed.json"), os.path.join(HERE, "data", "all.json")):
            if os.path.isfile(seed):
                with open(seed, encoding="utf-8") as f:
                    n = import_all(json.load(f))
                print(f"初期データを読み込みました: {seed}（{n}件）")
                break
    srv = ThreadingHTTPServer((HOST, PORT), H)
    srv.daemon_threads = True
    print(f"VINEPRO ボード: http://localhost:{PORT}  （データ: {DB_PATH}）"
          f"  自動翻訳: {'オン' if translate_ready() else 'オフ（ANTHROPIC_API_KEY 未設定）'}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def cmd_adduser(a):
    pw = a.password or os.environ.get("VB_PASSWORD")
    if not pw:
        pw = getpass.getpass("パスワード（8文字以上）: ")
        if getpass.getpass("もう一度: ") != pw:
            sys.exit("パスワードが一致しません")
    try:
        uid = create_user(a.email, a.name, a.role, pw)
    except ValueError as e:
        sys.exit(f"作成できません: {e}")
    print(f"作成しました: {a.email}（{a.role}） id={uid}")


def cmd_passwd(a):
    u = S.one("SELECT id FROM users WHERE email=?", (norm_email(a.email),))
    if not u:
        sys.exit("そのメールアドレスのアカウントはありません")
    pw = a.password or getpass.getpass("新しいパスワード（8文字以上）: ")
    if len(pw) < 8:
        sys.exit("8文字以上にしてください")
    S.x("UPDATE users SET pw=?, disabled=0 WHERE id=?", (hash_pw(pw), u["id"]))
    S.x("DELETE FROM sessions WHERE user_id=?", (u["id"],))
    print("変更しました")


def cmd_import(a):
    with open(a.file, encoding="utf-8") as f:
        n = import_all(json.load(f))
    print(f"{n}件を読み込みました（既存のボードの中身は置き換えました）")


def cmd_export(a):
    data = json.dumps(export_all(), ensure_ascii=False, indent=1)
    if a.file:
        with open(a.file, "w", encoding="utf-8") as f:
            f.write(data)
        print(f"書き出しました: {a.file}")
    else:
        print(data)


def cmd_users(_a):
    for r in S.q("SELECT * FROM users ORDER BY created_at"):
        fmt = lambda t: time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "-"
        print(f"{r['email']:32} {r['role']:7} {'停止中' if r['disabled'] else '':4} 最終ログイン {fmt(r['last_login_at'])}  {r['name']}")


def main():
    global S
    ap = argparse.ArgumentParser(description="VINEPRO ボード サーバー")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("serve")
    p = sub.add_parser("adduser")
    p.add_argument("--email", required=True)
    p.add_argument("--name", default="")
    p.add_argument("--role", default="editor", choices=ROLES)
    p.add_argument("--password")
    p = sub.add_parser("passwd")
    p.add_argument("--email", required=True)
    p.add_argument("--password")
    p = sub.add_parser("import")
    p.add_argument("file")
    p = sub.add_parser("export")
    p.add_argument("file", nargs="?")
    sub.add_parser("users")
    a = ap.parse_args()
    S = Store(DB_PATH)
    {"serve": cmd_serve, None: cmd_serve, "adduser": cmd_adduser, "passwd": cmd_passwd,
     "import": cmd_import, "export": cmd_export, "users": cmd_users}[a.cmd](a)


if __name__ == "__main__":
    main()
