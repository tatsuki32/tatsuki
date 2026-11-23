"""Start alarm for the morning and evening routines (Desk > Habits > Morning & Evening Routines).

server.py runs watch() every 60 seconds, on 4747 only. At the start time in the routine settings
(routinelog doc "settings": lists [{id, name, at, alarm}] for up to 5 routines, voice), this Mac plays an alarm sound, says it is
time, and opens the Routine app at http://localhost:4747/routine/#alarm/<when>.

- Times are Ho Chi Minh City time, the same clock the page uses for routines.
- It rings once per routine per day, and not at all if that routine already ran today (log[when+"Run"]).
- If the Mac was asleep at the start time, it rings when it wakes, up to 30 minutes late.
- Quiet hours (QUIET, 22:00-07:00 Ho Chi Minh City time; everyone at home is asleep): no sound and no voice.
  It only opens the Routine app's start screen (#start/<when>), which does not ring either.
- Goals (routines docs with kind "goal": things to do in the day, week or month): at a goal's remindAt, if it isn't done yet for
  its day / week / month (counts in the day docs' "goals"), a Mac notification and the voice say so. Once a day per goal.
  In quiet hours only the silent notification.
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
QUIET = (22, 7)  # from 22:00 to 07:00 the Mac stays silent
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


GOAL_SAY = {"en": "{}: not done {} yet.", "ja": "{}の「{}」はまだです。", "ru": "«{}» ещё не сделано."}
GOAL_WHEN = {"day": ("today", "今日"), "week": ("this week", "今週"), "month": ("this month", "今月")}
NOTE = ("on run argv", "display notification (item 1 of argv) with title (item 2 of argv)", "end run")


def period_days(period, day):
    t = time.strptime(day, "%Y-%m-%d")
    base = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 12, 0, 0, 0, 0, -1))
    if period == "week":
        mon = base - t.tm_wday * 86400
        return [time.strftime("%Y-%m-%d", time.localtime(mon + i * 86400)) for i in range(7)]
    if period == "month":
        return [d for d in (time.strftime("%Y-%m-%d", time.localtime(base + (i - t.tm_mday + 1) * 86400)) for i in range(31)) if d[:7] == day[:7]]
    return [day]


def goals_due(now=None):
    now = now or time.time()
    docs = log_docs()
    s = settings(docs)
    items = read(os.path.join(DATA, "routines.json"), [])
    state = read(STATE, {})
    out = []
    for g in items if isinstance(items, list) else []:
        if not isinstance(g, dict) or g.get("kind") != "goal" or g.get("deleted") or not g.get("remindAt"):
            continue
        at = start_of(now, g["remindAt"])
        if at is None or not (at <= now < at + LATE):
            continue
        day = day_of(now, s["cutoff"])
        if state.get("g:" + g["id"]) == day:
            continue
        period = g.get("period") if g.get("period") in GOAL_WHEN else "day"
        n = sum(int((docs.get(d) or {}).get("goals", {}).get(g["id"]) or 0) for d in period_days(period, day))
        if n >= max(1, int(g.get("target") or 1)):
            continue
        out.append((g, period, day, s))
    return out


def remind(g, period, s, now=None):
    title = g.get("title") or "Goal"
    en, ja = GOAL_WHEN[period]
    text = GOAL_SAY["ja"].format(ja, title) if s["voice"] == "ja" else GOAL_SAY["ru"].format(title) if s["voice"] == "ru" else GOAL_SAY["en"].format(title, en)
    cmd = ["/usr/bin/osascript"]
    for line in NOTE:
        cmd += ["-e", line]
    subprocess.run(cmd + ["--", text, "Routine"], capture_output=True, timeout=10)  # the words go in as arguments after "--", never into the script
    if quiet(now):
        return
    subprocess.run(["/usr/bin/afplay", SOUND], capture_output=True, timeout=10)
    vc = ["/usr/bin/say"] + (["-v", VOICE[s["voice"]]] if s["voice"] in VOICE else []) + [text]
    if subprocess.run(vc, capture_output=True, timeout=30).returncode != 0:
        subprocess.run(["/usr/bin/say", text], capture_output=True, timeout=30)


def quiet(now=None):
    h = time.gmtime((now or time.time()) + HCMC).tm_hour
    a, b = QUIET
    return h >= a or h < b


def ring(when, s, now=None):
    if quiet(now):  # silent: just leave the start screen open for when they wake up
        subprocess.run(["/usr/bin/open", f"http://localhost:{PORT}/routine/#start/{when}"], capture_output=True, timeout=10)
        return
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
        ring(when, s, now)
    for g, period, day, s in goals_due(now):
        state = read(STATE, {})
        state["g:" + g["id"]] = day
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, STATE)
        remind(g, period, s, now)


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
                          "due": [w for w, _, _ in due()], "goalsDue": [g["title"] for g, _, _, _ in goals_due()], "quietNow": quiet(), "state": read(STATE, {})}, ensure_ascii=False))
    elif arg[0] == "ring" and len(arg) > 1:
        s = settings(log_docs())
        r = next((x for x in routines(s) if x["id"] == arg[1]), None)
        if r:
            ring(arg[1], {**s, "name": r.get("name")})
    else:
        print(__doc__)
