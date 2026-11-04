#!/usr/bin/env python3
"""並列ライン. `/lines` runs this. See README.md in this folder.

  python3 lifeos/05-parallel/lines.py                 the one-screen list
  python3 lifeos/05-parallel/lines.py start 名前 --until 18:30 --what "何をするか"
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lifeos.lib.tools import load_tool  # noqa: E402

core = load_tool("05-parallel", "core")
SCRIPT = os.path.abspath(__file__)


def _parser():
    ap = argparse.ArgumentParser(prog="lines", description="並列ライン：1作業＝1ブランチ。上限と終了時刻つき")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list", help="一覧（省略時もこれ）")
    sub.add_parser("all", help="git worktree list の全部")
    p = sub.add_parser("start", help="始める（終了時刻が必須）")
    p.add_argument("name")
    p.add_argument("--until", required=True, help="18:30 か 90m")
    p.add_argument("--what", required=True, help="何をするか")
    p.add_argument("--branch", help="使うブランチ。省略すると lines/名前")
    p.add_argument("--base", help="分岐元。省略すると main")
    p = sub.add_parser("note", help="どこまで進んだかを書く")
    p.add_argument("name")
    p.add_argument("text", nargs="+")
    p = sub.add_parser("pause", help="止める（作りかけには数え続ける）")
    p.add_argument("name")
    p.add_argument("reason", nargs="*")
    p.add_argument("--at", help="実際に止めた時刻 18:30")
    p = sub.add_parser("resume", help="再開する（終了時刻が必須）")
    p.add_argument("name")
    p.add_argument("--until", required=True)
    p = sub.add_parser("extend", help="終了時刻を延ばす")
    p.add_argument("name")
    p.add_argument("until")
    p = sub.add_parser("end", help="終える（作業フォルダとブランチは残る）")
    p.add_argument("name")
    p.add_argument("--at", help="実際に止めた時刻 18:30")
    p = sub.add_parser("remove", help="終えたラインの作業フォルダを消す（--yes がないと説明だけ）")
    p.add_argument("name")
    p.add_argument("--yes", action="store_true", help="本人が承認した")
    sub.add_parser("week", help="週ごとの稼働時間")
    p = sub.add_parser("limit", help="同時に持てる数を見る・変える")
    p.add_argument("n", nargs="?", type=int)
    sub.add_parser("check", help="終了時刻を過ぎたラインの通知を送る")
    p = sub.add_parser("watch", help="終了時刻を見張る（start が自動で動かす）")
    p.add_argument("--interval", type=float, default=core.WATCH_INTERVAL_SEC)
    sub.add_parser("banner", help="終了時刻を過ぎたときだけ知らせを出す")
    sub.add_parser("json", help="一覧の中身を JSON で出す")
    return ap


def _run(a):
    if a.cmd in (None, "list"):
        core.check()
        print(core.render_list())
    elif a.cmd == "all":
        print(core.render_all())
    elif a.cmd == "start":
        line = core.start(a.name, a.until, a.what, a.branch, a.base)
        print(f"始めました：{line['name']}（{line['until'][11:16]} まで）")
        print(f"  ブランチ：{line['branch']}")
        print(f"  作業フォルダ：{line['path']}")
        print(f"  そこで Claude を始める：cd \"{line['path']}\" && claude")
    elif a.cmd == "note":
        core.note(a.name, " ".join(a.text))
        print(f"書きました：{a.name}")
    elif a.cmd == "pause":
        core.pause(a.name, " ".join(a.reason), a.at)
        print(f"止めました：{a.name}（作りかけには数え続けます）")
    elif a.cmd == "resume":
        line = core.resume(a.name, a.until)
        print(f"再開しました：{a.name}（{line['until'][11:16]} まで）")
    elif a.cmd == "extend":
        line = core.extend(a.name, a.until)
        print(f"延ばしました：{a.name}（{line['until'][11:16]} まで。延長{line['extended']}回目）")
    elif a.cmd == "end":
        core.end(a.name, a.at)
        print(f"終えました：{a.name}（作業フォルダとブランチは残っています）")
    elif a.cmd == "remove":
        r = core.remove_worktree(a.name, a.yes)
        if r["approved"]:
            print(f"作業フォルダを消しました：{r['path']}（ブランチ {r['branch']} は残っています）")
        else:
            print(f"消すもの：{r['path']}（未コミット{r['dirty']}件）")
            print(f"ブランチ {r['branch']} は残します。未コミットがあると git が止めます。")
            print("消すなら --yes を付けて、もう一度実行します。まだ何も変えていません。")
    elif a.cmd == "week":
        print(core.render_week())
    elif a.cmd == "limit":
        if a.n is not None:
            core.set_max_lines(a.n)
        print(f"同時に持てる数：{core.max_lines()}（変えられるのは {core.HARD_MAX} まで）")
    elif a.cmd == "check":
        sent = core.check()
        if sent:
            print(sent["text"])
            print(f"Telegram：{'送信' if sent['telegram'] else '未送信（未接続か失敗。送信待ちの記録に残しました）'}"
                  f"　Mac の通知：{'出した' if sent['screen'] else '出せなかった'}")
    elif a.cmd == "watch":
        core.watch(time.sleep, a.interval)
    elif a.cmd == "banner":
        for line in core.banner_lines(core.read_lines()):
            print(line)
    elif a.cmd == "json":
        print(json.dumps(core.snapshot(), ensure_ascii=False))


def main(argv=None):
    a = _parser().parse_args(argv)
    try:
        _run(a)
        if a.cmd not in ("watch", "banner", "json"):
            core.ensure_watcher(SCRIPT)  # also brings the watcher back after a restart
    except core.LinesError as e:
        print(f"止めました：{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
