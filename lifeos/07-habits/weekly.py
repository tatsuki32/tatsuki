#!/usr/bin/env python3
"""The weekly plant report.  python3 lifeos/07-habits/weekly.py [--preview] [--force]

Sends at most once per calendar week, so a repeated or caught-up schedule does not send twice.
--preview only prints. A tool.json status of "stopped" sends nothing.
"""
import argparse
import json
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from lifeos.bot import telegram  # noqa: E402
from lifeos.lib import paths, tools  # noqa: E402

core = tools.load_file(HERE, "core")
MESSAGES = {"stopped": "止めてあるので送りません。", "already": "今週の分は送ってあります。",
            "empty": "習慣がまだないので送りません。", "sent": "送りました。",
            "outbox": "Telegram につながらないので logs/outbox.jsonl に残しました。"}


def _state():
    return paths.data_path("tools", "07-habits", "weekly.json")


def _week_of(day):
    y, w, _ = date.fromisoformat(day).isocalendar()
    return f"{y}-W{w:02d}"


def _last_week():
    try:
        with open(_state(), encoding="utf-8") as f:
            return json.load(f).get("week")
    except (OSError, ValueError, AttributeError):
        return None


def run(day=None, force=False, root=HERE):
    """Returns a key of MESSAGES. send goes through telegram.notify, which keeps unsent text in the outbox."""
    day = day or paths.today()
    if tools.info(root).get("status") == "stopped":
        return "stopped"
    week = _week_of(day)
    if not force and _last_week() == week:
        return "already"
    text = core.week_report(day)
    if text is None:
        return "empty"
    sent = telegram.notify(text)
    with open(_state(), "w", encoding="utf-8") as f:
        json.dump({"week": week}, f)
    return "sent" if sent else "outbox"


def main(argv=None):
    ap = argparse.ArgumentParser(description="週に1回、植物の育ち具合を送る")
    ap.add_argument("--preview", action="store_true", help="送らずに文面だけ出す")
    ap.add_argument("--force", action="store_true", help="今週すでに送っていても送る")
    a = ap.parse_args(argv)
    if a.preview:
        print(core.week_report() or "習慣がまだありません。")
        return 0
    print(MESSAGES[run(force=a.force)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
