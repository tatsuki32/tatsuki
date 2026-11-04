"""Morning brief: the three nearest deadlines within a week."""
import os
from datetime import datetime, timedelta

from lifeos.lib import tools

_core = tools.load_file(os.path.dirname(os.path.abspath(__file__)), "core")
SHOW = 3
WITHIN_DAYS = 7


def lines(day):
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=_core.parse.HCMC)
    limit = (start + timedelta(days=WITHIN_DAYS + 1)).timestamp() * 1000
    soon = [t for t in _core.deadlines() if t["due"] < limit]
    return [f"締切：{t['title']} {_core.parse.countdown(t['due'], start)}" for t in soon[:SHOW]]
