"""一瞬メモ: add, pick the next task, finish it, and save where the work stopped.

Tasks live only in Desk's tasks (lifeos.lib.tasks); priority and project go in the note.
Task data is not in git, so every change made here also goes to logs/task-history.jsonl.
"""
import importlib.util
import json
import os
import re
import subprocess

from lifeos.lib import logs, paths, tasks

SOURCE = "lifeos-03"
PRIORITIES = ("高", "中", "低")
NO_PROJECT = "未分類"
HIGH = re.compile(r"大?至急|緊急|急ぎ|今日中|本日中|締切|締め切り|期限|asap", re.I)
LOW = re.compile(r"いつか|そのうち|余裕|できれば|暇な(?:とき|時)")
EXPLICIT_PRIORITY = re.compile(r"(?:^|\s)(?:[!！]|優先度[:：])(高|中|低)(?=\s|$)")
EXPLICIT_PROJECT = re.compile(r"(?:^|\s)(?:[#＃]|案件[:：])(\S+)")
NOTE_PRIORITY = re.compile(r"優先度[:：](高|中|低)")
NOTE_PROJECT = re.compile(r"案件[:：](\S+)")
LINKED_SOURCES = ("mail", "calendar", "gtasks", "claude", "daily")  # title and due date live at the source


def _state_path():
    return paths.data_path("tools", "03-task", "state.json")


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def _set_current(task_id):
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"current": task_id}, f)
    os.replace(tmp, _state_path())


def known_projects():
    """Project names used before, plus tools/03-task/projects.json (a list the person may edit)."""
    names = {m.group(1) for t in tasks.all_tasks(include_deleted=True) for m in [NOTE_PROJECT.search(t.get("note") or "")] if m}
    extra = _read_json(paths.data_path("tools", "03-task", "projects.json"), [])
    names.update(x for x in extra if isinstance(x, str) and x.strip())
    names.discard(NO_PROJECT)
    return names


def parse(text, projects=()):
    """Returns (title, priority, project). The explicit marks !高 and #案件 are removed from the title."""
    text = " ".join(str(text or "").split())
    priority = None
    m = EXPLICIT_PRIORITY.search(text)
    if m:
        priority = m.group(1)
        text = (text[:m.start()] + " " + text[m.end():]).strip()
    project = None
    m = EXPLICIT_PROJECT.search(text)
    if m:
        project = m.group(1)
        text = (text[:m.start()] + " " + text[m.end():]).strip()
    title = " ".join(text.split())
    if not priority:
        priority = "高" if HIGH.search(title) else "低" if LOW.search(title) else "中"
    if not project:
        hits = [p for p in projects if p in title]
        project = max(hits, key=len) if hits else NO_PROJECT
    return title, priority, project


def priority_of(task):
    m = NOTE_PRIORITY.search(task.get("note") or "")
    return m.group(1) if m else "中"


def _history(action, task):
    logs.append("task-history", {"action": action, "id": task["id"], "title": task.get("title", ""),
                                 "status": task.get("status", ""), "note": task.get("note", "")})


def add(text, source=SOURCE):
    title, priority, project = parse(text, known_projects())
    task = tasks.add(title, note=f"優先度:{priority} 案件:{project}", source=source)
    _history("add", task)
    return task


def _kickoff():
    """desk/kickoff.py owns the order of "today's three". Load it and point it at the real Desk data."""
    spec = importlib.util.spec_from_file_location("lifeos_03_kickoff", os.path.join(paths.REPO, "desk", "kickoff.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.DATA = paths.desk_data_dir()
    return mod


def queue():
    """Open tasks, best first: due today, overdue, in progress, then priority, then kickoff's order."""
    listed = _kickoff().candidates()["tasks"]
    seen = {t["id"] for t in listed}
    # kickoff returns its first 15: a high-priority task further down must still be reachable
    extra = [t for t in tasks.open_tasks() if t["id"] not in seen and t.get("source") != "mail"]
    now = paths.now_ms()
    end = int(paths.now().replace(hour=23, minute=59, second=59).timestamp() * 1000)

    def rank(pair):
        i, t = pair
        due = t.get("due")
        when = 2 if not isinstance(due, (int, float)) else 0 if now <= due <= end else 1 if due < now else 2
        return (when, 0 if t.get("status") == "doing" else 1, PRIORITIES.index(priority_of(t)), i)

    return [t for _, t in sorted(enumerate(listed + extra), key=rank)]


def next_task():
    """The one task to do next, remembered so that /done with no words finishes it. Changes no task."""
    q = queue()
    if not q:
        return None
    _set_current(q[0]["id"])
    return q[0]


def _finish(task):
    if tasks.get(task["id"]):
        rec = tasks.done(task["id"])
    else:
        # a mail or Google task that the Desk has only listed so far: store it the way the Desk does
        ms = paths.now_ms()
        rec = {"id": task["id"], "title": task.get("title", ""), "status": "done", "source": task.get("source", "manual"),
               "sourceRef": task.get("sourceRef", ""), "due": None if task.get("source") in LINKED_SOURCES else task.get("due"),
               "note": task.get("note", "")[:300], "createdAt": ms, "deleted": False, "updatedAt": ms}
        tasks._put(rec)
    _history("done", rec)
    return rec


def done(words=""):
    """Returns (finished task or None, candidates). Nothing is finished unless exactly one task is meant."""
    q = queue()
    words = " ".join(str(words or "").split())
    if words:
        hits = [t for t in q if t["id"] == words or words in t.get("title", "")]
    else:
        cur = _read_json(_state_path(), {}).get("current")
        hits = [t for t in q if t["id"] == cur] or [t for t in q if t.get("status") == "doing"]
    if len(hits) != 1:
        return None, (hits or q)[:3]
    return _finish(hits[0]), []


def _git(*args):
    try:
        r = subprocess.run(["git", "-C", paths.REPO, *args], capture_output=True, text=True, timeout=5)
        return r.stdout.rstrip() if r.returncode == 0 else ""  # status --short starts with a meaningful space
    except (OSError, subprocess.SubprocessError):
        return ""


def save(did="", next_step=""):
    """Saves where the work stopped to logs/work.jsonl with kind "save"."""
    cur_id = _read_json(_state_path(), {}).get("current")
    cur = tasks.get(cur_id) if cur_id else None
    changed = [line[3:] for line in _git("status", "--short").splitlines()]
    return logs.append("work", {"kind": "save", "did": did.strip(), "next": next_step.strip(),
                                "task": (cur or {}).get("title", ""), "branch": _git("branch", "--show-current"),
                                "changed": changed[:30]})


def line(task):
    return f"{task.get('title', '')}（{task.get('note') or '優先度:中'}）"
