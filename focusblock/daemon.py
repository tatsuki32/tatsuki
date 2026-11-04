#!/usr/bin/env python3
"""FocusBlock daemon.

Runs as root under launchd. It
  * serves the settings UI on http://127.0.0.1:47800
  * runs a filtering HTTP/HTTPS proxy on 127.0.0.1:47801 and points the system
    proxy at it while website blocking is active (works for every browser that
    follows the macOS proxy settings: Safari, Chrome, Arc, Edge, Brave, Firefox...)
  * quits blocked apps while app blocking is active
  * starts sessions from recurring schedules

Only standard library, compatible with the Python 3.9 shipped with macOS.
"""
import argparse
import asyncio
import datetime
import hashlib
import hmac
import json
import os
import plistlib
import secrets
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

UI_PORT = 47800
PROXY_PORT = 47801
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DATA_DIR = "/Library/Application Support/FocusBlock"
MAX_SESSION_SECONDS = 30 * 24 * 3600

# Apps that are never quit, whatever the settings say.
ALWAYS_ALLOWED_APPS = {"FocusBlock", "Claude", "System Settings", "System Preferences", "Finder"}
# Hosts (and their subdomains) that always pass through the proxy, whatever the settings say.
ALWAYS_ALLOWED_HOSTS = ["localhost", "127.0.0.1", "::1",
                        "claude.ai", "claude.com", "anthropic.com", "claudeusercontent.com"]
ALLOWLIST_EXTRA_HOSTS = ["apple.com", "icloud.com", "apple-cloudkit.com", "aaplimg.com",
                         "mzstatic.com", "apple-dns.net", "cdn-apple.com"]

DEFAULT_CONFIG = {
    "password": None,
    "apps": {"mode": "block", "list": []},
    "web": {"mode": "block", "list": ["youtube.com", "x.com", "twitter.com", "instagram.com",
                                      "tiktok.com", "facebook.com", "reddit.com", "netflix.com"]},
    "schedules": [],
    "session": None,
    "skips": {},
    "saved_proxies": None,
}

DEV = False
DATA_DIR = ROOT_DATA_DIR
LOCK = threading.RLock()
CFG = {}
# Snapshot read by the proxy without taking the lock.
POLICY = {"active": False, "mode": "block", "list": []}


def log(*args):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *args, file=sys.stderr, flush=True)


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout
    except Exception as e:
        log("command failed", cmd, e)
        return ""


# ---------------------------------------------------------------- config

def config_path():
    return os.path.join(DATA_DIR, "config.json")


def load_config():
    global CFG
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(config_path()) as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        log("config unreadable, using defaults:", e)
    CFG = cfg


def save_config():
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = config_path() + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(CFG, f, ensure_ascii=False, indent=1)
    os.replace(tmp, config_path())


def hash_password(pw, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200000).hex()
    return {"salt": salt, "hash": h}


def check_password(pw):
    stored = CFG.get("password")
    if not stored or not isinstance(pw, str):
        return False
    ok = hmac.compare_digest(hash_password(pw, stored["salt"])["hash"], stored["hash"])
    if not ok:
        time.sleep(1)  # slow down guessing
    return ok


def normalize_domain(s):
    s = s.strip().lower()
    if "://" in s:
        s = s.split("://", 1)[1]
    s = s.split("/", 1)[0].split("?", 1)[0].split(":", 1)[0].strip(".")
    if s.startswith("www."):
        s = s[4:]
    return s


# ---------------------------------------------------------------- scheduling

def parse_hm(s):
    h, m = s.split(":")
    return datetime.time(int(h), int(m))


def schedule_window(sch, now):
    """Return (start, end) epochs of this schedule's window containing `now`, or None."""
    try:
        start_t, end_t = parse_hm(sch["start"]), parse_hm(sch["end"])
    except Exception:
        return None
    today = datetime.datetime.fromtimestamp(now).date()
    for day in (today, today - datetime.timedelta(days=1)):
        if day.weekday() not in sch.get("days", []):
            continue
        start = datetime.datetime.combine(day, start_t)
        end = datetime.datetime.combine(day, end_t)
        if end <= start:
            end += datetime.timedelta(days=1)  # overnight window
        s, e = start.timestamp(), end.timestamp()
        if s <= now < e:
            return s, e
    return None


def active_state(now=None):
    now = now or time.time()
    st = {"apps": False, "web": False, "end": None, "sources": []}
    sess = CFG.get("session")
    if sess and sess.get("end", 0) > now:
        st["apps"] |= bool(sess.get("apps"))
        st["web"] |= bool(sess.get("web"))
        st["sources"].append({"type": "session", "name": "手動セッション", "end": sess["end"]})
    for sch in CFG.get("schedules", []):
        if not sch.get("enabled", True):
            continue
        win = schedule_window(sch, now)
        if not win or CFG.get("skips", {}).get(sch.get("id"), 0) >= win[1]:
            continue
        st["apps"] |= bool(sch.get("apps"))
        st["web"] |= bool(sch.get("web"))
        st["sources"].append({"type": "schedule", "id": sch.get("id"),
                              "name": sch.get("name") or "スケジュール", "end": win[1]})
    if st["sources"]:
        st["end"] = max(s["end"] for s in st["sources"])
    st["active"] = st["apps"] or st["web"]
    return st


# ---------------------------------------------------------------- system proxy

def network_services():
    out = run(["networksetup", "-listallnetworkservices"])
    return [l for l in out.splitlines()[1:] if l.strip() and not l.startswith("*")]


def get_proxy(kind, svc):
    info = {"enabled": False, "server": "", "port": 0}
    for line in run(["networksetup", "-get%sproxy" % kind, svc]).splitlines():
        k, _, v = line.partition(":")
        v = v.strip()
        if k == "Enabled":
            info["enabled"] = v == "Yes"
        elif k == "Server":
            info["server"] = v
        elif k == "Port":
            info["port"] = int(v) if v.isdigit() else 0
    return info


def is_ours(p):
    return p["enabled"] and p["server"] == "127.0.0.1" and p["port"] == PROXY_PORT


def apply_proxy():
    if DEV:
        log("[dev] would set system proxy to 127.0.0.1:%d" % PROXY_PORT)
        return
    with LOCK:
        if CFG.get("saved_proxies") is None:
            saved = {}
            for svc in network_services():
                saved[svc] = {k: get_proxy(k, svc) for k in ("web", "secureweb")}
            CFG["saved_proxies"] = saved
            save_config()
    for svc in network_services():
        for kind in ("web", "secureweb"):
            run(["networksetup", "-set%sproxy" % kind, svc, "127.0.0.1", str(PROXY_PORT)])
    log("system proxy on")


def proxy_still_applied():
    if DEV:
        return True
    return all(is_ours(get_proxy("secureweb", svc)) for svc in network_services())


def restore_proxy():
    if DEV:
        log("[dev] would restore system proxy")
        return
    with LOCK:
        saved = CFG.get("saved_proxies") or {}
    for svc in network_services():
        for kind in ("web", "secureweb"):
            orig = saved.get(svc, {}).get(kind)
            if orig and orig["enabled"] and not is_ours(orig):
                run(["networksetup", "-set%sproxy" % kind, svc, orig["server"], str(orig["port"])])
            elif is_ours(get_proxy(kind, svc)) or orig is not None:
                run(["networksetup", "-set%sproxystate" % kind, svc, "off"])
    with LOCK:
        CFG["saved_proxies"] = None
        save_config()
    log("system proxy restored")


# ---------------------------------------------------------------- apps

_bundle_cache = {}


def bundle_is_regular_app(bundle):
    """True for normal Dock apps in /Applications, ~/Applications or /System/Applications."""
    if bundle in _bundle_cache:
        return _bundle_cache[bundle]
    ok = bundle.startswith(("/Applications/", "/System/Applications/")) or (
        bundle.startswith("/Users/") and "/Applications/" in bundle)
    if ok:
        try:
            with open(os.path.join(bundle, "Contents", "Info.plist"), "rb") as f:
                info = plistlib.load(f)
            if str(info.get("LSUIElement", "")).lower() in ("1", "true", "yes") or \
                    str(info.get("LSBackgroundOnly", "")).lower() in ("1", "true", "yes"):
                ok = False
        except Exception:
            pass
    _bundle_cache[bundle] = ok
    return ok


def running_apps():
    """Yield (pid, uid, bundle_path, app_name) for processes inside a .app bundle."""
    for line in run(["ps", "-axo", "pid=,uid=,comm="]).splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        pid, uid, path = parts
        i = path.find(".app/")
        if i < 0:
            continue
        bundle = path[:i + 4]
        yield int(pid), int(uid), bundle, os.path.basename(bundle)[:-4]


def console_user():
    out = run(["stat", "-f", "%Su %u", "/dev/console"]).split()
    return (out[0], out[1]) if len(out) == 2 else (None, None)


def notify(text):
    user, uid = console_user()
    if not user or user == "root":
        return
    script = 'display notification "%s" with title "FocusBlock"' % text.replace('"', "'")
    subprocess.Popen(["launchctl", "asuser", uid, "sudo", "-u", user, "osascript", "-e", script],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


_kill_seen = {}
_last_notice = {}


def enforce_apps(mode, names):
    names = set(names)
    now = time.time()
    for pid, uid, bundle, name in running_apps():
        if uid == 0 or name in ALWAYS_ALLOWED_APPS or pid == os.getpid():
            continue
        if mode == "block":
            blocked = name in names
        else:
            blocked = name not in names and bundle_is_regular_app(bundle)
        if not blocked:
            continue
        if DEV:
            if now - _last_notice.get(name, 0) > 10:
                log("[dev] would quit", name, pid)
                _last_notice[name] = now
            continue
        first = _kill_seen.setdefault(pid, now)
        try:
            os.kill(pid, signal.SIGKILL if now - first > 3 else signal.SIGTERM)
        except ProcessLookupError:
            pass
        if now - _last_notice.get(name, 0) > 10:
            _last_notice[name] = now
            log("blocked app", name)
            notify("%s はブロック中です" % name)
    for pid in [p for p, t in _kill_seen.items() if now - t > 60]:
        del _kill_seen[pid]


def installed_apps():
    user, _ = console_user()
    roots = ["/Applications", "/Applications/Utilities", "/System/Applications",
             "/System/Applications/Utilities"]
    if user:
        roots.append(os.path.join("/Users", user, "Applications"))
    names = set()
    for root in roots:
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for e in entries:
            p = os.path.join(root, e)
            if e.endswith(".app"):
                names.add(e[:-4])
            elif os.path.isdir(p) and not e.startswith("."):
                try:
                    names.update(x[:-4] for x in os.listdir(p) if x.endswith(".app"))
                except OSError:
                    pass
    for _, _, bundle, name in running_apps():
        if bundle_is_regular_app(bundle):
            names.add(name)
    return sorted(names, key=str.lower)


# ---------------------------------------------------------------- enforcement loop

def enforcer():
    proxy_on = False
    last_check = 0
    while True:
        try:
            with LOCK:
                st = active_state()
                apps_cfg = dict(CFG["apps"])
                web_cfg = dict(CFG["web"])
            want_web = st["web"] and (web_cfg["mode"] == "allow" or bool(web_cfg["list"]))
            hosts = list(web_cfg["list"])
            if web_cfg["mode"] == "allow":
                hosts += ALLOWLIST_EXTRA_HOSTS
            POLICY.update({"active": want_web, "mode": web_cfg["mode"], "list": hosts})
            if want_web and (not proxy_on or time.time() - last_check > 15):
                if not proxy_on or not proxy_still_applied():
                    apply_proxy()
                proxy_on, last_check = True, time.time()
            elif not want_web and proxy_on:
                restore_proxy()
                proxy_on = False
            if st["apps"] and (apps_cfg["mode"] == "allow" or apps_cfg["list"]):
                enforce_apps(apps_cfg["mode"], apps_cfg["list"])
        except Exception as e:
            log("enforcer error:", repr(e))
        time.sleep(1)


# ---------------------------------------------------------------- filtering proxy

BLOCK_PAGE = """<!doctype html><meta charset="utf-8"><title>ブロック中</title>
<body style="font-family:-apple-system,sans-serif;display:grid;place-items:center;height:90vh;
background:#111;color:#eee"><div style="text-align:center"><h1>🔒 ブロック中</h1>
<p>%s は FocusBlock によってブロックされています。</p></div></body>"""


def host_blocked(host):
    if not POLICY["active"]:
        return False
    h = host.lower().strip("[]").rstrip(".")
    if any(h == d or h.endswith("." + d) for d in ALWAYS_ALLOWED_HOSTS):
        return False
    hit = any(h == d or h.endswith("." + d) for d in POLICY["list"])
    return hit if POLICY["mode"] == "block" else not hit


async def pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle_proxy(reader, writer):
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
        lines = head.decode("latin-1").split("\r\n")
        method, target, version = lines[0].split(" ", 2)
        if method.upper() == "CONNECT":
            host, _, port = target.rpartition(":")
            host = host.strip("[]")
            if host_blocked(host):
                writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
                writer.close()
                return
            r2, w2 = await asyncio.wait_for(asyncio.open_connection(host, int(port or 443)), 15)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
        else:
            url = urlsplit(target)
            host = url.hostname
            if not host:
                raise ValueError("no host")
            if host_blocked(host):
                body = (BLOCK_PAGE % host).encode()
                writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/html; charset=utf-8\r\n"
                             b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
                await writer.drain()
                writer.close()
                return
            path = (url.path or "/") + ("?" + url.query if url.query else "")
            headers = [l for l in lines[1:] if l and not l.lower().startswith(
                ("proxy-connection:", "connection:", "keep-alive:"))]
            req = "%s %s %s\r\n%s\r\nConnection: close\r\n\r\n" % (method, path, version, "\r\n".join(headers))
            r2, w2 = await asyncio.wait_for(asyncio.open_connection(host, url.port or 80), 15)
            w2.write(req.encode("latin-1"))
        await asyncio.gather(pipe(reader, w2), pipe(r2, writer))
    except Exception:
        try:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            writer.close()
        except Exception:
            pass


def proxy_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    server = loop.run_until_complete(asyncio.start_server(handle_proxy, "127.0.0.1", PROXY_PORT,
                                                          limit=1 << 16))
    log("proxy listening on 127.0.0.1:%d" % PROXY_PORT)
    loop.run_until_complete(server.serve_forever())


# ---------------------------------------------------------------- UI / API

def public_state():
    st = active_state()
    return {
        "now": time.time(),
        "hasPassword": bool(CFG.get("password")),
        "active": st,
        "apps": CFG["apps"],
        "web": CFG["web"],
        "schedules": CFG["schedules"],
        "dev": DEV,
    }


def clean_settings(body):
    apps = body.get("apps") or {}
    web = body.get("web") or {}
    out = {
        "apps": {"mode": "allow" if apps.get("mode") == "allow" else "block",
                 "list": sorted({str(a).strip() for a in apps.get("list", []) if str(a).strip()})},
        "web": {"mode": "allow" if web.get("mode") == "allow" else "block",
                "list": sorted({normalize_domain(str(d)) for d in web.get("list", []) if normalize_domain(str(d))})},
        "schedules": [],
    }
    for s in body.get("schedules", []):
        parse_hm(s["start"]), parse_hm(s["end"])
        out["schedules"].append({
            "id": str(s.get("id") or secrets.token_hex(4)),
            "name": str(s.get("name", ""))[:60],
            "days": sorted({int(d) for d in s.get("days", []) if 0 <= int(d) <= 6}),
            "start": s["start"], "end": s["end"],
            "apps": bool(s.get("apps")), "web": bool(s.get("web")),
            "enabled": bool(s.get("enabled", True)),
        })
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _host_ok(self):
        return self.headers.get("Host") in ("127.0.0.1:%d" % UI_PORT, "localhost:%d" % UI_PORT)

    def _send(self, code, obj=None, raw=None, ctype="application/json"):
        data = raw if raw is not None else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        if self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "ui.html"), "rb") as f:
                return self._send(200, raw=f.read(), ctype="text/html")
        if self.path == "/api/state":
            with LOCK:
                return self._send(200, public_state())
        if self.path == "/api/apps":
            return self._send(200, {"apps": installed_apps()})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        # JSON content type forces a CORS preflight, so other websites cannot call this API.
        if not self._host_ok() or "application/json" not in (self.headers.get("Content-Type") or ""):
            return self._send(403, {"error": "forbidden"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except Exception:
            return self._send(400, {"error": "bad json"})
        with LOCK:
            try:
                code, res = self.route(body)
            except Exception as e:
                code, res = 400, {"error": "入力が正しくありません (%s)" % e}
        self._send(code, res)

    def route(self, body):
        p = self.path
        now = time.time()
        if p == "/api/setup":
            if CFG.get("password"):
                return 400, {"error": "パスワードは設定済みです"}
            pw = body.get("password") or ""
            if len(pw) < 4:
                return 400, {"error": "4文字以上にしてください"}
            CFG["password"] = hash_password(pw)
            save_config()
            return 200, public_state()
        if not CFG.get("password"):
            return 400, {"error": "先にパスワードを設定してください"}
        if p == "/api/start":
            end = float(body["end"])
            if not (now + 30 <= end <= now + MAX_SESSION_SECONDS):
                return 400, {"error": "終了時刻が正しくありません"}
            apps, web = bool(body.get("apps")), bool(body.get("web"))
            if not (apps or web):
                return 400, {"error": "アプリかWebサイトを選んでください"}
            sess = CFG.get("session")
            if sess and sess["end"] > now:  # extending: never weaken a running session
                end = max(end, sess["end"])
                apps, web = apps or sess["apps"], web or sess["web"]
            CFG["session"] = {"start": now, "end": end, "apps": apps, "web": web}
            save_config()
            log("session started until", time.ctime(end), "apps" if apps else "", "web" if web else "")
            return 200, public_state()
        if p == "/api/stop":
            if not check_password(body.get("password")):
                return 403, {"error": "パスワードが違います"}
            CFG["session"] = None
            for src in active_state(now)["sources"]:
                if src["type"] == "schedule":
                    CFG["skips"][src["id"]] = src["end"]
            CFG["skips"] = {k: v for k, v in CFG["skips"].items() if v > now}
            save_config()
            log("session stopped with password")
            return 200, public_state()
        if p == "/api/settings":
            if active_state(now)["active"] and not check_password(body.get("password")):
                return 403, {"error": "ブロック中の設定変更にはパスワードが必要です"}
            CFG.update(clean_settings(body))
            save_config()
            return 200, public_state()
        if p == "/api/password":
            if not check_password(body.get("old")):
                return 403, {"error": "現在のパスワードが違います"}
            if len(body.get("new") or "") < 4:
                return 400, {"error": "4文字以上にしてください"}
            CFG["password"] = hash_password(body["new"])
            save_config()
            return 200, {"ok": True}
        return 404, {"error": "not found"}


# ---------------------------------------------------------------- main

def main():
    global DEV, DATA_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", action="store_true", help="run as a normal user: no proxy changes, no app quitting")
    ap.add_argument("--restore-proxy", action="store_true", help="restore the original proxy settings and exit")
    ap.add_argument("--reset-password", action="store_true", help="clear the password and any running session")
    args = ap.parse_args()
    DEV = args.dev
    DATA_DIR = os.path.join(HERE, "dev-data") if DEV else ROOT_DATA_DIR
    if not DEV and os.geteuid() != 0:
        sys.exit("root で実行してください (sudo)")
    load_config()

    if args.restore_proxy:
        restore_proxy()
        return
    if args.reset_password:
        CFG.update({"password": None, "session": None, "skips": {}})
        save_config()
        print("パスワードとセッションをリセットしました")
        return

    if CFG.get("saved_proxies") is not None:
        restore_proxy()  # left over from a crash; the enforcer re-applies it if still needed

    def shutdown(*_):
        if CFG.get("saved_proxies") is not None:
            restore_proxy()
        os._exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    threading.Thread(target=proxy_thread, daemon=True).start()
    threading.Thread(target=enforcer, daemon=True).start()
    ThreadingHTTPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer(("127.0.0.1", UI_PORT), Handler)
    log("FocusBlock UI on http://127.0.0.1:%d%s" % (UI_PORT, " (dev mode)" if DEV else ""))
    httpd.serve_forever()


if __name__ == "__main__":
    main()
