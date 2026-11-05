#!/usr/bin/env python3
"""Used by the scheduled tasks. Usage:
  cli.py prepare                                   -> {"send": bool, "subject", "body", ...}
  cli.py sent <date> <threadId> <messageId> <from>
  cli.py pending                                   -> {"threads": [...]}
  cli.py reply <date> <file.json>                  file: {"messageId","from","labels":[...],"text"}
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lifeos.lib.tools import load_tool  # noqa: E402

core = load_tool("19-daily-note", "core")


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "prepare" and len(argv) == 1:
        out = core.prepare()
    elif cmd == "sent" and len(argv) == 5:
        out = core.record_sent(*argv[1:])
    elif cmd == "pending" and len(argv) == 1:
        out = core.pending()
    elif cmd == "reply" and len(argv) == 3:
        with open(argv[2], encoding="utf-8") as f:
            m = json.load(f)
        out = core.save_reply(argv[1], m.get("messageId"), m.get("from"), m.get("labels"), m.get("text"))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
