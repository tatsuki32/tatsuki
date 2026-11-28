#!/usr/bin/env python3
"""Tatsuki's Desk — a Mac notification at each turn of a Study Focus session.

Reads desk/data/studylog.json every 5 seconds. When the running session turns from focus to break, from break
back to focus, starts or ends, one notification with sound shows (`notify.show_mac`). The page's own clock
decides nothing here: the server works the phase out from the saved start, so a closed or hidden window still
gets told. A turn more than 2 minutes old (the server was off) is noted and not shown. What was told is kept in
desk/data/notify/study.json so a restart does not tell it twice. The title of the session is not shown.

  python3 desk/study_notify.py plan    what would show right now (shows nothing, writes nothing)
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import notify  # noqa: E402

DATA = notify.DATA
STATE = os.path.join(notify.DIR, "study.json")
MIN = 60_000
FRESH = 120_000  # a turn older than this is not shown


def phase(s, now):
    """Same as sfPhase() in index.html. Times in milliseconds."""
    w, b, n = s["work"] * MIN, s["brk"] * MIN, s["rounds"]
    t = max(0, now - s["start"])
    total = n * w + (n - 1) * b
    if t >= total:
        return "done", n, s["start"] + total
    i, within = divmod(t, w + b)
    i = int(i)
    if within < w:
        return "focus", i + 1, s["start"] + i * (w + b)
    return "break", i + 1, s["start"] + i * (w + b) + w


def message(s, mode, rnd):
    n = s["rounds"]
    if mode == "focus":
        title = "Focus started" if rnd == 1 else "Break over — focus again"
        return {"title": title, "body": f"Round {rnd} of {n} · {s['work']} minutes", "tier": "now"}
    if mode == "break":
        return {"title": f"{s['brk']}-minute break", "body": f"Round {rnd} of {n} done. Stand up, stretch, drink some water.", "tier": "now"}
    return {"title": "Session done", "body": f"{n} round{'s' if n > 1 else ''} · {n * s['work']} minutes focused", "tier": "now"}


def sessions():
    try:
        with open(os.path.join(DATA, "studylog.json"), encoding="utf-8") as f:
            rows = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for s in rows if isinstance(rows, list) else []:
        if not isinstance(s, dict) or s.get("deleted") or not isinstance(s.get("id"), str):
            continue
        try:
            ok = int(s["start"]) >= 0 and int(s["work"]) > 0 and int(s["brk"]) >= 0 and int(s["rounds"]) > 0
        except (KeyError, TypeError, ValueError):
            continue
        if ok:
            out.append(s)
    return out


def plan(now, told):
    """-> (notifications to show, new state). `told` maps session id -> the last turn told ("focus1", "break1", "done")."""
    shown, after = [], {}
    for s in sessions():
        sid = s["id"]
        if s.get("status") == "running":
            mode, rnd, at = phase(s, now)
        elif s.get("status") == "done" and sid in told and told[sid] != "done":
            # the page saves "done" within a second, often before this watch sees the last turn running
            mode, rnd, at = "done", s["rounds"], s.get("end") or now
        else:
            continue  # stopped early by hand, or long finished: nothing to tell
        key = "done" if mode == "done" else f"{mode}{rnd}"
        after[sid] = key
        if told.get(sid) != key and now - at <= FRESH:
            shown.append(message(s, mode, rnd))
    return shown, after


def read_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def tick(now=None, show=notify.show_mac):
    now = now or int(time.time() * 1000)
    told = read_state()
    shown, after = plan(now, told)
    # a finished session stays in the file once told "done", so a later save of it is not told again
    keep = {k: v for k, v in told.items() if k not in after and v == "done"}
    after = {**dict(list(keep.items())[-10:]), **after}
    if after != told:
        os.makedirs(notify.DIR, exist_ok=True)
        notify.write_json("study.json", after)
    for n in shown:
        show(n, True)
    return shown


def watch():
    while True:
        try:
            tick()
        except Exception as e:  # one bad file must not end the watch
            print("study_notify:", type(e).__name__, e, file=sys.stderr, flush=True)
        time.sleep(5)


if __name__ == "__main__":
    if (sys.argv[1:] or [""])[0] == "plan":
        print(json.dumps(plan(int(time.time() * 1000), read_state())[0], ensure_ascii=False, indent=1))
    else:
        print(__doc__)
