"""Google Calendar, swappable. Nothing is connected yet: every deadline waits in a queue.

Connect one with set_writer(fn), or LIFEOS_CALENDAR_WRITER="package.module:function".
fn(title, due_ms, ref) writes one event and raises if it could not; ref is the Desk task id.
Without a writer, a scheduled Claude task can empty the queue instead: see INTEGRATION.md.
Personal titles stay in the data folder (logs/chatlog-calendar*.jsonl).
"""
import importlib
import os

from lifeos.lib import logs

QUEUE = "chatlog-calendar"
DONE = "chatlog-calendar-done"
_fn = None


def set_writer(fn):
    global _fn
    _fn = fn


def _current():
    if _fn:
        return _fn
    spec = os.environ.get("LIFEOS_CALENDAR_WRITER")
    if spec and ":" in spec:
        module, func = spec.split(":", 1)
        return getattr(importlib.import_module(module), func)
    return None


def connected():
    return _current() is not None


def pending():
    done = {r.get("ref") for r in logs.read(DONE)}
    return [r for r in logs.read(QUEUE) if r.get("ref") not in done]


def mark_done(ref):
    logs.append(DONE, {"ref": ref})


def flush():
    """Writes what is waiting, oldest first, and returns the refs written. Stops at the first failure."""
    fn = _current()
    written = []
    if not fn:
        return written
    for r in pending():
        try:
            fn(r["title"], r["due"], r["ref"])
        except Exception as e:
            logs.append("bot-errors", {"tool": "12-chatlog", "where": "calendar", "error": type(e).__name__})
            break
        mark_done(r["ref"])
        written.append(r["ref"])
    return written


def push(title, due_ms, ref):
    """Queues first, so a crash loses nothing. True if the event reached the calendar now."""
    logs.append(QUEUE, {"ref": ref, "title": title, "due": due_ms})
    return ref in flush()
