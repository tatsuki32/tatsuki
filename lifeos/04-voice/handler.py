"""Telegram: takes whatever no other tool took, and replies with where it was filed."""
import os

from lifeos.lib import tools

PRIORITY = 90

core = tools.load_file(os.path.dirname(os.path.abspath(__file__)), "core")


def match(text):
    t = text.strip()
    return bool(t) and not t.startswith("/")


def handle(text, ctx):
    return core.take(text, ctx.is_voice)
