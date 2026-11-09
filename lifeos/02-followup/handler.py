"""Telegram answers to the 14-day question: "nudge send 3" / "nudge hold 3" / "nudge drop 3" / "nudge undo 3" /
"nudge list", or the Japanese 催促 送る 3 / 催促 保留 3 / 催促 捨てる 3 / 催促 戻す 3 / 催促 一覧."""
import re

from lifeos.lib.tools import load_tool

PRIORITY = 10
_PAT = re.compile(r"^催促[\s　:：]*(送る|保留|捨てる|戻す|一覧)[\s　]*(?:(\d+)[\s　]*番?)?$")
_PAT_EN = re.compile(r"^nudge\s*:?\s+(send|hold|drop|undo|list)(?:\s+(?:no\.?\s*|#)?(\d+))?[.]?$", re.I)
# core.answer and the Desk keep the Japanese choice words
CHOICE = {"send": "送る", "hold": "保留", "drop": "捨てる", "undo": "戻す", "list": "一覧"}
WORD = {v: k for k, v in CHOICE.items()}


def _parse(text):
    """(choice in Japanese, number or None), or None."""
    t = text.strip()
    m = _PAT.match(t)
    if m:
        return m.groups()
    m = _PAT_EN.match(t)
    return (CHOICE[m.group(1).lower()], m.group(2)) if m else None


def match(text):
    return _parse(text) is not None


def handle(text, ctx):
    core = load_tool("02-followup", "core")
    choice, no = _parse(text)
    if choice == "一覧":
        items = core.open_items()
        if not items:
            return "Nothing is waiting for a reply."
        today = ctx.paths.today()
        return "\n".join(f"{i['no']}. {i['subject']} ({core._days(core.age_days(i, today))})" for i in items)
    if no is None:
        items = core.asking()
        if len(items) != 1:
            return f'Please add the number, like "nudge {WORD[choice]} 3". "nudge list" shows the numbers.'
        no = items[0]["no"]
    return core.answer(int(no), choice)
