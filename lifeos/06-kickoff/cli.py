#!/usr/bin/env python3
"""What /kickoff calls. Prints JSON; a refused request prints {"error": ...} and exits 1.

  cli.py stale                                   stalled tasks, up to 3
  cli.py begin "作業名"                          which task it means (reads only)
  cli.py finish --task ID --did 文 --made PATH --next 文
  cli.py finish --title 作業名 --did 文 --made PATH --next 文   (a new task)
  cli.py skip ID [--days 7]                      no proposal for this task for a while
  cli.py suggest on|off                          the morning proposals as a whole
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lifeos.lib.tools import load_file  # noqa: E402

core = load_file(os.path.dirname(os.path.abspath(__file__)), "core")


def run(a):
    if a.cmd == "suggest":
        core.set_suggesting(a.state == "on")
        return {"suggest": a.state == "on"}
    if core.stopped():
        return {"stopped": True, "message": "初動丸投げは止めてあります（tool.json の status）"}
    if a.cmd == "stale":
        return {"tasks": core.stale()[:core.SUGGEST_MAX]}
    if a.cmd == "begin":
        return core.begin(a.name)
    if a.cmd == "finish":
        return core.finish(a.did, a.made, a.next, task_id=a.task, title=a.title)
    return {"id": a.id, "until": core.skip(a.id, a.days)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="初動丸投げ")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stale")
    sub.add_parser("begin").add_argument("name")
    f = sub.add_parser("finish")
    f.add_argument("--task")
    f.add_argument("--title")
    f.add_argument("--did", required=True)
    f.add_argument("--made", action="append", default=[])
    f.add_argument("--next", required=True)
    s = sub.add_parser("skip")
    s.add_argument("id")
    s.add_argument("--days", type=int, default=core.SKIP_DAYS)
    sub.add_parser("suggest").add_argument("state", choices=("on", "off"))
    try:
        out = run(ap.parse_args(argv))
    except KeyError as e:
        out = {"error": "タスクが見つかりません：" + str(e.args[0])}
    except (ValueError, core.tasks.TaskStoreError) as e:
        out = {"error": str(e)}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 1 if "error" in out else 0


if __name__ == "__main__":
    sys.exit(main())
