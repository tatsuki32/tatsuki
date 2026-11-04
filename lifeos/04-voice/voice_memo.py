#!/usr/bin/env python3
"""Works without Telegram or the morning brief.

  python3 lifeos/04-voice/voice_memo.py yesterday   prints yesterday's summary
  python3 lifeos/04-voice/voice_memo.py sweep       files what is left in inbox/
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from lifeos.lib import tools  # noqa: E402

core = tools.load_file(HERE, "core")


def main(argv=None):
    cmd = (argv if argv is not None else sys.argv[1:] or [""])[0]
    if cmd == "yesterday":
        print("\n".join(core.summary()) or "昨日のメモはありません")
        return 0
    if cmd == "sweep":
        filed, left = core.sweep()
        print(f"仕分けました {filed}件 / 残り {left}件")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
