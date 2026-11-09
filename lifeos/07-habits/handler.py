"""Telegram: 「筋トレした」「読書30分」 or "did workout", "reading 30 min" add one day to that habit on the Desk."""
import os

from lifeos.lib import paths, tools

PRIORITY = 30
core = tools.load_file(os.path.dirname(os.path.abspath(__file__)), "core")
WHEN = {0: "today", 1: "yesterday", 2: "the day before yesterday"}


def match(text):
    return core.parse(text) is not None


def handle(text, ctx):
    p = core.parse(text)
    when = WHEN[p["days_ago"]]
    habit, added, is_new = core.record(p["name"], core.day_before(paths.today(), p["days_ago"]))
    name, count = habit["name"], core.count_of(habit)
    now = core.status_line(count)
    if not added:
        return f"{name} is already in for {when}.\n{now}"
    what = name + (" " + p["amount"] if p["amount"] else "")
    lines = [f"Recorded {what}." if p["days_ago"] == 0 else f"Recorded {what} for {when}."]
    if is_new:
        lines.append("Added it to the Desk as a new habit.")
    grown = core.stage(count)
    if count == 1 or count == grown[0]:
        lines.append(f"{grown[1]} {name}: {grown[3]}.")
    lines.append(now)
    return "\n".join(lines)
