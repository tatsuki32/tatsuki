#!/usr/bin/env python3
"""Tatsuki's Desk — bank details to give to people who send money. Keeps and copies them; moves no money.

Everything stays in desk/data/bank/accounts.json on this Mac, readable only by this user, and never leaves it:
backup_data.py ignores the folder, and the folder carries its own .gitignore in case that list is ever out of date.
The page gets the last four characters of a number; the whole number goes out only when a button asks for it, and a
copy goes straight to the Mac's clipboard without passing through the page. Nothing here logs a value.
Removing an account erases its contents and leaves only its id. The only way back is a backup such as Time Machine.
"""
import json
import os
import re
import subprocess
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("DESK_BANK_DIR") or os.path.join(ROOT, "data", "bank")
PBOARD = os.environ.get("DESK_BANK_PBOARD")  # a test copies to another pasteboard, so the real clipboard stays as it was
FILE = "accounts.json"
# field, Japanese label, English label; in the order they are given to someone
GIVEN = (
    ("holder", "受取人", "Account holder"),
    ("bank", "銀行", "Bank"),
    ("branch", "支店", "Branch"),
    ("kind", "種別", "Account type"),
    ("number", "口座番号", "Account number"),
    ("iban", "IBAN", "IBAN"),
    ("swift", "SWIFT", "SWIFT/BIC"),
    ("routing", "ルーティング番号", "Routing number"),
    ("address", "住所", "Address"),
    ("currency", "通貨", "Currency"),
)
FIELDS = ("label", "memo") + tuple(g[0] for g in GIVEN)
HIDDEN = ("number", "iban")
MAX_LEN = 1000
ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")

_lock = threading.Lock()


class BankError(Exception):
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
        raise BankError("read") from None
    if not isinstance(data, dict):
        raise BankError("read")
    return data


def _write(data):
    try:
        _ensure_dir()
        fd, tmp = tempfile.mkstemp(dir=DIR, prefix=".", suffix=".tmp")  # mkstemp makes it 0600
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
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
        raise BankError("write") from None


def mask(value):
    """The last four characters, when there are more than four. Spaces in the number do not count."""
    v = re.sub(r"\s", "", value or "")
    if not v:
        return ""
    return "••••" if len(v) <= 4 else "•••• " + v[-4:]


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
        if v:
            out[k] = v
    if not out.get("label"):
        raise ValueError("label required")
    return out


def _live(data):
    rows = [dict(r, id=i) for i, r in data.items() if isinstance(r, dict) and not r.get("deleted")]
    return sorted(rows, key=lambda r: (r.get("createdAt") or 0, r["id"]))


def view():
    """What the page holds: hidden fields only as their last four characters."""
    with _lock:
        rows = _live(_read())
    for r in rows:
        for k in HIDDEN:
            if r.get(k):
                r[k] = mask(r[k])
    return {"accounts": rows}


def _get(acct_id):
    r = _read().get(acct_id)
    return r if isinstance(r, dict) and not r.get("deleted") else None


def put(acct_id, rec):
    """Adds or replaces one account. A ValueError holds no value."""
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
    """Erases the contents. Only the id remains, marked deleted. Returns False when there is no such account."""
    with _lock:
        data = _read()
        if not isinstance(data.get(acct_id), dict) or data[acct_id].get("deleted"):
            return False
        data[acct_id] = {"deleted": True, "updatedAt": int(time.time() * 1000)}
        _write(data)
    return True


def reveal(acct_id):
    """One account in full, for 表示 and 直す. None when there is no such account."""
    with _lock:
        r = _get(acct_id)
    return dict(r, id=acct_id) if r else None


def text_of(rec, what):
    """The words a button copies. 'all' and 'en' leave out the name and the memo, which are for this user alone."""
    if what in ("all", "en"):
        col = 1 if what == "all" else 2
        sep = "：" if what == "all" else ": "
        return "\n".join(f"{g[col]}{sep}{rec[g[0]]}" for g in GIVEN if rec.get(g[0]))
    return rec.get(what) if what in FIELDS else None


def _pbcopy(text):
    # launchd starts the server without a language setting, and pbcopy then garbles Japanese
    env = dict(os.environ, LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8", __CF_USER_TEXT_ENCODING=f"{os.getuid():#x}:0x8000100:0x8000100")
    args = ["/usr/bin/pbcopy"] + (["-pboard", PBOARD] if PBOARD else [])
    # no capture of stderr into any log, and the text goes through stdin so it is not on a command line
    subprocess.run(args, input=text.encode("utf-8"), env=env, check=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def copy(acct_id, what):
    """Puts a field, or the whole account, on the clipboard. Returns False when there is nothing to copy."""
    with _lock:
        r = _get(acct_id)
    text = text_of(r, what) if r else None
    if not text:
        return False
    try:
        _pbcopy(text)
    except (OSError, subprocess.SubprocessError):
        raise BankError("copy") from None
    return True
