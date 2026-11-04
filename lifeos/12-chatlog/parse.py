"""Reads one line of chat. Pure: no files, no clock (now is passed in).

classify(text, now) -> ("list", None) | ("deadline", {...}) | ("expense", {...}) | ("habit", {...}) | (None, None)
A deadline wins over an expense: "家賃 500万ドン 明日 締切" is a deadline.
Anything unsure returns None, so the line goes on to the tools after this one.
"""
import calendar
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

HCMC = timezone(timedelta(hours=7))
MAX_LEN = 60
WEEK = "月火水木金土日"
END_OF_DAY = (23, 59)  # same as the Desk's daily tasks

# these belong to other tools (17 takes brain dumps after this one in the routing order)
_NOT_OURS = re.compile(r"^(?:メモ|ブレインダンプ|ダンプ|吐き出し|task|タスク)", re.I)

# ---------- amounts ----------
_KANJI = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_KUNIT = {"十": 10, "百": 100, "千": 1000}
_AMT = (r"(?<![0-9.,])(?:[0-9][0-9,]*(?:\.[0-9]+)?|[〇一二三四五六七八九十百千]+)"
        r"(?:万(?:[0-9]+千?|[〇一二三四五六七八九十百千]+)?|千|k)?")
_VND = r"ドン|VND|dong|đ|₫"
_JPY = r"円|JPY|yen"
_PAID_WORDS = r"(?:を|も)?(?:払った|使った|支払った|かかった|でした|だった|支払い)"
_AFTER = re.compile(r"^(?:$|\s|" + _PAID_WORDS + ")")
_WITH_UNIT = re.compile(r"(?P<amt>" + _AMT + r")\s*(?P<unit>" + _VND + "|" + _JPY + r")", re.I)
_SYMBOL = re.compile(r"(?P<unit>¥|₫|VND|JPY)\s*(?P<amt>" + _AMT + ")", re.I)
_BARE_END = re.compile(r"(?:^|\s)(?P<amt>" + _AMT + r")$", re.I)
_BARE_START = re.compile(r"^(?P<amt>" + _AMT + r")\s", re.I)
# money coming in, or money only talked about
_NOT_SPENT = re.compile(r"もらっ|もらう|入金|収入|給料|報酬|売上|売った|振り込まれ|請求|見積|予算|目標|貯金|返金")
# a number with no unit counts as money only next to one of these
_SPEND = re.compile(r"ランチ|朝食|昼|夕食|夜ご飯|飯|ごはん|カフェ|コーヒー|タクシー|grab|バス|電車|買い物|スーパー"
                    r"|コンビニ|家賃|電気|水道|ジム|散髪|飲み|[代費]$", re.I)
_PAID = re.compile(_PAID_WORDS)
_EDGE = r"[\s、,。.:・/\-]+"


def _small(s):
    if not s:
        return 0
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):  # Vietnamese style: 30.000đ
        return int(s.replace(".", ""))
    if s[0].isdigit():
        scale = 1000 if s[-1] in "千kK" else 1
        return float(s.rstrip("千kK").replace(",", "")) * scale
    total = cur = 0
    for c in s:
        if c in _KANJI:
            cur = _KANJI[c]
        elif c in _KUNIT:
            total += (cur or 1) * _KUNIT[c]
            cur = 0
    return total + cur


def amount_value(s):
    """"3万5千" -> 35000, "1,500" -> 1500, "50k" -> 50000, "三千" -> 3000."""
    left, man, right = s.partition("万")
    return int(round(_small(left) * 10000 + _small(right))) if man else int(round(_small(left)))


def _clean_item(s):
    s = _PAID.sub(" ", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub("^" + _EDGE + "|" + _EDGE + "$", "", s)
    return re.sub(r"(?:に|で|を|は)$", "", s).strip()


def parse_expense(text):
    """{item, amount, currency, guessed}, or None. text must be normalized."""
    if _NOT_SPENT.search(text):
        return None
    m = _SYMBOL.search(text)
    if not m:
        m = next((x for x in _WITH_UNIT.finditer(text) if _AFTER.match(text[x.end():])), None)
    if m:
        rest = text[:m.start()] + " " + text[m.end():]
        if _WITH_UNIT.search(rest) or _SYMBOL.search(rest):  # two amounts: not one record
            return None
        currency = "JPY" if m.group("unit").lower() in ("円", "jpy", "yen", "¥") else "VND"
        guessed = False
    else:
        m = _BARE_END.search(text) or _BARE_START.search(text)
        if not m:
            return None
        rest = text[:m.start()] + " " + text[m.end():]
        if not _SPEND.search(_clean_item(rest)):
            return None
        currency, guessed = None, True
    value = amount_value(m.group("amt"))
    if value <= 0:
        return None
    if currency is None:  # living costs in dong run to five digits and up
        currency = "VND" if value >= 10000 else "JPY"
    return {"item": _clean_item(rest) or "品目なし", "amount": value, "currency": currency, "guessed": guessed}


def money(amount, currency):
    return f"{amount:,}" + ("ドン" if currency == "VND" else "円")


# ---------- deadlines ----------
_WORD = r"(?:締め?切り?|〆切り?|しめきり|期限|期日|納期|deadline)日?"
_LIST = re.compile(r"^(?:締め?切り?|〆切り?|しめきり|期限)(?:一覧|リスト|確認|を見る|は\??)?$", re.I)
# the word must stand at either end of what remains, so "締切を延ばす" is not a deadline
_WORD_END = re.compile(_WORD + r"(?:は|まで(?:に)?|です|だ)?$", re.I)
_WORD_START = re.compile("^" + _WORD + r"(?:は|:|\s|$)", re.I)
_TIME = re.compile(r"(?:(午前|午後)\s*)?(?<![0-9])(\d{1,2})時(?:(半)|(\d{1,2})分)?|(?<![0-9:/])(\d{1,2}):(\d{2})(?![0-9])")
_TITLE_EDGE = r"(?:\s|、|,|。|:|・|の|は|が|を|まで(?:に)?|です|だ)+"
_DATES = [
    ("ymd", re.compile(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})|(\d{4})年(\d{1,2})月(\d{1,2})日")),
    ("md", re.compile(r"(?<![0-9/])(\d{1,2})月(\d{1,2})日|(?<![0-9/])(\d{1,2})/(\d{1,2})(?![0-9/])")),
    ("after", re.compile(r"(\d{1,3})(日|週間)後")),
    ("word", re.compile(r"しあさって|明明後日|明後日|あさって|今日中?|本日中?|きょう|明日中?|あした|あす")),
    ("week", re.compile(r"(今週|来週|再来週)?の?([" + WEEK + r"])曜日?")),
    ("eom", re.compile(r"今月末|来月末|月末")),
    ("d", re.compile(r"(?<![0-9/月])(\d{1,2})日(?!後)")),
]
_WORDS = {"しあさって": 3, "明明後日": 3, "明後日": 2, "あさって": 2, "今日": 0, "本日": 0, "きょう": 0,
          "明日": 1, "あした": 1, "あす": 1}


def _month_end(y, m):
    return date(y, m, calendar.monthrange(y, m)[1])


def _next_month(y, m):
    return (y + 1, 1) if m == 12 else (y, m + 1)


def _resolve(kind, m, today):
    """The date a match means, or None if there is no such date."""
    g = m.groups()
    try:
        if kind == "ymd":
            y, mo, d = (g[0], g[1], g[2]) if g[0] else (g[3], g[4], g[5])
            return date(int(y), int(mo), int(d))
        if kind == "md":
            mo, d = (g[0], g[1]) if g[0] else (g[2], g[3])
            out = date(today.year, int(mo), int(d))
            return out if out >= today else date(today.year + 1, int(mo), int(d))
        if kind == "after":
            return today + timedelta(days=int(g[0]) * (7 if g[1] == "週間" else 1))
        if kind == "word":
            return today + timedelta(days=_WORDS[m.group().rstrip("中")])
        if kind == "week":
            wd = WEEK.index(g[1])
            monday = today - timedelta(days=today.weekday())
            if g[0]:
                return monday + timedelta(days=wd + {"今週": 0, "来週": 7, "再来週": 14}[g[0]])
            return today + timedelta(days=(wd - today.weekday()) % 7)
        if kind == "eom":
            if m.group() == "来月末":
                return _month_end(*_next_month(today.year, today.month))
            return _month_end(today.year, today.month)
        if kind == "d":
            y, mo = today.year, today.month
            for _ in range(13):  # the next month that has this day
                if int(g[0]) <= calendar.monthrange(y, mo)[1]:
                    out = date(y, mo, int(g[0]))
                    if out >= today:
                        return out
                y, mo = _next_month(y, mo)
    except ValueError:
        pass
    return None


def _time_of(m):
    if m.group(5):
        h, mi = int(m.group(5)), int(m.group(6))
    else:
        h, mi = int(m.group(2)), 30 if m.group(3) else int(m.group(4) or 0)
        if m.group(1) == "午後" and h < 12:
            h += 12
        if m.group(1) == "午前" and h == 12:
            h = 0
    return (h, mi) if h < 24 and mi < 60 else None


def parse_deadline(text, now):
    """{title, due} with due in epoch ms or None if no date could be read; None if not a deadline."""
    today = now.date()
    rest, clock = text, None
    t = _TIME.search(rest)
    if t and _time_of(t):
        clock = _time_of(t)
        rest = rest[:t.start()] + " " + rest[t.end():]
    day = None
    for kind, rx in _DATES:
        m = rx.search(rest)
        if m:
            day = _resolve(kind, m, today)
            if day:
                rest = rest[:m.start()] + " " + rest[m.end():]
                break
    rest = " ".join(rest.split())
    hit = _WORD_END.search(rest) or _WORD_START.search(rest)
    if not hit:
        return None
    title = rest[:hit.start()] + " " + rest[hit.end():]
    title = re.sub("^" + _TITLE_EDGE + "|" + _TITLE_EDGE + "$", "", " ".join(title.split()))
    if not title:
        return None
    if not day and clock:
        day = today if clock > (now.hour, now.minute) else today + timedelta(days=1)
    if not day:
        return {"title": title, "due": None}
    h, mi = clock or END_OF_DAY
    return {"title": title, "due": int(datetime(day.year, day.month, day.day, h, mi, tzinfo=HCMC).timestamp() * 1000)}


def countdown(due_ms, now):
    """Whole days by the calendar: あと3日 / 今日まで / 2日過ぎ."""
    left = (datetime.fromtimestamp(due_ms / 1000, HCMC).date() - now.date()).days
    return f"あと{left}日" if left > 0 else "今日まで" if left == 0 else f"{-left}日過ぎ"


def when(due_ms):
    d = datetime.fromtimestamp(due_ms / 1000, HCMC)
    day = f"{d.month}/{d.day}({WEEK[d.weekday()]})"
    return day if (d.hour, d.minute) == END_OF_DAY else day + f" {d.hour}:{d.minute:02d}"


# ---------- habits (these go to tool 07) ----------
_HABIT = re.compile(r"^(?P<name>[^\s0-9][^0-9]{0,14}?)\s*(?P<n>\d+(?:\.\d+)?)\s*(?P<unit>分|時間|回|ページ|km|キロ|歩)$", re.I)


def parse_habit(text):
    m = _HABIT.match(text)
    return {"name": m.group("name"), "n": float(m.group("n")), "unit": m.group("unit")} if m else None


def classify(text, now):
    s = unicodedata.normalize("NFKC", text or "").strip()
    if not s or "\n" in s or len(s) > MAX_LEN or _NOT_OURS.match(s):
        return None, None
    if _LIST.match(s):
        return "list", None
    if "?" in s:
        return None, None
    found = parse_deadline(s, now)
    if found:
        return "deadline", found
    found = parse_expense(s)
    if found:
        return "expense", found
    found = parse_habit(s)
    return ("habit", found) if found else (None, None)
