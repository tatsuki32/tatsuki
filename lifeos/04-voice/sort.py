"""Sorts a memo into task / idea / diary / expense / event by rules. Reads and writes nothing.

Speech is messy, so a message is cut into sentences and each is judged alone. A sentence with
no clue joins the one before it. If nothing in the message gives a clue the memo is marked
unsure and kept as an idea, which is the kind that does the least harm when wrong.
"""
import re
from collections import namedtuple
from datetime import datetime, timedelta

# title, at (ISO time) and timed are filled by the language model, or by core.py from the rules
Part = namedtuple("Part", "kind text unsure title at timed minutes", defaults=("", "", False, 0))

LABELS = {"task": "タスク", "event": "予定", "expense": "支出", "idea": "アイデア", "diary": "日記"}

# 「日記：…」: the person said what it is, so the whole message follows it
_PREFIXES = {"タスク": "task", "やること": "task", "todo": "task", "アイデア": "idea", "アイディア": "idea",
             "ひらめき": "idea", "日記": "diary", "支出": "expense", "出費": "expense", "予定": "event"}
_PREFIX = re.compile(r"^\s*(" + "|".join(_PREFIXES) + r")\s*(?:[:：、,]|\s)\s*(.+)$", re.I | re.S)

_SPLIT = re.compile(r"[。．！？!?\n]+")
_PAST_WORD = re.compile(r"昨日|きのう|一昨日|おととい|先週|先月|去年|さっき")

# a number in digits, kanji or both: 3万 / 三千五百 / 1,500 / 2.5万
_NUM = r"[0-9０-９〇一二三四五六七八九十百千万][0-9０-９〇一二三四五六七八九十百千万,.]*"
_AMOUNT = re.compile(r"(" + _NUM + r")\s*(k|K)?\s*(円|ドン|vnd|VND|đ|ドル)|([$＄])\s*(" + _NUM + ")")
_CURRENCY = {"円": "JPY", "ドン": "VND", "vnd": "VND", "VND": "VND", "đ": "VND", "ドル": "USD"}
_NOT_PAID_YET = re.compile(r"払う|払わ|買う|買わ|買いたい|振り込|返す|しないと|しなきゃ|予定|つもり|までに|欲しい")

_EVENT_WORD = re.compile(r"会議|打ち合わせ|打合せ|ミーティング|面談|面接|予約|約束|病院|歯医者|美容院|飲み会|食事会"
                         r"|集合|待ち合わせ|会う|出張|旅行|フライト|試験|授業|レッスン|イベント")
_FOR_EVENT = re.compile(r"(会議|打ち合わせ|打合せ|ミーティング|面談|面接|旅行|出張|試験|授業|レッスン|イベント)の(資料|準備|予約)")
_DEADLINE = re.compile(r"までに|まで$|締切|締め切り|〆切|しないと|しなきゃ|しなければ")

_IDEA = re.compile(r"アイデア|アイディア|思いつ|思い付|ひらめ|どうだろう|どうかな|いいかも|良いかも|面白いかも|おもしろいかも"
                   r"|たらいい|たら良い|たら面白|たらおもしろ|してみたい|やってみたい|作ってみ|試してみ|たい$")
_PAST_END = re.compile(r"((?<!あし)(?<!ま)た|[んい]だ|だった|ました|でした)$")
_TASK = re.compile(r"しないと|しなきゃ|しなければ|やらないと|やらなきゃ|忘れずに|忘れないように|やること|締切|締め切り|〆切|までに"
                   r"|必要がある|ておく$|とく$|ないと$|なきゃ$|なくちゃ$"
                   r"|(買う|送る|出す|行く|やる|する|払う|返す|作る|書く|読む|調べる|直す|洗う|頼む|聞く|申し込む|片付ける|決める)(こと)?$"
                   r"|(ます)$")
_NOT_TASK = re.compile(r"(気がする|感じがする|がする|思います|感じます|あります|います)$")
_FEELING = re.compile(r"疲れ|つかれ|眠い|ねむい|嬉し|うれし|楽し|たのし|悲し|かなし|つらい|辛い|しんどい|不安|気分|体調|感謝|寂し|さみし|イライラ|頭痛")


def number(s):
    """A number written in digits, kanji or both, as a float. None if it is not one."""
    s = s.translate(str.maketrans("０１２３４５６７８９〇一二三四五六七八九", "0123456789" + "0123456789")).replace(",", "")
    left, unit, rest = s.partition("万")
    try:
        if not unit:
            return _section(s)
        return (_section(left) if left else 1) * 10000 + (_section(rest) if rest else 0)
    except ValueError:
        return None


def _section(s):
    """Below 10000: 1500 / 2.5 / 5千 / 5百2十."""
    if not re.search("[千百十]", s):
        return float(s)
    total = 0.0
    for unit, size in (("千", 1000), ("百", 100), ("十", 10)):
        if unit in s:
            left, s = s.split(unit, 1)
            total += (float(left) if left else 1) * size
    return total + (float(s) if s else 0)


def amount(text):
    """(number, currency) of the first amount of money in the text, or None."""
    m = _AMOUNT.search(text)
    if not m:
        return None
    if m.group(4):
        v, cur = number(m.group(5)), "USD"
    else:
        v, cur = number(m.group(1)), _CURRENCY[m.group(3)]
        if v is not None and m.group(2):
            v *= 1000
    if v is None:
        return None
    return (int(v) if v == int(v) else v), cur


_WEEKDAYS = "月火水木金土日"
_DIGITS = re.compile(r"[〇一二三四五六七八九十]+(?=時|月|日|分)")
_LATER = re.compile(r"(\d+)\s*(分|時間半|時間)後")
JAPAN_TIME = "日本時間"
_PLAN = re.compile(r"明日|あした|明後日|あさって|来週|再来週|今夜|今晩|\d+\s*(分|時間半?)後|日本時間|カレンダー|予定|予約"
                   r"|\d{1,2}\s*時(半|\d{1,2}\s*分)?\s*(に|から|まで)|\d{1,2}:\d{2}\s*(に|から)|[月火水木金土日]曜")


def _plain(text):
    s = _DIGITS.sub(lambda m: str(int(number(m.group()))), text)
    return s.translate(str.maketrans("０１２３４５６７８９：／", "0123456789:/"))


def looks_like_plan(text):
    """Whether any sentence talks about a time still to come. Other tools use this to leave a plan to this tool."""
    for sentence in _SPLIT.split(_plain(text)):
        s = sentence.strip(" 　、,")
        if s and not _PAST_END.search(s) and not _PAST_WORD.search(s) and _PLAN.search(s):
            return True
    return False


def when(text, now):
    """(datetime, has_clock_time) said in the text, or (None, False). Past words give nothing."""
    if _PAST_WORD.search(text):
        return None, False
    s = _plain(text)
    m = _LATER.search(s)
    if m:
        amount = int(m.group(1)) * (60 if m.group(2).startswith("時間") else 1) + (30 if m.group(2).endswith("半") else 0)
        return (now + timedelta(minutes=amount)).replace(second=0, microsecond=0), True
    day = _day(s, now)
    clock = _clock(s)
    if day is None and clock is None:
        return None, False
    if clock is None:
        return day.replace(hour=23, minute=59, second=0, microsecond=0), False  # as the Desk does
    base = day or now
    at = base.replace(hour=clock[0] % 24, minute=clock[1], second=0, microsecond=0)
    if clock[0] == 24:
        at += timedelta(days=1)
    if JAPAN_TIME in s:
        at -= timedelta(hours=2)  # Japan is always two hours ahead of Ho Chi Minh City
    if day is None and at <= now:
        at += timedelta(days=1)
    return at, True


def _day(s, now):
    m = re.search(r"(\d+)\s*日後", s)
    if m:
        return now + timedelta(days=int(m.group(1)))
    for words, delta in ((("明後日", "あさって"), 2), (("明日", "あした", "あす"), 1), (("今日", "本日", "今夜", "今晩"), 0)):
        if any(w in s for w in words):
            return now + timedelta(days=delta)
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日|(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])", s)
    if m:
        month, date = (int(x) for x in (m.group(1, 2) if m.group(1) else m.group(3, 4)))
        try:
            d = now.replace(month=month, day=date)
            return d.replace(year=now.year + 1) if d.date() < now.date() else d
        except ValueError:
            return None
    m = re.search(r"([" + _WEEKDAYS + r"])曜", s)
    if m:
        wd = _WEEKDAYS.index(m.group(1))
        if "来週" in s:
            return now + timedelta(days=7 - now.weekday() + wd)
        return now + timedelta(days=(wd - now.weekday()) % 7 or 7)
    if "来週" in s:
        return now + timedelta(days=7)
    return None


def _clock(s):
    m = re.search(r"(午前|午後)?\s*(\d{1,2})\s*時(?!間)\s*(半|(\d{1,2})\s*分)?|(?<![\d:/])(\d{1,2}):(\d{2})(?![\d:])", s)
    if not m:
        return None
    if m.group(5):
        hour, minute = int(m.group(5)), int(m.group(6))
    else:
        hour = int(m.group(2))
        minute = 30 if m.group(3) == "半" else int(m.group(4) or 0)
        if m.group(1) == "午後" and hour < 12:
            hour += 12
    return (hour, minute) if hour <= 24 and minute < 60 and not (hour == 24 and minute) else None


def judge(sentence, now):
    """The kind of one sentence, or None when it gives no clue."""
    s = sentence.strip(" 　、,")
    if amount(s) and not _NOT_PAID_YET.search(s):
        return "expense"
    at, timed = when(s, now)
    if at and not _PAST_END.search(s) and not _DEADLINE.search(s) and not _FOR_EVENT.search(s) and (timed or _EVENT_WORD.search(s)):
        return "event"
    if _IDEA.search(s):
        return "idea"
    if _PAST_END.search(s):
        return "diary"
    if _TASK.search(s) and not _NOT_TASK.search(s):
        return "task"
    if _FEELING.search(s):
        return "diary"
    return None


# several tasks are several things to do; several diary sentences are one entry
_JOIN_SAME = ("idea", "diary")


def names_its_kind(text):
    return bool(_PREFIX.match(text.strip()))


def split(text, now=None):
    """The memo as a list of Part."""
    now = now or datetime.now()
    text = text.strip()
    m = _PREFIX.match(text)
    if m:
        return [Part(_PREFIXES[m.group(1).lower()], m.group(2).strip(), False)]
    parts = []  # [kind, text]
    lead = ""   # sentences with no clue, before the first one that has a clue
    for sentence in (x.strip() for x in _SPLIT.split(text)):
        if not sentence:
            continue
        kind = judge(sentence, now)
        if kind is None and parts:
            parts[-1][1] += "。" + sentence
        elif kind is None:
            lead += sentence + "。"
        elif parts and kind == parts[-1][0] and kind in _JOIN_SAME:
            parts[-1][1] += "。" + sentence
        else:
            parts.append([kind, lead + sentence])
            lead = ""
    if not parts:
        return [Part("idea", text, True)]
    return [Part(k, t, False) for k, t in parts]
