"""Telegram: "task: 内容" adds a task, the same as /task."""
import re

from lifeos.lib.tools import load_tool

PRIORITY = 10
_MARK = re.compile(r"^\s*(?:task|タスク)\s*[:：]\s*", re.I)


def match(text):
    return bool(_MARK.match(text or ""))


def handle(text, ctx):
    body = _MARK.sub("", text, count=1).strip()
    if not body:
        return "内容がありません。「task: 内容」と送ってください"
    core = load_tool("03-task", "core")
    return "追加しました：" + core.line(core.add(body, source="lifeos-03"))
