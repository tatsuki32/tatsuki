"""Where Life OS data lives, and the clock (Ho Chi Minh time, same as desk/kickoff.py).

Personal data never goes into git. It lives in LIFEOS_DATA (default: the main checkout's
desk/data/lifeos, also when the code runs from a git worktree).

Setting LIFEOS_DATA to any other folder makes a sandbox: Desk tasks then live inside that
folder too, the Desk server is not called, and Telegram is not contacted. Tests rely on this.
"""
import os
import time
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DATA = os.path.expanduser("~/TatsukiDesk/desk/data/lifeos")
DEFAULT_API = "http://localhost:4747"
HCMC = timezone(timedelta(hours=7))


def is_sandbox():
    v = os.environ.get("LIFEOS_DATA")
    return bool(v) and os.path.abspath(os.path.expanduser(v)) != DEFAULT_DATA


def data_dir():
    return os.path.abspath(os.path.expanduser(os.environ.get("LIFEOS_DATA") or DEFAULT_DATA))


def data_path(*parts, mkdir=True):
    """Absolute path inside the data folder. Refuses to leave it; creates parent folders."""
    root = data_dir()
    p = os.path.abspath(os.path.join(root, *parts))
    if p != root and not p.startswith(root + os.sep):
        raise ValueError("path is outside the Life OS data folder")
    if mkdir:
        os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def desk_data_dir():
    """The folder holding Desk's tasks.json."""
    v = os.environ.get("LIFEOS_DESK_DATA")
    if v:
        return os.path.abspath(os.path.expanduser(v))
    if is_sandbox():
        return os.path.join(data_dir(), "_desk")
    return os.path.dirname(DEFAULT_DATA)


def desk_api():
    """Base URL of the Desk server, or None when it must not be called (sandbox)."""
    v = os.environ.get("LIFEOS_DESK_API")
    if v:
        return v.rstrip("/")
    return None if is_sandbox() else DEFAULT_API


def now():
    return datetime.now(HCMC)


def today():
    return now().strftime("%Y-%m-%d")


def now_ms():
    return int(time.time() * 1000)
