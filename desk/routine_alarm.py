"""Start alarm for the morning and evening routines (Desk > Habits > Morning & Evening Routines).

server.py runs watch() every 60 seconds, on 4747 only. At the start time in the routine settings
(routinelog doc "settings": lists [{id, name, at, alarm}] for up to 5 routines, voice), this Mac plays an alarm sound, says it is
time, and opens the Routine app at http://localhost:4747/routine/#alarm/<when>.

- Times are Ho Chi Minh City time, the same clock the page uses for routines.
- It rings once per routine per day, and not at all if that routine already ran today (log[when+"Run"]).
- If the Mac was asleep at the start time, it rings when it wakes, up to 30 minutes late.
- Nothing is written except its own small state file (data/routine-alarm.json).

  python3 desk/routine_alarm.py plan     what would ring now
  python3 desk/routine_alarm.py ring morning   ring once now (to try the sound)
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
STATE = os.path.join(DATA, "routine-alarm.json")
PORT = int(os.environ.get("DESK_PORT", "4747"))
HCMC = 7 * 3600
LATE = 30 * 60  # still ring this long after the start time (a Mac that just woke up)
DEFAULT = {"morningAt": "04:00", "nightAt": "17:00", "alarm": True, "cutoff": 4, "voice": "en"}
SOUND = "/System/Library/Sounds/Glass.aiff"
SAY = {"en": "It's time for {}.", "ja": "{}の時間です。", "ru": "Пора: {}."}  # {} = the routine's name
VOICE = {"ja": "Kyoko", "ru": "Milena"}  # macOS voices; the default voice is used when one is missing


def read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def log_docs():
    v = read(os.path.join(DATA, "routinelog.json"), [])
    return {x["id"]: x for x in v if isinstance(x, dict) and x.get("id")} if isinstance(v, list) else {}


def settings(docs):
    s = {**DEFAULT, **{k: v for k, v in docs.get("settings", {}).items() if v is not None}}
    if s.get("voice") not in SAY:
        s["voice"] = "en"
    return s


def routines(s):
    """Up to 5 routines from settings "lists" ({id, name, at, alarm}); older settings had only morningAt / nightAt."""
    ls = s.get("lists")
    if not isinstance(ls, list) or not ls:
        ls = [{"id": "morning", "name": "Morning routine", "at": s["morningAt"]}, {"id": "night", "name": "Evening routine", "at": s["nightAt"]}]
    return [l for l in ls if isinstance(l, dict) and isinstance(l.get("id"), str) and l["id"]][:5]


def day_of(now, cutoff):
    return time.strftime("%Y-%m-%d", time.gmtime(now + HCMC - float(cutoff or 0) * 3600))


def start_of(now, hm):
    """The start time today in HCMC, as epoch seconds."""
    try:
        h, m = (int(x) for x in str(hm).split(":"))
    except ValueError:
        return None
    t = time.gmtime(now + HCMC)
    return int(now) - (t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec) + h * 3600 + m * 60


def due(now=None):
    now = now or time.time()
    docs = log_docs()
    s = settings(docs)
    if s.get("alarm") is False:
        return []
    state = read(STATE, {})
    out = []
    for r in routines(s):
        when = r["id"]
        if r.get("alarm") is False or not r.get("at"):
            continue
        at = start_of(now, r["at"])
        if at is None or not (at <= now < at + LATE):
            continue
        day = day_of(at, s["cutoff"])
        if state.get(when) == day or (docs.get(day) or {}).get(when + "Run"):
            continue
        out.append((when, day, {**s, "name": r.get("name") or "your routine"}))
    return out


def ring(when, s):
    say = SAY[s["voice"]].format(s.get("name") or "your routine")
    for i in range(3):
        subprocess.run(["/usr/bin/afplay", SOUND], capture_output=True, timeout=10)
    cmd = ["/usr/bin/say"] + (["-v", VOICE[s["voice"]]] if s["voice"] in VOICE else []) + [say]
    if subprocess.run(cmd, capture_output=True, timeout=30).returncode != 0:
        subprocess.run(["/usr/bin/say", say], capture_output=True, timeout=30)
    subprocess.run(["/usr/bin/open", f"http://localhost:{PORT}/routine/#alarm/{when}"], capture_output=True, timeout=10)


def tick(now=None):
    for when, day, s in due(now):
        state = read(STATE, {})
        state[when] = day
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, STATE)
        ring(when, s)


def watch():
    while True:
        try:
            tick()
        except Exception as e:  # a bad file must not end the watch
            print("routine_alarm:", type(e).__name__, e, file=sys.stderr, flush=True)
        time.sleep(60)


if __name__ == "__main__":
    arg = sys.argv[1:] or ["plan"]
    if arg[0] == "plan":
        s = settings(log_docs())
        print(json.dumps({"routines": [{k: r.get(k) for k in ("id", "name", "at", "alarm")} for r in routines(s)], "settings": {k: s[k] for k in ("alarm", "voice")},
                          "due": [w for w, _, _ in due()], "state": read(STATE, {})}, ensure_ascii=False))
    elif arg[0] == "ring" and len(arg) > 1:
        s = settings(log_docs())
        r = next((x for x in routines(s) if x["id"] == arg[1]), None)
        if r:
            ring(arg[1], {**s, "name": r.get("name")})
    else:
        print(__doc__)
