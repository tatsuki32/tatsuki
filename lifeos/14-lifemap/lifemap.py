#!/usr/bin/env python3
"""Life map from the command line.

  python3 lifeos/14-lifemap/lifemap.py tick             what the scheduler runs every 15 minutes
  python3 lifeos/14-lifemap/lifemap.py ask              asks now (still one question a day)
  python3 lifeos/14-lifemap/lifemap.py insight 2026-10  prints a month's patterns (sends nothing)
  python3 lifeos/14-lifemap/lifemap.py pause | resume   stops and restarts the questions
  python3 lifeos/14-lifemap/lifemap.py status           shows today's state
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from lifeos.lib import paths  # noqa: E402
from lifeos.lib.tools import load_file  # noqa: E402

core = load_file(HERE, "core")


def status():
    s = core.load_state()
    print("止めています" if s.get("paused") else "動いています")
    if s.get("day") != paths.today():
        print("今日はまだ何もしていません")
    elif s.get("askedAt"):
        print(f"今日の問いは聞きました（{s['askedAt'][11:16]}）")
        print("返事を待っています" if core.pending(s) else "受付は終わりました")
    else:
        print(f"今日の問いの時刻：{s.get('planned') or 'なし'}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="人生マップ")
    ap.add_argument("command", choices=["tick", "ask", "insight", "pause", "resume", "status"])
    ap.add_argument("month", nargs="?", help="insight の対象（YYYY-MM）。省略すると先月")
    a = ap.parse_args(argv)
    if a.command == "tick":
        core.tick()
    elif a.command == "ask":
        if core.load_state().get("paused"):
            print("問いかけを止めています。再開は resume です。")
        else:
            print(core.ask_now() or "今日はもう聞きました。問いは1日1問までです。")
    elif a.command == "insight":
        print(core.insight(a.month or core.previous_month(paths.now())) or "分野をまたいだパターンはありませんでした。")
    elif a.command == "pause":
        core.set_paused(True)
        print("問いかけを止めました。")
    elif a.command == "resume":
        core.set_paused(False)
        print("問いかけを再開しました。")
    else:
        status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
