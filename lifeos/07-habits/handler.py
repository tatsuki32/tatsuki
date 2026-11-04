"""Telegram: 「筋トレした」「読書30分」 add one day to that habit on the Desk."""
import os

from lifeos.lib import paths, tools

PRIORITY = 30
core = tools.load_file(os.path.dirname(os.path.abspath(__file__)), "core")
WHEN = {0: "今日", 1: "昨日", 2: "一昨日"}


def match(text):
    return core.parse(text) is not None


def handle(text, ctx):
    p = core.parse(text)
    when = WHEN[p["days_ago"]]
    habit, added, is_new = core.record(p["name"], core.day_before(paths.today(), p["days_ago"]))
    name, count = habit["name"], core.count_of(habit)
    now = core.status_line(count)
    if not added:
        return f"{when}の{name}は、もう入っています。\n{now}"
    what = name + (" " + p["amount"] if p["amount"] else "")
    lines = [f"{what}、記録しました。" if p["days_ago"] == 0 else f"{what}、{when}の分として記録しました。"]
    if is_new:
        lines.append("新しい習慣として Desk に足しました。")
    grown = core.stage(count)
    if count == 1 or count == grown[0]:
        lines.append(f"{grown[1]} {name}：{grown[3]}。")
    lines.append(now)
    return "\n".join(lines)
