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
    (0, "🌰", "Seed", "a seed is planted"),
    (10, "🌱", "Sprout", "it sprouted"),
    (30, "🌸", "Flower", "it flowered"),
    (100, "🌳", "Tree", "it grew into a tree"),
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
_EXPLICIT = re.compile(r"(?:習慣|habit)\s*:\s*(?P<body>\S.*)", re.S | re.I)
_EXPLICIT_NAME = re.compile(r"(?P<name>.+?)\s*" + _TAIL, re.I | re.S)
# English: "did workout", "did reading 30 min yesterday", "reading 30 min", "habit: walk 5 min"
_EN_DAY = r"(?:the\s+)?day\s+before\s+yesterday|yesterday|today|this\s+morning"
_EN_AMOUNT = r"\d+(?:\.\d+)?\s*(?:min|mins|minutes?|hours?|hrs?|h|times|pages?|km|miles?|sets?|books?)"
_EN = re.compile(r"(?:(?P<day1>" + _EN_DAY + r")\s*,?\s+)?(?:i\s+)?(?P<verb>did|done|finished)?\s*(?P<name>[^\d]+?)"
                 r"(?:\s+(?:for\s+)?(?P<amount>" + _EN_AMOUNT + r"))?(?:\s+(?P<day2>" + _EN_DAY + r"))?[.!~]*", re.I)
_EN_EXPLICIT = re.compile(r"(?=.*[a-z])(?P<name>.+?)\s+(?:for\s+)?(?P<amount>" + _EN_AMOUNT + r")[.!~]*", re.I | re.S)
_EN_AGO = {"yesterday": 1, "today": 0, "this morning": 0}
# an English word for a habit the Desk keeps under its Japanese name
ALIASES = {"workout": "筋トレ", "work out": "筋トレ", "strength training": "筋トレ", "reading": "読書", "read": "読書",
           "walk": "散歩", "a walk": "散歩", "walking": "ウォーキング", "running": "ランニング", "run": "ランニング",
           "a run": "ランニング", "jogging": "ジョギング", "jog": "ジョギング", "stretching": "ストレッチ", "stretch": "ストレッチ",
           "yoga": "ヨガ", "meditation": "瞑想", "meditate": "瞑想", "journal": "日記", "journaling": "日記",
           "study": "勉強", "studying": "勉強", "english": "英語", "early rise": "早起き", "woke up early": "早起き",
           "cooking": "自炊", "cooked": "自炊", "swimming": "水泳", "swim": "水泳"}
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
    rest = f" · {nxt[0] - count} more to {nxt[2]}" if nxt else ""
    return f"{emoji} {name}{rest}" if not count else f"{emoji} {name} · {count} so far{rest}"


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
        body = m.group("body").strip()
        n = _EN_EXPLICIT.fullmatch(body) or _EXPLICIT_NAME.fullmatch(body)
        name = n.group("name").strip()[:NAME_MAX] if n else ""
        amount = " ".join(n.group("amount").split()) if n and n.group("amount") else None
        return {"name": name, "amount": amount, "days_ago": 0} if name else None
    en = _parse_en(raw)
    if en:
        return en
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


def _parse_en(raw):
    """The English forms. A name must be a Desk habit or a known word, and needs a verb or an amount."""
    m = _EN.fullmatch(raw)
    if not m or not (m.group("verb") or m.group("amount")):
        return None
    word = " ".join(m.group("name").split()).lower()
    names = {_key(n): n for n in _known_names() if _key(n)}
    name = names.get(_key(word)) or ALIASES.get(word)
    if not name:
        return None
    day = " ".join((m.group("day1") or m.group("day2") or "today").lower().split())
    amount = " ".join(m.group("amount").split()) if m.group("amount") else None
    return {"name": name, "amount": amount, "days_ago": _EN_AGO.get(day, 2)}


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
    lines = ["🌿 How your habits grew this week"]
    for h in habits:
        n = len(week & set(h.get("done") or []))
        lines.append(f"{h.get('name') or 'Habit'}: {status_line(count_of(h))}" + (f" · +{n} in the last 7 days" if n else ""))
    lines.append("Everything that grew stays.")
    return "\n".join(lines)
