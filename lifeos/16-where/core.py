"""/where: gathers what is known about a project and prints it as exactly 5 lines.

Reads Desk tasks (the spec's tasks/: Life OS keeps tasks only in Desk), the last /where result,
and whatever the Drive and Gmail readers return. Writes only logs/where.jsonl.
"""
import os
import re
import unicodedata
from datetime import datetime

from lifeos.lib import logs, paths, tasks
from lifeos.lib.tools import load_file

connectors = load_file(os.path.dirname(os.path.abspath(__file__)), "connectors")

LINE_MAX = 100
UNKNOWN = "記録なし"
LOG = "where"
_PROJECT = re.compile(r"案件[:：]\s*(\S+)")
_LABELS = (("tasks", "タスク"), ("drive", "Drive"), ("gmail", "Gmail"))


def norm(s):
    return unicodedata.normalize("NFKC", str(s or "")).casefold()


def matches(name, *texts):
    """Every word of the name appears somewhere in the texts."""
    words = norm(name).split()
    blob = norm(" ".join(str(t or "") for t in texts))
    return bool(words) and all(w in blob for w in words)


def _date(ms):
    return datetime.fromtimestamp(ms / 1000, paths.HCMC).strftime("%Y-%m-%d") if ms else ""


def _touched(t):
    return t.get("updatedAt") or t.get("createdAt") or 0


def _brief(t):
    return {"id": t.get("id"), "title": t.get("title", ""), "status": t.get("status"), "note": (t.get("note") or "")[:300],
            "date": _date(_touched(t))}


def find_tasks(name):
    """The project's tasks, newest first. Paused and archived ones say nothing about progress and are left out."""
    found = sorted((t for t in tasks.all_tasks() if matches(name, t.get("title"), t.get("note"))), key=_touched, reverse=True)
    return {"done": [_brief(t) for t in found if t.get("status") == "done"],
            "open": [_brief(t) for t in found if t.get("status") in tasks.OPEN]}


def previous(name):
    """The last /where result for this exact project, or None."""
    return next((r for r in reversed(logs.read(LOG)) if norm(r.get("name")) == norm(name)), None)


def _read(reader, name):
    try:
        items = reader(name)
    except Exception:
        return {"status": "失敗", "items": []}
    return {"status": "未接続", "items": []} if items is None else {"status": "ok", "items": list(items)}


def gather(name, readers=None):
    readers = readers or {"drive": connectors.read_drive, "gmail": connectors.read_gmail}
    try:
        found, status = find_tasks(name), "ok"
    except tasks.TaskStoreError:
        found, status = {"done": [], "open": []}, "失敗"
    return {"name": " ".join(str(name).split()), "started_ms": paths.now_ms(), "tasks": {"status": status, **found},
            "previous": previous(name), "drive": _read(readers["drive"], name), "gmail": _read(readers["gmail"], name),
            "queries": {"drive": connectors.drive_query(name), "gmail": connectors.gmail_query(name)}}


def _titles(items, limit=2):
    head = "、".join(i.get("title", "") for i in items[:limit])
    return head + ("、ほか%d件" % (len(items) - limit) if len(items) > limit else "")


def summarize(g):
    """The 5-line fields from the gathered facts alone, for running without Claude. Guesses nothing."""
    done, todo = g["tasks"]["done"], g["tasks"]["open"]
    found = g["drive"]["items"] + g["gmail"]["items"]
    prev = g.get("previous") or {}
    dates = sorted(d for d in [i.get("date") for i in done + todo + found] + [(prev.get("at") or "")[:10]] if d)
    doing = [t for t in todo if t.get("status") == "doing"]
    # oldest open task: the one that has been waiting longest
    first = (doing or list(reversed(todo)) or [None])[0]
    return {"name": g["name"], "started_ms": g.get("started_ms"), "last_date": dates[-1] if dates else "",
            "done": _titles(done) or prev.get("done") or (("最新：" + _titles(found)) if found else UNKNOWN),
            "remaining": ("%d件：%s" % (len(todo), _titles(todo))) if todo else prev.get("remaining") or UNKNOWN,
            "next": ("「%s」を開く" % first["title"]) if first else prev.get("next") or UNKNOWN,
            "sources": {"tasks": {"status": g["tasks"]["status"], "count": len(done) + len(todo)},
                        "drive": {"status": g["drive"]["status"], "count": len(g["drive"]["items"])},
                        "gmail": {"status": g["gmail"]["status"], "count": len(g["gmail"]["items"])}}}


def _line(text):
    """Always one line: whatever the text holds cannot break the 5-line shape."""
    text = " ".join(str(text or "").split()) or UNKNOWN
    return text if len(text) <= LINE_MAX else text[:LINE_MAX - 1] + "…"


def _last(date):
    try:
        d = datetime.strptime(str(date), "%Y-%m-%d").date()
    except ValueError:
        return UNKNOWN
    days = (paths.now().date() - d).days
    return "%d/%d・%s" % (d.month, d.day, "今日" if days == 0 else "%d日前" % days if days > 0 else "先の日付")


def _sources(sources):
    parts = []
    for key, label in _LABELS:
        s = (sources or {}).get(key) or {}
        status = s.get("status") or "未確認"
        parts.append("%s%d件" % (label, int(s.get("count") or 0)) if status == "ok" else "%s %s" % (label, status))
    return "・".join(parts)


def _spent(started_ms):
    if not started_ms:
        return ""
    sec = max(0, int((paths.now_ms() - int(started_ms)) / 1000))
    return "／所要%d分%02d秒" % (sec // 60, sec % 60)


def render(f):
    return "\n".join([
        _line("📍 案件：%s（最後の動き：%s）" % (f.get("name") or UNKNOWN, _last(f.get("last_date")))),
        _line("✅ どこまで：%s" % (f.get("done") or UNKNOWN)),
        _line("⏳ 残り：%s" % (f.get("remaining") or UNKNOWN)),
        _line("👉 次の一手：%s" % (f.get("next") or UNKNOWN)),
        _line("🔎 根拠：%s%s" % (_sources(f.get("sources")), _spent(f.get("started_ms")))),
    ])


def remember(f):
    return logs.append(LOG, {k: f.get(k) for k in ("name", "done", "remaining", "next", "last_date")})


def stalled(limit=3):
    """Projects named as 案件:X in open task notes, longest untouched first."""
    last = {}
    for t in tasks.open_tasks():
        m = _PROJECT.search(t.get("note") or "")
        if m:
            last[m.group(1)] = max(last.get(m.group(1), 0), _touched(t))
    return [{"name": k, "last_date": _date(v)} for k, v in sorted(last.items(), key=lambda kv: kv[1])[:limit]]
