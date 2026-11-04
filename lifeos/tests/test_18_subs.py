import contextlib
import inspect
import io
import json
import os
import re

from lifeos.lib import paths, tasks
from lifeos.lib.tools import load_tool, root_dir
from lifeos.tests.sandbox import SandboxCase


def mail_(i, day, amount="¥1,200", sender="Example Notes <billing@example.com>", subject="領収書", **kw):
    return {"id": "m%s" % i, "date": day, "from": sender, "subject": subject, "snippet": "ご請求額 " + amount, **kw}


class Base(SandboxCase):
    def setUp(self):
        super().setUp()
        self.core = load_tool("18-subs", "core")
        self.mail = load_tool("18-subs", "mail")
        self.cli = load_tool("18-subs", "subs")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cli.main(list(argv))
        return code, out.getvalue() + err.getvalue()


class TestScope(Base):
    def test_nothing_is_read_before_the_scope_is_decided(self):
        with self.assertRaises(self.core.ScopeNotDecided):
            self.core.ingest([mail_(1, "2026-10-01")])
        code, out = self.run_cli("query")
        self.assertEqual(code, 1)
        self.assertIn("決まっていません", out)
        self.assertFalse(self.core.load())

    def test_query_follows_the_scope(self):
        self.core.set_scope(6, keywords=["明細"], exclude_senders=["example.org"])
        q = self.mail.queries(self.core.get_scope())[0]
        self.assertRegex(q, r"after:\d{4}/\d{2}/\d{2} ")
        self.assertIn("明細", q)
        self.assertIn("-from:example.org", q)

    def test_scope_options_add_to_the_decided_scope(self):
        self.assertEqual(self.run_cli("scope", "--platform-sender", "store.example")[0], 1)
        self.assertIsNone(self.core.get_scope())
        self.run_cli("scope", "--months", "6")
        self.run_cli("scope", "--platform-sender", "store.example")
        scope = self.core.get_scope()
        self.assertEqual((scope["months"], scope["platform_senders"]), (6, ["store.example"]))

    def test_default_connection_says_it_is_not_connected(self):
        self.core.set_scope(12)
        code, out = self.run_cli("scan")
        self.assertEqual(code, 1)
        self.assertIn("/subs", out)

    def test_connection_can_be_replaced(self):
        self.core.set_scope(12)
        original = self.mail.fetch
        self.addCleanup(setattr, self.mail, "fetch", original)
        self.mail.fetch = lambda scope: [mail_(1, "2026-10-01", subject="月額プランの領収書")]
        code, _ = self.run_cli("scan")
        self.assertEqual(code, 0)
        self.assertEqual(self.core.get("example")["monthly"], 1200)


class TestAmount(Base):
    def test_amount_forms(self):
        cases = [("¥1,200", 1200, "JPY"), ("1,200円", 1200, "JPY"), ("$20.00", 20, "USD"), ("US$9.99", 9.99, "USD"),
                 ("USD 1,020.50", 1020.5, "USD"), ("12,50 €", None, None), ("€12,50", 12.5, "EUR"),
                 ("200.000đ", 200000, "VND"), ("₫200,000", 200000, "VND"), ("120,000 VND", 120000, "VND")]
        for text, amount, currency in cases:
            got = self.core.pick_amount(text)
            self.assertEqual(got[:2], (amount, currency), text)

    def test_total_beats_subtotal_and_tax(self):
        for text in ("subtotal $10.00 tax $1.00 total $11.00", "小計 ¥1,000 消費税 ¥100 合計 ¥1,100"):
            amount, _, sure = self.core.pick_amount(text)
            self.assertIn(amount, (11, 1100))
            self.assertTrue(sure)

    def test_several_unlabelled_amounts_are_not_sure(self):
        self.assertFalse(self.core.pick_amount("$10.00 and $3.00")[2])

    def test_bare_numbers_are_not_money(self):
        self.assertEqual(self.core.pick_amount("注文番号 123456"), (None, None, False))


class TestList(Base):
    def setUp(self):
        super().setUp()
        self.core.set_scope(12)

    def test_monthly_from_dates_and_latest_price(self):
        self.core.ingest([mail_(1, "2026-08-03"), mail_(2, "2026-09-03"), mail_(3, "2026-10-03", amount="¥1,500")])
        v = self.core.get("example")
        self.assertEqual((v["interval"], v["monthly"], v["last_charge"]), ("monthly", 1500, "2026-10-03"))

    def test_yearly_is_divided_by_twelve(self):
        self.core.ingest([mail_(1, "2026-03-01", amount="¥12,000", subject="年額プランの領収書")])
        self.assertEqual(self.core.get("example", )["monthly"], 1000)

    def test_rescan_adds_nothing(self):
        batch = [mail_(1, "2026-10-03"), mail_(2, "2026-09-03")]
        self.core.ingest(batch)
        r = self.core.ingest(batch)
        self.assertEqual((r["charges"], r["repeat"]), (0, 2))
        self.assertEqual(len(self.core.get("example")["charges"]), 2)

    def test_receipt_and_invoice_for_one_charge_count_once(self):
        self.core.ingest([mail_(1, "2026-10-03"), mail_(2, "2026-10-03", subject="請求書")])
        self.assertEqual(len(self.core.get("example")["charges"]), 1)

    def test_refund_and_failed_payment_are_not_charges(self):
        r = self.core.ingest([mail_(1, "2026-10-03", subject="返金のお知らせ"),
                              mail_(2, "2026-10-03", subject="Payment failed")])
        self.assertEqual(r["skipped"], {"not_a_charge": 2})
        self.assertFalse(self.core.load())

    def test_refund_policy_in_the_body_does_not_drop_a_receipt(self):
        m = mail_(1, "2026-10-03", subject="領収書", interval="monthly")
        m["snippet"] += " 返金ポリシーはこちら"
        self.core.ingest([m])
        self.assertEqual(self.core.get("example")["monthly"], 1200)

    def test_mail_text_is_never_stored(self):
        self.core.ingest([mail_(1, "2026-10-03", subject="領収書 ひみつの件名")])
        with open(paths.data_path("tools", "18-subs", "subs.json"), encoding="utf-8") as f:
            self.assertNotIn("ひみつ", f.read())

    def test_unknown_interval_is_asked_about_and_not_summed(self):
        self.core.ingest([mail_(1, "2026-10-03")])
        now = self.core.current("2026-10-10")
        self.assertEqual([v["id"] for v in now["unsure"]], ["example"])
        self.assertEqual(self.core.totals(now["paying"]), {})

    def test_stopped_subscription_is_not_in_the_total(self):
        self.core.ingest([mail_(1, "2026-01-03"), mail_(2, "2026-02-03")])
        now = self.core.current("2026-11-04")
        self.assertEqual([v["id"] for v in now["stale"]], ["example"])
        self.assertEqual(now["paying"], [])

    def test_platform_receipt_needs_a_service_name(self):
        self.core.set_scope(12, platform_senders=["store.example"])
        store = "Store <no-reply@store.example>"
        r = self.core.ingest([mail_(1, "2026-10-03", sender=store, subject="領収書 動画プラン"),
                              mail_(2, "2026-10-04", sender=store, service="ノート", interval="monthly", amount="¥500")])
        self.assertEqual(r["skipped"], {"platform_needs_service": 1})
        self.assertEqual(set(self.core.load()), {"ノート"})

    def test_edit_survives_a_rescan(self):
        self.core.ingest([mail_(1, "2026-10-03", interval="monthly")])
        self.core.edit("example", name="ノート", amount=900)
        self.core.ingest([mail_(2, "2026-11-03")])
        v = self.core.get("example")
        self.assertEqual((v["name"], v["monthly"]), ("ノート", 900))

    def test_totals_stay_per_currency(self):
        self.core.add_manual("甲", 1000)
        self.core.add_manual("乙", 10, "USD")
        self.core.add_manual("丙", 500)
        self.assertEqual(self.core.totals(self.core.current()["paying"]), {"JPY": 1500, "USD": 10})

    def test_works_by_hand_without_any_scope_or_mail(self):
        os.remove(paths.data_path("tools", "18-subs", "scope.json"))
        code, out = self.run_cli("add", "ノート", "1200")
        self.assertEqual(code, 0)
        self.assertIn("1,200 JPY", out)

    def test_broken_store_is_not_overwritten(self):
        p = paths.data_path("tools", "18-subs", "subs.json")
        with open(p, "w") as f:
            f.write("{broken")
        with self.assertRaises(ValueError):
            self.core.add_manual("ノート", 100)
        with open(p) as f:
            self.assertEqual(f.read(), "{broken")


class TestJudge(Base):
    def test_table_orders_and_saves(self):
        self.core.add_manual("動画プラン", 2000)
        self.core.add_manual("習慣メモ", 500)
        self.core.add_manual("大きな習慣メモ", 800)
        self.core.add_manual("なにか", 300)
        rows = self.core.judge_table()
        self.assertEqual([r["name"] for r in rows], ["大きな習慣メモ", "習慣メモ", "なにか", "動画プラン"])
        self.assertEqual([r["difficulty"] for r in rows], ["易", "易", "未判定", "不可"])
        self.assertIsNone(rows[-1]["saving"])
        self.assertEqual(rows[0]["saving"], 800)

    def test_run_cost_lowers_the_saving_and_never_below_zero(self):
        self.core.add_manual("習慣メモ", 500)
        self.assertEqual(self.core.set_judgement("習慣メモ", run_cost=200)["saving"], 300)
        self.assertEqual(self.core.set_judgement("習慣メモ", run_cost=900)["saving"], 0)

    def test_judgement_can_be_set_and_is_checked(self):
        self.core.add_manual("なにか", 300)
        self.assertEqual(self.core.set_judgement("なにか", category="automation")["difficulty"], "中")
        with self.assertRaises(ValueError):
            self.core.set_judgement("なにか", category="nope")

    def test_table_command_saves_a_report(self):
        self.core.add_manual("習慣メモ", 500)
        code, out = self.run_cli("table")
        self.assertEqual(code, 0)
        self.assertIn("| 習慣メモ |", out)
        self.assertTrue(os.path.exists(paths.data_path("tools", "18-subs", "report.md")))


class TestHandoff(Base):
    def test_replace_prints_a_newtool_line_without_the_service_name(self):
        self.core.add_manual("Example Notes", 500)
        code, out = self.run_cli("decide", "Example Notes", "replace", "--purpose", "毎日の メモを書く")
        self.assertEqual(code, 0)
        line = next(x for x in out.splitlines() if x.startswith("/newtool"))
        self.assertEqual(line, "/newtool 自作毎日のメモを書く 毎日の メモを書く")
        self.assertNotIn("example", line.lower())

    def test_replace_without_a_purpose_asks_for_one(self):
        self.core.add_manual("ノート", 500)
        code, out = self.run_cli("decide", "ノート", "replace")
        self.assertEqual(code, 0)
        self.assertNotIn("/newtool", out)
        self.assertIn("--purpose", out)
        self.assertEqual(self.core.get("ノート")["status"], "replace")

    def test_tool_name_fits_what_toolshop_accepts(self):
        self.core.add_manual("ノート", 500)
        self.core.decide("ノート", "replace", "長い用途" * 20)
        self.assertLessEqual(len(self.core.newtool_line("ノート").split(" ")[1]), 40)

    def test_keep_and_ignore_print_no_newtool_line(self):
        self.core.add_manual("ノート", 500)
        _, out = self.run_cli("decide", "ノート", "keep")
        self.assertNotIn("/newtool", out)
        self.core.decide("ノート", "ignore")
        self.assertEqual(self.core.current()["paying"], [])


class TestTrial(Base):
    def begin(self, day="2026-10-04"):
        self.core.add_manual("ノート", 500)
        self.core.decide("ノート", "replace")
        self.core.start_trial("ノート", "自作ノート", day)

    def test_trial_needs_a_replace_decision(self):
        self.core.add_manual("ノート", 500)
        with self.assertRaises(self.core.WrongState):
            self.core.start_trial("ノート", "自作ノート")

    def test_no_proposal_before_a_month(self):
        self.begin()
        for d in ("2026-10-05", "2026-10-10", "2026-10-20", "2026-11-01"):
            self.core.used("ノート", d)
        r = self.core.check("2026-11-03")
        self.assertEqual(r["proposed"], [])
        self.assertEqual(tasks.open_tasks(), [])
        self.assertEqual(self.core.get("ノート")["status"], "trial")

    def test_month_of_use_makes_one_task_and_does_not_cancel(self):
        self.begin()
        for d in ("2026-10-05", "2026-10-10", "2026-10-20", "2026-11-01"):
            self.core.used("ノート", d)
        r = self.core.check("2026-11-04")
        self.assertEqual(r["proposed"][0]["saving"], 500)
        open_now = tasks.open_tasks()
        self.assertEqual(len(open_now), 1)
        self.assertEqual(open_now[0]["source"], "lifeos-18")
        self.assertIn("解約は本人が行います", open_now[0]["note"])
        self.assertEqual(self.core.get("ノート")["status"], "proposed")
        self.core.check("2026-11-05")
        self.assertEqual(len(tasks.open_tasks()), 1)

    def test_a_month_without_records_asks_instead_of_proposing(self):
        self.begin()
        r = self.core.check("2026-11-04")
        self.assertEqual(r["proposed"], [])
        self.assertEqual(r["waiting"][0]["state"], "ask")
        self.core.confirm_month("ノート")
        self.assertEqual(len(self.core.check("2026-11-04")["proposed"]), 1)

    def test_month_end_dates_are_clamped(self):
        self.begin("2026-01-31")
        self.assertEqual(self.core.trial_state(self.core.get("ノート"), "2026-02-27")[0], "waiting")
        self.assertNotEqual(self.core.trial_state(self.core.get("ノート"), "2026-02-28")[0], "waiting")

    def test_give_up_keeps_the_subscription(self):
        self.begin()
        self.core.give_up("ノート")
        self.assertEqual(self.core.get("ノート")["status"], "keep")
        self.assertEqual(self.core.check("2026-12-31")["proposed"], [])

    def test_at_most_three_open_proposals(self):
        for i in range(4):
            name = "ノート%d" % i
            self.core.add_manual(name, 500)
            self.core.decide(name, "replace")
            self.core.start_trial(name, "自作", "2026-10-04")
            self.core.confirm_month(name)
        r = self.core.check("2026-11-04")
        self.assertEqual((len(r["proposed"]), len(r["waiting"])), (3, 1))
        self.assertEqual(len(tasks.open_tasks()), 3)

    def test_proposal_is_not_raised_again_after_keeping(self):
        self.begin()
        self.core.confirm_month("ノート")
        self.core.check("2026-11-04")
        code, _ = self.run_cli("keep-paying", "ノート", "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(self.core.check("2026-11-05")["proposed"], [])
        self.assertEqual(tasks.open_tasks(), [])

    def test_cancelled_record_needs_approval_and_closes_the_task(self):
        self.begin()
        self.core.confirm_month("ノート")
        self.core.check("2026-11-04")
        code, out = self.run_cli("cancelled", "ノート")
        self.assertIn("確認が要ります", out)
        self.assertEqual(self.core.get("ノート")["status"], "proposed")
        self.run_cli("cancelled", "ノート", "--yes")
        self.assertEqual(self.core.get("ノート")["status"], "cancelled")
        self.assertEqual(tasks.open_tasks(), [])
        self.assertEqual(self.core.current()["paying"], [])

    def test_cancelled_cannot_be_recorded_before_a_proposal(self):
        self.core.add_manual("ノート", 500)
        code, _ = self.run_cli("cancelled", "ノート", "--yes")
        self.assertEqual(code, 1)
        self.assertEqual(self.core.get("ノート")["status"], "found")

    def test_remove_needs_approval(self):
        self.core.add_manual("ノート", 500)
        self.run_cli("remove", "ノート")
        self.assertEqual(self.core.get("ノート")["name"], "ノート")
        self.run_cli("remove", "ノート", "--yes")
        with self.assertRaises(self.core.NotFound):
            self.core.get("ノート")


class TestBrief(Base):
    def test_brief_is_short_and_writes_nothing(self):
        for i in range(5):
            name = "ノート%d" % i
            self.core.add_manual(name, 500)
            self.core.decide(name, "replace")
            self.core.start_trial(name, "自作", "2026-10-04")
        brief = load_tool("18-subs", "brief")
        self.assertEqual(brief.lines("2026-10-10"), [])
        before = self.core.load()
        self.assertEqual(len(brief.lines("2026-11-04")), 3)
        self.assertEqual(self.core.load(), before)
        self.assertEqual(tasks.open_tasks(), [])


class TestSafety(Base):
    def test_code_has_no_way_to_send_cancel_or_call_out(self):
        for name in ("core", "mail", "subs", "brief"):
            src = inspect.getsource(load_tool("18-subs", name))
            for word in ("urllib", "http.client", "smtplib", "requests", "socket", "subprocess", "send_message"):
                self.assertNotIn(word, src, name)

    def test_command_names_only_reading_tools_and_preapproves_no_connector(self):
        with open(os.path.join(paths.REPO, ".claude", "commands", "subs.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("mcp__", text)
        self.assertEqual(set(re.findall(r"`(search_threads|get_thread|send_message|create_draft|trash_\w+)`", text)),
                         {"search_threads", "get_thread"})

    def test_tool_json_is_consistent(self):
        with open(os.path.join(root_dir(), "18-subs", "tool.json"), encoding="utf-8") as f:
            t = json.load(f)
        self.assertEqual(t["commands"], ["/subs"])
        self.assertIn(t["status"], ("working", "blocked"))
        if t["status"] == "blocked":
            self.assertTrue(t["blockers"])
