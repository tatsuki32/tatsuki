"""Markdown files in the data folder. Paths are relative, e.g. "briefs/2026-11-04.md"."""
import fcntl
import os
import tempfile

from lifeos.lib import paths


def _path(rel):
    if not rel.endswith(".md"):
        raise ValueError("Markdown files only (.md)")
    return paths.data_path(rel)


def save(rel, text):
    """Writes the whole file atomically and returns its absolute path."""
    p = _path(rel)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".md", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    os.replace(tmp, p)
    return p


def append(rel, text):
    p = _path(rel)
    with open(p, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(text if text.endswith("\n") else text + "\n")
    return p


def read(rel):
    """The file's text, or None if it does not exist."""
    try:
        with open(_path(rel), encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None
