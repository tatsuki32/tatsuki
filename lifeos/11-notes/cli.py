#!/usr/bin/env python3
"""Life OS 11: run by hand. Try it safely with LIFEOS_DATA="$TMPDIR/try" in front."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lifeos.lib import md, paths  # noqa: E402
from lifeos.lib.tools import load_tool  # noqa: E402

core = load_tool("11-notes", "core")
summary = load_tool("11-notes", "summary")
links = load_tool("11-notes", "links")
memos = load_tool("11-notes", "memos")


def cmd_run(a):
    r = core.run(desk=False if a.no_desk else None)
    if r["stopped"]:
        print("止めてあります。動かすときは start を実行します。")
        return
    print("メモ {} 件（関連メモのあるもの {} 件）".format(r["memos"], r["linked"]))
    print("まとめを更新：{} 件（briefs/ の中）".format(r["briefs"]))
    if r["desk"]:
        print("Desk のノートを更新：{} 件".format(r["written"]))
        if r["kept"]:
            print("Desk で直してあるので触らなかったノート：{} 件".format(r["kept"]))
    if r["unread"]:
        print("文字をまだ読めていない写真：{} 枚".format(r["unread"]))


def cmd_add(a):
    print("追加しました：" + memos.add(a.text, kind=a.kind or ""))


def cmd_show(a):
    hits, index = core.find(a.word)
    if not hits:
        print("見つかりません。先に run を実行してください。")
    for _, v in hits[:5]:
        print(v["title"])
        print("  タグ：" + ("、".join(v["tags"]) or "なし"))
        print("  関連メモ：" + ("" if v["related"] else "なし"))
        for r in v["related"]:
            print("    - " + index.get(r, {}).get("title", r))
        for title, url in v.get("cosense", []):
            print("  Cosense：{} {}".format(title, url))


def cmd_summary(a):
    today = paths.today()
    label = {"day": today, "week": summary.week_of(today), "year": today[:4]}[a.period]
    print(md.read("briefs/{}-memo.md".format(label)) or "まだありません（{}）".format(label))


def cmd_status(a):
    s = core.settings()
    scope = (s.get("cosense") or {}).get("scope")
    print("動作：" + ("動かす" if s["enabled"] else "止めてある"))
    print("Desk のノートに出す：" + ("はい" if s["desk"] else "いいえ"))
    print("Cosense の範囲：" + (str(scope) if scope else "未決定（1ページも使いません）"))
    print("写真の読み取り：" + ("接続済み" if memos.reader_connected() else "未接続（同じ名前の .txt があればそれを使います）"))
    print("メモ {} 件".format(len(core.load_index())))


def cmd_stop(a):
    core.save_settings(enabled=False)
    print("止めました。元のメモも、できているノートも、そのままです。")


def cmd_start(a):
    core.save_settings(enabled=True, desk=True)
    print("動かします。次の run で反映します。")


def cmd_unpublish(a):
    if not a.yes:
        print("Desk のノート {} 件を削除扱いにします（元のメモは残ります。あとで start と run で戻せます）。".format(core.count_published()))
        print("よければ --yes を付けてもう一度実行してください。何も変えていません。")
        return
    r = core.unpublish()
    print("削除扱いにしました：{} 件。Desk で直してあるので残したもの：{} 件".format(r["removed"], r["kept"]))


def _scope(values):
    scope = {}
    for v in values:
        key, _, val = v.partition(":")
        if key == "all":
            scope["all"] = True
        elif key == "tag" and val:
            scope.setdefault("tags", []).append(val)
        elif key == "prefix" and val:
            scope.setdefault("prefixes", []).append(val)
        elif key == "since" and val:
            scope["since"] = val
        else:
            raise SystemExit("範囲は all / tag:言葉 / prefix:言葉 / since:YYYY-MM-DD で書きます")
    return scope


def cmd_cosense(a):
    core.save_settings(cosense={"scope": _scope(a.scope), "project": a.project})
    print("Cosense の範囲を決めました。ページの一覧は次の run で使います。")


def main(argv=None):
    p = argparse.ArgumentParser(description="メモ自動連携")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run", help="タグ・リンク・まとめ・Desk のノートを更新する")
    s.add_argument("--no-desk", action="store_true", help="Desk のノートは更新しない")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("add", help="メモを1つ足す")
    s.add_argument("text")
    s.add_argument("--kind")
    s.set_defaults(fn=cmd_add)
    s = sub.add_parser("show", help="メモのタグと関連メモを見る")
    s.add_argument("word")
    s.set_defaults(fn=cmd_show)
    s = sub.add_parser("summary", help="今日・今週・今年のまとめを見る")
    s.add_argument("period", nargs="?", default="day", choices=("day", "week", "year"))
    s.set_defaults(fn=cmd_summary)
    sub.add_parser("status", help="今の設定を見る").set_defaults(fn=cmd_status)
    sub.add_parser("stop", help="止める").set_defaults(fn=cmd_stop)
    sub.add_parser("start", help="また動かす").set_defaults(fn=cmd_start)
    s = sub.add_parser("unpublish", help="Desk のノートから下げる（--yes が必要）")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_unpublish)
    s = sub.add_parser("cosense-scope", help="Cosense の取り込み範囲を決める")
    s.add_argument("scope", nargs="+")
    s.add_argument("--project", required=True, help="Cosense のプロジェクト名")
    s.set_defaults(fn=cmd_cosense)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
