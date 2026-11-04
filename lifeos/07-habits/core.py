"""Habits that are counted, never punished. The records are the Desk's `habits` collection.

A habit is {name, emoji, done: [YYYY-MM-DD, ...], createdAt, updatedAt, deleted}, the same shape as
store.putHabit in desk/index.html. The count is the number of days in done: it only goes up when
something is recorded, and no code here looks at the gaps between days.
"""
import json
import os
import re
import tempfile
import unicodedata
import urllib.parse
from datetime import date, timedelta

from lifeos.lib import paths, tasks

# (from this many days, emoji, name, what to say on arrival). Same table as PLANTS in desk/index.html
STAGES = (
    (0, "🌰", "種", "種をまきました"),
    (10, "🌱", "芽", "芽が出ました"),
    (30, "🌸", "花", "花が咲きました"),
    (100, "🌳", "木", "木になりました"),
)
# recognised before the first record, when the Desk has no habit of that name yet
STARTERS = ("筋トレ", "読書", "散歩", "ウォーキング", "ランニング", "ジョギング", "ストレッチ", "ヨガ",
            "瞑想", "日記", "勉強", "英語", "早起き", "自炊", "水泳")
NAME_MAX = 40  # the Desk's limit

_AMOUNT = r"\d+(?:\.\d+)?(?:分|時間|回|ページ|km|キロ|冊|セット)"
_VERB = r"した|しました|してきた|してきました|やった|やりました|できた|できました|終わった|終わりました|行った|行ってきた|行ってきました"
_END = r"[。.!！~〜♪\U0001F300-\U0001FAFF☀-➿️]*"
_TAIL = r"[をは]?(?P<amount>" + _AMOUNT + r")?を?(?P<verb>" + _VERB + r")?" + _END
_TAIL_RE = re.compile(_TAIL, re.I)
_EXPLICIT = re.compile(r"習慣\s*:\s*(?P<body>\S.*)", re.S)
_EXPLICIT_NAME = re.compile(r"(?P<name>.+?)\s*" + _TAIL, re.I | re.S)
_DAY = (("一昨日", 2), ("おととい", 2), ("昨日", 1), ("きのう", 1), ("今日", 0), ("きょう", 0), ("今朝", 0), ("さっき", 0))


class HabitStoreError(Exception):
    """habits.json exists but cannot be read. Nothing was written."""


def stage(count):
    return [s for s in STAGES if count >= s[0]][-1]


def next_stage(count):
    return next((s for s in STAGES if count < s[0]), None)


def status_line(count):
    _, emoji, name, _ = stage(count)
    nxt = next_stage(count)
    rest = f"（{nxt[2]}まであと{nxt[0] - count}回）" if nxt else ""
    return f"{emoji} {name}{rest}" if not count else f"{emoji} {name} · これまで {count}回{rest}"


def count_of(habit):
    return len(set(habit.get("done") or []))


def _file():
    return os.path.join(paths.desk_data_dir(), "habits.json")


def _read_file():
    try:
        with open(_file(), encoding="utf-8") as f:
            v = json.load(f)
    except FileNotFoundError:
        return []
    except ValueError as e:
        raise HabitStoreError("habits.json を読めません") from e
    if not isinstance(v, list):
        raise HabitStoreError("habits.json の形が違います")
    return [x for x in v if isinstance(x, dict) and x.get("id")]


def _write_file(rec):
    with tasks._locked():
        items = _read_file()
        for i, x in enumerate(items):
            if x["id"] == rec["id"]:
                items[i] = rec
                break
        else:
            items.append(rec)
        fd, tmp = tempfile.mkstemp(dir=paths.desk_data_dir(), prefix=".habits", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _file())


def _put(rec):
    if tasks._server_up():
        try:
            body = {k: v for k, v in rec.items() if k != "id"}
            tasks._call("PUT", "/api/habits/" + urllib.parse.quote(rec["id"], safe=""), body)
            return
        except (OSError, ValueError):
            pass  # the server went away: fall through to the file
    _write_file(rec)


def all_habits(include_deleted=False):
    items = None
    if tasks._server_up():
        try:
            items = tasks._call("GET", "/api/habits")
        except (OSError, ValueError):
            pass
    if items is None:
        items = _read_file()
    return [h for h in items if include_deleted or not h.get("deleted")]


def _key(name):
    return "".join(unicodedata.normalize("NFKC", str(name)).split()).lower()


def _known_names():
    """Names of the habits in use, read from the file only: match() must stay fast and offline."""
    try:
        return [h.get("name") or "" for h in _read_file() if not h.get("deleted")]
    except (OSError, HabitStoreError):
        return []


def parse(text):
    """{name, amount, days_ago} if the whole text reports a habit as done, else None.

    The whole text must fit, so 「筋トレしたい」 and 「読書30分したら連絡する」 stay out.
    """
    raw = unicodedata.normalize("NFKC", text or "").strip()
    m = _EXPLICIT.fullmatch(raw)
    if m:
        n = _EXPLICIT_NAME.fullmatch(m.group("body").strip())
        name = n.group("name").strip()[:NAME_MAX] if n else ""
        return {"name": name, "amount": n.group("amount"), "days_ago": 0} if name else None
    t = "".join(raw.split())
    days_ago = 0
    for word, ago in _DAY:
        if t.startswith(word):
            t, days_ago = t[len(word):].lstrip("はも、,"), ago
            break
    low = t.lower()
    known = [(n, True) for n in _known_names() if _key(n)] + [(n, False) for n in STARTERS]
    for name, is_known in sorted(known, key=lambda x: -len(_key(x[0]))):
        k = _key(name)
        if not low.startswith(k):
            continue
        tail = _TAIL_RE.fullmatch(t[len(k):])
        # a starter word alone (「読書」) says nothing about doing it, so it needs a verb or an amount
        if tail and (is_known or tail.group("amount") or tail.group("verb")):
            return {"name": name, "amount": tail.group("amount"), "days_ago": days_ago}
    return None


def _new_id():
    return "h" + format(paths.now_ms(), "x") + os.urandom(2).hex()


def record(name, day=None):
    """Marks one day as done. Returns (habit, added, is_new); recording a day twice counts once.

    A habit stopped on the Desk comes back with its past days still counted.
    """
    day = day or paths.today()
    k = _key(name)
    same = [h for h in all_habits(include_deleted=True) if _key(h.get("name") or "") == k]
    same.sort(key=lambda h: bool(h.get("deleted")))
    cur = same[0] if same else None
    is_new = cur is None
    if is_new:
        cur = {"id": _new_id(), "name": name, "emoji": "🌱", "done": [], "createdAt": paths.now_ms()}
    done = set(cur.get("done") or [])
    added = day not in done
    if not added and not cur.get("deleted"):
        return cur, False, False
    rec = {**cur, "done": sorted(done | {day}), "deleted": False, "updatedAt": paths.now_ms()}
    _put(rec)
    return rec, added, is_new


def day_before(day, n):
    return (date.fromisoformat(day) - timedelta(days=n)).isoformat()


def week_report(day=None):
    """The weekly message, or None when there is no habit to show. Only counts what was done."""
    day = day or paths.today()
    week = {day_before(day, i) for i in range(7)}
    habits = sorted(all_habits(), key=lambda h: h.get("createdAt") or 0)
    if not habits:
        return None
    lines = ["🌿 今週の育ち具合"]
    for h in habits:
        n = len(week & set(h.get("done") or []))
        lines.append(f"{h.get('name') or '習慣'}：{status_line(count_of(h))}" + (f" · この7日で +{n}" if n else ""))
    lines.append("育った分はこのまま残ります。")
    return "\n".join(lines)
