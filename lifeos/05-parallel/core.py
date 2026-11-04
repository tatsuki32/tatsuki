"""並列ライン: one piece of work = one branch = one git worktree, with safety limits.

A line is "running" or "paused" (both count against the limit, so half-finished work stays
capped) until it is "ended". Every line needs an end time when it starts. check() sends the
"time to stop" alert; the overdue banner in the list is worked out from the state each time,
so it shows up whether or not Telegram is connected.

State: <data>/tools/05-parallel/lines.json. Time worked: <data>/logs/work.jsonl, one record per
stretch of running, written when the stretch closes.
"""
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta

from lifeos.bot import telegram
from lifeos.lib import logs, paths

SLUG = "05-parallel"
DEFAULT_MAX = 3
HARD_MAX = 5
MAX_SPAN_HOURS = 12        # an end time further away than this is almost always a typo
MAX_ALERTS = 3             # after this only the screen keeps saying it, so nothing buzzes all night
ALERT_EVERY_MIN = 30
QUIET_MIN = 30             # no commit or file change for this long: "動きなし"
WATCH_INTERVAL_SEC = 30
WEEKDAYS = "月火水木金土日"
ACTIVE = ("running", "paused")
_NAME = re.compile(r"^\w[\w-]{0,39}$")


class LinesError(Exception):
    """A refusal or a failure; the message is shown to the owner as it is."""


# ---- connections (replaceable) -------------------------------------------------------------

def send_telegram(text):
    """True if it reached Telegram. Otherwise the library leaves it in logs/outbox.jsonl."""
    return telegram.notify(text)


def show_on_screen(text):
    """A macOS notification. The text goes in as an argument, so it cannot act as a script."""
    if paths.is_sandbox() or sys.platform != "darwin":
        return False
    script = ["-e", "on run argv", "-e", 'display notification (item 1 of argv) with title "並列ライン" sound name "Glass"',
              "-e", "end run"]
    try:
        return subprocess.run(["osascript"] + script + ["--", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def telegram_connected():
    token, chat = telegram.config()
    return bool(token and chat)


# ---- state ---------------------------------------------------------------------------------

def _file(name):
    return paths.data_path("tools", SLUG, name)


def _load():
    try:
        with open(_file("lines.json"), encoding="utf-8") as f:
            v = json.load(f)
    except FileNotFoundError:
        return {"lines": []}
    except ValueError:
        raise LinesError(f"{_file('lines.json')} が壊れていて読めません。中身を直すまで何も書きません。") from None
    if not isinstance(v, dict) or not isinstance(v.get("lines"), list):
        raise LinesError(f"{_file('lines.json')} の形が違います。中身を直すまで何も書きません。")
    return v


def _save(state):
    path = _file("lines.json")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".lines-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


@contextmanager
def _locked():
    """The state, under a lock, saved when the block finishes without an error."""
    with open(_file("lines.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = _load()
        yield state
        _save(state)


def read_lines():
    return _load()["lines"]


def _active(state):
    return [x for x in state["lines"] if x["status"] in ACTIVE]


def _find(state, name):
    for x in _active(state):
        if x["name"] == name:
            return x
    known = "、".join(x["name"] for x in _active(state)) or "なし"
    raise LinesError(f"「{name}」というラインはありません（今あるライン：{known}）")


def max_lines():
    try:
        with open(_file("config.json"), encoding="utf-8") as f:
            v = json.load(f).get("max_lines")
    except (OSError, ValueError, AttributeError):
        return DEFAULT_MAX
    return v if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= HARD_MAX else DEFAULT_MAX


def set_max_lines(n):
    if not 1 <= n <= HARD_MAX:
        raise LinesError(f"上限は1から{HARD_MAX}の間で決めます。")
    with open(_file("config.json"), "w", encoding="utf-8") as f:
        json.dump({"max_lines": n}, f)
    return n


# ---- time ----------------------------------------------------------------------------------

def _t(iso):
    return datetime.fromisoformat(iso)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def fmt_dur(minutes):
    minutes = int(minutes)
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}時間{m}分"
    return f"{h}時間" if h else f"{m}分"


def _span_minutes(text):
    """"90m" "2h" "1h30m" "90分" "2時間" "1時間30分" -> minutes, or None."""
    m = re.match(r"^(?:(\d+)(?:h|時間))?(?:(\d+)(?:m|分)?)?$", text)
    if not m or not (m.group(1) or m.group(2)):
        return None
    if not m.group(1) and text.isdigit():
        return None  # a bare number could mean anything
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def parse_until(text, now=None):
    """"18:30" (the next time the clock shows it) or a length of time. Returns the end time."""
    now = now or paths.now()
    text = str(text).strip().replace("：", ":")
    clock = re.match(r"^(\d{1,2}):(\d{2})$", text)
    if clock:
        h, m = int(clock.group(1)), int(clock.group(2))
        if h > 23 or m > 59:
            raise LinesError(f"「{text}」は時刻として読めません。")
        end = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if end <= now:
            end += timedelta(days=1)
    else:
        minutes = _span_minutes(text)
        if not minutes:
            raise LinesError(f"「{text}」を終了時刻として読めません。18:30 か 90m のように書きます。")
        end = now + timedelta(minutes=minutes)
    if end - now > timedelta(hours=MAX_SPAN_HOURS):
        raise LinesError(f"終了時刻が{MAX_SPAN_HOURS}時間より先です（{end:%m/%d %H:%M}）。"
                         f"もっと近い時刻にしてください。")
    return end


def parse_past(text, since, now=None):
    """--at HH:MM for "I actually stopped back then". The latest such time not after now."""
    now = now or paths.now()
    m = re.match(r"^(\d{1,2}):(\d{2})$", str(text).strip().replace("：", ":"))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise LinesError(f"「{text}」は時刻として読めません。18:30 のように書きます。")
    at = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    if at > now:
        at -= timedelta(days=1)
    if at < since:
        raise LinesError(f"{at:%m/%d %H:%M} は、そのラインが動き始めた時刻より前です。")
    return at


# ---- git -----------------------------------------------------------------------------------

def _repo():
    return os.environ.get("LIFEOS_LINES_REPO") or paths.REPO


def _git(args, cwd=None, check=True):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        r = subprocess.run(["git"] + args, cwd=cwd or _repo(), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=60, universal_newlines=True, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        raise LinesError(f"git を動かせませんでした（{type(e).__name__}）") from None
    if check and r.returncode:
        raise LinesError(f"git {args[0]} が失敗しました：{r.stderr.strip()[:300]}")
    return r


def list_worktrees():
    """From `git worktree list --porcelain`. The first one is the main checkout."""
    out, cur = [], None
    for line in _git(["worktree", "list", "--porcelain"]).stdout.splitlines():
        if line.startswith("worktree "):
            cur = {"path": line[len("worktree "):], "branch": None}
            out.append(cur)
        elif line.startswith("branch refs/heads/") and cur:
            cur["branch"] = line[len("branch refs/heads/"):]
    return out


def _same_path(a, b):
    return os.path.realpath(a) == os.path.realpath(b)


def inspect(path, base=None):
    """What git says about a worktree: exists, dirty count, last commit, last activity (epoch)."""
    info = {"exists": os.path.isdir(path), "dirty": 0, "subject": "", "activity": None, "ahead": None}
    if not info["exists"]:
        return info
    status = _git(["status", "--porcelain", "-z"], cwd=path, check=False)
    if status.returncode:
        info["exists"] = False
        return info
    times = []
    parts = status.stdout.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        info["dirty"] += 1
        if entry[0] in "RC":
            i += 1  # the original name follows a rename
        if info["dirty"] <= 200:
            try:
                times.append(os.path.getmtime(os.path.join(path, entry[3:])))
            except OSError:
                pass
    head = _git(["log", "-1", "--format=%ct%x00%s"], cwd=path, check=False)
    if head.returncode == 0 and "\0" in head.stdout:
        ct, subject = head.stdout.strip().split("\0", 1)
        info["subject"] = subject
        if ct.isdigit():
            times.append(int(ct))
    info["activity"] = max(times) if times else None
    if base:
        n = _git(["rev-list", "--count", f"{base}..HEAD"], cwd=path, check=False)
        if n.returncode == 0 and n.stdout.strip().isdigit():
            info["ahead"] = int(n.stdout.strip())
    return info


def _default_base():
    has_main = _git(["rev-parse", "--verify", "--quiet", "main^{commit}"], check=False).returncode == 0
    return "main" if has_main else "HEAD"


def _prepare_worktree(name, branch, base):
    """Finds the branch's worktree or makes one. Returns (path, created)."""
    if branch.startswith("-") or _git(["check-ref-format", "--branch", branch], check=False).returncode:
        raise LinesError(f"「{branch}」はブランチ名に使えません。")
    trees = list_worktrees()
    for t in trees:
        if t["branch"] == branch:
            if _same_path(t["path"], trees[0]["path"]):
                raise LinesError(f"{branch} は本体の作業フォルダで開いています。別のブランチに分けてください。")
            return t["path"], False
    target = os.path.join(trees[0]["path"], ".worktrees", name)
    if os.path.exists(target):
        raise LinesError(f"{target} が既にあります。別の名前にするか、--branch でそのブランチを指定してください。")
    if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], check=False).returncode == 0:
        _git(["worktree", "add", target, branch])
        return target, True
    base = base or _default_base()
    if base.startswith("-") or _git(["rev-parse", "--verify", "--quiet", base + "^{commit}"], check=False).returncode:
        raise LinesError(f"分岐元「{base}」が見つかりません。")
    _git(["worktree", "add", "-b", branch, target, base])
    return target, True


# ---- the lines -----------------------------------------------------------------------------

def start(name, until, what="", branch=None, base=None, now=None):
    now = now or paths.now()
    if not _NAME.match(name or ""):
        raise LinesError("ラインの名前は40字まで。文字・数字・- と _ だけで、先頭は文字か数字にします。")
    if not what.strip():
        raise LinesError("何をするラインかを --what で書いてください。一覧に出ます。")
    if not until:
        raise LinesError("終了時刻を決めてから始めます。--until 18:30 か --until 90m を付けてください。")
    end = parse_until(until, now)
    with _locked() as state:
        active = _active(state)
        limit = max_lines()
        if len(active) >= limit:
            names = "、".join(x["name"] for x in active)
            raise LinesError(f"同時に持てるのは{limit}つまでです（今：{names}）。"
                             f"新しく始めるには、どれかを end で終えてください。")
        if any(x["name"] == name for x in active):
            raise LinesError(f"「{name}」は今も開いています。別の名前にしてください。")
        branch = branch or f"lines/{name}"
        if any(x["branch"] == branch for x in active):
            raise LinesError(f"{branch} は別のラインが使っています。1作業に1ブランチです。")
        path, created = _prepare_worktree(name, branch, base)
        line = {"name": name, "what": what.strip(), "branch": branch, "path": path, "base": base or _default_base(),
                "created_worktree": created, "status": "running", "started_at": _iso(now), "run_since": _iso(now),
                "until": _iso(end), "note": "", "note_at": None, "pause_reason": "", "alerts": 0, "last_alert_at": None,
                "extended": 0, "ended_at": None, "worktree_removed": False}
        state["lines"].append(line)
    logs.append("lines", {"event": "start", "line": name, "until": line["until"]})
    return line


def _close_stretch(line, end):
    """The running stretch goes into the work log."""
    begin = _t(line["run_since"])
    logs.append("work", {"line": line["name"], "start": _iso(begin), "end": _iso(end),
                         "minutes": int((end - begin).total_seconds() // 60)})
    line["run_since"] = None


def note(name, text, now=None):
    """Where the line has got to, as told by its session or the owner."""
    now = now or paths.now()
    with _locked() as state:
        line = _find(state, name)
        line["note"] = text.strip()
        line["note_at"] = _iso(now)
    return line


def pause(name, reason="", at=None, now=None):
    now = now or paths.now()
    with _locked() as state:
        line = _find(state, name)
        if line["status"] != "running":
            raise LinesError(f"「{name}」は既に止めています。")
        _close_stretch(line, parse_past(at, _t(line["run_since"]), now) if at else now)
        line.update(status="paused", pause_reason=reason.strip())
    return line


def resume(name, until, now=None):
    now = now or paths.now()
    if not until:
        raise LinesError("再開するときも終了時刻を決めます。--until 18:30 か --until 90m を付けてください。")
    end = parse_until(until, now)
    with _locked() as state:
        line = _find(state, name)
        if line["status"] != "paused":
            raise LinesError(f"「{name}」は動いています。")
        line.update(status="running", run_since=_iso(now), until=_iso(end), pause_reason="", alerts=0, last_alert_at=None)
    return line


def extend(name, until, now=None):
    now = now or paths.now()
    end = parse_until(until, now)
    with _locked() as state:
        line = _find(state, name)
        if line["status"] != "running":
            raise LinesError(f"「{name}」は止めています。resume で再開してください。")
        line.update(until=_iso(end), alerts=0, last_alert_at=None, extended=line.get("extended", 0) + 1)
    logs.append("lines", {"event": "extend", "line": name, "until": _iso(end)})
    return line


def end(name, at=None, now=None):
    """Closes the line. The worktree and the branch stay; removing is a separate, approved step."""
    now = now or paths.now()
    with _locked() as state:
        line = _find(state, name)
        if line["status"] == "running":
            _close_stretch(line, parse_past(at, _t(line["run_since"]), now) if at else now)
        line.update(status="ended", ended_at=_iso(now))
    logs.append("lines", {"event": "end", "line": name})
    return line


def remove_worktree(name, approved=False):
    """Removes an ended line's worktree. Only with approval, never by force, and the branch stays.

    Without approval it returns the description of what would happen and changes nothing.
    """
    with _locked() as state:
        ended = [x for x in state["lines"] if x["name"] == name and x["status"] == "ended" and not x.get("worktree_removed")]
        if not ended:
            raise LinesError(f"終えたライン「{name}」で、作業フォルダが残っているものはありません。")
        line = ended[-1]
        if not line.get("created_worktree"):
            raise LinesError("このラインが作った作業フォルダではないので消しません。")
        if not approved:
            return {"approved": False, "path": line["path"], "branch": line["branch"], "dirty": inspect(line["path"])["dirty"]}
        _git(["worktree", "remove", line["path"]])  # git refuses when there is uncommitted work
        line["worktree_removed"] = True
    return {"approved": True, "path": line["path"], "branch": line["branch"]}


# ---- the safety alert ----------------------------------------------------------------------

def overdue(lines, now=None):
    now = now or paths.now()
    return [x for x in lines if x["status"] == "running" and _t(x["until"]) <= now]


def _alert_due(line, now):
    if line.get("alerts", 0) >= MAX_ALERTS:
        return False
    last = line.get("last_alert_at")
    return not last or now - _t(last) >= timedelta(minutes=ALERT_EVERY_MIN)


def check(now=None):
    """Sends the "time to stop" alert for lines past their end time. Returns what was sent, or None."""
    now = now or paths.now()
    with _locked() as state:
        due = [x for x in overdue(state["lines"], now) if _alert_due(x, now)]
        for x in due:
            x["alerts"] = x.get("alerts", 0) + 1
            x["last_alert_at"] = _iso(now)
    if not due:
        return None
    parts = [f"{x['name']}（{_t(x['until']):%H:%M}）" for x in due]
    text = f"並列ライン：終了時刻を過ぎました。{'、'.join(parts)}。区切りのよいところで止めましょう。"
    sent = {"text": text, "telegram": False, "screen": False}
    for key, channel in (("telegram", send_telegram), ("screen", show_on_screen)):
        try:
            sent[key] = bool(channel(text))
        except Exception:  # one channel failing must not silence the other
            pass
    logs.append("lines", {"event": "alert", "lines": [x["name"] for x in due], "telegram": sent["telegram"],
                          "screen": sent["screen"]})
    return sent


def _pending(lines):
    """Running lines that may still need an alert. The watcher stays alive for these."""
    return [x for x in lines if x["status"] == "running" and x.get("alerts", 0) < MAX_ALERTS]


def watcher_held():
    with open(_file("watch.lock"), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
    return False


def ensure_watcher(script):
    """Starts the background watcher if a line needs one. Sandboxes opt in with LIFEOS_LINES_WATCHER=1."""
    if paths.is_sandbox() and os.environ.get("LIFEOS_LINES_WATCHER") != "1":
        return False
    if not _pending(read_lines()) or watcher_held():
        return False
    subprocess.Popen([sys.executable, script, "watch"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, cwd=os.path.dirname(os.path.abspath(script)))
    return True


def watch(sleep, interval=WATCH_INTERVAL_SEC):
    """Runs check() until no running line can need another alert. Only one watcher runs at a time."""
    with open(_file("watch.lock"), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        while True:
            check()
            if not _pending(read_lines()):
                return True
            sleep(interval)


# ---- time worked ---------------------------------------------------------------------------

def _stretches(now):
    out = []
    for r in logs.read("work"):
        try:
            out.append((_t(r["start"]), _t(r["end"])))
        except (KeyError, TypeError, ValueError):
            continue
    try:
        lines = read_lines()
    except LinesError:
        lines = []
    for x in lines:
        if x["status"] == "running" and x.get("run_since"):
            out.append((_t(x["run_since"]), now))
    return out


def minutes_by_day(now=None):
    """{date: minutes} of clock time with at least one line running. Overlaps count once, because
    this is about how long the owner was on, not how much the sessions did."""
    now = now or paths.now()
    merged = []
    for a, b in sorted(x for x in _stretches(now) if x[1] > x[0]):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    days = {}
    for a, b in merged:
        a, b = a.astimezone(paths.HCMC), b.astimezone(paths.HCMC)
        while a < b:
            midnight = (a + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            stop = min(b, midnight)
            key = a.strftime("%Y-%m-%d")
            days[key] = days.get(key, 0) + (stop - a).total_seconds() / 60
            a = stop
    return days


def _monday(now):
    return (now - timedelta(days=now.weekday())).date()


def week_days(now=None, weeks_ago=0):
    """[(date, minutes)] from Monday to Sunday."""
    now = now or paths.now()
    days = minutes_by_day(now)
    monday = _monday(now) - timedelta(weeks=weeks_ago)
    return [(d, days.get(d.strftime("%Y-%m-%d"), 0)) for d in (monday + timedelta(days=i) for i in range(7))]


def render_week(now=None, past_weeks=3):
    now = now or paths.now()
    rows = week_days(now)
    out = [f"今週の稼働（{rows[0][0]:%m/%d} から）"]
    for d, m in rows:
        if d > now.date():
            out.append(f"  {WEEKDAYS[d.weekday()]} {d:%m/%d}  -")
        else:
            bar = "▇" * int(m // 30)
            out.append(f"  {WEEKDAYS[d.weekday()]} {d:%m/%d}  {fmt_dur(m):<8}{bar}")
    out.append(f"  合計 {fmt_dur(sum(m for _, m in rows))}")
    out.append("これまで")
    for w in range(1, past_weeks + 1):
        prev = week_days(now, w)
        out.append(f"  {prev[0][0]:%m/%d} の週  {fmt_dur(sum(m for _, m in prev))}")
    out.append("ラインが1つでも動いていた時間です。重なりは1回だけ数えます。")
    return "\n".join(out)


# ---- the one screen ------------------------------------------------------------------------

def _width(text):
    return sum(2 if unicodedata.east_asian_width(c) in "WFA" else 1 for c in text)


def clip(text, width):
    text = re.sub(r"[ \t\r\n]+", " ", str(text)).strip(" ")
    if _width(text) <= width:
        return text
    out = ""
    for c in text:
        if _width(out + c) > width - 1:
            break
        out += c
    return out + "…"


def _ago(epoch_or_dt, now):
    then = epoch_or_dt if isinstance(epoch_or_dt, datetime) else datetime.fromtimestamp(epoch_or_dt, paths.HCMC)
    minutes = max(0, int((now - then).total_seconds() // 60))
    return "たった今" if minutes < 1 else f"{fmt_dur(minutes)}前"


def _state_label(line, info, now):
    until = _t(line["until"])
    if not info["exists"]:
        return "作業フォルダなし"
    if line["status"] == "paused":
        return "止めている"
    if until <= now:
        over = max(1, int((now - until).total_seconds() // 60))
        return f"時間切れ（{until:%H:%M} から{fmt_dur(over)}）"
    left = f"{until:%H:%M}まで（あと{fmt_dur(max(1, (until - now).total_seconds() // 60))}）"
    since = _t(line["started_at"]).timestamp()
    last = max(info["activity"] or 0, since)
    if now.timestamp() - last >= QUIET_MIN * 60:
        return f"動きなし {fmt_dur((now.timestamp() - last) // 60)}・{left}"
    return f"動いている・{left}"


def _where(line, info, now):
    if line["status"] == "paused" and line.get("pause_reason"):
        return f"止めた理由：{line['pause_reason']}"
    if line.get("note"):
        return f"{line['note']}（{_ago(_t(line['note_at']), now)}）"
    if info["ahead"] == 0:
        return "このラインのコミットはまだありません"
    if info["subject"]:
        return f"最新のコミット「{info['subject']}」"
    return "まだ記録がありません"


def snapshot(now=None):
    """Everything the list shows, as data. The Desk can read this (see INTEGRATION.md)."""
    now = now or paths.now()
    lines = read_lines()
    active = [x for x in lines if x["status"] in ACTIVE]
    rows = []
    for x in active:
        info = inspect(x["path"], x.get("base"))
        rows.append({"name": x["name"], "what": x["what"], "branch": x["branch"], "path": x["path"], "status": x["status"],
                     "until": x["until"], "state": _state_label(x, info, now), "where": _where(x, info, now),
                     "dirty": info["dirty"], "ahead": info["ahead"], "extended": x.get("extended", 0),
                     "activity": None if info["activity"] is None else _ago(info["activity"], now)})
    return {"now": _iso(now), "limit": max_lines(), "open": len(active), "lines": rows,
            "banner": banner_lines(lines, now), "today_minutes": int(minutes_by_day(now).get(now.strftime("%Y-%m-%d"), 0)),
            "week_minutes": int(sum(m for _, m in week_days(now)))}


def banner_lines(lines, now=None):
    now = now or paths.now()
    late = overdue(lines, now)
    if not late:
        return []
    names = "、".join(f"{x['name']}（{_t(x['until']):%H:%M}）" for x in late)
    out = [f"！終了時刻を過ぎました：{names}", "  区切りのよいところで止めましょう。止める：/lines end 名前　延ばす：/lines extend 名前 30m"]
    if not telegram_connected():
        out.append("  Telegram は未接続です。通知はこの画面と Mac の通知に出します。")
    return out


def _other_worktrees(lines):
    """Worktrees that are not open lines, apart from the main checkout."""
    trees = list_worktrees()[1:]
    taken = [x["path"] for x in lines if x["status"] in ACTIVE]
    return [t for t in trees if not any(_same_path(t["path"], p) for p in taken)]


def render_list(now=None, width=78):
    now = now or paths.now()
    snap = snapshot(now)
    lines = read_lines()
    head = f"並列ライン　{now:%m/%d}（{WEEKDAYS[now.weekday()]}）{now:%H:%M}　作りかけ {snap['open']}/{snap['limit']}"
    out = [head, ""]
    if snap["banner"]:
        out += snap["banner"] + [""]
    if not snap["lines"]:
        out += ["開いているラインはありません。", '始める：/lines start 名前 --until 18:30 --what "何をするか"', ""]
    for r in snap["lines"]:
        mark = "||" if r["status"] == "paused" else ">>"
        extra = f"・延長{r['extended']}回" if r["extended"] else ""
        out.append(clip(f"{mark} {r['name']}　{r['state']}{extra}", width))
        out.append("   " + clip(f"やること：{r['what']}", width - 3))
        out.append("   " + clip(f"どこまで：{r['where']}", width - 3))
        facts = [r["branch"], f"未コミット{r['dirty']}件"]
        if r["ahead"]:
            facts.append(f"コミット+{r['ahead']}")
        if r["activity"]:
            facts.append(f"最後の動き {r['activity']}")
        out.append("   " + clip("・".join(facts), width - 3))
    if snap["lines"]:
        out.append("")
    week = " ".join(f"{WEEKDAYS[d.weekday()]}{m / 60:.1f}" if d <= now.date() else f"{WEEKDAYS[d.weekday()]}-" for d, m in week_days(now))
    out.append(f"稼働　今日 {fmt_dur(snap['today_minutes'])}・今週 {fmt_dur(snap['week_minutes'])}（時間：{week}）")
    others = _other_worktrees(lines)
    if others:
        recent = 0
        for t in others:
            act = inspect(t["path"])["activity"]
            if act and now.timestamp() - act < QUIET_MIN * 60:
                recent += 1
        tail = f"、{QUIET_MIN}分以内に動きあり{recent}本" if recent else ""
        out.append(f"ラインに入れていない worktree：{len(others)}本（上限には数えません{tail}）→ /lines all")
    return "\n".join(out)


def render_all(now=None, width=78):
    """Every worktree from `git worktree list`, one row each."""
    now = now or paths.now()
    lines = read_lines()
    open_lines = [x for x in lines if x["status"] in ACTIVE]
    out = ["git worktree list の全部"]
    for i, t in enumerate(list_worktrees()):
        info = inspect(t["path"])
        owner = next((x["name"] for x in open_lines if _same_path(x["path"], t["path"])), None)
        label = "本体" if i == 0 else (f"ライン {owner}" if owner else "ラインなし")
        act = _ago(info["activity"], now) if info["activity"] else "動き不明"
        out.append(clip(f"{t['branch'] or '（ブランチなし）'}　{label}・未コミット{info['dirty']}件・{act}", width))
    return "\n".join(out)
