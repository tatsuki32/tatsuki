"""Desk tasks: the only task list in Life OS (there is no tasks/MASTER_PLAN.md).

Records have the same shape as taskRecord in desk/index.html, plus updatedAt so the hourly
sync carries them online. If the Desk server is running, reads and writes go through its API;
otherwise tasks.json is read and written directly, under a lock and atomically.
Nothing is hard-deleted: delete() sets deleted:true.
"""
import fcntl
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager

from lifeos.lib import paths

STATUSES = ("notyet", "doing", "done", "paused", "archived")
OPEN = ("notyet", "doing")
# desk/index.html drops every other field the next time the task is edited there
FIELDS = ("title", "status", "source", "sourceRef", "due", "note", "createdAt", "deleted")
TITLE_MAX = 120
# no proxy: this only ever talks to the local Desk server
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class TaskStoreError(Exception):
    """tasks.json exists but cannot be read. Nothing was written."""


def _call(method, path, body=None, timeout=3):
    base = paths.desk_api()
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _server_up():
    if not paths.desk_api():
        return False
    try:
        return bool(_call("GET", "/health", timeout=0.5).get("ok"))
    except (OSError, ValueError):
        return False


def _file():
    return os.path.join(paths.desk_data_dir(), "tasks.json")


@contextmanager
def _locked():
    os.makedirs(paths.desk_data_dir(), exist_ok=True)
    with open(_file() + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _read_file():
    try:
        with open(_file(), encoding="utf-8") as f:
            v = json.load(f)
    except FileNotFoundError:
        return []
    except ValueError as e:
        raise TaskStoreError("tasks.json を読めません") from e
    if not isinstance(v, list):
        raise TaskStoreError("tasks.json の形が違います")
    return [x for x in v if isinstance(x, dict) and x.get("id")]


def _write_file(rec):
    with _locked():
        items = _read_file()
        for i, x in enumerate(items):
            if x["id"] == rec["id"]:
                items[i] = rec
                break
        else:
            items.append(rec)
        fd, tmp = tempfile.mkstemp(dir=paths.desk_data_dir(), prefix=".tasks", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _file())


def _put(rec):
    if _server_up():
        try:
            body = {k: v for k, v in rec.items() if k != "id"}
            _call("PUT", "/api/tasks/" + urllib.parse.quote(rec["id"], safe=""), body)
            return
        except (OSError, ValueError):
            pass  # the server went away: fall through to the file
    _write_file(rec)


def _load():
    if _server_up():
        try:
            return _call("GET", "/api/tasks")
        except (OSError, ValueError):
            pass
    return _read_file()


def _clean_title(title):
    title = " ".join(str(title or "").split())[:TITLE_MAX]
    if not title:
        raise ValueError("タイトルが空です")
    return title


def _check_status(status):
    if status not in STATUSES:
        raise ValueError("status は " + " / ".join(STATUSES) + " のどれか")


def all_tasks(include_deleted=False):
    return [t for t in _load() if include_deleted or not t.get("deleted")]


def open_tasks():
    return [t for t in all_tasks() if t.get("status") in OPEN]


def get(task_id):
    return next((t for t in all_tasks(include_deleted=True) if t["id"] == task_id), None)


def add(title, status="notyet", due=None, note="", source="lifeos", source_ref=""):
    """Adds a task and returns it. due is epoch milliseconds or None (same as the Desk)."""
    _check_status(status)
    ms = paths.now_ms()
    rec = {"id": "t" + format(ms, "x") + os.urandom(2).hex(), "title": _clean_title(title), "status": status,
           "source": source, "sourceRef": source_ref, "due": due, "note": note,
           "createdAt": ms, "deleted": False, "updatedAt": ms}
    _put(rec)
    return rec


def update(task_id, **fields):
    """Changes the given fields and returns the task. Unknown fields are refused."""
    bad = [k for k in fields if k not in FIELDS]
    if bad:
        raise ValueError("Desk が保持しない項目です: " + ", ".join(bad) + "（note に書いてください）")
    if "status" in fields:
        _check_status(fields["status"])
    if "title" in fields:
        fields["title"] = _clean_title(fields["title"])
    cur = get(task_id)
    if not cur:
        raise KeyError(task_id)
    rec = {**cur, **fields, "id": task_id, "updatedAt": paths.now_ms()}
    _put(rec)
    return rec


def done(task_id):
    return update(task_id, status="done")


def delete(task_id):
    return update(task_id, deleted=True)
