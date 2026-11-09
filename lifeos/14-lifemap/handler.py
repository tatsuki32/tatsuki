"""Takes the reply to today's question, only while that question is open. "skip" or 「パス」 closes it."""
import os

from lifeos.lib.tools import load_file

PRIORITY = 20
LAST_BEFORE_FALLBACK = 90  # 04 takes everything at 90, so it must not count as "wanting" a text
SKIP_WORDS = ("パス", "スキップ", "pass", "skip")

_core = load_file(os.path.dirname(os.path.abspath(__file__)), "core")


def _another_tool_wants(text):
    """A habit or an expense sent while a question is open still belongs to its own tool."""
    from lifeos.bot import router  # imported late: the router is what loads this file

    for r in router.discover().routes:
        if PRIORITY < r.priority < LAST_BEFORE_FALLBACK:
            try:
                if r.match(text):
                    return True
            except Exception:
                continue
    return False


def match(text):
    return bool(_core.pending()) and not text.startswith("/") and not _another_tool_wants(text)


def handle(text, ctx):
    if text.strip().lower() in SKIP_WORDS:
        _core.skip()
        return "OK. No answer needed."
    area = _core.add_answer(text, is_voice=ctx.is_voice)
    if not area:
        return None
    return f"Saved it in your {_core.AREAS_EN[area]} notes."
