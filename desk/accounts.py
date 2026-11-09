#!/usr/bin/env python3
"""Tatsuki's Desk — a list of the sites you have accounts on: what each is for and how you sign in. No passwords.

This first version keeps no password and refuses anything that looks like one or like a key, so a slip of the
keyboard cannot leave a secret in the file. A locked password field is the next phase.
Everything stays in desk/data/accounts/accounts.json on this Mac, readable only by this user: backup_data.py
ignores the folder, and the folder carries its own .gitignore in case that list is ever out of date.
Nothing here logs a value. Removing an entry erases its contents and leaves only its id.
"""
import json
import os
import re
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("DESK_ACCOUNTS_DIR") or os.path.join(ROOT, "data", "accounts")
FILE = "accounts.json"
FIELDS = ("label", "url", "purpose", "login", "account", "memo")
MAX_LEN = 1000
ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")
# the same patterns backup_data.py stops on, plus a field written as "password: ..."
SECRET = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|gho_[A-Za-z0-9]{30,}"
    r"|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|xox[abps]-[A-Za-z0-9-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\b(?:password|passwd|pass|pw|api[_-]?key|secret|token|パスワード|暗証番号)\s*[:=：]\s*\S{4,}",
    re.IGNORECASE,
)

_lock = threading.Lock()


class AccountsError(Exception):
    """The file could not be read or written. The message holds no value."""


def _ensure_dir():
    os.makedirs(DIR, mode=0o700, exist_ok=True)
    os.chmod(DIR, 0o700)
    guard = os.path.join(DIR, ".gitignore")
    if not os.path.exists(guard):
        with open(guard, "w", encoding="utf-8") as f:
            f.write("*\n")


def _read():
    """A file that cannot be read is an error and never an empty list, so that a save cannot overwrite it."""
    try:
        with open(os.path.join(DIR, FILE), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise AccountsError("read") from None
    if not isinstance(data, dict):
        raise AccountsError("read")
    return data


def _write(data):
    try:
        _ensure_dir()
        fd, tmp = tempfile.mkstemp(dir=DIR, prefix=".", suffix=".tmp")  # mkstemp makes it 0600
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, os.path.join(DIR, FILE))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        raise AccountsError("write") from None


def _clean(rec):
    out = {}
    for k in FIELDS:
        v = rec.get(k)
        if v is None or v == "":
            continue
        if not isinstance(v, str):
            raise ValueError("text only")
        v = re.sub(r"[\x00-\x09\x0b-\x1f\x7f]", " ", v).strip()
        if len(v) > MAX_LEN:
            raise ValueError("too long")
        if SECRET.search(v):
            raise ValueError("looks like a password or key")
        if k == "url" and v and not re.match(r"^https?://", v):
            v = "https://" + v
        if v:
            out[k] = v
    if not out.get("label"):
        raise ValueError("label required")
    return out


def view():
    with _lock:
        data = _read()
    rows = [dict(r, id=i) for i, r in data.items() if isinstance(r, dict) and not r.get("deleted")]
    return {"accounts": sorted(rows, key=lambda r: (r.get("label", "").lower(), r["id"]))}


def put(acct_id, rec):
    """Adds or replaces one entry. A ValueError holds no value."""
    if not ID_RE.match(acct_id or ""):
        raise ValueError("bad id")
    clean = _clean(rec)
    now = int(time.time() * 1000)
    with _lock:
        data = _read()
        old = data.get(acct_id)
        clean["createdAt"] = old.get("createdAt", now) if isinstance(old, dict) else now
        clean["updatedAt"] = now
        data[acct_id] = clean
        _write(data)
    return view()


def remove(acct_id):
    """Erases the contents. Only the id remains, marked deleted. Returns False when there is no such entry."""
    with _lock:
        data = _read()
        if not isinstance(data.get(acct_id), dict) or data[acct_id].get("deleted"):
            return False
        data[acct_id] = {"deleted": True, "updatedAt": int(time.time() * 1000)}
        _write(data)
    return True
