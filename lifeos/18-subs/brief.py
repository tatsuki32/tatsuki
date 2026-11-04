"""Lines for the morning brief (01 collects them). Reads only."""
from lifeos.lib.tools import load_tool


def lines(day):
    return load_tool("18-subs", "core").brief_lines(day)
