#!/usr/bin/env python3
"""The commands behind /task /next /done /save.

  python3 lifeos/03-task/cli.py add "内容 !高 #案件"
  python3 lifeos/03-task/cli.py next
  python3 lifeos/03-task/cli.py done ["題の一部"]
  python3 lifeos/03-task/cli.py save "やったこと" --next "次の一手"
  python3 lifeos/03-task/cli.py saved
  python3 lifeos/03-task/cli.py history
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lifeos.lib import logs, tasks  # noqa: E402
from lifeos.lib.tools import load_tool  # noqa: E402

core = load_tool("03-task", "core")


def main(argv=None):
    ap = argparse.ArgumentParser(description="一瞬メモ")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("add").add_argument("text", nargs="*")
    sub.add_parser("next")
    sub.add_parser("done").add_argument("words", nargs="*")
    s = sub.add_parser("save")
    s.add_argument("did", nargs="*")
    s.add_argument("--next", dest="next_step", default="")
    sub.add_parser("saved")
    sub.add_parser("history").add_argument("-n", type=int, default=20)
    a = ap.parse_args(argv)
    try:
        return run(a)
    except (ValueError, tasks.TaskStoreError) as e:
        print(f"できませんでした：{e}")
        return 1
    except OSError as e:  # the task may already be in: do not invite a blind retry that would add it twice
        print(f"途中で書き込めませんでした（{e.strerror}）。もう一度実行する前に Desk のタスクを見てください")
        return 1


def run(a):
    if a.cmd == "add":
        print("追加しました：" + core.line(core.add(" ".join(a.text))))
    elif a.cmd == "next":
        t = core.next_task()
        print("次の1件：" + core.line(t) if t else "開いているタスクはありません")
    elif a.cmd == "done":
        t, options = core.done(" ".join(a.words))
        if t:
            print("完了にしました：" + t["title"])
            return 0
        if not options:
            print("開いているタスクはありません")
            return 0
        words = " ".join(a.words)
        if words and not any(words in o.get("title", "") for o in options):
            print(f"「{words}」を含むタスクはありません。開いているもの：")
        else:
            print("どれか1つに決められません。題の一部を付けて /done してください：")
        for o in options:
            print("- " + o.get("title", ""))
        return 1
    elif a.cmd == "save":
        core.save(" ".join(a.did), a.next_step)
        print("保存しました" + (f"。次の一手：{a.next_step}" if a.next_step else ""))
    elif a.cmd == "saved":
        saves = [r for r in logs.read("work") if r.get("kind") == "save"]
        if not saves:
            print("保存した作業状態はありません")
            return 0
        r = saves[-1]
        print(f"{r['at']}\nやったこと：{r.get('did') or 'なし'}\n次の一手：{r.get('next') or 'なし'}\nタスク：{r.get('task') or 'なし'}")
    elif a.cmd == "history":
        for r in logs.read("task-history", limit=a.n):
            print(f"{r['at']}  {r['action']}  {r['title']}  {r['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
