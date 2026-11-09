"""Telegram: "task: buy milk" (or 「タスク：牛乳」) adds a task, the same as /task."""
import re

from lifeos.lib.tools import load_tool

PRIORITY = 10
_MARK = re.compile(r"^\s*(?:task|todo|タスク)\s*[:：]\s*", re.I)
# the note keeps the Japanese marks (優先度:中 案件:…); only the reply is in English
PRIORITY_EN = {"高": "high", "中": "medium", "低": "low"}


def match(text):
    return bool(_MARK.match(text or ""))


def line(core, task):
    """'Send the slides (high priority, project A)'."""
    m = core.NOTE_PROJECT.search(task.get("note") or "")
    project = m.group(1) if m and m.group(1) != core.NO_PROJECT else ""
    pri = PRIORITY_EN.get(core.priority_of(task), "medium")
    return f"{task.get('title', '')} ({pri} priority" + (f", project {project})" if project else ")")


def handle(text, ctx):
    body = _MARK.sub("", text, count=1).strip()
    if not body:
        return "There is nothing to add. Send it like this: task: buy milk"
    core = load_tool("03-task", "core")
    return "Added: " + line(core, core.add(body, source="lifeos-03"))
