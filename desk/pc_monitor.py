#!/usr/bin/env python3
"""Tatsuki's Desk — what is running on this Mac and how much memory it holds.

Reads ps, vm_stat, sysctl and lsappinfo when asked and keeps nothing: no file, no log, and never the
command-line arguments, which can hold keys. An app is asked to quit through NSRunningApplication
(desk/app/appctl.swift), so it shows its own save prompt and macOS asks for no permission. A tool gets SIGTERM.
Forcing is accepted only 5 seconds after a close that did not end the thing.
"""
import os
import re
import signal
import subprocess
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
HELPER_SOURCE = os.path.join(ROOT, "app", "appctl.swift")
HELPER = os.path.expanduser("~/Applications/TatsukiAppCtl")
FORCE_AFTER = 5  # seconds after the first close
SMALL = 20 * 1024 * 1024  # tools under this and idle are only counted
ALERT_AFTER = 120  # memory pressure has to last this long
NOTIFY_EVERY = 1800
PROTECTED_BUNDLES = {"com.tatsuki.desk.app", "com.apple.finder", "com.apple.dock"}
CLAUDE_WARN = "Scheduled tasks and open sessions will stop."
SYSTEM_PATHS = ("/System/", "/usr/libexec/", "/usr/sbin/", "/usr/bin/", "/sbin/", "/bin/", "/Library/Apple/", "/private/var/", "/usr/local/libexec/")
LEVELS = {"1": "normal", "2": "warn", "4": "critical"}

LOCK = threading.Lock()
ASKED = {}  # (pid, started) -> when close was first pressed
ALERT = {"since": None, "alert": False, "notified": 0}


def run(cmd, timeout=5):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env={**os.environ, "LC_ALL": "C"}).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def sysctl(name):
    return run(["/usr/sbin/sysctl", "-n", name]).strip()


def memory():
    """Used memory the way Activity Monitor counts it: app memory + wired + compressed."""
    vm = run(["/usr/bin/vm_stat"])
    page = re.search(r"page size of (\d+)", vm)
    pages = {k.strip(): int(v) for k, v in re.findall(r"^([^:\n]+):\s+(\d+)\.", vm, re.M)}
    used = None
    if page and "Anonymous pages" in pages:
        used = (pages["Anonymous pages"] - pages.get("Pages purgeable", 0) + pages.get("Pages wired down", 0)
                + pages.get("Pages occupied by compressor", 0)) * int(page.group(1))
    total, swap = sysctl("hw.memsize"), re.search(r"used = ([\d.]+)([MG])", sysctl("vm.swapusage"))
    return {
        "used": used,
        "total": int(total) if total.isdigit() else None,
        "swap": int(float(swap.group(1)) * (1 << (30 if swap.group(2) == "G" else 20))) if swap else None,
        "pressure": LEVELS.get(sysctl("kern.memorystatus_vm_pressure_level")),
    }


def processes():
    out, rows = run(["/bin/ps", "-axo", "pid=,ppid=,rss=,%cpu=,uid=,lstart=,comm="]), []
    for line in out.splitlines():
        f = line.split(None, 10)
        if len(f) < 11:
            continue
        try:
            started = int(time.mktime(time.strptime(" ".join(f[5:10]), "%a %b %d %H:%M:%S %Y")))
            rows.append({"pid": int(f[0]), "ppid": int(f[1]), "mem": int(f[2]) * 1024, "cpu": float(f[3]), "uid": int(f[4]), "started": started, "path": f[10]})
        except ValueError:
            continue
    return rows


def registered_apps():
    """What macOS knows as apps. type Foreground is what the Dock shows."""
    apps = []
    for block in re.split(r"^\s*\d+\) ", run(["/usr/bin/lsappinfo", "list"]), flags=re.M)[1:]:
        name, pid = re.match(r'"(.*?)"', block), re.search(r"\bpid = (\d+)", block)
        if not name or not pid:
            continue
        kind, bundle, path = (re.search(p, block) for p in (r'\btype="(.*?)"', r'bundleID="(.*?)"', r'bundle path="(.*?)"'))
        apps.append({"name": name.group(1), "pid": int(pid.group(1)), "type": kind.group(1) if kind else "", "bundle": bundle.group(1) if bundle else "", "bundlePath": path.group(1) if path else ""})
    return apps


def bundle_of(path):
    """The outermost .app a program sits in."""
    i = path.find(".app/")
    return path[:i + 4] if i >= 0 else None


def own_line():
    """This server and whatever started it."""
    procs = {p["pid"]: p for p in processes()}
    pids, pid = set(), os.getpid()
    while pid in procs and pid > 1 and pid not in pids:
        pids.add(pid)
        pid = procs[pid]["ppid"]
    return pids


def group(procs, apps, own, me):
    """Sort every process into apps, tools or the system total."""
    bundles = {a["bundlePath"]: a for a in apps if a["bundlePath"]}
    by_pid = {a["pid"]: a for a in apps}
    items, system = {}, {"mem": 0, "cpu": 0.0}
    for p in procs:
        path, reg = p["path"], by_pid.get(p["pid"])
        bundle = bundle_of(path)  # a helper can be registered too, but it belongs to the app around it
        if bundle not in bundles and reg:
            bundle = reg["bundlePath"]
        app = bundles.get(bundle)
        foreground = bool(app and app["type"] == "Foreground")
        if p["uid"] != me or path == "<defunct>" or (not foreground and path.startswith(SYSTEM_PATHS)):
            system["mem"] += p["mem"]
            system["cpu"] += p["cpu"]
            continue
        # only a bundle macOS knows as an app is one thing to close; anything else, like each Python or claude run, stands alone
        it = items.setdefault(bundle if app else p["pid"], {"kind": "app" if foreground else "tool", "name": app["name"].replace("‎", "") if app else os.path.basename(path),
                                                            "pid": None, "started": None, "mem": 0, "cpu": 0.0, "count": 0, "appkit": bool(app), "path": bundle if app else path, "bundle": app["bundle"] if app else "", "members": []})
        it["mem"] += p["mem"]
        it["cpu"] += p["cpu"]
        it["count"] += 1
        it["members"].append(p)
    for it in items.values():
        pids = {m["pid"] for m in it["members"]}
        app = next((a for a in apps if a["pid"] in pids and it["appkit"] and a["bundlePath"] == it["path"]), None)
        main = next((m for m in it["members"] if app and m["pid"] == app["pid"]), None) or min((m for m in it["members"] if m["ppid"] not in pids), key=lambda m: m["pid"], default=it["members"][0])
        it["pid"], it["started"] = main["pid"], main["started"]
        it["appkit"] = bool(app)
        it["protected"] = it["bundle"] in PROTECTED_BUNDLES or "focusblock" in it["path"].lower() or bool(pids & own)
        it["warn"] = CLAUDE_WARN if "claude" in (it["name"] + it["path"]).lower() else ""
        del it["members"], it["bundle"]
    return list(items.values()), system


def listing():
    return group(processes(), registered_apps(), own_line(), os.getuid())


def snapshot(now=None):
    now = time.time() if now is None else now
    items, system = listing()
    live = {(i["pid"], i["started"]) for i in items}
    with LOCK:
        for key in [k for k in ASKED if k not in live]:
            del ASKED[key]
        for i in items:
            asked = ASKED.get((i["pid"], i["started"]))
            i["closing"] = asked is not None
            i["canForce"] = asked is not None and now - asked >= FORCE_AFTER
    items.sort(key=lambda i: -i["mem"])
    tools = [i for i in items if i["kind"] == "tool"]
    shown = [i for i in tools if i["mem"] >= SMALL or i["cpu"] >= 1 or i["closing"]]
    small = [i for i in tools if i not in shown]
    cores = sysctl("hw.ncpu")
    total_cpu = sum(i["cpu"] for i in items) + system["cpu"]
    return {
        **summary(),
        "cpu": round(total_cpu / int(cores), 1) if cores.isdigit() else None,
        "apps": [i for i in items if i["kind"] == "app"],
        "tools": shown,
        "small": {"mem": sum(i["mem"] for i in small), "items": small},
        "system": {"mem": system["mem"], "cpu": round(system["cpu"], 1)},
    }


def summary():
    with LOCK:
        alert = ALERT["alert"]
    return {**memory(), "alert": alert}


def helper():
    """The quit helper. setup-mac.sh builds it; this covers a Mac that has not run setup since."""
    if os.path.exists(HELPER) and os.path.getmtime(HELPER) >= os.path.getmtime(HELPER_SOURCE):
        return HELPER
    try:
        os.makedirs(os.path.dirname(HELPER), exist_ok=True)
        subprocess.run(["swiftc", "-O", "-framework", "Cocoa", HELPER_SOURCE, "-o", HELPER], check=True, capture_output=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        return None
    return HELPER


def stop(item, force):
    """True when the request reached it. An app that answers no still counts: forcing comes next."""
    if not item["appkit"]:
        os.kill(item["pid"], signal.SIGKILL if force else signal.SIGTERM)
        return True
    tool = helper()
    if not tool:
        return False
    return subprocess.run([tool, "force" if force else "quit", str(item["pid"])], capture_output=True, timeout=10).returncode != 2


def close(pid, started, force=False, now=None):
    """Returns (status, body). The start time stops a reused pid from closing the wrong thing."""
    now = time.time() if now is None else now
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1 or not isinstance(started, int) or isinstance(started, bool):
        return 400, {"error": "bad request"}
    items, _ = listing()
    same_pid = [i for i in items if i["pid"] == pid]
    if not same_pid:
        return 404, {"error": "not found"}
    item = same_pid[0]
    if item["started"] != started:
        return 409, {"error": "changed"}
    if item["protected"]:
        return 403, {"error": "protected"}
    key = (pid, started)
    with LOCK:
        asked = ASKED.get(key)
        if force and (asked is None or now - asked < FORCE_AFTER):
            return 409, {"error": "too early"}
        ASKED.setdefault(key, now)
    try:
        ok = stop(item, bool(force))
    except ProcessLookupError:
        return 404, {"error": "not found"}
    except (OSError, subprocess.SubprocessError):
        ok = False
    return (200, {"ok": True}) if ok else (503, {"error": "could not close"})


def tick(level, now, notify):
    """One look at memory pressure. It has to last ALERT_AFTER, and Mac notifications come NOTIFY_EVERY at most."""
    with LOCK:
        if level not in ("warn", "critical"):
            ALERT.update(since=None, alert=False)
            return
        if ALERT["since"] is None:
            ALERT["since"] = now
        if now - ALERT["since"] < ALERT_AFTER:
            return
        ALERT["alert"] = True
        due = now - ALERT["notified"] >= NOTIFY_EVERY
        if due:
            ALERT["notified"] = now
    if due:
        notify()


def notify_mac():
    run(["/usr/bin/osascript", "-e", 'display notification "Memory is running low. See the PC screen in Desk for details." with title "Tatsuki\'s Desk"'])


def watch():
    while True:
        tick(memory()["pressure"], time.time(), notify_mac)
        time.sleep(60)
