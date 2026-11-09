"""Routes a message to the first tool that wants it.

Each tool may have lifeos/NN-slug/handler.py with:
    PRIORITY = 50          # optional. Smaller goes first; ties go by tool number.
    def match(text): ...   # True if this tool takes the message
    def handle(text, ctx): # returns the reply text (or None for no reply)

A broken or failing tool never stops the others. A tool whose tool.json says "stopped"
is not loaded. A message no tool takes is kept in logs/unrouted.jsonl.
"""
import os
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from lifeos.lib import logs, md, paths, tasks, tools

DEFAULT_PRIORITY = 50
# the secretary bot's tools. Every other tool stays with the records bot, new ones included.
SECRETARY_TOOLS = ("04-voice", "03-task", "02-followup")
UNROUTED = "Got it. No tool takes this yet, so I kept it in the unsorted list."
EMPTY = "I could not find any text in that."


@dataclass
class Ctx:
    """What a handler gets besides the text."""
    source: str = "cli"                    # "telegram" or "cli"
    chat_id: Any = None
    is_voice: bool = False                 # the text came from speech
    send: Callable[[str], None] = print    # send an extra message before the reply
    tasks: Any = tasks
    logs: Any = logs
    md: Any = md
    paths: Any = paths
    routes: Any = None                     # this bot's tools, when a tool passes a message on


@dataclass
class Route:
    slug: str
    no: str
    name: str
    priority: int
    match: Callable[[str], Any]
    handle: Callable[[str, Ctx], Optional[str]]


@dataclass
class Found:
    routes: List[Route] = field(default_factory=list)
    errors: List[tuple] = field(default_factory=list)  # (slug, what went wrong)


def discover(root=None):
    found = Found()
    for d in tools.tool_dirs(root):
        slug = os.path.basename(d)
        if not os.path.exists(os.path.join(d, "handler.py")):
            continue
        meta = tools.info(d)
        if meta.get("status") == "stopped":
            continue
        try:
            mod = tools.load_file(d, "handler")
            if not callable(getattr(mod, "match", None)) or not callable(getattr(mod, "handle", None)):
                raise AttributeError("needs match and handle")
            priority = int(getattr(mod, "PRIORITY", DEFAULT_PRIORITY))
        except Exception as e:
            found.errors.append((slug, type(e).__name__ + ": " + str(e)))
            logs.append("bot-errors", {"tool": slug, "where": "load", "error": type(e).__name__})
            continue
        found.routes.append(Route(slug, slug[:2], meta.get("name") or slug, priority, mod.match, mod.handle))
    found.routes.sort(key=lambda r: (r.priority, r.slug))
    return found


def for_bot(routes, bot):
    """The routes one bot serves: "secretary" or "records"."""
    return [r for r in routes if (r.slug in SECRETARY_TOOLS) == (bot == "secretary")]


def which(text, routes):
    """The route that would take this text, or None. Runs no handler."""
    for r in routes:
        try:
            if r.match(text):
                return r
        except Exception as e:
            logs.append("bot-errors", {"tool": r.slug, "where": "match", "error": type(e).__name__})
    return None


def dispatch(text, ctx, routes=None):
    """Handles one message and returns the reply text ("" for no reply)."""
    text = (text or "").strip()
    if not text:
        return EMPTY
    routes = discover().routes if routes is None else routes
    r = which(text, routes)
    if r is None:
        logs.append("unrouted", {"text": text, "source": ctx.source, "voice": ctx.is_voice})
        return UNROUTED
    try:
        return r.handle(text, ctx) or ""
    except Exception as e:
        # keep the words: the person should not have to say them again
        logs.append("unrouted", {"text": text, "source": ctx.source, "voice": ctx.is_voice, "failedTool": r.slug})
        logs.append("bot-errors", {"tool": r.slug, "where": "handle", "error": type(e).__name__})
        return r.name + " hit an error. I kept your message in the unsorted list."
