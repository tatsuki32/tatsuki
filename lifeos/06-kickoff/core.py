"""Life OS 06: finds stalled Desk tasks and records what a /kickoff run actually did.

The first step itself is done by Claude in .claude/commands/kickoff.md. This file holds what must
not depend on Claude's memory: which tasks count as stalled, which task a name means, and the
check that a run left something real plus exactly one next step.

Task details stay in the Desk and in LIFEOS_DATA. Nothing here sends, pays or hard-deletes.
"""
import difflib
import json
import os
import re
from datetime import datetime, timedelta

from lifeos.lib import logs, paths, tasks, tools

SLUG = "06-kickoff"
STALE_DAYS = 7
SUGGEST_MAX = 3      # "今やること" is never more than 3
DOING_MAX = 3        # a run does not push a 4th task into "doing"
SKIP_DAYS = 7
NEXT_MAX = 100       # one small action fits in one short line
RECENT_HOURS = 6     # what a run made must have changed this recently
TITLE_SHOW = 30
CLOSE = 0.6          # how alike two names must be to ask "is it this one?"
LOG = "kickoff"
NOTE_MARK = "[初動]"
# these come back by themselves (daily, claude) or belong to a date (calendar)
NOT_STALLED_SOURCES = ("daily", "claude", "calendar")
# what Life OS tools ask the owner to do by hand (connecting accounts): Claude cannot start those
OWNER_ASK = re.compile(r"^Life OS \d\d：")


def stopped():
    return tools.info(os.path.dirname(os.path.abspath(__file__))).get("status") == "stopped"


def _state_path():
    return paths.data_path("tools", SLUG, "state.json")


def _state():
    try:
        with open(_state_path(), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state):
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _state_path())


def _date(day):
    return datetime.strptime(str(day)[:10], "%Y-%m-%d").date()


def _last_touched(task):
    """Epoch ms of the task's last change. Older records have only createdAt."""
    for key in ("updatedAt", "createdAt"):
        v = task.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            return v
    return None


def _idle_days(task, day):
    ms = _last_touched(task)
    if ms is None:
        return None
    return (day - datetime.fromtimestamp(ms / 1000, paths.HCMC).date()).days


def _one_line(text):
    return " ".join(str(text or "").split())


def stale(day=None, days=STALE_DAYS):
    """Open tasks untouched for `days` or more, longest first, leaving out skipped ones."""
    day = _date(day or paths.today())
    skips = _state().get("skips") or {}
    out = []
    for t in tasks.open_tasks():
        if t.get("source") in NOT_STALLED_SOURCES or OWNER_ASK.match(t.get("title") or ""):
            continue
        idle = _idle_days(t, day)
        if idle is None or idle < days:
            continue
        until = skips.get(t["id"])
        if isinstance(until, str) and until > day.isoformat():
            continue
        out.append({"id": t["id"], "title": _one_line(t.get("title")), "status": t.get("status"), "idleDays": idle})
    return sorted(out, key=lambda x: -x["idleDays"])


def suggestions(day=None):
    if not suggesting():
        return []
    return stale(day)[:SUGGEST_MAX]


def suggesting():
    return _state().get("suggest", True) is not False


def set_suggesting(on):
    state = _state()
    state["suggest"] = bool(on)
    _save_state(state)


def skip(task_id, days=SKIP_DAYS):
    """No proposal for this task for `days` days. Touches the proposal only, never the task."""
    if not tasks.get(task_id):
        raise KeyError(task_id)
    if days < 1:
        raise ValueError("日数は1以上です")
    until = (_date(paths.today()) + timedelta(days=days)).isoformat()
    state = _state()
    state.setdefault("skips", {})[task_id] = until
    _save_state(state)
    return until


def _norm(text):
    return "".join(str(text or "").split()).lower()


def resolve(name):
    """Which open task `name` means: {"match": "one"|"many"|"none", "task", "candidates"}."""
    wanted = _norm(name)
    if not wanted:
        raise ValueError("作業名が空です")
    items = tasks.open_tasks()

    def result(found):
        if len(found) == 1:
            return {"match": "one", "task": found[0], "candidates": []}
        return {"match": "many", "task": None, "candidates": found[:SUGGEST_MAX]}

    for test in (lambda t: t["id"] == str(name).strip(),
                 lambda t: _norm(t.get("title")) == wanted,
                 lambda t: wanted in _norm(t.get("title"))):
        found = [t for t in items if test(t)]
        if found:
            return result(found)
    # a near match is only a question: guessing here would add first steps to the wrong task
    close = [t for t in items if t.get("title") and _norm(t["title"]) in wanted
             or difflib.SequenceMatcher(None, wanted, _norm(t.get("title"))).ratio() >= CLOSE]
    if close:
        return {"match": "many", "task": None, "candidates": close[:SUGGEST_MAX]}
    return {"match": "none", "task": None, "candidates": []}


def work_dir(title, task_id=None):
    """Where a first step's drafts go. Inside LIFEOS_DATA, so never in git."""
    safe = re.sub(r"[^\w-]+", "_", _one_line(title))[:40].strip("_") or "work"
    folder = task_id or (paths.today() + "-" + safe)
    path = paths.data_path("tools", SLUG, "work", re.sub(r"[^\w-]+", "_", folder))
    os.makedirs(path, exist_ok=True)
    return path


def begin(name):
    """What a run needs before it starts. Only prepares its folder: nothing is claimed until finish()."""
    found = resolve(name)
    task = found["task"]
    out = {"match": found["match"],
           "candidates": [{"id": t["id"], "title": _one_line(t.get("title"))} for t in found["candidates"]]}
    if found["match"] == "many":
        return out
    title = task["title"] if task else _one_line(name)
    folder = work_dir(title, task["id"] if task else None)
    out.update(title=title, workDir=folder, existingFiles=sorted(os.listdir(folder)))
    if task:
        out["task"] = {k: task.get(k) for k in ("id", "title", "status", "source", "note")}
        earlier = [r for r in logs.read(LOG) if r.get("taskId") == task["id"]]
        if earlier:
            out["lastRun"] = {k: earlier[-1].get(k) for k in ("at", "did", "made", "next")}
    return out


def _newest_change(path):
    """mtime of a non-empty file, or of the newest non-empty file in a folder. None if there is none."""
    if os.path.isfile(path):
        return os.path.getmtime(path) if os.path.getsize(path) > 0 else None
    times = []
    for folder, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        times += [_newest_change(os.path.join(folder, f)) for f in files]
    return max([t for t in times if t], default=None)


def _check_made(made):
    """Absolute paths of what the run made. Refuses a run that left nothing new."""
    if not made:
        raise ValueError("できたものがありません。雛形か下書きを作ってから記録してください")
    out = []
    limit = paths.now().timestamp() - RECENT_HOURS * 3600
    for m in made:
        p = os.path.abspath(os.path.expanduser(str(m)))
        changed = _newest_change(p)
        if changed is None:
            raise ValueError("できたものが見つからないか、空です：" + str(m))
        if changed < limit:
            raise ValueError("今回作ったものではありません（古いファイルです）：" + str(m))
        out.append(p)
    return out


def _check_next(text):
    """The next step must be one action: a person who stalled should not get a list."""
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("次の5分の作業がありません")
    if "\n" in raw or len(raw) > NEXT_MAX:
        raise ValueError(f"次の5分の作業は1つだけ、1行{NEXT_MAX}字までにしてください")
    return raw


def _note_with_run(note, did, made, next_step):
    """The person's own note stays. Only this tool's previous lines are replaced."""
    kept = [line for line in str(note or "").splitlines() if not line.startswith(NOTE_MARK)]
    while kept and not kept[-1].strip():
        kept.pop()
    lines = [f"{NOTE_MARK} {paths.today()} やったこと：{did}",
             f"{NOTE_MARK} できたもの：{', '.join(made)}",
             f"{NOTE_MARK} 次の5分：{next_step}"]
    return "\n".join(kept + ([""] if kept else []) + lines)


def finish(did, made, next_step, task_id=None, title=None):
    """Records a finished first step on the task (adding the task if it is new) and returns it."""
    did = _one_line(did)
    if not did:
        raise ValueError("やったことがありません")
    next_step = _check_next(next_step)
    made = _check_made(made if isinstance(made, (list, tuple)) else [made])
    task = tasks.get(task_id) if task_id else None
    if task_id and (not task or task.get("deleted")):
        raise KeyError(task_id)
    if not task:
        if not title:
            raise ValueError("タスクの id か作業名が必要です")
        found = resolve(title)
        if found["match"] == "many":
            raise ValueError("同じような名前のタスクがあります。id で指定してください")
        task = found["task"]
    doing = len([t for t in tasks.open_tasks() if t.get("status") == "doing"])
    if task:
        status = task.get("status")
        if status == "notyet" and doing < DOING_MAX:
            status = "doing"
        task = tasks.update(task["id"], status=status, note=_note_with_run(task.get("note"), did, made, next_step))
    else:
        task = tasks.add(title, status="doing" if doing < DOING_MAX else "notyet",
                         note=_note_with_run("", did, made, next_step), source="lifeos-06")
    logs.append(LOG, {"taskId": task["id"], "title": task["title"], "did": did, "made": made, "next": next_step})
    return {"task": {"id": task["id"], "title": task["title"], "status": task["status"]},
            "did": did, "made": made, "next": next_step}


def brief_lines(day):
    if stopped():
        return []
    out = []
    for s in suggestions(day):
        out.append(f"初動を任せますか？ /kickoff {s['title'][:TITLE_SHOW]}（{s['idleDays']}日動きなし）")
    return out
