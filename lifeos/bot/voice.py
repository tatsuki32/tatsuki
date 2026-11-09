"""Speech to text, swappable. Groq's Whisper (lifeos/04-voice/transcribe.py) once its key is saved.

Connect another with set_transcriber(fn), or LIFEOS_TRANSCRIBER="package.module:function".
The function takes an audio file path and returns the text.
"""
import importlib
import os

from lifeos.lib import tools

NOT_CONNECTED = "Transcription is not connected yet."
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
    return _groq()


def _groq():
    """Read on every call, so saving the key is enough: the bot need not restart."""
    try:
        mod = tools.load_tool("04-voice", "transcribe")
    except (OSError, ImportError):
        return None
    return mod.transcribe if mod.key() else None


def connected():
    return _current() is not None


def transcribe(path):
    """The text, or None when no transcriber is connected."""
    fn = _current()
    return fn(path) if fn else None
