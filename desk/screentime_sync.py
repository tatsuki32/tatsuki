#!/usr/bin/env python3
"""Tatsuki's Desk — Screen Time on this Mac (launchd, every hour). Reads only.

macOS keeps about four weeks in knowledgeC.db, so each pass adds the days up into desk/data/screentime/days.json
and older days stay there. Only totals per app and per site are kept, never a page address. backup_data.py never
sends this folder anywhere. Reading the database needs Full Disk Access for ~/Applications/TatsukiScreenTime, which starts this file.

  python3 screentime_sync.py          one pass
  python3 screentime_sync.py status   the last pass
"""
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("SCREENTIME_DIR") or os.path.join(ROOT, "data", "screentime")
DB = os.environ.get("SCREENTIME_DB") or os.path.expanduser("~/Library/Application Support/Knowledge/knowledgeC.db")
HELPER = os.path.expanduser("~/Applications/TatsukiScreenTime")
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
EPOCH = 978307200  # knowledgeC counts seconds from 2001-01-01
PERIODS = {"today": 1, "week": 7, "month": 30}
DEVICES = ("mac", "other")


def read_json(name, default):
    try:
        with open(os.path.join(DIR, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(name, value):
    os.makedirs(DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DIR, prefix=".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(DIR, name))


def pieces(start, end):
    """One session cut at each hour: (date, hour, seconds)."""
    t = start
    while t < end:
        at = dt.datetime.fromtimestamp(t, TZ)
        stop = min(end, t + 3600 - (at.minute * 60 + at.second + at.microsecond / 1e6))
        yield at.strftime("%Y-%m-%d"), at.hour, stop - t
        t = stop


def blank():
    return {"apps": {}, "sites": {}, "hours": [0] * 24}


def read_days(path=None):
    """Every day the database still holds: {date: {device: {apps, sites, hours}}}, in seconds."""
    con = sqlite3.connect(f"file:{path or DB}?mode=ro", uri=True)
    try:
        meta = [r[1] for r in con.execute("PRAGMA table_info(ZSTRUCTUREDMETADATA)")]
        source = [r[1] for r in con.execute("PRAGMA table_info(ZSOURCE)")]
        domain = next((c for c in meta if c.upper().endswith("__WEBDOMAIN")), None)
        device = "s.ZDEVICEID" if "ZDEVICEID" in source else "NULL"
        rows = con.execute(
            f"""SELECT o.ZSTREAMNAME, o.ZVALUESTRING, o.ZSTARTDATE, o.ZENDDATE, {device}, {'m.' + domain if domain else 'NULL'}
                FROM ZOBJECT o
                LEFT JOIN ZSOURCE s ON o.ZSOURCE = s.Z_PK
                LEFT JOIN ZSTRUCTUREDMETADATA m ON o.ZSTRUCTUREDMETADATA = m.Z_PK
                WHERE o.ZSTREAMNAME IN ('/app/usage', '/app/webUsage')"""
        ).fetchall()
    finally:
        con.close()
    days = {}
    for stream, value, start, end, dev, site in rows:
        if start is None or end is None or end <= start:
            continue
        side = "other" if dev else "mac"
        key = (site or "").lower().removeprefix("www.") if stream == "/app/webUsage" else value
        if not key:
            continue
        for date, hour, secs in pieces(start + EPOCH, end + EPOCH):
            day = days.setdefault(date, {}).setdefault(side, blank())
            if stream == "/app/webUsage":
                day["sites"][key] = day["sites"].get(key, 0) + secs
            else:
                day["apps"][key] = day["apps"].get(key, 0) + secs
                day["hours"][hour] += secs
    for sides in days.values():
        for day in sides.values():
            day["apps"] = {k: round(v) for k, v in day["apps"].items()}
            day["sites"] = {k: round(v) for k, v in day["sites"].items()}
            day["hours"] = [round(v) for v in day["hours"]]
    return days


def coverage(path=None):
    """What the database can see, for status.json: the browsers that report sites, and time on screen without overlaps."""
    con = sqlite3.connect(f"file:{path or DB}?mode=ro", uri=True)
    try:
        browsers = dict(con.execute("SELECT ZVALUESTRING, COUNT(*) FROM ZOBJECT WHERE ZSTREAMNAME = '/app/webUsage' GROUP BY 1"))
        spans = con.execute("SELECT ZSTARTDATE, ZENDDATE FROM ZOBJECT WHERE ZSTREAMNAME = '/app/usage' AND ZENDDATE > ZSTARTDATE ORDER BY 1").fetchall()
    finally:
        con.close()
    total, reach = 0, None
    for start, end in spans:
        reach = start if reach is None else max(reach, start)
        if end > reach:
            total += end - reach
            reach = end
    return {"siteBrowsers": browsers, "appSum": round(sum(e - s for s, e in spans)), "appUnion": round(total)}


def merge(old, new):
    """The newest copy of each day wins. The oldest day in the database may be cut short, so it never replaces a saved one."""
    out = dict(old)
    first = min(new) if new else None
    for date, sides in new.items():
        if date != first or date not in out:
            out[date] = sides
    return out


def app_name(bundle, names):
    if bundle in names:
        return names[bundle]
    return bundle.rsplit(".", 1)[-1]


def find_names(days, names):
    """Bundle ids to the names in Finder. Asked once per app."""
    for sides in days.values():
        for day in sides.values():
            for bundle in day["apps"]:
                if bundle in names:
                    continue
                try:
                    out = subprocess.run(["mdfind", f"kMDItemCFBundleIdentifier == '{bundle}'"], capture_output=True, text=True, timeout=10).stdout
                except (OSError, subprocess.SubprocessError):
                    continue
                hit = next((line for line in out.splitlines() if line.endswith(".app")), "")
                if hit:
                    names[bundle] = os.path.basename(hit)[:-4]
    return names


def python_program():
    """What macOS asks permission for: the helper the hourly job starts, or else Python itself (Python.app, not bin/python3.9)."""
    if os.path.exists(HELPER):
        return HELPER
    exe = os.path.realpath(sys.executable)
    app = os.path.join(os.path.dirname(os.path.dirname(exe)), "Resources", "Python.app")
    return app if os.path.isdir(app) else exe


def run():
    at = dt.datetime.now(TZ).isoformat(timespec="seconds")
    try:
        new = read_days()
    except (sqlite3.Error, OSError) as e:
        python = python_program()
        write_json("status.json", {"ok": False, "at": at, "error": str(e), "python": python})
        print(f"Could not read the data ({e}). Add {python} to Full Disk Access in System Settings.", file=sys.stderr)
        return 1
    days = merge(read_json("days.json", {}), new)
    write_json("days.json", days)
    write_json("names.json", find_names(new, read_json("names.json", {})))
    write_json("status.json", {"ok": True, "at": at, "days": len(days), **coverage()})
    print(f"Screen time: {len(days)} days")
    return 0


def rank(cur, prev, label=lambda k: k, limit=30):
    rows = [{"key": k, "name": label(k), "sec": v, "prev": prev.get(k, 0)} for k, v in cur.items() if v >= 60]
    return sorted(rows, key=lambda r: -r["sec"])[:limit]


def tab_seconds(date):
    """Chrome and Brave, from app/tabwatch.swift: one line is one minute."""
    out = {}
    try:
        with open(os.path.join(DIR, "tabs", f"{date}.log"), encoding="utf-8") as f:
            for line in f:
                site = line.split()[-1] if line.strip() else ""
                if site:
                    out[site] = out.get(site, 0) + 60
    except OSError:
        pass
    return out


def tab_status():
    try:
        with open(os.path.join(DIR, "tabs", "status.txt"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def add_up(days, dates, device):
    out = {"apps": {}, "sites": {}, "hours": [0] * 24, "byDay": {}}
    for date in dates:
        total = 0
        if device != "other":
            for k, v in tab_seconds(date).items():
                out["sites"][k] = out["sites"].get(k, 0) + v
        for side, day in days.get(date, {}).items():
            if device != "all" and side != device:
                continue
            for field in ("apps", "sites"):
                for k, v in day[field].items():
                    out[field][k] = out[field].get(k, 0) + v
            out["hours"] = [a + b for a, b in zip(out["hours"], day["hours"])]
            total += sum(day["apps"].values())
        out["byDay"][date] = total
    return out


def view(period="week", device="mac", today=None):
    """GET /api/screentime: one period against the one before it. Reads the saved files only."""
    days = read_json("days.json", {})
    names = read_json("names.json", {})
    device = device if device in DEVICES + ("all",) else "mac"
    today = today or dt.datetime.now(TZ).date()
    if period == "all":
        first = dt.date.fromisoformat(min(days)) if days else today
        length = (today - first).days + 1
    else:
        period = period if period in PERIODS else "week"
        length = PERIODS[period]
    dates = [(today - dt.timedelta(days=i)).isoformat() for i in range(length - 1, -1, -1)]
    before = [] if period == "all" else [(today - dt.timedelta(days=i)).isoformat() for i in range(2 * length - 1, length - 1, -1)]
    cur, old = add_up(days, dates, device), add_up(days, before, device)
    total, prev = sum(cur["byDay"].values()), sum(old["byDay"].values())
    used = sum(1 for v in cur["byDay"].values() if v)
    return {
        "period": period, "device": device,
        "total": total, "prev": prev if any(old["byDay"].values()) else None, "avg": round(total / used) if used else 0,
        "days": [{"date": d, "sec": cur["byDay"][d]} for d in dates], "hours": cur["hours"],
        "apps": rank(cur["apps"], old["apps"], lambda k: app_name(k, names)), "sites": rank(cur["sites"], old["sites"]),
        "hasOther": any("other" in sides for sides in days.values()), "tabWatch": tab_status(),
        "status": read_json("status.json", {}), "savedDays": len(days), "checked": int(time.time()),
    }


if __name__ == "__main__":
    if sys.argv[1:] == ["status"]:
        print(json.dumps(read_json("status.json", {}), ensure_ascii=False, indent=1))
    else:
        sys.exit(run())
