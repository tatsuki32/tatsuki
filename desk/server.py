#!/usr/bin/env python3
"""Tatsuki's Desk — Mac server.

Serves desk/index.html at http://localhost:4747 and keeps memos, due dates,
tasks (with their projects and sections) and Claude prompts in desk/data/*.json. Only the Python standard library is used.
POST /api/claude opens Terminal with a Claude Code command (Desk's ⌨️ buttons, macOS only).
/api/focus/* relays to FocusBlock (focusblock/, 127.0.0.1:47800); the password only passes through.

- Listens on 127.0.0.1 only; requests whose Host is not localhost are refused.
- index.html is read from disk on every request, so a `git pull` shows up on reload.
- Read-only data written by the hourly sync (tools, world news, mail, calendar,
  Google tasks, Claude reports) lives in desk/data/*.js and is inlined into the page.
- Nothing is ever hard-deleted: the page sends deleted:true, same as online.
"""
import glob
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import bank
import accounts
import netspeed
import finder
import transcribe_desk
import notify
import pc_monitor
import routine_alarm
import routine_sync
import chrome_web_sync
import gmail_clean
import lifelog
import utilities
import posts
import golinks
import showcase
import screentime_sync
import usage
import wise_analysis
import wise_goals
import wise_report
import wise_sync

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
PORT = int(os.environ.get("DESK_PORT", "4747"))
COLLECTIONS = ("memos", "dues", "tasks", "prompts", "notes", "habits", "lifemap", "wins", "worldlog", "presents", "heritagelog", "routines", "routinelog", "rewards", "ideaslog", "gear", "projects", "sections", "jobapps", "replywin", "genres", "tasktypes", "mergeprops", "timelog")
# file in data/, global the page reads, value when the file is missing
DATA_JS = [
    ("tools.js", "TOOLS", "[]"),
    ("world.js", "WORLD", "[]"),
    ("mail.js", "MAIL", "[]"),
    ("calendar.js", "CAL", "null"),
    ("gtasks.js", "GTASKS", "null"),
    ("reports.js", "REPORTS", "[]"),
    ("watch.js", "WATCH", "[]"),
    ("heritage.js", "HERITAGE", "[]"),
    ("ideas.js", "IDEAS", "[]"),
    ("autos.js", "AUTOS", "[]"),
]
# what the scheduled tasks and the tools view write: one file per doc in data/docs/<collection>/, joined into the page's
# data/*.js. collection -> (file, global, newest docs kept in the page; the docs stay on disk)
FEEDS = {
    "tools": ("tools.js", "TOOLS", None),
    "editions": ("world.js", "WORLD", 60),
    "days": ("mail.js", "MAIL", 30),
    "reports": ("reports.js", "REPORTS", 30),
    "watch": ("watch.js", "WATCH", 30),
    "heritage": ("heritage.js", "HERITAGE", None),
    "ideas": ("ideas.js", "IDEAS", 52),
    "autos": ("autos.js", "AUTOS", 52),  # automation ideas (desk/automation.py), weekly
    "calendar": ("calendar.js", "CAL", "upcoming"),
    "gtasks": ("gtasks.js", "GTASKS", "current"),
}
LOCK = threading.Lock()
# Task Desk Chrome extension (Applications/programming/Tatsuki Desk/task-desk-extension). Its id is fixed by the "key" in its manifest.
TASKDESK_ORIGIN = "chrome-extension://nofajepofefkhjckmgciabfjbeeenhlm"
MEMOLINK_LOCK = threading.Lock()
REPO = os.path.dirname(ROOT)
# commands that work in the person's Chrome (Claude in Chrome), started with --chrome
CLAUDE_CHROME = ("/joins-apply",)
# Desk's ⌨️ buttons may start only these Claude Code commands (POST /api/claude)
CLAUDE_CMDS = ("/morning-kickoff", "/begin", "/recap", "/rsd", "/learn", "/sync", "/knowledge", "/desk-feature", "/followup", "/newtool", "/kickoff", "/clarify", "/split", "/where", "/lowenergy", "/mail-send", "/money-review", "/joins-apply")
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


def safe_doc_id(doc_id):
    return bool(doc_id) and doc_id[0] != "." and "/" not in doc_id and "\\" not in doc_id


def doc_path(col, doc_id):
    return os.path.join(DATA, "docs", col, doc_id + ".json")


def write_atomic(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".doc", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def write_bytes_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".doc", suffix=".tmp")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def put_doc(col, doc_id, rec):
    if col not in FEEDS or not safe_doc_id(doc_id):
        raise ValueError("bad doc")
    rec = {k: v for k, v in rec.items() if k != "id"}
    write_atomic(doc_path(col, doc_id), json.dumps(rec, ensure_ascii=False, indent=1))


def feed_docs(col):
    d = os.path.join(DATA, "docs", col)
    out = []
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not name.endswith(".json") or name.startswith("."):
            continue
        try:
            with open(os.path.join(d, name), encoding="utf-8") as f:
                out.append({"id": name[:-5], **json.load(f)})
        except (OSError, ValueError):
            continue  # one broken doc must not hide the rest
    return out


def by_date_desc(docs, key="date"):
    return sorted(docs, key=lambda d: str(d.get(key) or d.get("id") or ""), reverse=True)


def rebuild_feed(col):
    """Rewrites the page's data/*.js from the docs on disk."""
    name, var, keep = FEEDS[col]
    docs = feed_docs(col)
    strip = lambda d: {k: v for k, v in d.items() if k != "id"}
    if col == "tools":
        value = [d for d in docs if not d.get("deleted")]
    elif isinstance(keep, str):
        value = next((strip(d) for d in docs if d["id"] == keep), None)
    elif col in ("editions", "days"):
        value = [{"id": d["id"], "data": strip(d)} for d in by_date_desc(docs)[:keep]]
    elif col == "watch":
        value = [d for d in by_date_desc(docs)[:keep] if not d.get("deleted")]
    else:
        value = by_date_desc(docs)[:keep]
    text = json.dumps(value, ensure_ascii=False).replace("</", "<\\/")
    write_atomic(os.path.join(DATA, name), f"window.{var}={text};\n")


def set_tool_url(tool_id, url):
    """PUT /api/tools/<id>: only a tool marked urlEditable, and only its url."""
    if not safe_doc_id(tool_id) or not isinstance(url, str) or not url.startswith("https://"):
        return None
    with LOCK:
        try:
            with open(doc_path("tools", tool_id), encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            return None
        if not rec.get("urlEditable"):
            return None
        rec.update(url=url, updatedAt=int(time.time() * 1000))
        put_doc("tools", tool_id, rec)
        rebuild_feed("tools")
    return {"id": tool_id, **rec}


def whatsapp_view():
    """Drafts and messages of the open WhatsApp tasks (desk/whatsapp_sync.py writes them; the page only reads)."""
    try:
        with open(os.path.join(DATA, "whatsapp", "items.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def line_view():
    """Drafts of the open LINE tasks and how the last check went (desk/line_sync.py writes them; the page only reads)."""
    out = {}
    for key, name in (("items", "items.json"), ("status", "status.json")):
        try:
            with open(os.path.join(DATA, "line", name), encoding="utf-8") as f:
                out[key] = json.load(f)
        except (OSError, ValueError):
            out[key] = {}
    return out


def keep_clear(old, new):
    """A page opened before the night's run saves the whole task: it must not erase what the run wrote."""
    cur = old.get("clear")
    if cur and (not new.get("clear") or (new["clear"].get("at") or 0) < (cur.get("at") or 0)):
        new["clear"] = cur


def keep_labels(old, new):
    """Same for what the nightly desk-task-skip writes: "If I don't do it" (skip) and the type and genre (cat, genre)."""
    cur = old.get("skip")
    if cur and (not new.get("skip") or (new["skip"].get("at") or 0) < (cur.get("at") or 0)):
        new["skip"] = cur
    if (old.get("tagAt") or 0) > (new.get("tagAt") or 0):
        for k in ("cat", "genre", "tagBy", "tagAt"):
            if k in old:
                new[k] = old[k]


def upsert(col, doc_id, rec, merge):
    with LOCK:
        items = load(col)
        for i, x in enumerate(items):
            if x["id"] == doc_id:
                items[i] = {**x, **rec, "id": doc_id} if merge else {**rec, "id": doc_id}
                if col == "tasks":
                    keep_clear(x, items[i])
                    keep_labels(x, items[i])
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
            rec = {"no": slug[:2], "name": slug, "status": "blocked", "oneline": "Cannot read tool.json",
                   "blockers": [{"what": "tool.json", "why": "The file is malformed.", "needs": "Fix it in that tool's own work"}]}
        out.append({**rec, "id": slug})
    return out


def lifemap_ticks():
    """Life OS 14: the daily question. This server always runs, so no other scheduled job is needed.
    A failure here never touches the Desk: the next round simply tries again."""
    sys.path.insert(0, REPO)
    while True:
        try:
            from lifeos.lib.tools import load_tool
            core = load_tool("14-lifemap", "core")
            core.tick()
            wait = core.TICK_MINUTES * 60
        except Exception:
            wait = 900
        time.sleep(wait)


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


def subs_core():
    """Life OS 18, or None in a checkout without lifeos/."""
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    try:
        from lifeos.lib.tools import load_tool
        return load_tool("18-subs", "core")
    except (ImportError, OSError):
        return None


def wise_query(path):
    """The period a request asks for, as wise_report.view() takes it."""
    q = parse_qs(urlsplit(path).query)
    one = lambda k: (q.get(k) or [None])[0]
    return {"period": one("period") or "month", "month": one("month"), "start": one("from"), "end": one("to")}


def wise_view(period=None):
    """GET /api/wise: one period of Wise activity, read from disk. Opening the page never calls Wise."""
    try:
        view = wise_report.view(**(period or {}))
    except ValueError:
        return 400, {"error": "bad period"}
    core = subs_core()
    known = {core.slug(core.view(v).get("name") or "") for v in core.load().values()} if core else set()
    for r in view["recurring"]:
        r["inSubs"] = bool(core) and core.slug(r["title"]) in known
    return 200, view


def wise_to_subs(rec):
    """POST /api/wise/subs: puts a monthly-charge candidate on Life OS 18's list. The amount comes from the
    statements, not the page. Nothing is cancelled: that stays with the person."""
    core = subs_core()
    hit = next((r for r in wise_sync.summary()["recurring"] if r["key"] == rec.get("key")), None)
    if core is None or hit is None:
        return 404, {"error": "no such candidate"}
    try:
        core.add_manual(hit["title"], hit["amount"], hit["currency"], "monthly")
    except ValueError:
        return 400, {"error": "cannot add"}
    return 200, {"ok": True}


def wise_number(v):
    """A non-negative number, or None for an empty field. ValueError for anything else."""
    if v is None or v == "":
        return None
    n = float(v)
    if not 0 <= n < 1e13:
        raise ValueError("bad number")
    return n


def wise_settings(parts, rec):
    """PUT /api/wise/category, /kind, /recurring, /seen, /close/<month>, /limits and /recon/<month>: the person's own settings, kept with the statements."""
    try:
        if parts == ["category"]:
            title, name = str(rec.get("title") or "").strip().lower(), rec.get("category")
            if not title or len(title) > 300 or name not in wise_report.CATEGORIES:
                return 400, {"error": "bad category"}
            chosen = wise_sync.read_json("categories.json", {})
            chosen[title] = name
            wise_sync.write_json("categories.json", chosen)
            return 200, {"ok": True}
        if parts == ["kind"]:
            title, direction, kind = str(rec.get("title") or "").strip().lower(), rec.get("flow"), rec.get("kind")
            if not title or len(title) > 300 or direction not in ("in", "out") or kind not in wise_analysis.KINDS:
                return 400, {"error": "bad kind"}
            chosen = wise_sync.read_json("flows.json", {})
            chosen[direction + ":" + title] = kind
            wise_sync.write_json("flows.json", chosen)
            return 200, {"ok": True}
        if parts == ["recurring"]:
            key = str(rec.get("key") or "").strip()
            if not key or len(key) > 340 or rec.get("choice") not in ("keep", "later", "review", None) or not isinstance(rec.get("mark", True), bool):
                return 400, {"error": "bad recurring"}
            saved = wise_sync.read_json("recurring.json", {})
            entry = saved.setdefault(key, {})
            if "mark" in rec:
                entry["mark"] = rec["mark"]
            if rec.get("choice"):
                entry.update(choice=rec["choice"], reviewed=wise_report.today_hcmc().isoformat() if rec["choice"] == "keep" else entry.get("reviewed"))
            wise_sync.write_json("recurring.json", saved)
            return 200, {"ok": True}
        if parts == ["budget"]:
            wise_goals.put_budget(rec, wise_report.CATEGORIES)
            return 200, {"ok": True}
        if len(parts) == 2 and parts[0] == "trips" and re.fullmatch(r"[\w-]{1,60}", parts[1]):
            wise_goals.put_trip(parts[1], rec)
            return 200, {"ok": True}
        if parts == ["goals"]:
            wise_goals.put_goals(rec)
            return 200, {"ok": True}
        if parts == ["sims"]:
            wise_goals.put_sims(rec)
            return 200, {"ok": True}
        if len(parts) == 2 and parts[0] == "learn":
            wise_goals.answer_lesson(parts[1], rec.get("choice"))
            return 200, {"ok": True}
        if parts == ["direct"]:
            title = str(rec.get("title") or "").strip().lower()
            if not title or len(title) > 300 or not isinstance(rec.get("on"), bool):
                return 400, {"error": "bad direct"}
            chosen = set(wise_sync.read_json("direct.json", []))
            (chosen.add if rec["on"] else chosen.discard)("out:" + title)
            wise_sync.write_json("direct.json", sorted(chosen))
            return 200, {"ok": True}
        if parts == ["upcoming-hide"]:
            item = str(rec.get("id") or "").strip()
            if not item or len(item) > 80:
                return 400, {"error": "bad id"}
            hidden = wise_sync.read_json("upcoming-hidden.json", [])
            wise_sync.write_json("upcoming-hidden.json", sorted(set(hidden) | {item}))
            return 200, {"ok": True}
        if parts == ["seen"]:
            today, seen = wise_report.today_hcmc().isoformat(), wise_sync.read_json("seen.json", {})
            if seen.get("last") != today:
                wise_sync.write_json("seen.json", {"previous": seen.get("last"), "last": today})
            return 200, {"ok": True}
        if len(parts) == 2 and parts[0] == "close" and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", parts[1]):
            saved = wise_sync.read_json("close.json", {})
            saved[parts[1]] = {k: bool(rec.get(k)) for k in ("recon", "review", "next")}
            wise_sync.write_json("close.json", saved)
            return 200, {"ok": True}
        if parts == ["limits"]:
            wise_sync.write_json("limits.json", {"week": wise_number(rec.get("week")), "month": wise_number(rec.get("month"))})
        elif len(parts) == 2 and parts[0] == "recon" and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", parts[1]):
            currency = str(rec.get("currency") or "USD").upper()
            if not re.fullmatch(r"[A-Z]{3}", currency):
                return 400, {"error": "bad currency"}
            saved = wise_sync.read_json("recon.json", {})
            saved[parts[1]] = {"in": wise_number(rec.get("in")), "out": wise_number(rec.get("out")), "currency": currency}
            wise_sync.write_json("recon.json", saved)
        else:
            return 404, {"error": "not found"}
    except (TypeError, ValueError):
        return 400, {"error": "bad number"}
    return 200, {"ok": True}


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
        return 404, {"error": "Memo link is not installed in this Desk"}
    if not MEMOLINK_LOCK.acquire(blocking=False):
        return 409, {"error": "Linking is in progress. Please wait for it to finish"}
    try:
        core.run()
        return 200, core.desk_view()
    except core.desk_notes.NoteStoreError as e:
        return 500, {"error": str(e)}
    except Exception:
        return 500, {"error": "Linking did not complete. The original memos are unchanged"}
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
    try:
        with open(os.path.join(ROOT, "heritage_countries.json"), encoding="utf-8") as f:
            parts.append("window.HERITAGE_COUNTRIES=" + json.dumps(json.load(f), ensure_ascii=False).replace("</", "<\\/") + ";")
    except (OSError, ValueError):
        parts.append("window.HERITAGE_COUNTRIES=[];")
    try:  # how the menu and the Home cards are arranged this week (desk/usage.py); the page shows the first arrangement without it
        lay = json.dumps(usage.public(usage.read_layout(), usage.enabled(PORT)), ensure_ascii=False).replace("</", "<\\/")
    except Exception:
        lay = "null"
    parts.append(f"window.LAYOUT={lay};")
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
            'if ! command -v claude >/dev/null; then echo "claude was not found (install Claude Code)"; read -r; exit 1; fi\n'
            + ('exec claude --chrome "$P"\n' if cmd in CLAUDE_CHROME else 'exec claude "$P"\n')
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
        return 502, {"error": "Cannot reach FocusBlock"}


def focus_state():
    code, st = focus_call("/api/state")
    if code != 200:
        return code, st
    return 200, {"hasPassword": st["hasPassword"], "active": st["active"],
                 "apps": {"mode": st["apps"]["mode"], "count": len(st["apps"]["list"])},
                 "web": {"mode": st["web"]["mode"], "count": len(st["web"]["list"])}}


UPLOADS = os.path.join(DATA, "uploads")
HERITAGE_PHOTO = re.compile(r"^[0-9a-f]{16}\.(jpg|png)$")
UPLOAD_MAX = 20 * 1024 * 1024
UPLOAD_NAME = re.compile(r"^[0-9a-f]{32}\.(png|jpg|gif|webp|heic|pdf)$")
UPLOAD_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
                "heic": "image/heic", "pdf": "application/pdf"}


def upload_kind(data):
    """What a file really is, from its first bytes. The name and declared type are not trusted, and SVG (which can hold scripts) is not accepted."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"hevc", b"mif1", b"msf1"):
        return "heic"
    if data.startswith(b"%PDF-"):
        return "pdf"
    return None


def save_upload(data):
    """Stores a file under a name made from its content, so the same file is kept once. Returns its URL."""
    kind = upload_kind(data)
    if not kind:
        raise ValueError("Only images and PDFs can be uploaded")
    name = hashlib.sha256(data).hexdigest()[:32] + "." + kind
    os.makedirs(UPLOADS, exist_ok=True)
    path = os.path.join(UPLOADS, name)
    if not os.path.exists(path):
        tmp = path + "." + secrets.token_hex(4) + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    return "/uploads/" + name


def lifelog_put(parts, rec):
    """parts after /api/: body records <id> | body targets <key> | body settings | receipts <id>"""
    parts = [unquote(x) for x in parts]
    if parts[:2] == ["body", "records"] and len(parts) == 3:
        out = lifelog.update_record(parts[2], rec.get("action"), rec)
    elif parts[:2] == ["body", "targets"] and len(parts) == 3:
        out = lifelog.set_target(parts[2], rec.get("target"), rec.get("dir"))
    elif parts[:2] == ["body", "repeat"] and len(parts) == 3:
        out = lifelog.repeat_meal(parts[2])
    elif parts[:2] == ["body", "exercises"] and len(parts) == 3:
        out = lifelog.set_muscle(parts[2], rec.get("muscle"))
    elif parts == ["body", "settings"]:
        try:
            out = lifelog.set_settings(rec)
        except (TypeError, ValueError):
            return 400, {"error": "The value has the wrong shape"}
    elif parts[0] == "receipts" and len(parts) == 2:
        out = lifelog.settle_receipt(parts[1], rec.get("state"), rec.get("wiseId"))
    else:
        return 404, {"error": "not found"}
    if out is None:
        return 404, {"error": "not found"}
    if isinstance(out, dict) and out.get("error"):
        return 400, out
    return 200, out


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
            if origin == TASKDESK_ORIGIN:
                # the Task Desk Chrome extension may only save tasks, add memos and log the time it measured
                p = urlsplit(self.path).path
                return (self.command == "PUT" and p.startswith(("/api/tasks/", "/api/timelog/"))) or (self.command == "POST" and p == "/api/memos")
            return urlsplit(origin).hostname in ("localhost", "127.0.0.1")
        return True

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        v = json.loads(self.rfile.read(n) or b"{}") if n else {}
        if not isinstance(v, dict):
            raise ValueError("object expected")
        return v

    def utility_file(self, token):
        entry = utilities.get_file(token)
        if not entry:
            return self.send(404, {"error": "not found"})
        try:
            size = len(entry["data"]) if "data" in entry else os.path.getsize(entry["path"])
            self.send_response(200)
            self.send_header("Content-Type", entry["ctype"])
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", utilities.disposition(entry["name"]))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if "data" in entry:
                self.wfile.write(entry["data"])
            else:
                with open(entry["path"], "rb") as f:
                    shutil.copyfileobj(f, self.wfile)
        except OSError:
            self.close_connection = True

    def utilities_post(self, path):
        """小さな道具: QR, is.gd and the PDF. Same protection as uploads and the other JSON posts: another site cannot send these."""
        step = path.split("/")[3:]
        if step == ["pdf"]:
            if self.headers.get("X-Desk-Upload") != "1":
                return self.send(415, {"error": "upload header required"})
            n = int(self.headers.get("Content-Length") or 0)
            if n > utilities.PDF_MAX:
                self.close_connection = True
                return self.send(413, {"error": "The limit is 500MB"})
            q = parse_qs(urlsplit(self.path).query)
            return self.send(*utilities.start_pdf(self.rfile, n, q.get("name", [""])[0], q.get("level", [""])[0]))
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send(415, {"error": "json only"})
        try:
            rec = self.body()
        except (ValueError, json.JSONDecodeError):
            return self.send(400, {"error": "bad json"})
        if step == ["qr"]:
            return self.send(*utilities.qr(rec.get("text")))
        if step == ["file"]:
            return self.send(*utilities.text_file(rec.get("name"), rec.get("text"), rec.get("kind")))
        if step == ["shorten"]:
            return self.send(*utilities.shorten(rec.get("url")))
        if len(step) == 3 and step[0] == "pdf" and step[2] == "run":
            return self.send(*utilities.run_pdf(step[1], rec.get("level")))
        if len(step) == 3 and step[0] == "pdf" and step[2] == "clear":
            return self.send(*utilities.clear_pdf(step[1]))
        return self.send(404, {"error": "not found"})

    def posts_get(self, step):
        """Post Studio: the list, one post, its pictures. Photos are served only by their own hashed names."""
        if not step:
            return self.send(200, posts.view())
        if step == ["links"]:  # go.tany.im counts, read from the Worker
            try:
                days = int(parse_qs(urlsplit(self.path).query).get("days", ["30"])[0])
                return self.send(200, golinks.stats(days))
            except (RuntimeError, ValueError) as e:
                return self.send(502, {"error": str(e)})
        if len(step) == 2 and step[0] == "photo":
            p = posts.photo_path(step[1].removesuffix(".jpg"), "api") or posts.photo_path(step[1].removesuffix(".jpg"))
            return self.image_file(p)
        post = posts.get(unquote(step[0]))
        if not post:
            return self.send(404, {"error": "not found"})
        if len(step) == 1:
            return self.send(200, posts.public(post))
        if len(step) == 4 and step[1] == "img" and step[3].removesuffix(".jpg").isdigit():
            return self.image_file(posts.image(post["id"], step[2], int(step[3].removesuffix(".jpg"))))
        return self.send(404, {"error": "not found"})

    def image_file(self, p):
        if not p:
            return self.send(404, {"error": "not found"})
        with open(p, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def posts_write(self, method, step):
        """Post Studio writes. Nothing here posts to any site; the person does that from each site."""
        if method == "POST" and step == ["photo"]:
            if self.headers.get("X-Desk-Upload") != "1":
                return self.send(415, {"error": "upload header required"})
            n = int(self.headers.get("Content-Length") or 0)
            if n > posts.PHOTO_MAX:
                self.close_connection = True
                return self.send(413, {"error": "A photo can be up to 40 MB"})
            return self.send(*posts.add_photo(self.rfile, n))
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send(415, {"error": "json only"})
        try:
            rec = self.body()
        except (ValueError, json.JSONDecodeError):
            return self.send(400, {"error": "bad json"})
        if method == "POST" and not step:
            return self.send(*posts.create(rec))
        if method == "POST" and step == ["links"]:  # a short link made by hand on the Links tab
            try:
                return self.send(200, {"short": golinks.shorten(str(rec.get("dest") or ""), str(rec.get("source") or "other"),
                                                                 str(rec.get("campaign") or "")[:60], str(rec.get("label") or "")[:120])})
            except RuntimeError as e:
                return self.send(400, {"error": str(e)})
        if method == "POST" and len(step) == 3 and step[0] == "links" and step[2] == "off":
            try:
                return self.send(200, golinks.turn_off(step[1]))
            except RuntimeError as e:
                return self.send(502, {"error": str(e)})
        if method == "PUT" and step == ["settings"]:
            return self.send(*posts.save_settings(rec))
        if method == "POST" and step == ["copy"]:
            return self.send(*posts.copy_html(rec.get("html"), rec.get("plain")))
        pid = unquote(step[0]) if step else ""
        if method == "PUT" and len(step) == 1:
            return self.send(*posts.change(pid, rec))
        if method == "POST" and step[1:] == ["regen"]:
            return self.send(*posts.regen(pid, rec))
        if method == "POST" and step[1:] == ["save"]:
            return self.send(*posts.save_image(pid, str(rec.get("target") or ""), int(rec.get("n") or 0)))
        return self.send(404, {"error": "not found"})

    def lifelog_photo(self, name):
        p = lifelog.photo_path(name)
        if not p:
            return self.send(404, {"error": "not found"})
        with open(p, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", lifelog.MEDIA.get(os.path.splitext(p)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def route(self):
        p = urlsplit(self.path).path.rstrip("/").split("/")[1:]  # ['api','memos','id']
        if len(p) >= 2 and p[0] == "api" and p[1] in COLLECTIONS:
            return p[1], (unquote(p[2]) if len(p) > 2 else None)
        return None, None

    ROUTINE_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".png": "image/png",
                     ".webmanifest": "application/manifest+json", ".svg": "image/svg+xml"}

    def routine_file(self, path):
        """The Routine app (routine/public) at /routine/. Here it reads and writes this Desk's routines directly.
        Only plain file names in that one folder are served."""
        if path == "/routine":
            self.send_response(301)
            self.send_header("Location", "/routine/")
            self.end_headers()
            return
        name = path[len("/routine/"):] or "index.html"
        ext = os.path.splitext(name)[1]
        if "/" in name or name.startswith(".") or ext not in self.ROUTINE_TYPES:
            return self.send(404, {"error": "not found"})
        try:
            with open(os.path.join(ROOT, "..", "routine", "public", name), "rb") as f:
                return self.send(200, f.read(), self.ROUTINE_TYPES[ext])
        except OSError:
            return self.send(404, {"error": "not found"})

    def do_GET(self):
        if not self.allowed():
            return self.send(403, {"error": "forbidden"})
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            return self.send(200, page(), "text/html; charset=utf-8")
        if path == "/routine" or path.startswith("/routine/"):
            return self.routine_file(path)
        if path == "/api/routine-link":  # the phone's one-time setup link; the passphrase rides in the # part, never sent to a server
            k = routine_sync.key()
            return self.send(200, {"url": routine_sync.APP + ("#key=" + urllib.parse.quote(k, safe="") if k else ""), "sync": bool(k)})
        if path.startswith("/uploads/"):
            return self.upload_get(path.rsplit("/", 1)[1])
        if path.startswith("/heritage-photos/"):
            return self.heritage_photo(path.rsplit("/", 1)[1])
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
        if path == "/api/whatsapp":
            return self.send(200, whatsapp_view())
        if path == "/api/line":
            return self.send(200, line_view())
        if path == "/api/bank":
            try:
                return self.send(200, bank.view())
            except bank.BankError:
                return self.send(500, {"error": "read"})
        if path == "/api/accounts":
            try:
                return self.send(200, accounts.view())
            except accounts.AccountsError:
                return self.send(500, {"error": "read"})
        if path == "/api/netspeed":
            return self.send(200, netspeed.view())
        if path == "/api/transcribe":
            return self.send(200, transcribe_desk.view())
        if path.startswith("/api/utilities/file/"):
            return self.utility_file(path.rsplit("/", 1)[1])
        if path == "/api/posts" or path.startswith("/api/posts/"):
            return self.posts_get(path.split("/")[3:])
        if path == "/api/showcase":
            return self.send(200, showcase.view())
        if path == "/api/mailclean":
            return self.send(200, gmail_clean.view())
        if path == "/api/body":
            return self.send(200, lifelog.view())
        if path == "/api/receipts":
            return self.send(200, lifelog.receipts_view(parse_qs(urlsplit(self.path).query).get("month", [None])[0]))
        if path.startswith("/lifelog-photos/"):
            return self.lifelog_photo(path.rsplit("/", 1)[1])
        if path == "/api/notify":
            return self.send(200, notify.view())
        if path == "/api/pc":
            brief = "summary" in parse_qs(urlsplit(self.path).query)
            return self.send(200, pc_monitor.summary() if brief else pc_monitor.snapshot())
        if path.startswith("/api/web/"):
            return self.web_get(path.rsplit("/", 1)[1], parse_qs(urlsplit(self.path).query))
        if path == "/api/usage":
            return self.send(200, {**usage.view(parse_qs(urlsplit(self.path).query).get("period", ["14"])[0]), "on": usage.enabled(PORT)})
        if path == "/api/layout":
            return self.send(200, usage.public(usage.read_layout(), usage.enabled(PORT)))
        if path == "/api/screentime":
            q = parse_qs(urlsplit(self.path).query)
            return self.send(200, screentime_sync.view(q.get("period", ["week"])[0], q.get("device", ["mac"])[0]))
        if path == "/api/wise":
            return self.send(*wise_view(wise_query(self.path)))
        if path == "/api/wise/world":
            return self.send(200, wise_report.world())
        if path == "/api/wise/export":
            return self.wise_export()
        if path == "/api/memolink":
            core = memolink_core()
            return self.send(200, core.desk_view()) if core else self.send(404, {"error": "not found"})
        self.send(404, {"error": "not found"})

    def heritage_photo(self, name):
        m = HERITAGE_PHOTO.match(name)
        try:
            with open(os.path.join(DATA, "heritage", "photos", name), "rb") as f:
                data = f.read() if m else None
        except OSError:
            data = None
        if data is None:
            return self.send(404, {"error": "no such file"})
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg" if m.group(1) == "jpg" else "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(data)

    def upload_get(self, name):
        m = UPLOAD_NAME.match(name)
        try:
            with open(os.path.join(UPLOADS, name), "rb") as f:
                data = f.read() if m else None
        except OSError:
            data = None
        if data is None:
            return self.send(404, {"error": "no such file"})
        self.send_response(200)
        self.send_header("Content-Type", UPLOAD_TYPES[m.group(1)])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", "inline")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(data)

    def upload_post(self):
        # the custom header needs a CORS preflight this server never answers, so another site's page cannot post here
        if self.headers.get("X-Desk-Upload") != "1":
            return self.send(415, {"error": "upload header required"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > UPLOAD_MAX:
            self.close_connection = True
            return self.send(413, {"error": "The limit is 20MB"})
        try:
            return self.send(200, {"url": save_upload(self.rfile.read(n))})
        except ValueError as e:
            return self.send(415, {"error": str(e)})
        except OSError:
            return self.send(500, {"error": "Could not save"})

    def web_get(self, what, q):
        one = lambda k, default="": q.get(k, [default])[0]
        num = lambda k: float(one(k)) if re.fullmatch(r"\d+(\.\d+)?", one(k)) else None
        scope = (one("period", "week"), one("from") or None, one("to") or None)
        moment = (num("after"), num("before"))
        offset = int(num("offset") or 0)
        if what == "sites":
            return self.send(200, chrome_web_sync.sites_view(*scope, one("q")))
        if what == "visits":
            return self.send(200, chrome_web_sync.visits_view(*scope, one("q"), one("site"), *moment, offset))
        if what == "searches":
            return self.send(200, chrome_web_sync.searches_view(*scope, one("q"), offset))
        if what == "export":
            data = chrome_web_sync.export_csv(one("tab", "sites"), *scope, one("q"), one("site"), *moment).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Disposition", 'attachment; filename="web-history.csv"')
            self.end_headers()
            return self.wfile.write(data)
        return self.send(404, {"error": "not found"})

    def web_time(self):
        # the one write that comes from outside localhost: only this Desk's own extension, by its fixed id
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("localhost", "127.0.0.1") or self.headers.get("Origin") != "chrome-extension://" + chrome_web_sync.EXTENSION_ID:
            return self.send(403, {"error": "forbidden"})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send(415, {"error": "json only"})
        try:
            rec = self.body()
            added = chrome_web_sync.add_spans(rec.get("spans"), rec.get("diag"))
        except (ValueError, json.JSONDecodeError):
            return self.send(400, {"error": "bad json"})
        return self.send(400, {"error": "bad spans"}) if added is None else self.send(200, {"added": added})

    def web_put(self, parts, rec):
        if parts == ["exclude"]:
            out = chrome_web_sync.set_excluded(rec.get("site"), rec.get("on") is not False)
            return self.send(400, {"error": "bad site"}) if out is None else self.send(200, {"excluded": out})
        if len(parts) == 2 and parts[0] in ("visits", "searches"):
            ok = chrome_web_sync.hide(parts[0], unquote(parts[1]))
            return self.send(200, {"ok": True}) if ok else self.send(404, {"error": "not found"})
        return self.send(404, {"error": "not found"})

    def write(self, method):
        if method == "POST" and urlsplit(self.path).path == "/api/web/time":
            return self.web_time()
        if not self.allowed():
            return self.send(403, {"error": "forbidden"})
        if method == "POST" and urlsplit(self.path).path == "/api/uploads":
            return self.upload_post()
        if method == "POST" and urlsplit(self.path).path.startswith("/api/utilities/"):
            return self.utilities_post(urlsplit(self.path).path)
        if urlsplit(self.path).path == "/api/posts" or urlsplit(self.path).path.startswith("/api/posts/"):
            return self.posts_write(method, urlsplit(self.path).path.split("/")[3:])
        col, doc_id = self.route()
        try:
            rec = self.body()
        except (ValueError, json.JSONDecodeError):
            return self.send(400, {"error": "bad json"})
        if method == "PUT" and urlsplit(self.path).path.startswith("/api/web/"):
            return self.web_put(urlsplit(self.path).path.split("/")[3:], rec)
        if urlsplit(self.path).path.startswith("/api/bank/"):
            return self.bank_write(method, rec)
        if urlsplit(self.path).path.startswith("/api/accounts/"):
            return self.accounts_write(method, rec)
        if method == "POST" and urlsplit(self.path).path == "/api/usage":
            # counts only; a test server on another port answers and keeps nothing
            return self.send(200, {"ok": usage.enabled(PORT) and usage.record(rec)})
        if method == "PUT" and urlsplit(self.path).path == "/api/layout":
            return self.send(200, usage.change(rec, usage.enabled(PORT)))
        if method == "POST" and urlsplit(self.path).path == "/api/claude":
            return self.claude(rec)
        if method == "POST" and urlsplit(self.path).path in ("/api/focus/start", "/api/focus/stop"):
            return self.focus(urlsplit(self.path).path.rsplit("/", 1)[1], rec)
        if method == "POST" and urlsplit(self.path).path == "/api/finder":
            # Ask Desk: the page sends the list of places; Claude picks from it (desk/finder.py). Nothing is saved
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            try:
                return self.send(200, finder.handle(rec))
            except ValueError as e:
                return self.send(400, {"error": str(e)})
        if method == "POST" and urlsplit(self.path).path == "/api/netspeed/run":
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            netspeed.start()
            return self.send(200, netspeed.view())
        if method == "POST" and urlsplit(self.path).path == "/api/transcribe/start":
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            transcribe_desk.start()
            return self.send(200, transcribe_desk.view())
        if method == "POST" and urlsplit(self.path).path == "/api/showcase/open-local":
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            return self.send(200, {"ok": showcase.open_local()})
        if method == "POST" and urlsplit(self.path).path.startswith("/api/mailclean/"):
            # same protection as claude(): Host/Origin checked, and JSON from another origin needs a preflight
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            step = urlsplit(self.path).path.rsplit("/", 1)[1]
            if step == "scan":
                gmail_clean.scan()
                return self.send(200, gmail_clean.view())
            if step == "act":
                return self.send(*gmail_clean.act(rec.get("id"), rec.get("action"), rec.get("confirm") is True))
            if step == "undo":
                return self.send(*gmail_clean.undo(rec.get("id")))
            if step == "block":
                return self.send(*gmail_clean.block(rec.get("target"), rec.get("confirm") is True))
            return self.send(404, {"error": "not found"})
        if method == "PUT" and urlsplit(self.path).path.startswith(("/api/body/", "/api/receipts/")):
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            return self.send(*lifelog_put(urlsplit(self.path).path.split("/")[2:], rec))
        if urlsplit(self.path).path.startswith("/api/notify/"):
            # same protection as claude(): Host/Origin checked, and JSON from another origin needs a preflight
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            step = (method, urlsplit(self.path).path.rsplit("/", 1)[1])
            if step == ("PUT", "settings"):
                return self.send(*notify.put_settings(rec))
            if step == ("POST", "pause"):
                return self.send(*notify.pause(rec))
            if step == ("POST", "test"):
                return self.send(*notify.test())
            return self.send(404, {"error": "not found"})
        if method == "POST" and urlsplit(self.path).path == "/api/pc/close":
            # same protection as claude(): Host/Origin checked, and JSON from another origin needs a preflight
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            return self.send(*pc_monitor.close(rec.get("pid"), rec.get("started"), rec.get("force") is True))
        if method == "POST" and urlsplit(self.path).path == "/api/memolink/run":
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            return self.send(*memolink_run())
        if method == "PUT" and urlsplit(self.path).path.startswith("/api/wise/"):
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            return self.send(*wise_settings(urlsplit(self.path).path.split("/")[3:], rec))
        if method == "POST" and urlsplit(self.path).path == "/api/wise/subs":
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            return self.send(*wise_to_subs(rec))
        parts = urlsplit(self.path).path.split("/")
        if method == "POST" and len(parts) == 5 and parts[1:3] == ["api", "followups"] and parts[4] == "answer":
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            return self.send(*followup_answer(parts[3], rec))
        if method == "PUT" and len(parts) == 4 and parts[1:3] == ["api", "tools"]:
            out = set_tool_url(unquote(parts[3]), rec.get("url"))
            return self.send(200, out) if out else self.send(400, {"error": "bad tool"})
        rec.pop("id", None)
        rec.setdefault("updatedAt", int(time.time() * 1000))
        if method == "POST" and col == "memos" and doc_id is None:
            doc_id = "m" + str(int(time.time() * 1000)) + secrets.token_hex(2)
            return self.send(200, upsert(col, doc_id, rec, merge=False))
        if method == "PUT" and col in COLLECTIONS and doc_id:
            return self.send(200, upsert(col, doc_id, rec, merge=False))
        if method == "PATCH" and col == "memos" and doc_id:
            out = upsert(col, doc_id, rec, merge=True)
            return self.send(200, out) if out else self.send(404, {"error": "no such memo"})
        self.send(405, {"error": "method not allowed"})

    def wise_export(self):
        """A file the person asked for. Made fresh and not kept here."""
        q = parse_qs(urlsplit(self.path).query)
        try:
            data, ctype, name = wise_report.export((q.get("format") or ["csv"])[0], (q.get("kind") or ["report"])[0], **wise_query(self.path))
        except ValueError:
            return self.send(400, {"error": "bad export"})
        if data is None:
            return self.send(503, "Could not create the PDF. Run xcode-select --install, then try again.", "text/plain; charset=utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.end_headers()
        self.wfile.write(data)

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

    def accounts_write(self, method, rec):
        # the same guard as bank_write: JSON only, so another origin needs a preflight. Errors carry no value.
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send(415, {"error": "json only"})
        p = urlsplit(self.path).path.split("/")[3:]  # <id> or <id>/remove
        acct = unquote(p[0]) if p else ""
        try:
            if method == "PUT" and len(p) == 1:
                return self.send(200, accounts.put(acct, rec))
            if method == "POST" and len(p) == 2 and p[1] == "remove":
                return self.send(200, accounts.view()) if accounts.remove(acct) else self.send(404, {"error": "not found"})
            return self.send(404, {"error": "not found"})
        except ValueError as e:
            return self.send(400, {"error": str(e)})
        except accounts.AccountsError:
            return self.send(500, {"error": "storage"})

    def bank_write(self, method, rec):
        # same protection as claude(): Host/Origin checked, and JSON from another origin needs a preflight.
        # The answers and errors carry no value; only reveal returns a whole number.
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send(415, {"error": "json only"})
        p = urlsplit(self.path).path.split("/")[3:]  # <id> or <id>/<action>
        acct = unquote(p[0]) if p else ""
        action = p[1] if len(p) == 2 else None
        try:
            if method == "PUT" and len(p) == 1:
                return self.send(200, bank.put(acct, rec))
            if method != "POST" or not action:
                return self.send(404, {"error": "not found"})
            if action == "reveal":
                r = bank.reveal(acct)
                return self.send(200, r) if r else self.send(404, {"error": "not found"})
            if action == "copy":
                ok = bank.copy(acct, rec.get("what") if isinstance(rec.get("what"), str) else "")
                return self.send(200, {"ok": True}) if ok else self.send(404, {"error": "not found"})
            if action == "remove":
                return self.send(200, bank.view()) if bank.remove(acct) else self.send(404, {"error": "not found"})
            return self.send(404, {"error": "not found"})
        except ValueError as e:
            return self.send(400, {"error": str(e)})
        except bank.BankError:
            return self.send(500, {"error": "storage"})

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
    threading.Thread(target=lifemap_ticks, daemon=True).start()
    threading.Thread(target=pc_monitor.watch, daemon=True).start()
    if PORT == 4747:  # a test server on another port shows nothing
        threading.Thread(target=notify.watch, daemon=True).start()
        threading.Thread(target=routine_alarm.watch, daemon=True).start()  # morning / evening routine start alarm
        threading.Thread(target=routine_sync.watch, daemon=True).start()  # routines <-> the Routine app on the phone
        threading.Thread(target=transcribe_desk.watch, daemon=True).start()  # the transcriber on 8791, kept running
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
