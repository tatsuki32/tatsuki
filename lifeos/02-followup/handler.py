"""Telegram answers to the 14-day question: 催促 送る 3 / 催促 保留 3 / 催促 捨てる 3."""
import re

from lifeos.lib.tools import load_tool

PRIORITY = 10
_PAT = re.compile(r"^催促[\s　:：]*(送る|保留|捨てる|戻す|一覧)[\s　]*(?:(\d+)[\s　]*番?)?$")


def match(text):
    return bool(_PAT.match(text.strip()))


def handle(text, ctx):
    core = load_tool("02-followup", "core")
    choice, no = _PAT.match(text.strip()).groups()
    if choice == "一覧":
        items = core.open_items()
        if not items:
            return "返信待ちはありません。"
        today = ctx.paths.today()
        return "\n".join(f"{i['no']}番 {i['subject']}（{core.age_days(i, today)}日）" for i in items)
    if no is None:
        items = core.asking()
        if len(items) != 1:
            return "番号を付けてください。例：催促 " + choice + " 3（番号は「催促 一覧」で見られます）"
        no = items[0]["no"]
    return core.answer(int(no), choice)
