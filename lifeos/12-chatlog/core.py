"""Records one line of chat and returns the one-line reply. Used by handler.py and chatlog.py."""
import os
import re

from lifeos.lib import logs, md, paths, tasks, tools

HERE = os.path.dirname(os.path.abspath(__file__))
parse = tools.load_file(HERE, "parse")
gcal = tools.load_file(HERE, "gcal")

SOURCE = "lifeos-12"
HABIT_TOOL = "07-habits"
PENDING_HABITS = "habits-pending"
LIST_MAX = 10
_SENTENCE_BREAK = re.compile(r"[。．！？!?\n]")


def habit_tool():
    """Tool 07's handler if it is there and not stopped, else None."""
    d = os.path.join(tools.root_dir(), HABIT_TOOL)
    if not os.path.exists(os.path.join(d, "handler.py")) or tools.info(d).get("status") == "stopped":
        return None
    return tools.load_file(d, "handler")


def _expense(found, text, source, is_voice):
    logs.append("expenses", {**found, "text": text, "source": source, "voice": is_voice})
    shown = parse.money(found["amount"], found["currency"])
    md.append("logs/expenses.md", f"- {paths.now().strftime('%Y-%m-%d %H:%M')} {found['item']} {shown}")
    item = "(no item)" if found["item"] == "品目なし" else found["item"]
    return f"Expense: {item} {parse.money_en(found['amount'], found['currency'])}" + (" (currency guessed)" if found["guessed"] else "")


def _deadline(found):
    title, due = found["title"], found["due"]
    if due is None:
        tasks.add(title, source=SOURCE, source_ref="chat")
        return f"Deadline: {title}, added without a date. You can set the date on the Desk."
    head = f"Deadline: {title}, {parse.when(due)}, {parse.countdown(due, paths.now())}"
    if any(t.get("title") == title and t.get("due") == due for t in tasks.open_tasks()):
        return head + " (already on the Desk)"
    t = tasks.add(title, due=due, source=SOURCE, source_ref="chat")
    return head + (" · on the calendar" if gcal.push(title, due, t["id"]) else " · calendar pending")


def _habit(text, ctx):
    tool = habit_tool()
    if tool and tool.match(text):
        return tool.handle(text, ctx)
    logs.append(PENDING_HABITS, {"text": text, "source": getattr(ctx, "source", "cli")})
    return f"Habit: kept \"{text}\" until tool 07 is running."


def deadlines():
    """Open Desk tasks that have a due date, nearest first."""
    return sorted((t for t in tasks.open_tasks() if isinstance(t.get("due"), (int, float))), key=lambda t: t["due"])


def deadline_list(now=None):
    now = now or paths.now()
    items = deadlines()
    if not items:
        return "No deadlines."
    rows = [f"{parse.when(t['due'])}, {parse.countdown(t['due'], now)}: {t['title']}" for t in items[:LIST_MAX]]
    if len(items) > LIST_MAX:
        rows.append(f"and {len(items) - LIST_MAX} more")
    return "\n".join(rows)


def takes(text):
    """Whether this tool should take the line. A habit stays with tool 07 whenever 07 is running."""
    if _SENTENCE_BREAK.search(text.strip().rstrip("。．！？!?")):
        return False  # several sentences are a spoken memo: 04 sorts them one by one
    kind, _ = parse.classify(text, paths.now())
    return kind in ("list", "deadline", "expense") or (kind == "habit" and habit_tool() is None)


def record(text, ctx):
    kind, found = parse.classify(text, paths.now())
    source, is_voice = getattr(ctx, "source", "cli"), getattr(ctx, "is_voice", False)
    if kind == "expense":
        return _expense(found, text.strip(), source, is_voice)
    if kind == "deadline":
        return _deadline(found)
    if kind == "list":
        return deadline_list()
    if kind == "habit":
        return _habit(text.strip(), ctx)
    return None
