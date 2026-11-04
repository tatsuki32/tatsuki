"""This tool's files in <data>/tools/13-brain/: locked, atomic JSON, the scope and the off switch."""
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager

from lifeos.lib import paths, tools

SLUG = "13-brain"


class StoreError(Exception):
    """A file exists but cannot be read. Nothing was written."""


class Stopped(Exception):
    """The tool is stopped, so nothing is ingested, reconciled, approved or run."""


def path(*parts):
    return paths.data_path("tools", SLUG, *parts)


@contextmanager
def locked(name):
    with open(path(name + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def read_json(name, default):
    try:
        with open(path(name), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except ValueError as e:
        # never fall back to the default here: the next write would erase the file
        raise StoreError(name + " を読めません") from e


def write_json(name, value):
    p = path(name)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".brain", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def scope_orgs():
    """Which of the owner's companies are in scope. Empty means not decided yet: nothing is excluded."""
    return read_json("settings.json", {}).get("orgs") or []


def set_scope(orgs):
    with locked("settings"):
        s = read_json("settings.json", {})
        s["orgs"] = [o.strip() for o in orgs if o.strip()]
        write_json("settings.json", s)


def in_scope(org):
    orgs = scope_orgs()
    return not orgs or not org or org in orgs


def is_stopped():
    if os.path.exists(path("stopped")):
        return True
    return tools.info(os.path.join(tools.root_dir(), SLUG)).get("status") == "stopped"


def stop():
    with open(path("stopped"), "w"):
        pass


def start():
    try:
        os.remove(path("stopped"))
    except FileNotFoundError:
        pass


def require_running():
    if is_stopped():
        raise Stopped("第二の脳は止めてあります。再開は brain.py start です。")
