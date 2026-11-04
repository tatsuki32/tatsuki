#!/usr/bin/env python3
"""サブスク自作のコマンド。使い方は lifeos/18-subs/README.md。

  scope [--months N ...]        洗い出す範囲を見る・決める（決まるまでメールは読まない）
  query                         決めた範囲の Gmail 検索語を出す（読むのは /subs の Claude）
  import FILE|-                 読んだメールを一覧に入れる
  scan                          差し替えた接続（mail.fetch）から読む
  add 名前 金額 [--currency --interval]
  edit 名前 [--name --amount --currency --interval]
  list / table                  一覧と月額 / 判定の表
  judge 名前 [--category --difficulty --run-cost --note]
  decide 名前 replace|keep|ignore [--purpose 用途]
  start 名前 自作ツール名       試用を始める
  used 名前 [--date] / confirm 名前 / giveup 名前
  check                         1か月たったものを解約の提案にする（Desk のタスク）
  cancelled 名前 [--yes]        本人が解約したことを記録する
  keep-paying 名前 [--yes]      解約の提案を取り下げる
  remove 名前 [--yes]           一覧から外す
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lifeos.lib.tools import load_tool  # noqa: E402

core = load_tool("18-subs", "core")
mail = load_tool("18-subs", "mail")


def approved(question, yes):
    """Irreversible steps need the person's yes: --yes, or typing y at a terminal."""
    if yes:
        return True
    if not sys.stdin.isatty():
        print(question + "（確認が要ります。本人の了承を得てから --yes を付けてください）")
        return False
    return input(question + " [y/N] ").strip().lower() == "y"


def print_ingest(r):
    platform = r["skipped"].get("platform_needs_service", 0)
    skipped = sum(r["skipped"].values()) - platform
    print("請求 {} 件を入れました（新しいサービス {}、重複 {}、請求と読めなかったもの {}）".format(
        r["charges"], r["new"], r["repeat"], skipped))
    if platform:
        print("決済代行などからの {} 件は、サービス名（service）がないので入れていません".format(platform))
    print(core.list_text())
    core.save_report()


def build_parser():
    p = argparse.ArgumentParser(description="サブスク自作")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scope")
    s.add_argument("--months", type=int)
    s.add_argument("--keyword", action="append", default=[])
    s.add_argument("--exclude-sender", action="append", default=[])
    s.add_argument("--platform-sender", action="append", default=[])

    sub.add_parser("query")
    sub.add_parser("scan")
    sub.add_parser("list")
    sub.add_parser("table")
    sub.add_parser("check")

    s = sub.add_parser("import")
    s.add_argument("file")

    s = sub.add_parser("add")
    s.add_argument("name")
    s.add_argument("amount", type=float)
    s.add_argument("--currency", default="JPY")
    s.add_argument("--interval", default="monthly")

    s = sub.add_parser("edit")
    s.add_argument("ref")
    s.add_argument("--name")
    s.add_argument("--amount", type=float)
    s.add_argument("--currency")
    s.add_argument("--interval")

    s = sub.add_parser("judge")
    s.add_argument("ref")
    s.add_argument("--category")
    s.add_argument("--difficulty")
    s.add_argument("--run-cost", type=float)
    s.add_argument("--note")

    s = sub.add_parser("decide")
    s.add_argument("ref")
    s.add_argument("choice", choices=["replace", "keep", "ignore"])
    s.add_argument("--purpose")

    s = sub.add_parser("start")
    s.add_argument("ref")
    s.add_argument("tool")

    s = sub.add_parser("used")
    s.add_argument("ref")
    s.add_argument("--date")

    for name in ("confirm", "giveup"):
        sub.add_parser(name).add_argument("ref")

    for name in ("cancelled", "keep-paying", "remove"):
        s = sub.add_parser(name)
        s.add_argument("ref")
        s.add_argument("--yes", action="store_true")
    return p


def run(a):
    if a.cmd == "scope":
        cur = core.get_scope() or {}
        if a.months or a.keyword or a.exclude_sender or a.platform_sender:
            if not (a.months or cur.get("months")):
                raise ValueError("先に --months で期間を決めてください")
            scope = core.set_scope(a.months or cur["months"],
                                   sorted(set(cur.get("keywords", []) + a.keyword)),
                                   sorted(set(cur.get("exclude_senders", []) + a.exclude_sender)),
                                   sorted(set(cur.get("platform_senders", []) + a.platform_sender)))
            print("範囲を決めました：過去{}か月".format(scope["months"]))
        else:
            print(cur or "範囲はまだ決まっていません")
    elif a.cmd == "query":
        for q in mail.queries(core.require_scope()):
            print(q)
    elif a.cmd == "import":
        text = sys.stdin.read() if a.file == "-" else open(a.file, encoding="utf-8").read()
        print_ingest(core.ingest(mail.from_json(text)))
    elif a.cmd == "scan":
        print_ingest(core.ingest(mail.fetch(core.require_scope())))
    elif a.cmd == "add":
        v = core.add_manual(a.name, a.amount, a.currency, a.interval)
        print("追加しました：{}（月 {}）".format(v["name"], core.money(v["monthly"], v["currency"])))
    elif a.cmd == "edit":
        v = core.edit(a.ref, a.name, a.amount, a.currency, a.interval)
        print("直しました：{}（月 {}）".format(v["name"], core.money(v["monthly"], v["currency"])))
    elif a.cmd == "list":
        print(core.list_text())
    elif a.cmd == "table":
        print(core.table_text())
        print("保存先：" + core.save_report())
    elif a.cmd == "judge":
        r = core.set_judgement(a.ref, a.category, a.difficulty, a.run_cost, a.note)
        print("{}：難易度 {}、節約 月 {}".format(r["name"], r["difficulty"], core.money(r["saving"], r["currency"])))
    elif a.cmd == "decide":
        v = core.decide(a.ref, a.choice, a.purpose)
        print("{}：{}にしました".format(v["name"], core.STATUS_JA[v["status"]]))
        if a.choice == "replace":
            line = core.newtool_line(a.ref)
            if not line:
                print("用途を --purpose で教えてください。09番に渡す1行を作ります（公開されるのでサービス名は書きません）")
                return
            print("09番のツール工房で作ります。次の1行を打ってください：")
            print(line)
            if not core.newtool_ready():
                print("（/newtool はまだこの作業場所に入っていません）")
            print("できて使い始めたら：subs.py start {} 自作ツール名".format(v["id"]))
    elif a.cmd == "start":
        v = core.start_trial(a.ref, a.tool)
        print("{}：試用を始めました。{}".format(v["name"], core.trial_state(v)[1]))
        if not core.tool_exists(a.tool):
            print("（「{}」は09番の一覧に見当たりません。名前を確かめてください）".format(a.tool))
    elif a.cmd == "used":
        v = core.used(a.ref, a.date)
        print("{}：使った日 {}日".format(v["name"], len(v["trial"]["used"])))
    elif a.cmd == "confirm":
        print("{}：1か月使えたと記録しました。check で解約の提案になります".format(core.confirm_month(a.ref)["name"]))
    elif a.cmd == "giveup":
        print("{}：試用を終えました。サブスクはそのままです".format(core.give_up(a.ref)["name"]))
    elif a.cmd == "check":
        r = core.check()
        for x in r["proposed"]:
            print("解約の提案を Desk のタスクに入れました：{}（節約 月 {}）。解約はご自身で行ってください".format(
                x["name"], core.money(x["saving"], x["currency"])))
        for x in r["waiting"]:
            print("{}：{}".format(x["name"], x["text"]))
        if not r["proposed"] and not r["waiting"]:
            print("試用中のものはありません")
    elif a.cmd == "cancelled":
        if approved("{}を解約済みとして記録します。ご自身で解約しましたか？".format(a.ref), a.yes):
            print("{}：解約済みと記録しました".format(core.cancelled(a.ref)["name"]))
    elif a.cmd == "keep-paying":
        if approved("{}の解約の提案を取り下げ、そのまま払い続けますか？".format(a.ref), a.yes):
            print("{}：そのままにしました".format(core.stop_proposal(a.ref)["name"]))
    elif a.cmd == "remove":
        if approved("{}を一覧から外しますか？".format(a.ref), a.yes):
            print("{}：一覧から外しました".format(core.remove(a.ref)["name"]))


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        run(a)
    except (core.ScopeNotDecided, core.NotFound, core.WrongState, mail.NotConnected, ValueError, OSError) as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
