#!/usr/bin/env python3
"""Decision-free start. See lifeos/10-autostart/README.md.

  python3 lifeos/10-autostart/autostart.py hook           SessionStart hook: prints JSON for Claude Code
  python3 lifeos/10-autostart/autostart.py first          shows the first item (changes nothing)
  python3 lifeos/10-autostart/autostart.py defer --dry    shows what would be parked (changes nothing)
  python3 lifeos/10-autostart/autostart.py defer          parks stale tasks now
  python3 lifeos/10-autostart/autostart.py review         shows the parked tasks
  python3 lifeos/10-autostart/autostart.py restore <id>   puts a task back (--all for every one)
  python3 lifeos/10-autostart/autostart.py off | on       stops or restarts the whole tool
  python3 lifeos/10-autostart/autostart.py defer-off | defer-on   stops or restarts parking only
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lifeos.lib import paths, tools  # noqa: E402

core = tools.load_file(os.path.dirname(os.path.abspath(__file__)), "core")


def hook():
    """A hook must never get in the way of the session: any trouble means saying nothing."""
    try:
        out = core.session_start()
    except Exception:
        return 0
    if out:
        # ASCII only: the hook's environment may not be UTF-8
        sys.stdout.write(json.dumps(out) + "\n")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="決断ゼロ起動")
    ap.add_argument("command", choices=("hook", "first", "defer", "review", "restore",
                                        "off", "on", "defer-off", "defer-on"))
    ap.add_argument("task_id", nargs="?")
    ap.add_argument("--dry", action="store_true", help="表示だけで何も変えない")
    ap.add_argument("--all", action="store_true", help="restore で全部戻す")
    a = ap.parse_args(argv)

    if a.command == "hook":
        return hook()
    if a.command == "first":
        first = core.pick_first(core.gather())
        print(f"最初の1件：{first['title']}（{first['reason']}）")
    elif a.command == "defer":
        if a.dry:
            items = core.stale_tasks(paths.now_ms())
            print(f"延期になるタスク：{len(items)}件（まだ何も動かしていません）")
            for t in items:
                print(f"- {core.clean(t.get('title', ''))}")
        else:
            result = core.defer()
            print(f"延期に移しました：{len(result['moved'])}件")
            for t in result["moved"]:
                print(f"- {core.clean(t.get('title', ''))}")
            if result["failed"]:
                print(f"移せなかったもの：{result['failed']}件")
                return 1
    elif a.command == "review":
        print(core.review_text() or "延期中のタスクはありません。")
    elif a.command == "restore":
        if not a.task_id and not a.all:
            ap.error("restore には ID か --all が要ります")
        back = core.restore(None if a.all else a.task_id)
        if not back:
            print("戻せるタスクがありませんでした。")
            return 1
        print(f"戻しました：{len(back)}件")
        for title in back:
            print(f"- {title}")
    elif a.command in ("off", "on"):
        core.set_config(enabled=a.command == "on")
        print("決断ゼロ起動を止めました。" if a.command == "off" else "決断ゼロ起動を動かします。")
    else:
        core.set_config(autoDefer=a.command == "defer-on")
        print("自動の延期を止めました。" if a.command == "defer-off" else "自動の延期を動かします。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
