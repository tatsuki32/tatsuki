#!/usr/bin/env python3
"""Tatsuki's Desk — keeps desk/data/ in its own private repository (launchd, every 15 minutes).

desk/data/ is a separate git repository, ignored by the public code repository. A commit and push happen only
when something changed, and only after the scan below passes. The log holds file names and counts, never contents.

  python3 backup_data.py          one pass
  python3 backup_data.py --init URL   makes desk/data a repository with that private remote (once)
"""
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
IGNORE = "*.lock\n.*.tmp\n.published\n.DS_Store\n__pycache__/\nwise/\nwhatsapp/\npresents.json\n"  # wise/, whatsapp/, presents.json: statements and messages stay on this Mac, even out of the private copy
BRANCH = "main"
# things that must not leave this Mac even privately: keys and passwords in the clear
SECRETS = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|gho_[A-Za-z0-9]{30,}"
    r"|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|xox[abps]-[A-Za-z0-9-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\b(?:password|passwd|api[_-]?key|secret|token)\\?\"?\s*[:=]\s*\\?\"?[A-Za-z0-9_\-+/=!@#$%^&*]{8,}",
    re.IGNORECASE,
)


def log(msg):
    print(time.strftime("%F %T ") + msg, flush=True)


def git(*args, check=True):
    return subprocess.run(["git", "-C", DATA, *args], check=check, capture_output=True, text=True, timeout=120)


def is_repo():
    return os.path.isdir(os.path.join(DATA, ".git"))


def changed_files():
    """Paths git would add, relative to data/."""
    out = git("status", "--porcelain", "--untracked-files=all", "-z").stdout
    parts = out.split("\0")
    files, i = [], 0
    while i < len(parts) and parts[i]:
        entry = parts[i]
        status, path = entry[:2], entry[3:]
        if "R" in status or "C" in status:
            i += 1  # the old name follows; only the new one holds content
        if "D" not in status:
            files.append(path)
        i += 1
    return files


def leaks(files):
    """The files holding something that looks like a secret. Contents are never printed."""
    hits = []
    for path in files:
        if path.startswith("uploads/"):  # images and PDFs: binary, so a chance match would stop every backup
            continue
        try:
            with open(os.path.join(DATA, path), encoding="utf-8", errors="ignore") as f:
                if SECRETS.search(f.read()):
                    hits.append(path)
        except OSError:
            continue
    return hits


def ensure_ignore():
    """Adds whatever IGNORE lines the data folder's .gitignore lacks, and keeps the lines already there."""
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, ".gitignore")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        text = ""
    missing = [line for line in IGNORE.splitlines() if line not in text.splitlines()]
    if missing:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + ("" if not text or text.endswith("\n") else "\n") + "\n".join(missing) + "\n")


def init(url):
    os.makedirs(DATA, exist_ok=True)
    if not is_repo():
        git("init", "-q", "-b", BRANCH)
    ensure_ignore()
    if git("remote", check=False).stdout.strip():
        git("remote", "set-url", "origin", url)
    else:
        git("remote", "add", "origin", url)
    log("ready")


def backup():
    if not is_repo():
        return log("not set up: run backup_data.py --init <private repository URL>")
    ensure_ignore()
    files = changed_files()
    if files:
        hits = leaks(files)
        if hits:
            return log("stopped, looks like a secret, nothing sent: " + ", ".join(hits))
        git("add", "-A")
        git("-c", "user.name=Tatsuki's Desk", "-c", "user.email=desk@localhost", "commit", "-q", "-m", time.strftime("backup %F %T"))
        log(f"saved {len(files)} files")
    ahead = git("rev-list", "--count", f"origin/{BRANCH}..HEAD", check=False)
    if ahead.returncode == 0 and ahead.stdout.strip() == "0":
        return
    try:
        git("push", "-q", "-u", "origin", BRANCH)
        log("sent")
    except subprocess.CalledProcessError:
        log("not sent, will try again: no network or no GitHub sign-in")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--init"] and len(sys.argv) == 3:
        init(sys.argv[2])
    else:
        backup()
