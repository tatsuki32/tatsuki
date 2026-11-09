#!/usr/bin/env python3
"""Tatsuki's Desk — how fast this Mac's connection is, measured only when asked (one run uses about 500 MB).

macOS's own networkQuality does the measuring, against Apple's servers. A run that stops halfway is not a result:
it is reported and not saved. History stays in desk/data/netspeed/history.json; a Wi-Fi name says where someone was,
so backup_data.py never sends this folder anywhere. No password and no IP address is read or kept.

  python3 netspeed.py          one run, printed
  python3 netspeed.py view     what the page shows
"""
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("NETSPEED_DIR") or os.path.join(ROOT, "data", "netspeed")
TOOL = "/usr/bin/networkQuality"
KEEP = 500
RUN_TIMEOUT = 150
NO_LINE = (-1001, -1003, -1004, -1005, -1009)
# Published figures, in Mbps. Each was read on the page it links to on 2026-11-05.
ZOOM = "https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0060748"
NETFLIX = "https://help.netflix.com/en/node/306"
USES = (
    {"id": "voice", "name": "Voice call", "down": 0.08, "up": 0.08, "source": "Zoom", "url": ZOOM},
    {"id": "meeting", "name": "Video meeting (1080p)", "down": 3.0, "up": 3.8, "source": "Zoom", "url": ZOOM},
    {"id": "video", "name": "4K video", "down": 15.0, "up": 0, "source": "Netflix", "url": NETFLIX},
)
ROOM = 2  # twice the published figure counts as enough; between one and two is tight

_lock = threading.Lock()
_state = {"running": False, "startedAt": None, "error": None}


def read_history():
    try:
        with open(os.path.join(DIR, "history.json"), encoding="utf-8") as f:
            rows = json.load(f)
        return rows if isinstance(rows, list) else []
    except (OSError, ValueError):
        return []


def write_history(rows):
    os.makedirs(DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DIR, prefix=".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(rows[-KEEP:], f, ensure_ascii=False)
    os.replace(tmp, os.path.join(DIR, "history.json"))


def parse_result(raw):
    """networkQuality's JSON to one row. Returns (row, None), or (None, a sentence for the page)."""
    code = raw.get("error_code")
    if code:
        if code in NO_LINE:
            return None, "Could not connect to the internet. Check the connection and try again."
        return None, "The test stopped partway. This run was not saved. Please try again."
    down, up = raw.get("dl_throughput"), raw.get("ul_throughput")
    if not down or not up:
        return None, "Could not read the result. This run was not saved. Please try again."
    return {
        "down": round(down / 1e6, 1),
        "up": round(up / 1e6, 1),
        "rttMs": round(raw["base_rtt"], 1) if raw.get("base_rtt") else None,
        "rpmDown": round(raw["dl_responsiveness"]) if raw.get("dl_responsiveness") else None,
        "rpmUp": round(raw["ul_responsiveness"]) if raw.get("ul_responsiveness") else None,
        "bytes": (raw.get("dl_bytes_transferred") or 0) + (raw.get("ul_bytes_transferred") or 0),
        "interface": raw.get("interface_name") or "",
    }, None


def parse_summary(text):
    """`ipconfig getsummary`: is this Wi-Fi, and what is it called. macOS hides the name from some programs."""
    kind = re.search(r"^\s*InterfaceType\s*:\s*(.+?)\s*$", text, re.M)
    name = re.search(r"^\s*SSID\s*:\s*(.+?)\s*$", text, re.M)
    ssid = name.group(1) if name else None
    return {"wifi": bool(kind and kind.group(1) == "WiFi"), "ssid": None if ssid in (None, "<redacted>") else ssid}


def parse_airport(text):
    """`system_profiler SPAirPortDataType`: only the network in use, never the ones nearby."""
    part = text.split("Current Network Information:", 1)
    if len(part) < 2:
        return {}
    now = part[1].split("Other Local Wi-Fi Networks:", 1)[0]
    out = {}
    m = re.search(r"Signal / Noise:\s*(-?\d+) dBm / (-?\d+) dBm", now)
    if m:
        out["signal"], out["noise"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"Channel:\s*(\d+) \((\d)GHz(?:, (\d+)MHz)?\)", now)
    if m:
        out["channel"], out["band"] = int(m.group(1)), m.group(2) + "GHz"
        if m.group(3):
            out["width"] = int(m.group(3))
    m = re.search(r"PHY Mode:\s*(\S+)", now)
    if m:
        out["phy"] = m.group(1)
    m = re.search(r"Transmit Rate:\s*(\d+)", now)
    if m:
        out["rate"] = int(m.group(1))
    return out


def _output(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def link_info(interface):
    info = parse_summary(_output(["/usr/sbin/ipconfig", "getsummary", interface or "en0"]))
    if info["wifi"]:
        info.update(parse_airport(_output(["/usr/sbin/system_profiler", "SPAirPortDataType"])))
    return info


def measure():
    """One run. Returns (row, None) or (None, a sentence for the page)."""
    try:
        done = subprocess.run([TOOL, "-c", "-s"], capture_output=True, text=True, timeout=RUN_TIMEOUT)
        raw = json.loads(done.stdout)
    except subprocess.TimeoutExpired:
        return None, "It took too long, so it stopped. This run was not saved."
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, "Could not start the test. This run was not saved."
    row, error = parse_result(raw)
    if error:
        return None, error
    row.update(link_info(row["interface"]))
    row["at"] = int(time.time() * 1000)
    return row, None


def _run():
    row, error = measure()
    with _lock:
        if row:
            write_history(read_history() + [row])
        _state.update(running=False, error=error)


def start():
    """Starts a run in the background. False when one is already going."""
    with _lock:
        if _state["running"]:
            return False
        _state.update(running=True, startedAt=int(time.time() * 1000), error=None)
    threading.Thread(target=_run, daemon=True).start()
    return True


def judge(row, use):
    need = [(row["down"], use["down"]), (row["up"], use["up"])]
    if any(need_v and have < need_v for have, need_v in need):
        return "short"
    return "tight" if any(need_v and have < need_v * ROOM for have, need_v in need) else "ok"


def signal_word(dbm):
    """A rule of thumb, not a published limit; the page says so."""
    if dbm is None:
        return None
    return "Strong" if dbm >= -60 else "Fair" if dbm >= -70 else "Weak"


def place_name(row):
    return row.get("ssid") or ("No name" if row.get("wifi") else "Not Wi-Fi")


def wait_seconds(rpm):
    """Round trips per minute to seconds per round trip."""
    return round(60 / rpm, 2) if rpm else None


def sentence(row, uses):
    """The one line at the top: what this speed is good for, and if it falls short, where to look."""
    left = {u["id"]: u["verdict"] for u in uses}
    if all(v == "ok" for v in left.values()):
        times = row["down"] / USES[2]["down"]
        text = "Calls, meetings and video all have room to spare."
        text += f" Download is about {times:.0f}x what 4K video needs." if times >= 3 else ""
        return text
    if left["voice"] == "short":
        text = "Too slow even for a voice call."
    elif left["meeting"] == "short":
        text = "Fine for voice calls, not enough for video meetings."
    elif left["video"] == "short":
        text = "Fine for calls and meetings, not enough for 4K video."
    else:
        text = "Usable, with little room to spare."
    word = signal_word(row.get("signal"))
    if word == "Weak":
        text += " The signal is weak, so moving closer to the router may help."
    elif word:
        text += " The signal is fine, so the line itself may be busy."
    return text


def describe(row, before):
    uses = [{**u, "verdict": judge(row, u)} for u in USES]
    out = {
        **row,
        "place": place_name(row),
        "uses": uses,
        "sentence": sentence(row, uses),
        "signalWord": signal_word(row.get("signal")),
        "waitDown": wait_seconds(row.get("rpmDown")),
        "waitUp": wait_seconds(row.get("rpmUp")),
        "oneGbDown": round(8000 / row["down"]),
        "oneGbUp": round(8000 / row["up"]),
    }
    if before:
        out["before"] = {"down": before["down"], "up": before["up"], "at": before.get("at")}
    return out


def places(rows):
    """The middle run per place rather than the average: one bad run should not move a whole place."""
    groups = {}
    for r in rows:
        groups.setdefault(place_name(r), []).append(r)
    out = [{
        "name": name,
        "count": len(g),
        "down": round(statistics.median(x["down"] for x in g), 1),
        "up": round(statistics.median(x["up"] for x in g), 1),
        "at": g[-1].get("at"),
    } for name, g in groups.items()]
    return sorted(out, key=lambda p: -(p["at"] or 0))


def view():
    with _lock:
        rows, state = read_history(), dict(_state)
    latest = None
    if rows:
        before = next((r for r in reversed(rows[:-1]) if place_name(r) == place_name(rows[-1])), None)
        latest = describe(rows[-1], before)
    return {
        **state,
        "available": bool(shutil.which(TOOL)),
        "latest": latest,
        "places": places(rows),
        "history": [{"at": r.get("at"), "place": place_name(r), "down": r["down"], "up": r["up"]} for r in reversed(rows[-10:])],
        "count": len(rows),
    }


if __name__ == "__main__":
    if sys.argv[1:] == ["view"]:
        print(json.dumps(view(), ensure_ascii=False, indent=1))
    else:
        result, problem = measure()
        print(json.dumps(result, ensure_ascii=False, indent=1) if result else problem)
