#!/usr/bin/env python3
"""Tatsuki's Desk — Startup Clean: after the Mac starts, only Claude, the Desk and Terminal stay open.

launchd (com.tatsuki.desk.startclean) runs `run` once at each login. It acts only on the first login after the
Mac starts (within RUN_WINDOW of boot, once per boot), never on waking from sleep. Every other app in the Dock gets
the same quit as Cmd-Q through ~/Applications/TatsukiAppCtl, so an app with unsaved work shows its own prompt and
stays. Nothing is forced. Apps that only live in the menu bar (FocusBlock, the Desk helpers) are not touched.

The names of the apps it closed go to desk/data/startclean/last.json, and Desk Home shows one line to reopen them.
A browser reopened this way brings its tabs back when its own "continue where you left off" setting is on.
Window titles and tabs are never read. Nothing leaves the Mac. macOS settings are not changed.

  python3 desk/startup_clean.py run          what launchd starts
  python3 desk/startup_clean.py mark         remember this boot as done (setup-mac.sh, so installing closes nothing)
  python3 desk/startup_clean.py plan         what it would close now; closes nothing
"""
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("STARTCLEAN_DIR") or os.path.join(ROOT, "data", "startclean")
HELPER = os.path.expanduser("~/Applications/TatsukiAppCtl")
HELPER_SOURCE = os.path.join(ROOT, "app", "appctl.swift")
CLAUDE = "com.anthropic.claudefordesktop"
DESK_APP = "com.tatsuki.desk.app"
KEEP = [CLAUDE, DESK_APP, "com.apple.Terminal", "com.apple.finder"]
NEVER = ("com.tatsuki.", "com.apple.dock", "com.apple.loginwindow", "com.apple.systemuiserver")  # always left alone
RUN_WINDOW = 3600  # seconds after boot; a later login (or reloading the job) closes nothing
FIRST_LOOK = 40  # seconds after login: macOS has reopened most apps by now
SECOND_LOOK = 90  # one more look for slow apps
LATE = 75  # an app launched later than this after login was opened by the person
WAIT_QUIT = 12  # seconds an app gets to quit before it counts as staying (it may be showing a save prompt)
SHOW_FOR = 3 * 86400  # the Home line goes away on its own after this


def path(name):
    return os.path.join(DIR, name)


def read(name, default):
    try:
        with open(path(name), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return default


def write(name, value):
    os.makedirs(DIR, exist_ok=True)
    tmp = path(name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path(name))


def settings():
    s = read("settings.json", {})
    keep = s.get("keep") if isinstance(s.get("keep"), list) else KEEP
    return {"enabled": s.get("enabled") is not False, "keep": [k for k in keep if isinstance(k, str)]}


def boot_time():
    out = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=5).stdout
    m = re.search(r"sec = (\d+)", out)
    return int(m.group(1)) if m else 0


def helper():
    """setup-mac.sh builds it; this covers a Mac that has not run setup since appctl.swift changed."""
    if os.path.exists(HELPER) and os.path.getmtime(HELPER) >= os.path.getmtime(HELPER_SOURCE):
        return HELPER
    try:
        os.makedirs(os.path.dirname(HELPER), exist_ok=True)
        subprocess.run(["swiftc", "-O", "-framework", "Cocoa", HELPER_SOURCE, "-o", HELPER], check=True, capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return None
    return HELPER


def parse(text):
    apps = []
    for line in text.splitlines():
        f = line.split("\t")
        if len(f) < 6 or not f[0].isdigit():
            continue
        apps.append({"pid": int(f[0]), "bundle": f[1], "path": f[2], "name": f[3] or os.path.basename(f[2]),
                     "launched": int(f[4]) if f[4].isdigit() else 0, "active": f[5] == "1"})
    return apps


def running():
    tool = helper()
    if not tool:
        return None
    try:
        return parse(subprocess.run([tool, "list"], capture_output=True, text=True, timeout=10).stdout)
    except (OSError, subprocess.SubprocessError):
        return None


def pick(apps, keep, login, until=None):
    """The apps to close. `until`: only apps launched before then (the person opens things after that)."""
    out = []
    for a in apps:
        b = a["bundle"]
        if b in keep or any(b.startswith(n) for n in NEVER) or a["pid"] == os.getpid():
            continue
        if not b and not a["path"].endswith(".app"):
            continue
        late = a["launched"] and a["launched"] > login + 20
        if late and a["active"]:
            continue  # opened and in use right now
        if until is not None and a["launched"] and a["launched"] > until:
            continue
        out.append(a)
    return out


def quit_all(apps):
    """Asks each to quit, then returns the ones that are gone."""
    tool = helper()
    if not tool or not apps:
        return []
    for a in apps:
        try:
            subprocess.run([tool, "quit", str(a["pid"])], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
    end = time.time() + WAIT_QUIT
    while time.time() < end:
        now = running() or []
        pids = {x["pid"] for x in now}
        if not any(a["pid"] in pids for a in apps):
            break
        time.sleep(1)
    pids = {x["pid"] for x in (running() or [])}
    return [a for a in apps if a["pid"] not in pids]


def open_app(bundle, app_path=""):
    cmd = ["/usr/bin/open", "-g", "-b", bundle] if bundle else ["/usr/bin/open", "-g", app_path]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def notify(n):
    body = f"Closed {n} app{'s' if n != 1 else ''} at startup. Reopen them from Desk Home."
    script = ["-e", "on run argv", "-e", "display notification (item 1 of argv) with title \"Desk\"", "-e", "end run"]
    try:
        subprocess.run(["/usr/bin/osascript", *script, body], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def due(now, boot, state, cfg):
    return cfg["enabled"] and boot > 0 and 0 <= now - boot <= RUN_WINDOW and state.get("boot") != boot


def mark():
    state = read("state.json", {})
    state["boot"] = boot_time()
    write("state.json", state)


def run():
    login = int(time.time())
    cfg, state, boot = settings(), read("state.json", {}), boot_time()
    if not due(login, boot, state, cfg):
        return
    state["boot"] = boot
    write("state.json", state)  # once per boot, even if something below fails
    closed = []
    for wait, until in ((FIRST_LOOK, None), (SECOND_LOOK - FIRST_LOOK, login + LATE)):
        time.sleep(wait)
        apps = running()
        if apps is None:
            break
        seen = {a["pid"] for a in closed}
        closed += quit_all([a for a in pick(apps, cfg["keep"], login, until) if a["pid"] not in seen])
    now = running() or []
    have = {a["bundle"] for a in now}
    for bundle in (CLAUDE, DESK_APP):
        if bundle not in have:
            open_app(bundle)
    apps = []
    for a in closed:  # one row per app, even if two copies were open
        if not any(x["bundle"] == a["bundle"] and x["path"] == a["path"] for x in apps):
            apps.append({"name": a["name"], "bundle": a["bundle"], "path": a["path"]})
    write("last.json", {"at": int(time.time()), "apps": apps, "reopened": [], "dismissed": False})
    if apps:
        notify(len(apps))


# ---------- for the Desk server ----------

def view(now=None):
    now = time.time() if now is None else now
    last = read("last.json", {})
    apps = [a for a in last.get("apps", []) if isinstance(a, dict)]
    back = set(last.get("reopened") or [])
    left = [a for a in apps if (a.get("bundle") or a.get("path")) not in back]
    show = bool(left) and not last.get("dismissed") and now - (last.get("at") or 0) < SHOW_FOR
    return {"at": last.get("at"), "apps": [{**a, "reopened": (a.get("bundle") or a.get("path")) in back} for a in apps],
            "show": show, "enabled": settings()["enabled"]}


def reopen(keys=None):
    """keys: bundle ids (or paths) to open; empty means all that are not open yet."""
    last = read("last.json", {})
    apps = [a for a in last.get("apps", []) if isinstance(a, dict)]
    back = list(last.get("reopened") or [])
    want = set(keys) if keys else None
    opened, failed = [], []
    for a in apps:
        key = a.get("bundle") or a.get("path")
        if not key or key in back or (want is not None and key not in want):
            continue
        if open_app(a.get("bundle") or "", a.get("path") or ""):
            back.append(key)
            opened.append(a.get("name"))
        else:
            failed.append(a.get("name"))
    if apps:
        last["reopened"] = back
        write("last.json", last)
    return {"opened": opened, "failed": failed, **view()}


def dismiss():
    last = read("last.json", {})
    if last:
        last["dismissed"] = True
        write("last.json", last)
    return view()


def plan():
    apps = running()
    if apps is None:
        return {"error": "TatsukiAppCtl is missing and could not be built"}
    cfg = settings()
    return {"would_close": [a["name"] for a in pick(apps, cfg["keep"], int(time.time()))], "keep": cfg["keep"],
            "enabled": cfg["enabled"], "boot": boot_time()}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "plan"
    if cmd == "run":
        run()
    elif cmd == "mark":
        mark()
    elif cmd == "plan":
        print(json.dumps(plan(), ensure_ascii=False, indent=1))
    else:
        sys.exit(__doc__)
