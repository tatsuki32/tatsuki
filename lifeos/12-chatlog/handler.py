"""Telegram: "ランチ 3万ドン" is an expense, "明日 資料 締切" a deadline, "締切" lists them."""
import os

from lifeos.lib import tools

PRIORITY = 40
_core = tools.load_file(os.path.dirname(os.path.abspath(__file__)), "core")


def match(text):
    return _core.takes(text)


def handle(text, ctx):
    return _core.record(text, ctx)
