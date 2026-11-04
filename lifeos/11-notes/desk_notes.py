"""Desk's notes collection, read and written the way lifeos/lib/tasks.py does it for tasks:
through the Desk server when it is running, otherwise notes.json directly, under a lock and atomically.
Nothing is hard-deleted.
"""
import fcntl
import json
import os
import tempfile
import urllib.parse

from lifeos.lib import paths
from lifeos.lib.tasks import _call, _server_up


class NoteStoreError(Exception):
    """notes.json exists but cannot be read. Nothing was written."""


def _file():
    return os.path.join(paths.desk_data_dir(), "notes.json")


def _read_file():
    try:
        with open(_file(), encoding="utf-8") as f:
            v = json.load(f)
    except FileNotFoundError:
        return []
    except ValueError as e:
        raise NoteStoreError("notes.json を読めません") from e
    if not isinstance(v, list):
        raise NoteStoreError("notes.json の形が違います")
    return [x for x in v if isinstance(x, dict) and x.get("id")]


def load():
    """Every note, the deleted ones too."""
    if _server_up():
        try:
            return _call("GET", "/api/notes")
        except (OSError, ValueError):
            pass
    return _read_file()


def _write_file(records):
    os.makedirs(paths.desk_data_dir(), exist_ok=True)
    with open(_file() + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        items = {x["id"]: x for x in _read_file()}
        items.update({r["id"]: r for r in records})
        fd, tmp = tempfile.mkstemp(dir=paths.desk_data_dir(), prefix=".notes", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(list(items.values()), f, ensure_ascii=False, indent=1)
        os.replace(tmp, _file())


def put_many(records):
    """Saves whole records, each with an id. Fields are the ones putNote in desk/index.html keeps."""
    rest = list(records)
    if rest and _server_up():
        try:
            while rest:
                body = {k: v for k, v in rest[0].items() if k != "id"}
                _call("PUT", "/api/notes/" + urllib.parse.quote(rest[0]["id"], safe=""), body)
                rest.pop(0)
        except (OSError, ValueError):
            pass  # the server went away: the rest go to the file
    if rest:
        _write_file(rest)
