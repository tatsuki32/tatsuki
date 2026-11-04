"""What 02 adds to the morning brief (see lifeos/lib/tools.py brief_parts)."""
from lifeos.lib.tools import load_tool


def lines(day):
    core = load_tool("02-followup", "core")
    return core.today_lines(day) if core.config()["enabled"] else []
