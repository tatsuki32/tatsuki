"""Speech to text, swappable. Nothing is connected yet: the method is still undecided.

Connect one with set_transcriber(fn), or LIFEOS_TRANSCRIBER="package.module:function".
The function takes an audio file path and returns the text.
"""
import importlib
import os

NOT_CONNECTED = "文字起こし未接続"
_fn = None


def set_transcriber(fn):
    global _fn
    _fn = fn


def _current():
    if _fn:
        return _fn
    spec = os.environ.get("LIFEOS_TRANSCRIBER")
    if spec and ":" in spec:
        module, func = spec.split(":", 1)
        return getattr(importlib.import_module(module), func)
    return None


def connected():
    return _current() is not None


def transcribe(path):
    """The text, or None when no transcriber is connected."""
    fn = _current()
    return fn(path) if fn else None
