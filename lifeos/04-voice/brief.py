"""Morning brief: yesterday's memos, at most 10 lines."""
import os

from lifeos.lib import tools

core = tools.load_file(os.path.dirname(os.path.abspath(__file__)), "core")


def lines(day):
    return core.summary(day)
