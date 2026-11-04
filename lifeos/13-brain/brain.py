#!/usr/bin/env python3
"""Command line for the second brain. Run `python3 lifeos/13-brain/brain.py -h`."""
import argparse
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lifeos.lib.tools import load_tool  # noqa: E402

store = load_tool("13-brain", "store")
connectors = load_tool("13-brain", "connectors")
contacts = load_tool("13-brain", "contacts")
reconcile = load_tool("13-brain", "reconcile")
approvals = load_tool("13-brain", "approvals")


def is_person():
    """Approving needs a person at a real terminal. Claude's commands and scheduled runs do not qualify."""
    return sys.stdin.isatty() and not os.environ.get("CLAUDECODE")


def show_request(r):
    print("{}  [{}]  {}".format(r["id"], approvals.STATUS_JA[r["status"]], approvals.KINDS[r["kind"]]))
    print("  相手：{}".format(r["target"]))
    print("  金額：{} {}".format(r["amount"], r["currency"]))
    if r["payload"]:
        print("  中身：{}".format(json.dumps(r["payload"], ensure_ascii=False)))
    if r["summary"]:
        print("  説明：{}".format(r["summary"]))
    print("  期限：{}".format(r["expires"][:16].replace("T", " ")))


def show_result(result, limit=None):
    c = result["counts"]
    print("  ".join("{} {}件".format(reconcile.LABELS[k], c[k]) for k in reconcile.LABELS))
    for it in result["items"][:limit]:
        who = it["customer"] or it["payer"] or ""
        ids = "請求 {} / 入金 {}".format(it["invoice_id"] or "-", it["payment_id"] or "-")
        print("- [{}] {} {}：{}".format(it["label"], who, ids, it["reason"]))
        if it["candidates"]:
            print("    候補：" + "、".join(it["candidates"]))
    for s in result["skipped"]:
        print("- [読めなかった行] {}：{}".format(s["id"] or "-", s["reason"]))


def cmd_status(_):
    print("状態：" + ("止めています" if store.is_stopped() else "動いています"))
    print("接続：")
    for label, on in connectors.state():
        print("  {}：{}".format(label, "接続済み" if on else "未接続"))
    for kind, label in approvals.KINDS.items():
        print("  {}：{}".format(label, "接続済み" if approvals.is_connected(kind) else "未接続"))
    orgs = store.scope_orgs()
    print("対象の範囲：" + ("、".join(orgs) if orgs else "未決定（絞っていません）"))
    print("顧客メモ：{}件".format(len(contacts.all_contacts())))
    print("承認待ちなど：{}件".format(len(approvals.open_requests())))
    return 0


def cmd_contact(a):
    if a.action == "add":
        if not a.name:
            print("--name が要ります")
            return 2
        c = contacts.add(a.name, a.addr or [], a.alias or [], a.org or "", a.note or "", a.next or "")
        print("追加しました：{} {}".format(c["id"], c["name"]))
    elif a.action == "list":
        for c in sorted(contacts.all_contacts(), key=lambda x: x.get("last_contact") or "", reverse=True):
            print("{}  {}  最後のやりとり：{}".format(c["id"], c["name"], (c.get("last_contact") or "なし")[:10]))
    else:
        if not a.id:
            print("顧客の番号が要ります")
            return 2
        if a.action == "edit":
            fields = {k: v for k, v in (("note", a.note), ("next_action", a.next), ("org", a.org)) if v is not None}
            if a.addr:
                fields["addresses"] = a.addr
            if a.alias:
                fields["aliases"] = a.alias
            contacts.update(a.id, **fields)
            print("直しました")
        c = contacts.get(a.id)
        if not c:
            print("見つかりません：" + a.id)
            return 1
        print(contacts.render(c), end="")
    return 0


def cmd_ingest(a):
    with open(a.file, encoding="utf-8") as f:
        items = json.load(f)
    if not isinstance(items, list):
        print("やりとりの配列を渡してください")
        return 2
    print(_counts(contacts.ingest(items)))
    return 0


def _counts(c):
    return "追加 {added}件、重複 {duplicate}件、未登録の相手 {unmatched}件、対象外 {out_of_scope}件、読めない {bad}件".format(**c)


def cmd_sync(a):
    store.require_running()
    for name in ("gmail", "whatsapp"):
        try:
            print("{}：{}".format(connectors.LABELS[name], _counts(contacts.ingest(connectors.call(name, a.since)))))
        except connectors.NotConnected as e:
            print(e)
    return 0


def cmd_unmatched(_):
    for u in contacts.unmatched():
        print("{}  {}回  最後：{}".format(u["address"], u["count"], (u["last"] or "")[:10]))
    return 0


def cmd_reconcile(a):
    store.require_running()
    if bool(a.invoices) != bool(a.payments):
        print("--invoices と --payments は両方渡してください")
        return 2
    if a.invoices:
        invoices, payments = reconcile.load_invoices(a.invoices), reconcile.load_payments(a.payments)
    else:
        invoices, payments = connectors.call("invoices"), connectors.call("payments")
    result = reconcile.reconcile(invoices, payments, contacts.same_party())
    reconcile.save(result)
    show_result(result)
    if a.propose:
        made = approvals.propose_from(result)
        print("承認待ちは {}件です。お金はまだ動いていません。".format(sum(1 for m in made if m["status"] == "pending")))
    return 0


def cmd_pending(_):
    items = approvals.open_requests()
    for r in items:
        show_request(r)
    if not items:
        print("承認待ちはありません")
    return 0


def cmd_approve(a):
    if not is_person():
        print("承認は本人が普通のターミナルで打つものです。ここからは承認できません。お金は動いていません。")
        return 3
    r = approvals.get(a.id)
    if not r:
        print("見つかりません：" + a.id)
        return 1
    show_request(r)
    typed = input("よければ金額を数字で打ってください（やめるなら空のまま Enter）：").strip()
    if not typed:
        print("やめました")
        return 0
    approvals.approve(a.id, typed)
    print("承認しました。実行は brain.py run {} です。".format(a.id))
    return 0


def cmd_reject(a):
    approvals.reject(a.id, a.reason or "")
    print("却下しました")
    return 0


def cmd_run(a):
    r = approvals.execute(a.id)
    print("実行しました：{}".format(r.get("result", "")))
    return 0


def cmd_retry(a):
    r = approvals.retry(a.id)
    print("新しい承認待ちを作りました。もう一度承認が要ります。")
    show_request(r)
    return 0


def cmd_scope(a):
    if a.orgs:
        store.set_scope(a.orgs)
    orgs = store.scope_orgs()
    print("対象の範囲：" + ("、".join(orgs) if orgs else "未決定（絞っていません）"))
    return 0


def cmd_stop(_):
    store.stop()
    print("止めました。取り込み・照合・承認・実行はしません。再開は brain.py start です。")
    return 0


def cmd_start(_):
    store.start()
    print("再開しました")
    return 0


def cmd_demo(_):
    """The whole flow with made-up data in a temporary folder that is removed afterwards."""
    tmp = tempfile.mkdtemp(prefix="lifeos-13-demo-")
    before = os.environ.get("LIFEOS_DATA")
    os.environ["LIFEOS_DATA"] = tmp
    moved = []
    try:
        print("見本のデータで試します。本物のデータには触りません。\n")
        print("1. 顧客メモ")
        c = contacts.add("サンプル商事株式会社", ["tanaka@example.com"], ["サンプルシヨウジ"], note="見本のお客さま")
        print("   " + _counts(contacts.ingest([
            {"channel": "gmail", "direction": "in", "address": "Tanaka <tanaka@example.com>", "at": "2026-11-01T09:00:00+07:00", "subject": "見積もりのお礼", "summary": "来週返事をもらえる", "ref": "demo-1", "body": "本文は保存しません"},
            {"channel": "gmail", "direction": "in", "address": "news@example.org", "at": "2026-11-01T10:00:00+07:00", "subject": "お知らせ", "ref": "demo-2"},
        ])))
        print("\n" + contacts.render(contacts.get(c["id"])))
        print("2. 請求と入金の照合")
        result = reconcile.reconcile(
            [{"id": "INV-101", "customer": "サンプル商事", "amount": "110000", "currency": "JPY", "issued": "2026-10-01", "due": "2026-10-31"},
             {"id": "INV-102", "customer": "例示工業", "amount": "55000", "currency": "JPY", "issued": "2026-10-05", "due": "2026-10-31"},
             {"id": "INV-103", "customer": "見本物産", "amount": "33000", "currency": "JPY", "issued": "2026-10-05", "due": "2099-12-31"}],
            [{"id": "P1", "payer": "ｻﾝﾌﾟﾙｼﾖｳｼﾞ", "amount": "110000", "currency": "JPY", "date": "2026-10-20", "memo": ""},
             {"id": "P2", "payer": "別の名義", "amount": "55000", "currency": "JPY", "date": "2026-10-21", "memo": ""}],
            contacts.same_party())
        show_result(result)
        print("\n3. お金が動く操作の承認")
        r = approvals.propose_from(result)[0]
        show_request(r)

        @approvals.money_action("accounting.apply_payment")
        def fake_accounting(action):
            moved.append(action["id"])
            return "見本の会計に書きました"

        for label, fn in (("承認の前に実行", lambda: approvals.execute(r["id"])),
                          ("承認の前に会計を直接呼ぶ", lambda: fake_accounting(r)),
                          ("違う金額で承認", lambda: approvals.approve(r["id"], "1"))):
            try:
                fn()
                print("   {}：通ってしまいました".format(label))
            except approvals.Refused as e:
                print("   {}：止まりました（{}）".format(label, e))
        print("   この時点で動いた回数：{}".format(len(moved)))
        approvals.approve(r["id"], r["amount"])
        approvals.execute(r["id"])
        print("   正しい金額で承認して実行：動いた回数 {}".format(len(moved)))
        try:
            approvals.execute(r["id"])
        except approvals.Refused as e:
            print("   もう一度実行：止まりました（{}）".format(e))
        print("   最後の動いた回数：{}".format(len(moved)))
    finally:
        approvals.disconnect("accounting.apply_payment")
        if before is None:
            os.environ.pop("LIFEOS_DATA", None)
        else:
            os.environ["LIFEOS_DATA"] = before
        shutil.rmtree(tmp, True)
    return 0 if len(moved) == 1 else 1


def build_parser():
    p = argparse.ArgumentParser(prog="brain.py", description="第二の脳：顧客メモ・請求と入金の照合・お金の承認")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="接続と件数を見る").set_defaults(fn=cmd_status)
    sub.add_parser("demo", help="見本のデータで全体を試す（本物には触らない）").set_defaults(fn=cmd_demo)

    c = sub.add_parser("contact", help="顧客メモ")
    c.add_argument("action", choices=("add", "list", "show", "edit"))
    c.add_argument("id", nargs="?")
    c.add_argument("--name")
    c.add_argument("--addr", action="append", help="メール、@ドメイン、電話番号。何度でも")
    c.add_argument("--alias", action="append", help="振込名義など別の呼び名。何度でも")
    c.add_argument("--org", help="どの会社の取引か")
    c.add_argument("--note")
    c.add_argument("--next")
    c.set_defaults(fn=cmd_contact)

    i = sub.add_parser("ingest", help="やりとりの JSON を顧客メモに入れる")
    i.add_argument("file")
    i.set_defaults(fn=cmd_ingest)
    s = sub.add_parser("sync", help="つないだ先からやりとりを取り込む")
    s.add_argument("--since")
    s.set_defaults(fn=cmd_sync)
    sub.add_parser("unmatched", help="未登録の相手を見る").set_defaults(fn=cmd_unmatched)

    r = sub.add_parser("reconcile", help="請求と入金を照合する")
    r.add_argument("--invoices", help="請求の CSV")
    r.add_argument("--payments", help="入金の CSV")
    r.add_argument("--propose", action="store_true", help="一致したものを承認待ちに入れる")
    r.set_defaults(fn=cmd_reconcile)

    sub.add_parser("pending", help="承認待ちなどを見る").set_defaults(fn=cmd_pending)
    for name, fn, text in (("approve", cmd_approve, "本人が承認する"), ("run", cmd_run, "承認済みを実行する"), ("retry", cmd_retry, "失敗したものの承認を取り直す")):
        x = sub.add_parser(name, help=text)
        x.add_argument("id")
        x.set_defaults(fn=fn)
    x = sub.add_parser("reject", help="却下する")
    x.add_argument("id")
    x.add_argument("--reason")
    x.set_defaults(fn=cmd_reject)

    x = sub.add_parser("scope", help="対象の会社を見る・決める")
    x.add_argument("orgs", nargs="*")
    x.set_defaults(fn=cmd_scope)
    sub.add_parser("stop", help="止める").set_defaults(fn=cmd_stop)
    sub.add_parser("start", help="再開する").set_defaults(fn=cmd_start)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (approvals.Refused, connectors.NotConnected, store.Stopped, store.StoreError, ValueError, KeyError, OSError) as e:
        print(e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
