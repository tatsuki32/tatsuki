"""Append-only logs: one JSON object per line in <data>/logs/<name>.jsonl."""
import fcntl
import json
import re

from lifeos.lib import paths

_NAME = re.compile(r"^[\w-]+$")


def _path(name):
    if not _NAME.match(name):
        raise ValueError("log name: letters, digits, _ and - only")
    return paths.data_path("logs", name + ".jsonl")


def append(name, record):
    """Adds one record (a dict) and returns it with its time in "at"."""
    rec = {"at": paths.now().isoformat(timespec="seconds"), **record}
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with open(_path(name), "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(line)
    return rec


def read(name, limit=None):
    """Records oldest first; with limit, only the newest ones. Damaged lines are skipped."""
    out = []
    try:
        with open(_path(name), encoding="utf-8") as f:
            for line in f:
                try:
                    v = json.loads(line)
                except ValueError:
                    continue
                if isinstance(v, dict):
                    out.append(v)
    except FileNotFoundError:
        return []
    return out[-limit:] if limit else out
