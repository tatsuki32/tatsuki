#!/usr/bin/env python3
"""/tsuzuki: three lines on where you stopped and what to do next. Reads only.

  python3 lifeos/15-resume/tsuzuki.py         three lines
  python3 lifeos/15-resume/tsuzuki.py --all   the newest record of the last 5 sessions
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lifeos.lib.tools import load_tool  # noqa: E402


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    core = load_tool("15-resume", "core")
    if "--all" in argv:
        records = core.by_session()
        if not records:
            print("作業ログはまだありません")
        for r in records:
            print(f"{core.when(r.get('at'))} {r.get('branch') or '-'}：{core.short(r.get('done'))} → {core.short(r.get('next')) or '次の一手なし'}")
        return 0
    print("\n".join(core.lines()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
