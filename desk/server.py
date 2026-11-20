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
import wifi_login
import footprint
import finder
import transcribe_desk
import notify
import checkin
import tg_notify
import event_alarm
import pc_monitor
import startup_clean  # Startup Clean: the apps closed at the first login after the Mac starts, and reopening them
import wifi_qr  # a QR code that joins the Wi-Fi this Mac is on; the password is never kept
import routine_alarm
import routine_sync
import news_sync
import cards
import cards_sync
import reader
import translate  # Translate: lookups, concepts across languages, card candidates (desk/translate.py)
import chrome_web_sync
import gmail_clean
import lifelog
import utilities
import posts
import golinks
import youtube
import instagram_dm  # Instagram DMs in Replies (read and sent through go.tany.im)
import showcase
import campus  # 地球キャンパス通信: issues on this Mac, readers only in the tany.im Worker
import book  # Bookings: the rules behind tany.im/book; the page itself runs in the Worker (desk/data/tany/site/book.js)
import forms  # Forms on tany.im: made here, answered on tany.im, answers pulled back every 5 minutes (desk/forms.py)
import screentime_sync
import usage
import claude_usage
import site_sync
import wise_analysis
import wise_goals
import wise_report
import wise_sync

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
PORT = int(os.environ.get("DESK_PORT", "4747"))
COLLECTIONS = ("memos", "dues", "tasks", "prompts", "notes", "habits", "lifemap", "wins", "worldlog", "presents", "heritagelog", "routines", "routinelog", "rewards", "ideaslog", "gear", "projects", "sections", "jobapps", "replywin", "genres", "tasktypes", "mergeprops", "timelog", "genrelog", "typeprops", "wboards", "wcards", "okrs", "lookups", "concepts", "cards", "cardlog")
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
# Task Desk Chrome extension (Applications/programming/Tatsuki Desk/task-desk-extension). Its id is fixed by the "key" in its manifest.
TASKDESK_ORIGIN = "chrome-extension://nofajepofefkhjckmgciabfjbeeenhlm"
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


def keep_kr(col, old, new):
    """Project OKRs: a page opened before a key-result link was made must not erase it (krAt / okrAt say which is newer)."""
    if col in ("tasks", "sections") and (old.get("krAt") or 0) > (new.get("krAt") or 0):
        for k in ("krId", "krAt", "bet"):
            if k in old:
                new[k] = old[k]
    if col == "projects" and (old.get("okrAt") or 0) > (new.get("okrAt") or 0):
        for k in ("okr", "okrAt"):
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
                keep_kr(col, x, items[i])
                break
        else:
            if merge:
                return None
            items.append({**rec, "id": doc_id})
        save(col, items)
        return next(x for x in items if x["id"] == doc_id)


def load_locked(col):
    with LOCK:
        return load(col)


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
    own = [{"name": x.get("name") or "Schedule", "start": x.get("start"), "end": x.get("end"),
            "days": x.get("days", []), "enabled": x.get("enabled", True),
            "appList": x.get("appList") or [], "siteList": x.get("siteList") or []}
           for x in st.get("schedules", []) if x.get("appList") or x.get("siteList")]
    sites = sorted({d for x in own for d in x["siteList"]})
    return 200, {"hasPassword": st["hasPassword"], "active": st["active"],
                 "apps": {"mode": st["apps"]["mode"], "count": len(st["apps"]["list"])},
                 "web": {"mode": st["web"]["mode"], "count": len(st["web"]["list"])},
                 "own": own, "attempts": st.get("attempts"), "opens": focus_opens(sites)}


def focus_opens(sites):
    """Times each watched site was opened in Chrome today (Web history). A visit after
    10 minutes away counts as a new open. Only site names and counts leave this function."""
    path = os.path.join(DATA, "webhistory", "history.db")
    if not sites or not os.path.exists(path):
        return {}
    import sqlite3
    t = time.localtime()
    start = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=2)
        rows = con.execute("select at, site from visits where deleted=0 and at>=? order by at", (start,)).fetchall()
        con.close()
    except sqlite3.Error:
        return {}
    out, last = {}, {}
    for at, site in rows:
        hit = next((d for d in sites if site == d or site.endswith("." + d)), None)
        if not hit:
            continue
        if at - last.get(hit, 0) > 600:
            out[hit] = out.get(hit, 0) + 1
        last[hit] = at
    return out


UPLOADS = os.path.join(DATA, "uploads")
HERITAGE_PHOTO = re.compile(r"^[0-9a-f]{16}\.(jpg|png)$")
UPLOAD_MAX = 20 * 1024 * 1024
UPLOAD_NAME = re.compile(r"^[0-9a-f]{32}\.(png|jpg|gif|webp|heic|pdf)$")
UPLOAD_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
                "heic": "image/heic", "pdf": "application/pdf"}
# Videos for the Workout Board: kept in data/videos/ (out of the private backup, they are large), streamed with byte ranges.
VIDEOS = os.path.join(DATA, "videos")
VIDEO_MAX = 2 * 1024 ** 3
VIDEO_NAME = re.compile(r"^[0-9a-f]{32}\.(mp4|mov|webm)$")
VIDEO_TYPES = {"mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm"}


def video_kind(head):
    """mp4 / mov / webm from the first bytes, or None. HEIC also uses the ftyp box, so its brands are left to upload_kind."""
    if head[4:8] == b"ftyp" and head[8:12] not in (b"heic", b"heix", b"hevc", b"mif1", b"msf1"):
        return "mov" if head[8:12] == b"qt  " else "mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    return None


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
            return self.send(200, posts.public(posts.sync(post["id"]) or post))  # brings back what go.tany.im sent
        if len(step) == 3 and step[1] == "vimg":
            return self.image_file(posts.video_frame(post["id"], step[2].removesuffix(".jpg")))
        if step[1:] == ["news.html"]:  # the company-site article as it will look
            src = posts.news_preview(post["id"])
            if not src:
                return self.send(404, {"error": "not found"})
            data = src.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if len(step) == 3 and step[1] == "src" and step[2].removesuffix(".jpg").isdigit():  # the whole photo, for the crop box
            return self.image_file(posts.source(post["id"], int(step[2].removesuffix(".jpg"))))
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
            if n > posts.VIDEO_MAX:
                self.close_connection = True
                return self.send(413, {"error": "A file can be up to 1 GB"})
            name = unquote(self.headers.get("X-File-Name") or "")[:200]
            return self.send(*posts.add_file(self.rfile, n, name))
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send(415, {"error": "json only"})
        try:
            rec = self.body()
        except (ValueError, json.JSONDecodeError):
            return self.send(400, {"error": "bad json"})
        if method == "POST" and not step:
            return self.send(*posts.create(rec))
        if method == "POST" and step == ["connect"]:  # LinkedIn or Threads: the platform's own sign-in page; X: checks the keys on go.tany.im
            try:
                if rec.get("provider") == "x":
                    return self.send(200, {"handle": golinks.connect_x()})
                if rec.get("provider") in ("linkedin", "threads", "meta"):
                    return self.send(200, {"url": golinks.sign_in_link(rec["provider"])})
            except RuntimeError as e:
                return self.send(400, {"error": str(e)})
            return self.send(400, {"error": "unknown"})
        if method == "POST" and step == ["youtube-connect"]:  # opens Google's page in the default browser
            threading.Thread(target=youtube.auth, daemon=True).start()
            return self.send(200, {"ok": True})
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
        if method == "POST" and step[1:] == ["approve"]:  # the person pressed Post now, Schedule or Next slot
            return self.send(*posts.approve(pid, rec))
        if method == "POST" and step[1:] == ["unqueue"]:
            return self.send(*posts.cancel_queued(pid, str(rec.get("target") or "")))
        if method == "POST" and step[1:] == ["save"]:
            return self.send(*posts.save_image(pid, str(rec.get("target") or ""), int(rec.get("n") or 0)))
        if method == "POST" and step[1:] == ["savevideo"]:
            return self.send(*posts.save_video(pid, str(rec.get("target") or "")))
        if method == "POST" and step[1:] == ["note"]:  # saves a draft on note; never publishes
            return self.send(*posts.note_save(pid))
        if method == "POST" and step[1:] == ["savedoc"]:
            return self.send(*posts.save_doc(pid, str(rec.get("doc") or "")))
        if method == "POST" and step[1:] == ["publish"]:  # the company site: the person pressed Publish or Schedule
            return self.send(*posts.news_publish(pid, rec))
        if method == "POST" and step[1:] == ["unpublish"]:
            return self.send(*posts.news_unpublish(pid))
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
        if path.startswith("/videos/"):
            return self.video_get(path.rsplit("/", 1)[1])
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
        if path == "/api/instagram":
            return self.send(200, instagram_dm.view())
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
        if path == "/api/translate/state":  # which engine translates now, the month's characters, the card candidates
            return self.send(200, translate.state())
        if path == "/api/news/today":  # Sift Daily's headlines, kept by news_sync every 5 minutes
            return self.send(200, news_sync.today())
        if path == "/api/reader/books":  # Desk Reader: the books on this Mac (desk/reader.py); titles and progress only
            return self.send(200, reader.list_books())
        if path.startswith("/api/reader/books/") and path.count("/") == 4:
            return self.send(*reader.get(path.rsplit("/", 1)[1]))
        if path == "/api/reader/voices":
            return self.send(200, reader.voices_for(parse_qs(urlsplit(self.path).query).get("lang", ["en"])[0]))
        if path == "/api/cards/due":  # flashcards waiting today (desk/cards.py)
            with LOCK:
                return self.send(200, cards.due(load("cards"), load("cardlog")))
        if path == "/api/cards-link":  # the phone's one-time setup link for the Cards app; the passphrase rides in the # part
            k = cards_sync.key()
            return self.send(200, {"url": cards_sync.APP + ("#key=" + urllib.parse.quote(k, safe="") if k else ""), "sync": bool(k)})
        if path == "/api/netspeed":
            return self.send(200, netspeed.view())
        if path == "/api/startclean":  # the apps closed at startup (desk/startup_clean.py); names only
            return self.send(200, startup_clean.view())
        if path == "/api/wifi-login":  # what Wi-Fi this Mac is on and whether it needs a sign-in (desk/wifi_login.py); opens nothing
            return self.send(200, wifi_login.view())
        if path == "/api/footprint":  # rough daily CO2e from records already here (desk/footprint.py); nothing is sent anywhere
            return self.send(200, footprint.view(parse_qs(urlsplit(self.path).query).get("period", ["7"])[0]))
        if path == "/api/footprint/home":
            return self.send(200, footprint.home())
        if path == "/api/transcribe":
            return self.send(200, transcribe_desk.view())
        if path.startswith("/api/utilities/file/"):
            return self.utility_file(path.rsplit("/", 1)[1])
        if path == "/api/posts" or path.startswith("/api/posts/"):
            return self.posts_get(path.split("/")[3:])
        if path == "/api/showcase":
            return self.send(200, showcase.view())
        if path.startswith("/api/campus/issues/") and path.endswith("/preview"):
            page_ = campus.preview(path.split("/")[4])
            return self.send(200, page_, "text/html; charset=utf-8") if page_ else self.send(404, {"error": "not found"})
        if path == "/api/campus" or path.startswith("/api/campus/"):
            return self.send(*campus.handle("GET", [x for x in path.split("/")[3:] if x], {}))
        if path == "/api/book" or path.startswith("/api/book/"):
            q = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            return self.send(*book.handle("GET", [x for x in path.split("/")[3:] if x], q))
        if path == "/api/forms" or path.startswith("/api/forms/"):
            return self.send(*forms.handle("GET", [x for x in path.split("/")[3:] if x], {}))
        if path == "/api/mailclean":
            return self.send(200, gmail_clean.view())
        if path == "/api/body":
            return self.send(200, lifelog.view())
        if path == "/api/receipts":
            return self.send(200, lifelog.receipts_view(parse_qs(urlsplit(self.path).query).get("month", [None])[0]))
        if path.startswith("/lifelog-photos/"):
            return self.lifelog_photo(path.rsplit("/", 1)[1])
        if path == "/api/notify":
            return self.send(200, {**notify.view(), "telegram": tg_notify.view()})
        if path == "/api/pc":
            brief = "summary" in parse_qs(urlsplit(self.path).query)
            return self.send(200, pc_monitor.summary() if brief else pc_monitor.snapshot())
        if path == "/api/wifi":  # the name of the current Wi-Fi only; the password is not touched here
            return self.send(200, wifi_qr.current())
        if path.startswith("/api/web/"):
            return self.web_get(path.rsplit("/", 1)[1], parse_qs(urlsplit(self.path).query))
        if path == "/api/usage":
            return self.send(200, {**usage.view(parse_qs(urlsplit(self.path).query).get("period", ["14"])[0]), "on": usage.enabled(PORT)})
        if path == "/api/layout":
            return self.send(200, usage.public(usage.read_layout(), usage.enabled(PORT)))
        if path == "/api/site":
            # tany.im analytics (desk/site_sync.py): read from desk/data/site/, nothing is fetched here
            q = parse_qs(urlsplit(self.path).query)
            return self.send(200, site_sync.view(q.get("period", ["7"])[0], q.get("from", [None])[0], q.get("to", [None])[0]))
        if path == "/api/site/home":
            return self.send(200, site_sync.home_line() or {})
        if path == "/api/claude-usage":
            return self.send(200, claude_usage.view())
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

    def video_post(self, head, n):
        """A video, written to disk as it arrives (never whole in memory) and named by its content."""
        if n > VIDEO_MAX:
            self.close_connection = True
            return self.send(413, {"error": "The limit is 2GB"})
        os.makedirs(VIDEOS, exist_ok=True)
        tmp = os.path.join(VIDEOS, ".up-" + secrets.token_hex(6) + ".tmp")
        h, left = hashlib.sha256(head), n - len(head)
        try:
            with open(tmp, "wb") as f:
                f.write(head)
                while left > 0:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        break
                    h.update(chunk)
                    f.write(chunk)
                    left -= len(chunk)
            if left:
                os.remove(tmp)
                return self.send(400, {"error": "The upload stopped part way"})
            name = h.hexdigest()[:32] + "." + video_kind(head)
            path = os.path.join(VIDEOS, name)
            if os.path.exists(path):
                os.remove(tmp)
            else:
                os.replace(tmp, path)
            return self.send(200, {"url": "/videos/" + name, "size": n})
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return self.send(500, {"error": "Could not save"})

    def video_get(self, name):
        """Byte ranges: Safari and the Desk window will not play an MP4 without them, and seeking uses them everywhere."""
        m = VIDEO_NAME.match(name)
        path = os.path.join(VIDEOS, name) if m else ""
        if not m or not os.path.isfile(path):
            return self.send(404, {"error": "no such file"})
        size = os.path.getsize(path)
        start, end = 0, size - 1
        r = re.match(r"^bytes=(\d*)-(\d*)$", self.headers.get("Range") or "")
        if r and (r[1] or r[2]):
            if r[1]:
                start, end = int(r[1]), min(int(r[2]), size - 1) if r[2] else size - 1
            else:
                start = max(0, size - int(r[2]))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        else:
            self.send_response(200)
        self.send_header("Content-Type", VIDEO_TYPES[m.group(1)])
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "max-age=31536000, immutable")
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def upload_post(self):
        # the custom header needs a CORS preflight this server never answers, so another site's page cannot post here
        if self.headers.get("X-Desk-Upload") != "1":
            return self.send(415, {"error": "upload header required"})
        n = int(self.headers.get("Content-Length") or 0)
        head = self.rfile.read(min(n, 64))
        if video_kind(head) and not upload_kind(head):
            return self.video_post(head, n)
        if n > UPLOAD_MAX:
            self.close_connection = True
            return self.send(413, {"error": "The limit is 20MB"})
        try:
            return self.send(200, {"url": save_upload(head + self.rfile.read(n - len(head)))})
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
        if method == "POST" and urlsplit(self.path).path == "/api/reader/books" and self.headers.get("X-Desk-Upload") == "1":
            # a book file (.txt, .epub, PDF with text): the custom header needs a CORS preflight this server never answers
            n = int(self.headers.get("Content-Length") or 0)
            if n > reader.BOOK_MAX:
                self.close_connection = True
                return self.send(413, {"error": "A file can be up to 50 MB"})
            return self.send(*reader.add_file(self.rfile.read(n), unquote(self.headers.get("X-File-Name") or "")))
        if urlsplit(self.path).path.startswith("/api/reader/") or urlsplit(self.path).path in ("/api/cards/add", "/api/cards/lookup", "/api/cards/enrich", "/api/cards/suggest"):
            return self.reader_write(method, urlsplit(self.path).path.split("/")[2:])  # PUT /api/cards/<id> stays an ordinary collection write
        if method == "POST" and urlsplit(self.path).path.startswith("/api/utilities/"):
            return self.utilities_post(urlsplit(self.path).path)
        if method == "POST" and urlsplit(self.path).path == "/api/wifi/qr":
            # same protection as claude(): Host/Origin checked, and JSON from another origin needs a preflight.
            # macOS asks the person before the keychain answers; only the rows of the code go back, never the password
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            try:
                rec = self.body()
            except (ValueError, json.JSONDecodeError):
                return self.send(400, {"error": "bad json"})
            return self.send(*wifi_qr.make(rec.get("password"), rec.get("ssid")))
        if urlsplit(self.path).path == "/api/posts" or urlsplit(self.path).path.startswith("/api/posts/"):
            return self.posts_write(method, urlsplit(self.path).path.split("/")[3:])
        if method == "POST" and urlsplit(self.path).path == "/api/instagram/send":  # the person pressed Send on an Instagram reply
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            try:
                rec = self.body()
            except (ValueError, json.JSONDecodeError):
                return self.send(400, {"error": "bad json"})
            return self.send(*instagram_dm.send(str(rec.get("task") or ""), rec.get("text")))
        col, doc_id = self.route()
        try:
            rec = self.body()
        except (ValueError, json.JSONDecodeError):
            return self.send(400, {"error": "bad json"})
        if method == "PUT" and urlsplit(self.path).path.startswith("/api/web/"):
            return self.web_put(urlsplit(self.path).path.split("/")[3:], rec)
        if urlsplit(self.path).path == "/api/translate" or urlsplit(self.path).path.startswith(("/api/translate/", "/api/concepts/merge", "/api/concepts/split")):
            return self.translate_write(method, urlsplit(self.path).path, rec)
        if urlsplit(self.path).path.startswith("/api/bank/"):
            return self.bank_write(method, rec)
        if urlsplit(self.path).path.startswith("/api/accounts/"):
            return self.accounts_write(method, rec)
        if method == "POST" and urlsplit(self.path).path == "/api/usage":
            # counts only; a test server on another port answers and keeps nothing
            return self.send(200, {"ok": usage.enabled(PORT) and usage.record(rec)})
        if method == "PUT" and urlsplit(self.path).path == "/api/layout":
            return self.send(200, usage.change(rec, usage.enabled(PORT)))
        if urlsplit(self.path).path.startswith("/api/site/") and method in ("POST", "PUT"):
            return self.site_action(method, urlsplit(self.path).path.rsplit("/", 1)[1], rec)
        if method == "POST" and urlsplit(self.path).path == "/api/claude-usage/refresh":
            claude_usage.refresh()  # counts the local records again; nothing is sent anywhere
            return self.send(200, claude_usage.view())
        if method == "POST" and urlsplit(self.path).path == "/api/claude":
            return self.claude(rec)
        if method == "POST" and urlsplit(self.path).path in ("/api/focus/start", "/api/focus/stop",
                                                                  "/api/focus/pass", "/api/focus/passend"):
            return self.focus(urlsplit(self.path).path.rsplit("/", 1)[1], rec)
        if method == "POST" and urlsplit(self.path).path == "/api/finder":
            # Ask Desk: the page sends the list of places; Claude picks from it (desk/finder.py). Nothing is saved
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            try:
                return self.send(200, finder.handle(rec))
            except ValueError as e:
                return self.send(400, {"error": str(e)})
        if method == "POST" and urlsplit(self.path).path == "/api/wifi-login/open":
            # the person pressed Wi-Fi Login: find the Wi-Fi's sign-in page and open it in Safari. Only ever a page; nothing is pressed for them
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            return self.send(200, wifi_login.run())
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
        if urlsplit(self.path).path.startswith("/api/campus/") and method in ("POST", "PUT"):
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            return self.send(*campus.handle(method, [x for x in urlsplit(self.path).path.split("/")[3:] if x], rec if isinstance(rec, dict) else {}))
        if urlsplit(self.path).path.startswith("/api/book/") and method in ("POST", "PUT"):
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            return self.send(*book.handle(method, [x for x in urlsplit(self.path).path.split("/")[3:] if x], rec if isinstance(rec, dict) else {}))
        if urlsplit(self.path).path.startswith("/api/forms") and method in ("POST", "PUT"):
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            return self.send(*forms.handle(method, [x for x in urlsplit(self.path).path.split("/")[3:] if x], rec if isinstance(rec, dict) else {}))
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
            if step == ("PUT", "telegram"):  # Telegram: which kinds, sent by desk/tg_notify.py
                return self.send(*tg_notify.put_settings(rec))
            if step == ("POST", "tgtest"):
                return self.send(*tg_notify.test())
            if step == ("POST", "tgconnect"):
                return self.send(*tg_notify.connect())
            return self.send(404, {"error": "not found"})
        if method == "POST" and urlsplit(self.path).path in ("/api/startclean/reopen", "/api/startclean/dismiss"):
            # reopen only what startup_clean closed and listed itself; the page sends which ones, never a path to run
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            if urlsplit(self.path).path.endswith("/dismiss"):
                return self.send(200, startup_clean.dismiss())
            keys = rec.get("apps")
            if keys is not None and not (isinstance(keys, list) and all(isinstance(k, str) for k in keys)):
                return self.send(400, {"error": "bad request"})
            return self.send(200, startup_clean.reopen(keys))
        if method == "POST" and urlsplit(self.path).path == "/api/pc/close":
            # same protection as claude(): Host/Origin checked, and JSON from another origin needs a preflight
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            return self.send(*pc_monitor.close(rec.get("pid"), rec.get("started"), rec.get("force") is True))
        if method == "POST" and urlsplit(self.path).path == "/api/memolink/run":
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            return self.send(*memolink_run())
        if method == "PUT" and urlsplit(self.path).path.startswith("/api/footprint/trips/"):
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self.send(415, {"error": "json only"})
            return self.send(*footprint.put_trip(urlsplit(self.path).path.rsplit("/", 1)[1], rec))
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

    def translate_write(self, method, path, rec):
        """Translate (desk/translate.py). Only the text being translated leaves this Mac; the keys stay in the keychain."""
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send(415, {"error": "json only"})
        if method == "POST" and path == "/api/translate":
            return self.send(*translate.handle_translate(rec))
        if method == "POST" and path == "/api/translate/enrich":  # lemma, examples, the other languages, the concept (Claude)
            return self.send(*translate.handle_enrich(rec))
        if method == "POST" and path == "/api/translate/cards":  # Add to cards / Not now
            return self.send(*translate.handle_cards(rec))
        if method == "PUT" and path == "/api/translate/settings":
            return self.send(*translate.put_settings(rec))
        if method == "POST" and path in ("/api/concepts/merge", "/api/concepts/split"):
            return self.send(*translate.handle_concepts(path.rsplit("/", 1)[1], rec))
        return self.send(404, {"error": "not found"})

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

    def reader_write(self, method, step):
        """Desk Reader and flashcards (desk/reader.py, desk/cards.py). JSON only, so another origin would need a preflight.
        A book's text goes nowhere but `say`; one word and its sentence go to Claude for the meaning."""
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send(415, {"error": "json only"})
        try:
            rec = self.body()
        except (ValueError, json.JSONDecodeError):
            return self.send(400, {"error": "bad json"})
        if step[0] == "reader":
            if method == "POST" and step[1:] == ["books"]:
                return self.send(*reader.add_text(rec.get("text"), rec.get("title") or "", rec.get("author") or "", rec.get("lang") or ""))
            if method == "PUT" and len(step) == 4 and step[1] == "books" and step[3] == "pos":
                return self.send(*reader.set_pos(step[2], rec))
            if method == "PUT" and len(step) == 3 and step[1] == "books":
                return self.send(*reader.edit(step[2], rec))
            if method == "POST" and len(step) == 4 and step[1] == "books" and step[3] == "remove":
                return self.send(*reader.remove(step[2]))
            if method == "POST" and step[1:] == ["speak"]:
                code, out = reader.speak(rec)
                return self.send(code, out, "audio/mp4") if code == 200 else self.send(code, out)
            return self.send(404, {"error": "not found"})
        store = cards.Store(load_locked, upsert)
        if method == "POST" and step[1:] == ["add"]:
            return self.send(*cards.add(store, rec))
        if method == "POST" and step[1:] == ["lookup"]:
            try:
                return self.send(200, cards.lookup(str(rec.get("term") or "")[:cards.TERM_MAX], str(rec.get("lang") or "en")[:8], str(rec.get("context") or "")[:cards.CONTEXT_MAX]))
            except cards.Unavailable as e:
                return self.send(503, {"error": str(e)})
        if method == "POST" and step[1:] == ["enrich"]:
            return self.send(*cards.enrich_one(store, str(rec.get("id") or "")))
        if method == "POST" and step[1:] == ["suggest"]:
            return self.send(*cards.suggest(store, rec))
        return self.send(404, {"error": "not found"})

    def focus(self, action, rec):
        # same protection as claude(): Host/Origin checked, and JSON from another origin needs a preflight
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send(415, {"error": "json only"})
        # pass: the Mac password typed 3 times only passes through to FocusBlock (PAM check); never saved or logged
        keys = {"start": ("end", "apps", "web"), "stop": ("password",),
                "pass": ("passwords",), "passend": ()}[action]
        body = {k: rec.get(k) for k in keys}
        if action == "stop":
            body["scope"] = "focus"  # schedules with their own lists (night SNS, Brave) keep running
        code, out = focus_call("/api/" + action, body)
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

    def site_action(self, method, what, rec):
        # tany.im analytics: pull now, Search Console, the SEO check, UTM links and the Clarity id
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.send(415, {"error": "json only"})
        if method == "POST" and what == "refresh":
            site_sync.pull()
            return self.send(200, {"ok": True})
        if method == "POST" and what == "gsc":
            site_sync.gsc_pull()
            return self.send(200, {"ok": True})
        if method == "POST" and what == "gsc-connect":
            threading.Thread(target=site_sync.gsc_auth, daemon=True).start()  # opens Google's page in the default browser
            return self.send(200, {"ok": True})
        if method == "POST" and what == "audit":
            return self.send(200, site_sync.audit())
        if method == "POST" and what == "utm":
            if not str(rec.get("source") or "").strip():
                return self.send(400, {"error": "source"})
            return self.send(200, {"url": site_sync.utm(str(rec.get("source")), str(rec.get("medium") or ""), str(rec.get("campaign") or ""), str(rec.get("content") or ""), str(rec.get("path") or "/"))})
        if method == "PUT" and what == "settings":
            cid = str(rec.get("clarity") or "").strip()
            if cid and not re.fullmatch(r"[a-z0-9]{6,16}", cid):
                return self.send(400, {"error": "clarity id"})
            s = site_sync.settings()
            s["clarity"] = cid
            site_sync._save(site_sync.SETTINGS, s)
            return self.send(200, s)
        return self.send(404, {"error": "not found"})

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
    translate.bind(load, save, LOCK)  # lookups, concepts and cards are ordinary collections written under the same lock
    if PORT == 4747:  # a test server on another port shows nothing
        threading.Thread(target=notify.watch, daemon=True).start()
        threading.Thread(target=checkin.watch, args=(load, save, LOCK), daemon=True).start()  # in-progress timers: ask every 10 minutes
        threading.Thread(target=tg_notify.watch, daemon=True).start()  # tasks and events to Telegram
        threading.Thread(target=event_alarm.watch, daemon=True).start()  # alarm 3 minutes before each calendar event
        threading.Thread(target=instagram_dm.watch, daemon=True).start()  # Instagram DMs to Replies, every 5 minutes
        threading.Thread(target=posts.watch, daemon=True).start()  # Post Studio: company-site news scheduled for later
        threading.Thread(target=routine_alarm.watch, daemon=True).start()  # routine start alarm (quiet 22:00-07:00)
        threading.Thread(target=routine_sync.watch, daemon=True).start()  # routines <-> the Routine app on the phone
        threading.Thread(target=cards_sync.watch, daemon=True).start()  # flashcards <-> the Cards app on the phone
        threading.Thread(target=cards.watch, args=(cards.Store(load_locked, upsert),), daemon=True).start()  # fills missing meanings every 30 minutes
        try:
            lifelog.ensure_log_habits()  # "Log sleep" / "Log meals": tick themselves when a record arrives
        except Exception as e:
            print("log habits:", e, file=sys.stderr)
        threading.Thread(target=news_sync.watch, daemon=True).start()  # Sift Daily's "Make it a task" -> Desk tasks, every 5 minutes
        threading.Thread(target=transcribe_desk.watch, daemon=True).start()  # the transcriber on 8791, kept running
        threading.Thread(target=claude_usage.watch, daemon=True).start()  # Claude usage counted again every 5 minutes
        threading.Thread(target=site_sync.watch, daemon=True).start()  # tany.im analytics pulled every 15 minutes
        threading.Thread(target=book.watch, daemon=True).start()  # bookings from tany.im/book every 5 minutes (notify.py tells about new ones)
        forms.STORE.update(load=load, upsert=upsert)  # a form for yourself can write a line into the day's note
        threading.Thread(target=forms.watch, daemon=True).start()  # answers to tany.im forms pulled every 5 minutes
        threading.Thread(target=translate.watch, daemon=True).start()  # Google Translate pages and 「〜とは」 searches from the web history, hourly; concepts at night
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
