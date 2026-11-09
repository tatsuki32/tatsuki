#!/usr/bin/env python3
"""Use the tool without the bot.

  python3 lifeos/12-chatlog/chatlog.py "ランチ 3万ドン"      records one line
  python3 lifeos/12-chatlog/chatlog.py --list               deadlines with their countdown
  python3 lifeos/12-chatlog/chatlog.py --habits             habit lines waiting for tool 07
  python3 lifeos/12-chatlog/chatlog.py --calendar-pending   deadlines waiting for the calendar (JSON)
  python3 lifeos/12-chatlog/chatlog.py --calendar-done REF  marks one as written
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lifeos.bot import router  # noqa: E402
from lifeos.lib import logs, tools  # noqa: E402

core = tools.load_file(os.path.dirname(os.path.abspath(__file__)), "core")


def main(argv=None):
    ap = argparse.ArgumentParser(description="チャット記録を手元で使う")
    ap.add_argument("text", nargs="*", help="記録する1行")
    ap.add_argument("--list", action="store_true", help="締切をカウントダウン付きで出す")
    ap.add_argument("--habits", action="store_true", help="07番を待っている習慣の文を出す")
    ap.add_argument("--calendar-pending", action="store_true", help="カレンダー待ちの締切を JSON で出す")
    ap.add_argument("--calendar-done", metavar="REF", help="カレンダーに書いた締切を済みにする")
    a = ap.parse_args(argv)
    if a.list:
        print(core.deadline_list())
    elif a.habits:
        for r in logs.read(core.PENDING_HABITS):
            print(r.get("at", ""), r.get("text", ""))
    elif a.calendar_pending:
        print(json.dumps(core.gcal.pending(), ensure_ascii=False, indent=1))
    elif a.calendar_done:
        core.gcal.mark_done(a.calendar_done)
    elif a.text:
        print(core.record(" ".join(a.text), router.Ctx()) or "I could not read that as something to record. Nothing was recorded.")
    else:
        ap.print_usage()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
