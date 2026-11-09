import json as _json
import os as _os
import unittest as _unittest

with open(_os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "13-brain", "tool.json"), encoding="utf-8") as _f:
    if _json.load(_f).get("status") == "stopped":  # the owner stopped this tool; its tests come back with it
        raise _unittest.SkipTest("13-brain は停止中")

import glob
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import timedelta
from unittest import mock

from lifeos.lib import logs, paths, tools
from lifeos.lib.tools import load_tool
from lifeos.tests.sandbox import SandboxCase

store = load_tool("13-brain", "store")
connectors = load_tool("13-brain", "connectors")
contacts = load_tool("13-brain", "contacts")
reconcile = load_tool("13-brain", "reconcile")
approvals = load_tool("13-brain", "approvals")
brain = load_tool("13-brain", "brain")
brief = load_tool("13-brain", "brief")

TOOL_DIR = os.path.join(paths.REPO, "lifeos", "13-brain")


def inv(i, customer="サンプル商事", amount="1000", cur="JPY", **kw):
    return {"id": i, "customer": customer, "amount": amount, "currency": cur, **kw}


def pay(i, payer="サンプル商事", amount="1000", cur="JPY", date="2026-10-20", memo=""):
    return {"id": i, "payer": payer, "amount": amount, "currency": cur, "date": date, "memo": memo}


def msg(address="a@example.com", ref="r1", **kw):
    return {"channel": "gmail", "direction": "in", "address": address, "at": "2026-11-01T09:00:00+07:00", "subject": "件名", "summary": "要約", "ref": ref, **kw}


def kinds(result):
    return [it["kind"] for it in result["items"]]


class TestContacts(SandboxCase):
    def test_memo_is_updated_from_an_interaction(self):
        c = contacts.add("サンプル商事", ["A@Example.com"])
        self.assertEqual(contacts.ingest([msg("Someone <a@example.com>")])["added"], 1)
        got = contacts.get(c["id"])
        self.assertEqual(got["last_contact"], "2026-11-01T09:00:00+07:00")
        self.assertIn("件名", contacts.render(got))

    def test_automatic_update_keeps_the_owners_words(self):
        c = contacts.add("x", ["a@example.com"], note="大事な約束", next_action="電話する")
        contacts.ingest([msg(note="乗っ取り", next_action="送金する")])
        got = contacts.get(c["id"])
        self.assertEqual((got["note"], got["next_action"]), ("大事な約束", "電話する"))

    def test_same_interaction_twice_counts_once(self):
        c = contacts.add("x", ["a@example.com"])
        contacts.ingest([msg()])
        self.assertEqual(contacts.ingest([msg()])["duplicate"], 1)
        self.assertEqual(len(contacts.get(c["id"])["history"]), 1)

    def test_without_ref_it_is_still_deduplicated(self):
        contacts.add("x", ["a@example.com"])
        contacts.ingest([msg(ref="")])
        self.assertEqual(contacts.ingest([msg(ref="")])["duplicate"], 1)

    def test_body_is_not_stored(self):
        contacts.add("x", ["a@example.com"])
        contacts.ingest([msg(body="本文の秘密")])
        with open(store.path("contacts.json"), encoding="utf-8") as f:
            self.assertNotIn("本文の秘密", f.read())

    def test_outside_text_stays_one_short_line(self):
        c = contacts.add("x", ["a@example.com"])
        contacts.ingest([msg(subject="一\n# 二", summary="長" * 1000)])
        h = contacts.get(c["id"])["history"][0]
        self.assertEqual(h["subject"], "一 # 二")
        self.assertEqual(len(h["summary"]), contacts.SUMMARY_MAX)

    def test_unknown_address_does_not_become_a_customer(self):
        self.assertEqual(contacts.ingest([msg("news@example.org"), msg("news@example.org", "r2")])["unmatched"], 2)
        self.assertEqual(contacts.all_contacts(), [])
        self.assertEqual(contacts.unmatched()[0]["count"], 2)

    def test_domain_and_phone_match(self):
        a = contacts.add("会社", ["@example.com"])
        b = contacts.add("個人", ["+84 90-123-4567"])
        contacts.ingest([msg("x@example.com"), msg("84901234567", channel="whatsapp")])
        self.assertEqual(len(contacts.get(a["id"])["history"]), 1)
        self.assertEqual(len(contacts.get(b["id"])["history"]), 1)

    def test_exact_address_wins_over_domain(self):
        contacts.add("会社", ["@example.com"])
        b = contacts.add("担当", ["a@example.com"])
        contacts.ingest([msg()])
        self.assertEqual(len(contacts.get(b["id"])["history"]), 1)

    def test_address_cannot_belong_to_two(self):
        contacts.add("x", ["a@example.com"])
        with self.assertRaises(ValueError):
            contacts.add("y", ["A@example.com"])

    def test_scope_limits_memos_once_decided(self):
        a = contacts.add("x", ["a@example.com"], org="甲")
        b = contacts.add("y", ["b@example.com"], org="乙")
        store.set_scope(["甲"])
        got = contacts.ingest([msg(), msg("b@example.com")])
        self.assertEqual((got["added"], got["out_of_scope"]), (1, 1))
        self.assertEqual(contacts.get(b["id"])["history"], [])
        self.assertEqual(len(contacts.get(a["id"])["history"]), 1)

    def test_older_message_does_not_move_last_contact_back(self):
        c = contacts.add("x", ["a@example.com"])
        contacts.ingest([msg(), msg(ref="old", at="2026-01-01T09:00:00Z")])
        self.assertEqual(contacts.get(c["id"])["last_contact"], "2026-11-01T09:00:00+07:00")

    def test_bad_items_are_counted_and_nothing_breaks(self):
        contacts.add("x", ["a@example.com"])
        self.assertEqual(contacts.ingest([msg(at="いつか"), {"subject": "宛先なし"}, "文字列"])["bad"], 3)

    def test_damaged_file_is_not_overwritten(self):
        with open(store.path("contacts.json"), "w") as f:
            f.write("{壊れ")
        with self.assertRaises(store.StoreError):
            contacts.add("x")
        with open(store.path("contacts.json")) as f:
            self.assertEqual(f.read(), "{壊れ")


class TestReconcile(SandboxCase):
    def test_invoice_number_and_amount(self):
        r = reconcile.reconcile([inv("INV-1", "別名")], [pay("P", "誰か", memo="請求 INV-1")])
        self.assertEqual(kinds(r), ["matched"])

    def test_number_is_a_whole_token(self):
        r = reconcile.reconcile([inv("INV-1", "別名", due="2099-01-01")], [pay("P", "誰か", "500", memo="INV-10")])
        self.assertEqual(sorted(kinds(r)), ["unknown", "unpaid"])

    def test_short_number_does_not_match_a_date(self):
        r = reconcile.reconcile([inv("12", "別名", due="2099-01-01")], [pay("P", "誰か", memo="2026/12/01")])
        self.assertNotIn("matched", kinds(r))

    def test_number_with_a_different_amount_is_a_difference(self):
        r = reconcile.reconcile([inv("INV-1")], [pay("P", amount="990", memo="INV-1")])
        self.assertEqual(kinds(r), ["diff"])

    def test_same_customer_and_amount(self):
        r = reconcile.reconcile([inv("INV-1", "株式会社サンプル商事")], [pay("P", "ｻﾝﾌﾟﾙ商事")])
        self.assertEqual(kinds(r), ["matched"])

    def test_amount_alone_is_never_a_match(self):
        r = reconcile.reconcile([inv("INV-1", due="2099-01-01")], [pay("P", "別の名義")])
        self.assertEqual(sorted(kinds(r)), ["review", "unpaid"])
        unpaid = next(i for i in r["items"] if i["kind"] == "unpaid")
        self.assertEqual(unpaid["reason"], "入金の候補があります")

    def test_same_amount_invoices_of_one_customer_need_a_person(self):
        r = reconcile.reconcile([inv("A", due="2099-01-01"), inv("B", due="2099-01-01")], [pay("P")])
        self.assertNotIn("matched", kinds(r))
        self.assertEqual(kinds(r).count("review"), 1)

    def test_two_payments_for_one_invoice_are_not_both_matched(self):
        r = reconcile.reconcile([inv("A")], [pay("P1"), pay("P2", date="2026-10-21")])
        self.assertNotIn("matched", kinds(r))

    def test_paying_twice_by_number_is_flagged(self):
        r = reconcile.reconcile([inv("INV-1")], [pay("P1", memo="INV-1"), pay("P2", date="2026-10-21", memo="INV-1")])
        self.assertEqual(kinds(r), ["matched", "review"])

    def test_lump_sum_is_for_a_person(self):
        r = reconcile.reconcile([inv("A", amount="600", due="2099-01-01"), inv("B", amount="400", due="2099-01-01")], [pay("P")])
        self.assertIn("まとめ払い", next(i for i in r["items"] if i["kind"] == "review")["reason"])

    def test_currency_has_to_agree(self):
        r = reconcile.reconcile([inv("A", cur="USD", due="2099-01-01")], [pay("P", memo="A")])
        self.assertNotIn("matched", kinds(r))

    def test_payment_before_the_invoice_is_not_a_match(self):
        r = reconcile.reconcile([inv("A", issued="2026-11-01", due="2099-01-01")], [pay("P", date="2026-10-01")])
        self.assertNotIn("matched", kinds(r))

    def test_overdue_and_unpaid(self):
        r = reconcile.reconcile([inv("A", due="2026-01-01"), inv("B", due="2099-01-01")], [])
        self.assertEqual(kinds(r), ["overdue", "unpaid"])

    def test_closed_invoices_and_money_going_out_are_left_alone(self):
        r = reconcile.reconcile([inv("A", status="入金済")], [pay("P", amount="-1000")])
        self.assertEqual(r["items"], [])

    def test_unreadable_rows_are_reported(self):
        r = reconcile.reconcile([inv("A", amount="?")], [pay("P", amount="?")])
        self.assertEqual(len(r["skipped"]), 2)

    def test_rows_without_a_currency_or_id_are_reported(self):
        r = reconcile.reconcile([inv("A", cur="")], [pay("P", cur=""), pay("", memo="A")])
        self.assertEqual((r["items"][0]["kind"] if r["items"] else None, len(r["skipped"])), (None, 3))

    def test_duplicate_payment_ids_are_reported(self):
        r = reconcile.reconcile([inv("A")], [pay("P"), pay("P")])
        self.assertEqual(kinds(r), ["matched"])
        self.assertEqual(len(r["skipped"]), 1)

    def test_other_names_come_from_the_memo(self):
        contacts.add("サンプル商事", aliases=["ｻﾝﾌﾟﾙｼﾖｳｼﾞ"])
        r = reconcile.reconcile([inv("A")], [pay("P", "サンプルシヨウジ")], contacts.same_party())
        self.assertEqual(kinds(r), ["matched"])

    def test_scope_excludes_other_companies(self):
        store.set_scope(["甲"])
        r = reconcile.reconcile([inv("A", org="乙")], [])
        self.assertEqual(r["items"], [])

    def test_csv_with_japanese_headers_and_shift_jis(self):
        d = tempfile.mkdtemp(dir=self.tmp)
        ip, pp = os.path.join(d, "i.csv"), os.path.join(d, "p.csv")
        with open(ip, "w", encoding="cp932") as f:
            f.write("請求番号,取引先,金額,通貨,期日\nINV-1,サンプル商事,\"1,000\",JPY,2099-01-01\n")
        with open(pp, "w", encoding="utf-8-sig") as f:
            f.write("日付,振込人,入金額,通貨,摘要\n2026/10/20,サンプル商事,¥1000,JPY,\n")
        r = reconcile.reconcile(reconcile.load_invoices(ip), reconcile.load_payments(pp))
        self.assertEqual(kinds(r), ["matched"])

    def test_csv_missing_a_column_says_so(self):
        d = tempfile.mkdtemp(dir=self.tmp)
        p = os.path.join(d, "i.csv")
        with open(p, "w", encoding="utf-8") as f:
            f.write("請求番号,金額\nA,1\n")
        with self.assertRaises(ValueError):
            reconcile.load_invoices(p)


class ApprovalCase(SandboxCase):
    def setUp(self):
        super().setUp()
        self.moved = []
        self.addCleanup(approvals.disconnect, "payment.send")

        @approvals.money_action("payment.send")
        def send(action):
            self.moved.append(action["amount"])
            return "ok"
        self.send = send

    def req(self, **kw):
        return approvals.request(kw.pop("kind", "payment.send"), kw.pop("amount", "1000"), "JPY", "相手", {"x": 1}, **kw)

    def tamper(self, **fields):
        items = store.read_json(approvals.FILE, [])
        items[0].update(fields)
        store.write_json(approvals.FILE, items)


class TestApprovals(ApprovalCase):
    def test_nothing_moves_without_approval(self):
        r = self.req()
        with self.assertRaises(approvals.Refused):
            approvals.execute(r["id"])
        self.assertEqual(self.moved, [])

    def test_money_function_refuses_a_direct_call(self):
        r = self.req()
        for ticket in (None, "偽物", approvals.Ticket(r["id"], r["kind"], r["digest"])):
            with self.assertRaises(approvals.Refused):
                self.send(r, ticket)
        self.assertEqual(self.moved, [])

    def test_approved_runs_exactly_once(self):
        r = self.req()
        approvals.approve(r["id"], "1,000")
        self.assertEqual(approvals.execute(r["id"])["status"], "done")
        with self.assertRaises(approvals.Refused):
            approvals.execute(r["id"])
        self.assertEqual(self.moved, ["1000"])

    def test_wrong_typed_amount_is_refused(self):
        r = self.req()
        for typed in ("999", "", "はい", "yes"):
            with self.assertRaises(approvals.Refused):
                approvals.approve(r["id"], typed)
        self.assertEqual(approvals.get(r["id"])["status"], "pending")

    def test_changing_what_was_approved_cancels_it(self):
        for field in ({"amount": "9999"}, {"target": "別の相手"}, {"payload": {"x": 2}}):
            store.write_json(approvals.FILE, [])
            r = self.req()
            approvals.approve(r["id"], "1000")
            self.tamper(**field)
            with self.assertRaises(approvals.Refused):
                approvals.execute(r["id"])
        self.assertEqual(self.moved, [])

    def test_changed_before_approval_cannot_be_approved(self):
        r = self.req()
        self.tamper(amount="9999")
        with self.assertRaises(approvals.Refused):
            approvals.approve(r["id"], "9999")

    def test_rejected_cannot_run(self):
        r = self.req()
        approvals.reject(r["id"])
        with self.assertRaises(approvals.Refused):
            approvals.approve(r["id"], "1000")
        with self.assertRaises(approvals.Refused):
            approvals.execute(r["id"])

    def test_expired_cannot_be_approved_or_run(self):
        r = self.req()
        approvals.approve(r["id"], "1000")
        past = (paths.now() - timedelta(hours=1)).isoformat(timespec="seconds")
        self.tamper(expires=past)
        self.assertEqual(approvals.get(r["id"])["status"], "expired")
        with self.assertRaises(approvals.Refused):
            approvals.execute(r["id"])
        self.assertEqual(self.moved, [])

    def test_failure_is_not_retried_by_itself(self):
        calls = []

        @approvals.money_action("accounting.write")
        def boom(action):
            calls.append(1)
            raise RuntimeError("途中で失敗")
        self.addCleanup(approvals.disconnect, "accounting.write")
        r = self.req(kind="accounting.write", key="k")
        approvals.approve(r["id"], "1000")
        with self.assertRaises(RuntimeError):
            approvals.execute(r["id"])
        self.assertEqual(approvals.get(r["id"])["status"], "failed")
        with self.assertRaises(approvals.Refused):
            approvals.execute(r["id"])
        with self.assertRaises(approvals.Refused):
            self.req(kind="accounting.write", key="k")  # the same action cannot slip back in
        self.assertEqual(len(calls), 1)

    def test_retry_needs_a_new_approval(self):
        @approvals.money_action("accounting.write")
        def boom(action):
            raise RuntimeError("x")
        self.addCleanup(approvals.disconnect, "accounting.write")
        r = self.req(kind="accounting.write", key="k")
        approvals.approve(r["id"], "1000")
        with self.assertRaises(RuntimeError):
            approvals.execute(r["id"])
        again = approvals.retry(r["id"])
        self.assertNotEqual(again["id"], r["id"])
        self.assertEqual(again["status"], "pending")
        with self.assertRaises(approvals.Refused):
            approvals.execute(again["id"])

    def test_retry_can_fail_and_be_retried_again(self):
        @approvals.money_action("accounting.write")
        def boom(action):
            raise RuntimeError("x")
        self.addCleanup(approvals.disconnect, "accounting.write")
        r = self.req(kind="accounting.write", key="k")
        for _ in range(2):
            approvals.approve(r["id"], "1000")
            with self.assertRaises(RuntimeError):
                approvals.execute(r["id"])
            r = approvals.retry(r["id"])
        self.assertEqual([x["status"] for x in approvals.open_requests()], ["pending"])

    def test_crash_during_the_call_never_runs_again(self):
        r = self.req()
        approvals.approve(r["id"], "1000")
        self.tamper(status="executing")  # what a killed process leaves behind
        with self.assertRaises(approvals.Refused):
            approvals.execute(r["id"])
        self.assertEqual(self.moved, [])

    def test_not_connected_moves_nothing_and_keeps_the_approval(self):
        approvals.disconnect("payment.send")
        r = self.req()
        approvals.approve(r["id"], "1000")
        with self.assertRaises(connectors.NotConnected):
            approvals.execute(r["id"])
        self.assertEqual(approvals.get(r["id"])["status"], "approved")

    def test_same_key_does_not_make_a_second_request(self):
        a = self.req(key="k")
        self.assertEqual(self.req(key="k")["id"], a["id"])
        approvals.approve(a["id"], "1000")
        approvals.execute(a["id"])
        self.assertEqual(self.req(key="k")["status"], "done")
        self.assertEqual(self.moved, ["1000"])

    def test_after_a_rejection_it_can_be_asked_again(self):
        a = self.req(key="k")
        approvals.reject(a["id"])
        self.assertNotEqual(self.req(key="k")["id"], a["id"])

    def test_requests_must_be_complete(self):
        for bad in (dict(kind="知らない"), dict(amount="0"), dict(amount="-5"), dict(amount="nan"), dict(amount="abc")):
            with self.assertRaises(approvals.Refused):
                self.req(**bad)
        with self.assertRaises(approvals.Refused):
            approvals.request("payment.send", "1", "", "相手")
        with self.assertRaises(approvals.Refused):
            approvals.request("payment.send", "1", "JPY", " ")

    def test_every_step_is_logged(self):
        r = self.req()
        approvals.approve(r["id"], "1000")
        approvals.execute(r["id"])
        self.assertEqual([x["event"] for x in logs.read(approvals.LOG)], ["requested", "approved", "executing", "done"])

    def test_reconcile_only_proposes_matches(self):
        result = reconcile.reconcile([inv("A"), inv("B", "別", "500", due="2099-01-01")], [pay("P"), pay("Q", "別", "400")])
        made = approvals.propose_from(result)
        self.assertEqual([m["payload"]["invoice_id"] for m in made], ["A"])
        self.assertEqual(made[0]["status"], "pending")
        self.assertEqual(len(approvals.propose_from(result)), 1)
        self.assertEqual(len(approvals.open_requests()), 1)

    def test_tool_has_no_way_to_send_mail(self):
        for path in glob.glob(os.path.join(TOOL_DIR, "*.py")):
            with open(path, encoding="utf-8") as f:
                self.assertIsNone(re.search(r"smtplib|send_message|urllib|http\.client|import requests", f.read()), path)


class TestStop(ApprovalCase):
    def test_stopped_does_nothing(self):
        r = self.req()
        contacts.add("x", ["a@example.com"])
        approvals.approve(r["id"], "1000")
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(brain.main(["stop"]), 0)
        for fn in (lambda: approvals.execute(r["id"]), lambda: self.req(), lambda: contacts.ingest([msg()])):
            with self.assertRaises(store.Stopped):
                fn()
        self.assertEqual(brief.lines("2026-11-04"), [])
        self.assertEqual(self.moved, [])
        with mock.patch("sys.stdout", io.StringIO()):
            brain.main(["start"])
        self.assertEqual(approvals.execute(r["id"])["status"], "done")

    def test_stopped_in_tool_json_counts_too(self):
        root = os.path.join(self.tmp, "tools")
        os.makedirs(os.path.join(root, store.SLUG))
        with open(os.path.join(root, store.SLUG, "tool.json"), "w") as f:
            json.dump({"status": "stopped"}, f)
        os.environ["LIFEOS_TOOLS_ROOT"] = root
        self.assertTrue(store.is_stopped())


class TestBrief(ApprovalCase):
    def test_quiet_when_there_is_nothing(self):
        self.assertEqual(brief.lines("2026-11-04"), [])

    def test_counts_without_names_or_amounts(self):
        self.req(summary="相手の名前")
        reconcile.save(reconcile.reconcile([inv("A", due="2026-01-01")], [pay("P", "別", "7")]))
        text = "\n".join(brief.lines("2026-11-04"))
        self.assertIn("承認待ち：1件", text)
        self.assertIn("期限を過ぎた未入金：1件", text)
        self.assertNotIn("相手", text)
        self.assertNotIn("1000", text)

    def test_reaches_the_morning_brief(self):
        self.req()
        self.assertIn("13-brain", dict(tools.brief_parts("2026-11-04")))


class TestCli(ApprovalCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            code = brain.main(list(argv))
        return code, out.getvalue()

    def test_approve_refuses_without_a_person(self):
        r = self.req()
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        p = subprocess.run([sys.executable, os.path.join(TOOL_DIR, "brain.py"), "approve", r["id"]], env=env, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(p.returncode, 3, p.stderr)
        self.assertEqual(approvals.get(r["id"])["status"], "pending")

    def test_approve_refuses_inside_claude_even_with_a_terminal(self):
        r = self.req()
        os.environ["CLAUDECODE"] = "1"
        with mock.patch("sys.stdin") as stdin:
            stdin.isatty.return_value = True
            self.assertEqual(self.run_cli("approve", r["id"])[0], 3)
        self.assertEqual(approvals.get(r["id"])["status"], "pending")

    def test_a_person_types_the_amount(self):
        r = self.req()
        os.environ.pop("CLAUDECODE", None)
        with mock.patch("sys.stdin") as stdin, mock.patch("builtins.input", return_value="1000"):
            stdin.isatty.return_value = True
            self.assertEqual(self.run_cli("approve", r["id"])[0], 0)
        self.assertEqual(approvals.get(r["id"])["status"], "approved")

    def test_empty_answer_cancels(self):
        r = self.req()
        os.environ.pop("CLAUDECODE", None)
        with mock.patch("sys.stdin") as stdin, mock.patch("builtins.input", return_value=""):
            stdin.isatty.return_value = True
            self.assertEqual(self.run_cli("approve", r["id"])[0], 0)
        self.assertEqual(approvals.get(r["id"])["status"], "pending")

    def test_run_without_approval_says_no(self):
        r = self.req()
        code, out = self.run_cli("run", r["id"])
        self.assertEqual(code, 1)
        self.assertIn("承認されていません", out)
        self.assertEqual(self.moved, [])

    def test_contact_and_ingest_from_the_command_line(self):
        self.assertEqual(self.run_cli("contact", "add", "--name", "サンプル商事", "--addr", "a@example.com")[0], 0)
        p = os.path.join(self.tmp, "m.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump([msg()], f)
        self.assertIn("追加 1件", self.run_cli("ingest", p)[1])
        cid = contacts.all_contacts()[0]["id"]
        self.assertEqual(self.run_cli("contact", "edit", cid, "--note", "メモ")[0], 0)
        self.assertIn("メモ", self.run_cli("contact", "show", cid)[1])

    def test_nothing_connected_is_said_plainly(self):
        self.assertIn("未接続", self.run_cli("sync")[1])
        self.assertIn("未接続", self.run_cli("reconcile")[1])
        self.assertIn("未接続", self.run_cli("status")[1])

    def test_a_connected_source_is_used(self):
        self.addCleanup(connectors.use, "gmail", None)
        connectors.use("gmail", lambda since: [msg()])
        contacts.add("x", ["a@example.com"])
        self.assertIn("追加 1件", self.run_cli("sync")[1])

    def test_demo_runs_and_leaves_no_data(self):
        before = paths.data_dir()
        code, out = self.run_cli("demo")
        self.assertEqual(code, 0, out)
        self.assertIn("最後の動いた回数：1", out)
        self.assertEqual(paths.data_dir(), before)
        self.assertEqual(contacts.all_contacts(), [])
