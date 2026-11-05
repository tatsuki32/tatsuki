"""Wise on the Desk: what is read, what is counted, and that statements stay out of the private copy."""
import json
import os
import subprocess
import tempfile
import unittest

from lifeos.tests.desk_import import server  # noqa: F401  (puts desk/ on the import path)
from lifeos.tests.sandbox import SandboxCase

import backup_data  # noqa: E402
import wise_analysis  # noqa: E402
import wise_goals  # noqa: E402
import wise_plan  # noqa: E402
import wise_report  # noqa: E402
import wise_sync  # noqa: E402
import wise_world  # noqa: E402


def act(id_, when, title="<strong>Shop</strong>", amount="10.00 USD", type_="CARD_PAYMENT", status="COMPLETED", second=""):
    return {"id": id_, "type": type_, "status": status, "title": title, "description": "", "primaryAmount": amount,
            "secondaryAmount": second, "createdOn": when, "updatedOn": when}


class FakeWise:
    def __init__(self, pages):
        self.pages, self.paths, self.rate_paths = pages, [], []

    def __call__(self, path):
        self.paths.append(path)
        if path == "/v2/profiles":
            return [{"id": 1, "type": "PERSONAL"}]
        if "/balances" in path:
            return [{"amount": {"value": 100.0, "currency": "USD"}}, {"amount": {"value": 1000.0, "currency": "JPY"}}]
        if path.startswith("/v1/rates"):
            if "time=" in path:
                self.rate_paths.append(path)
            if "source=XXX" in path:
                raise wise_sync.Stop("no rate")
            return [{"rate": 0.5}]
        n = int(path.split("nextCursor=")[1]) if "nextCursor=" in path else 0
        return {"activities": self.pages[n], "cursor": str(n + 1) if n + 1 < len(self.pages) else None}


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        old = wise_sync.DATA, backup_data.DATA
        wise_sync.DATA = backup_data.DATA = self.td.name
        self.addCleanup(lambda: (setattr(wise_sync, "DATA", old[0]), setattr(backup_data, "DATA", old[1])))


class Amounts(unittest.TestCase):
    def test_plain_and_incoming(self):
        self.assertEqual(wise_sync.parse_amount("1,234.50 USD"), (1234.5, "USD", False))
        self.assertEqual(wise_sync.parse_amount("<positive>+ 2,000 JPY</positive>"), (2000.0, "JPY", True))

    def test_unreadable_is_none_not_zero(self):
        self.assertEqual(wise_sync.parse_amount("USD, EUR"), (None, None, False))

    def test_the_date_is_ho_chi_minh(self):
        self.assertEqual(wise_sync.local_date("2026-10-31T18:00:00.000Z"), "2026-11-01")


class Sync(Base):
    def test_running_twice_adds_nothing(self):
        fake = FakeWise([[act("a", "2026-11-01T01:00:00Z")], [act("b", "2026-10-01T01:00:00Z")]])
        self.assertEqual(wise_sync.run(fake)["count"], 2)
        again = wise_sync.run(fake)
        self.assertEqual((again["count"], again["added"]), (2, 0))

    def test_a_changed_state_replaces_the_old_row(self):
        wise_sync.run(FakeWise([[act("a", "2026-11-01T01:00:00Z", status="PENDING")]]))
        wise_sync.run(FakeWise([[act("a", "2026-11-01T01:00:00Z", status="CANCELLED")]]))
        self.assertEqual(wise_sync.read_json("activities.json", {})["a"]["status"], "CANCELLED")

    def test_the_second_pass_stops_at_old_pages(self):
        old = "2020-01-01T00:00:00Z"
        wise_sync.run(FakeWise([[act("a", old)]]))
        fake = FakeWise([[act("a", old)], [act("b", old)]])
        wise_sync.run(fake)
        self.assertEqual(len([p for p in fake.paths if "/activities" in p]), 1)

    def test_a_refusal_stops_and_keeps_the_statements(self):
        wise_sync.run(FakeWise([[act("a", "2026-11-01T01:00:00Z")]]))
        def refused(path):
            raise wise_sync.Stop("断られました")
        state = wise_sync.run(refused)
        self.assertFalse(state["ok"])
        self.assertEqual(state["error"], "断られました")
        self.assertIn("a", wise_sync.read_json("activities.json", {}))

    def test_no_token_is_reported_not_raised(self):
        os.environ.pop("WISE_TOKEN", None)
        old = wise_sync.token
        wise_sync.token = lambda: None
        self.addCleanup(lambda: setattr(wise_sync, "token", old))
        self.assertIn("トークン", wise_sync.run()["error"])


class Totals(Base):
    def put(self, *rows):
        wise_sync.run(FakeWise([list(rows)]))
        return wise_sync.summary("2026-11", "2026-11-20")

    def test_every_completed_movement_counts_in_usd(self):
        s = self.put(
            act("a", "2026-11-02T01:00:00Z", amount="10.00 USD"),
            act("b", "2026-11-03T01:00:00Z", amount="5.00 USD", status="CANCELLED"),
            act("c", "2026-11-04T01:00:00Z", amount="0.00 USD", type_="CARD_CHECK", status="CANCELLED"),
            act("d", "2026-11-05T01:00:00Z", amount="100.00 USD", type_="TRANSFER"),
            act("e", "2026-11-06T01:00:00Z", amount="<positive>+ 3.00 USD</positive>"),
            act("f", "2026-11-07T01:00:00Z", amount="20.00 USD", type_="CASH_WITHDRAWAL"),
            act("g", "2026-11-08T01:00:00Z", amount="<positive>+ 500 JPY</positive>", type_="BALANCE_DEPOSIT"),
        )
        u = s["usd"]
        self.assertEqual((u["in"], u["out"], u["net"], u["count"]), (253.0, 130.0, 123.0, 5))
        self.assertEqual((u["recorded"], u["converted"]), (4, 1))
        self.assertEqual(u["types"], {"out:CARD_PAYMENT": 10.0, "out:TRANSFER": 100.0, "in:CARD_PAYMENT": 3.0, "out:CASH_WITHDRAWAL": 20.0, "in:BALANCE_DEPOSIT": 250.0})
        self.assertEqual(s["items"][0]["id"], "g")
        self.assertEqual(len(s["items"]), 7)

    def test_the_paid_currency_is_kept(self):
        s = self.put(act("a", "2026-11-02T01:00:00Z", amount="1,000 JPY", second="389.00 PHP"), act("b", "2026-11-03T01:00:00Z", amount="500 JPY"))
        self.assertEqual(s["paid"]["JPY"]["out"], 1500.0)
        self.assertEqual(s["items"][1]["amountText"], "1,000 JPY")
        self.assertEqual(s["items"][1]["secondaryText"], "389.00 PHP")

    def test_a_recorded_usd_amount_is_used_without_a_rate(self):
        fake = FakeWise([[act("a", "2026-11-02T01:00:00Z", amount="320,000 VND", second="12.34 USD")]])
        wise_sync.run(fake)
        self.assertEqual(fake.rate_paths, [])
        s = wise_sync.summary("2026-11", "2026-11-20")
        self.assertEqual((s["usd"]["out"], s["items"][0]["usdSource"]), (12.34, "record"))

    def test_the_rate_is_asked_for_the_moment_and_only_once(self):
        fake = FakeWise([[act("a", "2026-11-02T01:30:15Z", amount="1,000 JPY")]])
        wise_sync.run(fake)
        wise_sync.run(fake)
        self.assertEqual(len(fake.rate_paths), 1)
        self.assertIn("source=JPY", fake.rate_paths[0])
        self.assertIn("target=USD", fake.rate_paths[0])
        self.assertIn("time=2026-11-02T01%3A30%3A15%2B0000", fake.rate_paths[0])

    def test_the_total_equals_the_sum_of_the_rounded_rows(self):
        s = self.put(*[act(f"a{i}", f"2026-11-0{i + 1}T01:00:00Z", amount="3.33 JPY") for i in range(3)])
        self.assertEqual(s["usd"]["out"], round(sum(a["usd"] for a in s["items"]), 2))
        self.assertEqual(s["items"][0]["usd"], 1.67)

    def test_a_row_with_no_rate_stays_out_and_is_named(self):
        s = self.put(act("a", "2026-11-02T01:00:00Z", amount="50 XXX"), act("b", "2026-11-03T01:00:00Z", amount="10.00 USD"))
        self.assertEqual(s["usd"]["out"], 10.0)
        self.assertEqual(s["unconverted"], {"XXX": 1})
        self.assertEqual(wise_sync.read_json("state.json", {})["ratesMissing"], 1)

    def test_a_split_payment_converts_the_first_amount(self):
        s = self.put(act("a", "2026-11-02T01:00:00Z", amount="50 AUD", second="JPY, USD"))
        self.assertEqual((s["paid"]["AUD"]["out"], s["usd"]["out"]), (50.0, 25.0))

    def test_moving_between_own_balances_is_not_income_or_spending(self):
        s = self.put(act("a", "2026-11-02T01:00:00Z", amount="100 USD", second="15,000 JPY", type_="INTERBALANCE"))
        self.assertEqual((s["usd"]["count"], s["paid"]), (0, {}))
        self.assertEqual(len(s["items"]), 1)

    def test_another_month_stays_out(self):
        s = self.put(act("a", "2026-10-02T01:00:00Z"))
        self.assertEqual((s["usd"]["count"], s["items"]), (0, []))
        self.assertEqual(s["months"], ["2026-11", "2026-10"])


class Recurring(Base):
    def titles(self, *rows):
        wise_sync.run(FakeWise([list(rows)]))
        return [r["title"] for r in wise_sync.summary("2026-11", "2026-11-20")["recurring"]]

    def test_monthly_at_a_steady_price(self):
        self.assertEqual(self.titles(act("a", "2026-09-02T01:00:00Z", "Tool"), act("b", "2026-10-02T01:00:00Z", "Tool")), ["Tool"])

    def test_one_month_is_not_monthly(self):
        self.assertEqual(self.titles(act("a", "2026-10-02T01:00:00Z", "Tool")), [])

    def test_a_shop_visited_often_is_left_out(self):
        rows = [act(f"s{i}", f"2026-10-{i + 1:02d}T01:00:00Z", "Mart") for i in range(5)] + [act("t", "2026-11-01T01:00:00Z", "Mart")]
        self.assertEqual(self.titles(*rows), [])

    def test_a_changing_price_is_left_out(self):
        self.assertEqual(self.titles(act("a", "2026-09-02T01:00:00Z", "Bar", "10.00 USD"), act("b", "2026-10-02T01:00:00Z", "Bar", "80.00 USD")), [])

    def test_stopped_long_ago_is_left_out(self):
        self.assertEqual(self.titles(act("a", "2026-05-02T01:00:00Z", "Old"), act("b", "2026-06-02T01:00:00Z", "Old")), [])


class Backup(Base):
    def test_statements_are_not_sent_by_the_backup(self):
        git = lambda *a: subprocess.run(["git", "-C", self.td.name, *a], check=True, capture_output=True, text=True)
        git("init", "-q", "-b", "main")
        with open(os.path.join(self.td.name, ".gitignore"), "w") as f:
            f.write("*.lock\n")
        wise_sync.run(FakeWise([[act("a", "2026-11-01T01:00:00Z")]]))
        self.assertEqual([p for p in backup_data.changed_files() if p.startswith("wise")], [])
        self.assertTrue(os.path.exists(os.path.join(self.td.name, "wise", "activities.json")))

    def test_ensure_ignore_keeps_what_is_there(self):
        with open(os.path.join(self.td.name, ".gitignore"), "w") as f:
            f.write("mine.txt")
        backup_data.ensure_ignore()
        backup_data.ensure_ignore()
        with open(os.path.join(self.td.name, ".gitignore")) as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], "mine.txt")
        self.assertEqual(lines.count("wise/"), 1)


class Privacy(Base):
    def test_files_are_for_this_user_only(self):
        wise_sync.run(FakeWise([[act("a", "2026-11-01T01:00:00Z")]]))
        mode = os.stat(os.path.join(self.td.name, "wise", "activities.json")).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_the_state_file_holds_no_statement_line(self):
        wise_sync.run(FakeWise([[act("a", "2026-11-01T01:00:00Z", "SECRETSHOP")]]))
        self.assertNotIn("SECRETSHOP", json.dumps(wise_sync.read_json("state.json", {})))


class Server(SandboxCase, Base):
    def setUp(self):
        SandboxCase.setUp(self)
        Base.setUp(self)
        today = wise_sync.summary()["months"][0]
        last = wise_sync.month_add(today, -1)
        wise_sync.run(FakeWise([[act("a", last + "-02T01:00:00Z", "Tool", "10.00 USD"), act("b", today + "-01T01:00:00Z", "Tool", "12.00 USD")]]))

    def test_a_bad_month_is_refused(self):
        self.assertEqual(server.wise_view({"month": "2026-13"})[0], 400)
        self.assertEqual(server.wise_view({"month": "../x"})[0], 400)
        self.assertEqual(server.wise_view({"period": "nope"})[0], 400)

    def test_a_candidate_reaches_life_os_18_with_the_statement_amount(self):
        code, view = server.wise_view()
        self.assertEqual(code, 200)
        self.assertFalse(view["recurring"][0]["inSubs"])
        key = view["recurring"][0]["key"]
        self.assertEqual(server.wise_to_subs({"key": key, "amount": 1})[0], 200)
        self.assertTrue(server.wise_view()[1]["recurring"][0]["inSubs"])
        sub = list(server.subs_core().load().values())[0]
        self.assertEqual((sub["manual"]["amount"], sub["manual"]["currency"]), (12.0, "USD"))

    def test_an_unknown_candidate_adds_nothing(self):
        self.assertEqual(server.wise_to_subs({"key": "nope|USD"})[0], 404)
        self.assertEqual(server.subs_core().load(), {})


class Periods(unittest.TestCase):
    TODAY = __import__("datetime").date(2026, 11, 5)  # a Thursday

    def range_of(self, *args, **kw):
        first, last, _, before = wise_report.period_range(*args, today=self.TODAY, **kw)
        return first.isoformat(), last.isoformat(), before and (before[0].isoformat(), before[1].isoformat())

    def test_this_week_starts_on_monday(self):
        self.assertEqual(self.range_of("week"), ("2026-11-02", "2026-11-05", ("2026-10-26", "2026-10-29")))

    def test_a_month_is_the_whole_month(self):
        self.assertEqual(self.range_of("month", "2026-02"), ("2026-02-01", "2026-02-28", ("2026-01-01", "2026-01-31")))

    def test_three_and_six_months(self):
        self.assertEqual(self.range_of("3m")[:2], ("2026-08-06", "2026-11-05"))
        self.assertEqual(self.range_of("6m")[:2], ("2026-05-06", "2026-11-05"))

    def test_a_short_month_does_not_break_months_back(self):
        self.assertEqual(wise_report.months_back(__import__("datetime").date(2026, 5, 31), 3).isoformat(), "2026-02-28")

    def test_custom_and_bad_ranges(self):
        self.assertEqual(self.range_of("custom", start="2026-01-01", end="2026-01-10")[2], ("2025-12-22", "2025-12-31"))
        for kw in ({"start": "2026-02-01", "end": "2026-01-01"}, {"start": "x", "end": "y"}, {}):
            with self.assertRaises(ValueError):
                self.range_of("custom", **kw)


class Report(Base):
    TODAY = __import__("datetime").date(2026, 11, 20)

    def put(self, *rows):
        wise_sync.run(FakeWise([list(rows)]))
        return wise_report.view("month", "2026-11", today=self.TODAY)

    def test_categories_rules_type_and_the_persons_choice(self):
        self.assertEqual(wise_report.category_of({"title": "Starbucks", "type": "CARD_PAYMENT"}), "外食・カフェ")
        self.assertEqual(wise_report.category_of({"title": "Anyone", "type": "TRANSFER"}), "送金")
        self.assertEqual(wise_report.category_of({"title": "Unknown Shop", "type": "CARD_PAYMENT"}), wise_report.UNSORTED)
        self.assertEqual(wise_report.category_of({"title": "Unknown Shop", "type": "CARD_PAYMENT"}, {"unknown shop": "医療"}), "医療")

    def test_category_totals_add_up_to_the_spending(self):
        v = self.put(act("a", "2026-11-02T01:00:00Z", "Coles", "10.00 USD"), act("b", "2026-11-03T01:00:00Z", "Zed", "5.50 USD"),
                     act("c", "2026-11-04T01:00:00Z", "Me", "7.25 USD", "TRANSFER"), act("d", "2026-11-05T01:00:00Z", "ATM", "20.00 USD", "CASH_WITHDRAWAL"))
        self.assertEqual(round(sum(c["usd"] for c in v["categories"]), 2), v["spent"])

    def test_spending_is_card_and_cash_less_refunds_not_transfers(self):
        v = self.put(act("a", "2026-11-02T01:00:00Z", amount="10.00 USD"), act("b", "2026-11-03T01:00:00Z", amount="<positive>+ 3.00 USD</positive>"),
                     act("c", "2026-11-04T01:00:00Z", amount="20.00 USD", type_="CASH_WITHDRAWAL"), act("d", "2026-11-05T01:00:00Z", amount="500.00 USD", type_="TRANSFER"))
        self.assertEqual(v["spent"], 27.0)

    def test_pace_needs_a_limit_and_projects_the_month(self):
        self.assertEqual(self.put(act("a", "2026-11-02T01:00:00Z", amount="100.00 USD"))["pace"], {})
        wise_sync.write_json("limits.json", {"month": 120.0, "week": None})
        v = wise_report.view("month", "2026-11", today=self.TODAY)
        self.assertEqual(v["pace"]["month"]["left"], 20.0)
        self.assertEqual(v["pace"]["month"]["forecast"], 150.0)
        self.assertIn("超えそう", v["finding"]["fact"])
        self.assertNotIn("week", v["pace"])

    def test_cash_is_the_finding_when_it_is_large(self):
        v = self.put(act("a", "2026-11-02T01:00:00Z", amount="10.00 USD"), act("b", "2026-11-03T01:00:00Z", amount="30.00 USD", type_="CASH_WITHDRAWAL"))
        self.assertIn("現金", v["finding"]["fact"])
        self.assertTrue(v["finding"]["lesson"]["url"].startswith("https://"))

    def test_findings_do_not_blame_or_advise_on_products(self):
        v = self.put(act("a", "2026-11-02T01:00:00Z", amount="10.00 USD"))
        text = str(v["finding"]) + str(v["lessons"])
        for word in ("使いすぎ", "ダメ", "だらしない", "NISA", "銘柄", "買うべき"):
            self.assertNotIn(word, text)

    def test_reconcile_shows_the_gap_in_the_chosen_currency(self):
        self.put(act("a", "2026-11-02T01:00:00Z", amount="1,000 JPY"), act("b", "2026-11-03T01:00:00Z", amount="10.00 USD"))
        wise_sync.write_json("recon.json", {"2026-11": {"in": None, "out": 1200.0, "currency": "JPY"}})
        r = wise_report.view("month", "2026-11", today=self.TODAY)["recon"]
        self.assertEqual((r["oursOut"], r["diffOut"], r["diffIn"]), (1000.0, -200.0, None))

    def test_the_balances_snapshot_is_kept_with_usd(self):
        v = self.put(act("a", "2026-11-02T01:00:00Z"))
        self.assertEqual(v["balances"]["usd"], 600.0)
        self.assertEqual(len(v["balances"]["rows"]), 2)


class Files(Base):
    def data(self):
        wise_sync.run(FakeWise([[act("a", "2026-11-02T01:00:00Z", "=HYPERLINK(1)", "1,000 JPY", second="389.00 PHP"), act("b", "2026-11-03T01:00:00Z", "<b>日本語</b>", "5.00 USD", status="CANCELLED")]]))
        return wise_report.view("month", "2026-11", today=__import__("datetime").date(2026, 11, 20))

    def test_csv_opens_in_excel_and_cannot_run_a_formula(self):
        raw = wise_report.to_csv(self.data())
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        text = raw.decode("utf-8-sig")
        self.assertIn("'=HYPERLINK(1)", text)
        self.assertEqual(len(text.strip().split("\r\n")), 3)
        self.assertIn('500.0,レートで換算,1000.0,JPY,"1,000 JPY",389.00 PHP', text)

    def test_the_report_escapes_titles_and_keeps_rows_to_the_list(self):
        wise_sync.write_json("categories.json", {})
        d = self.data()
        d["items"][0]["title"] = "<script>x</script>"
        report, full = wise_report.to_html(d), wise_report.to_html(d, "list")
        self.assertNotIn("<script>x", full)
        self.assertIn("&lt;script&gt;x", full)
        self.assertNotIn("件</h2>", report)
        self.assertIn("出入金 2件", full)

    def test_a_bad_format_is_refused(self):
        with self.assertRaises(ValueError):
            wise_report.export("exe", period="month")

    def test_a_file_name_has_the_period(self):
        self.data()
        _, ctype, name = wise_report.export("csv", period="custom", start="2026-11-01", end="2026-11-30")
        self.assertEqual((ctype, name), ("text/csv; charset=utf-8", "wise-list-2026-11-01_2026-11-30.csv"))


class Settings(SandboxCase, Base):
    def setUp(self):
        SandboxCase.setUp(self)
        Base.setUp(self)

    def test_a_category_is_kept_by_title(self):
        self.assertEqual(server.wise_settings(["category"], {"title": "Zed Shop", "category": "医療"})[0], 200)
        self.assertEqual(wise_sync.read_json("categories.json", {}), {"zed shop": "医療"})
        self.assertEqual(server.wise_settings(["category"], {"title": "Zed", "category": "nope"})[0], 400)

    def test_limits_and_recon_refuse_bad_numbers(self):
        self.assertEqual(server.wise_settings(["limits"], {"week": "100", "month": ""})[0], 200)
        self.assertEqual(wise_sync.read_json("limits.json", {}), {"week": 100.0, "month": None})
        self.assertEqual(server.wise_settings(["limits"], {"week": "-1"})[0], 400)
        self.assertEqual(server.wise_settings(["limits"], {"week": "abc"})[0], 400)
        self.assertEqual(server.wise_settings(["recon", "2026-13"], {})[0], 404)
        self.assertEqual(server.wise_settings(["recon", "2026-11"], {"out": "5", "currency": "jpy"})[0], 200)
        self.assertEqual(wise_sync.read_json("recon.json", {})["2026-11"]["currency"], "JPY")
        self.assertEqual(server.wise_settings(["../x"], {})[0], 404)


class Analysis(SandboxCase, Base):
    """Fake people and shops only: real titles stay in desk/data/wise/."""

    def setUp(self):
        SandboxCase.setUp(self)
        Base.setUp(self)

    def row(self, id_, date, usd, flow="out", type_="CARD_PAYMENT", title="Shop", currency="USD", value=None, category=None):
        return {"id": id_, "date": date, "createdOn": date + "T03:00:00Z", "flow": flow, "type": type_, "title": title, "currency": currency,
                "value": usd if value is None else value, "usd": usd, "kind": None, "category": category or "交通", "status": "COMPLETED", "amountText": f"{usd} USD"}

    def label(self, rows):
        return wise_analysis.enrich(rows, lambda a, overrides: a["category"])

    def test_a_refund_lowers_spending_and_a_move_is_not_spending(self):
        rows = self.label([self.row("a", "2026-10-01", 30), self.row("b", "2026-10-02", 5, flow="in"),
                           self.row("c", "2026-10-03", 100, flow="in", type_="BALANCE_DEPOSIT", title="To JPY")])
        self.assertEqual(wise_analysis.spent_total(rows), 25)
        self.assertEqual(rows[2]["kind"], "self")

    def test_a_transfer_stays_unknown_until_someone_says(self):
        rows = self.label([self.row("a", "2026-10-01", 30, type_="TRANSFER", title="Someone")])
        self.assertEqual(rows[0]["kind"], "unknown")
        self.assertEqual(wise_analysis.spent_total(rows), 0)

    def test_the_persons_choice_beats_the_proposal(self):
        wise_sync.write_json("guess.json", {"flows": {"out:someone": {"kind": "advance", "sure": False, "why": "x"}}})
        rows = self.label([self.row("a", "2026-10-01", 30, type_="TRANSFER", title="Someone")])
        self.assertEqual((rows[0]["kind"], rows[0]["kindSure"]), ("advance", False))
        wise_sync.write_json("flows.json", {"out:someone": "spend"})
        rows = self.label([self.row("a", "2026-10-01", 30, type_="TRANSFER", title="Someone")])
        self.assertEqual((rows[0]["kind"], rows[0]["kindSure"]), ("spend", True))
        self.assertEqual(wise_analysis.spent_total(rows), 30)

    def test_the_chart_series_add_up_to_the_rows(self):
        rows = self.label([self.row(str(i), f"2026-{m:02d}-{d:02d}", 10 + d) for i, (m, d) in enumerate([(8, 1), (9, 2), (9, 3), (10, 4)])])
        z = wise_analysis.analyze(rows, "2026-11-05", "2026-10")
        self.assertEqual(sum(m["spent"] for m in z["months"]), wise_analysis.spent_total(rows))
        self.assertEqual(sum(d["spent"] for d in z["days"]), 14)
        self.assertEqual(z["usual"]["months"], 3)

    def test_this_month_is_compared_with_the_same_days(self):
        rows = self.label([self.row("a", "2026-10-03", 10), self.row("b", "2026-10-25", 500), self.row("c", "2026-11-02", 25)])
        z = wise_analysis.analyze(rows, "2026-11-05")
        self.assertEqual((z["soFar"], z["lastSame"], z["cut"]), (25, 10, 5))

    def test_yesterday(self):
        rows = self.label([self.row("a", "2026-11-04", 3), self.row("b", "2026-11-03", 4), self.row("c", "2026-10-28", 5)])
        z = wise_analysis.analyze(rows, "2026-11-05")["yesterday"]
        self.assertEqual((z["usd"], z["before"], z["lastWeek"]), (3, 4, 5))

    def test_a_rate_change_is_told_apart_from_buying_more(self):
        rows = self.label([self.row("a", "2026-10-01", 100, currency="JPY", value=10000), self.row("b", "2026-11-01", 120, currency="JPY", value=10000)])
        fx = wise_analysis.analyze(rows, "2026-11-05")["fx"]
        self.assertEqual((fx["use"], fx["rate"], fx["show"]), (0, 20, True))

    def test_fx_parts_add_up_to_the_change(self):
        rows = self.label([self.row("a", "2026-10-01", 100, currency="JPY", value=10000), self.row("b", "2026-11-01", 150, currency="JPY", value=12000),
                           self.row("c", "2026-11-02", 7, currency="EUR", value=6)])
        fx = wise_analysis.analyze(rows, "2026-11-05")["fx"]
        self.assertAlmostEqual(fx["use"] + fx["rate"], 57, places=2)

    def test_outliers_need_six_months_of_history(self):
        few = self.label([self.row(str(m), f"2026-{m:02d}-01", 10) for m in range(8, 11)] + [self.row("x", "2026-11-01", 500)])
        self.assertEqual(wise_analysis.analyze(few, "2026-11-05")["outliers"], [])
        many = self.label([self.row(str(m), f"2026-{m:02d}-01", 10 + m) for m in range(1, 11)] + [self.row("x", "2026-11-01", 500)])
        self.assertEqual(wise_analysis.analyze(many, "2026-11-05")["outliers"][0]["name"], "交通")

    def test_a_monthly_charge_is_found_and_a_kept_one_is_not_asked_again(self):
        rows = self.label([self.row(str(m), f"2026-{m:02d}-05", 9, title="Tool") for m in range(7, 12)])
        z = wise_analysis.analyze(rows, "2026-11-06")
        self.assertEqual(z["charges"][0]["yearly"], 108)
        self.assertEqual(z["action"]["kind"], "charge")
        wise_sync.write_json("recurring.json", {z["charges"][0]["key"]: {"reviewed": "2026-11-06", "choice": "keep"}})
        self.assertNotEqual((wise_analysis.analyze(rows, "2026-11-06")["action"] or {}).get("kind"), "charge")

    def test_marked_not_monthly_leaves_the_list(self):
        rows = self.label([self.row(str(m), f"2026-{m:02d}-05", 9, title="Tool") for m in range(7, 12)])
        wise_sync.write_json("recurring.json", {"tool|USD": {"mark": False}})
        self.assertEqual(wise_analysis.analyze(rows, "2026-11-06")["charges"], [])

    def test_accounts_are_names_only_and_add_up(self):
        rows = self.label([self.row("a", "2026-10-01", 10), self.row("b", "2026-10-01", 5, category="外食・カフェ"), self.row("c", "2026-10-01", 2, category="未分類")])
        jp = {r["name"]: r["usd"] for r in wise_analysis.accounts(rows)["jp"]}
        self.assertEqual(jp, {"旅費交通費": 10, "生活費": 5, "使途不明": 2})

    def test_the_gap_between_records_and_the_balance_is_shown(self):
        wise_sync.write_json("balances.json", {"2026-11-05": [{"currency": "USD", "value": 50, "usd": 50}]})
        rows = self.label([self.row("a", "2026-10-01", 100, flow="in", type_="TRANSFER"), self.row("b", "2026-10-02", 90)])
        self.assertEqual(wise_analysis.analyze(rows, "2026-11-05")["bridge"]["gap"], 40)

    def test_advice_with_a_number_not_on_the_page_is_refused(self):
        allowed = wise_analysis.numbers_in({"a": 1234.5, "b": [99.25]})
        self.assertIsNone(wise_analysis.check_advice({"fact": "支出は 1,234.50 USD で、3か月の中央値は 99.25 です。", "next": "明細を見る"}, allowed))
        self.assertIn("計算にない", wise_analysis.check_advice({"fact": "支出は 5,000 USD です。", "next": "明細を見る"}, allowed))

    def test_advice_never_blames(self):
        self.assertIn("使わない言葉", wise_analysis.check_advice({"fact": "今月は使いすぎです。", "next": "見直す"}, set()))
        self.assertIn("必要", wise_analysis.check_advice({"fact": "事実だけ"}, set()))

    def test_a_person_setting_goes_through_the_server(self):
        self.assertEqual(server.wise_settings(["kind"], {"title": "Someone", "flow": "out", "kind": "advance"})[0], 200)
        self.assertEqual(wise_sync.read_json("flows.json", {})["out:someone"], "advance")
        self.assertEqual(server.wise_settings(["kind"], {"title": "Someone", "flow": "out", "kind": "gift"})[0], 400)
        self.assertEqual(server.wise_settings(["recurring"], {"key": "a|USD", "choice": "keep"})[0], 200)
        self.assertEqual(server.wise_settings(["close", "2026-11"], {"recon": True})[0], 200)
        self.assertEqual(wise_sync.read_json("close.json", {})["2026-11"], {"recon": True, "review": False, "next": False})

    def test_seen_keeps_the_day_before(self):
        wise_sync.write_json("seen.json", {"last": "2026-01-01"})
        server.wise_settings(["seen"], {})
        self.assertEqual(wise_sync.read_json("seen.json", {})["previous"], "2026-01-01")


if __name__ == "__main__":
    unittest.main()


class Plan(SandboxCase, Base):
    def setUp(self):
        SandboxCase.setUp(self)
        Base.setUp(self)

    def spent(self, day, usd, currency="THB", value=None):
        return {"date": day, "kind": "spend", "usd": usd, "currency": currency, "value": value or usd * 30, "flow": "out"}

    def test_paid_is_shown_but_not_counted_again(self):
        wise_plan.put_upcoming({"items": [{"id": "a", "title": "Fake Air", "kind": "flight", "date": "2026-12-01", "paid": {"value": 200, "currency": "USD"}, "due": {"value": 50, "currency": "USD"}}]}, "2026-11-05")
        h = wise_plan.ahead([], "2026-11-05")
        self.assertEqual((h["items"][0]["paidUsd"], h["due"], h["total"]), (200, 50, 50))

    def test_a_trip_is_estimated_from_ones_own_days_in_that_currency(self):
        rows = [self.spent(f"2026-01-{d:02d}", 10 + d) for d in range(1, 8)]
        wise_plan.put_upcoming({"items": [{"id": "a", "title": "Fake Trip", "date": "2026-12-01", "end": "2026-12-03", "currency": "THB"}]}, "2026-11-05")
        item = wise_plan.ahead(rows, "2026-11-05")["items"][0]
        self.assertEqual((item["estimate"], item["days"], item["basedOn"]), (42, 3, 7))
        self.assertEqual((item["low"], item["high"]), (29.4, 54.6))

    def test_no_history_means_no_estimate(self):
        wise_plan.put_upcoming({"items": [{"id": "a", "title": "Fake Trip", "date": "2026-12-01", "end": "2026-12-03", "currency": "ISK"}]}, "2026-11-05")
        self.assertIsNone(wise_plan.ahead([], "2026-11-05")["items"][0]["estimate"])

    def test_an_amount_with_no_rate_is_named_and_left_out(self):
        wise_plan.put_upcoming({"items": [{"id": "a", "title": "x", "date": "2026-12-01", "due": {"value": 5, "currency": "ISK"}}]}, "2026-11-05")
        h = wise_plan.ahead([], "2026-11-05")
        self.assertEqual((h["unknown"], h["total"]), (1, 0))

    def test_the_past_and_the_hidden_do_not_show(self):
        wise_plan.put_upcoming({"items": [{"id": "old", "title": "x", "date": "2026-01-01"}, {"id": "gone", "title": "y", "date": "2026-12-01"}, {"id": "ok", "title": "z", "date": "2026-12-02"}]}, "2026-11-05")
        self.assertEqual(server.wise_settings(["upcoming-hide"], {"id": "gone"})[0], 200)
        self.assertEqual([i["id"] for i in wise_plan.ahead([], "2026-11-05")["items"]], ["ok"])

    def test_mail_text_cannot_be_saved(self):
        wise_plan.put_upcoming({"items": [{"id": "a", "title": "x", "date": "2026-12-01", "body": "the whole mail", "snippet": "more"}]}, "2026-11-05")
        self.assertNotIn("the whole mail", json.dumps(wise_sync.read_json("upcoming.json", {})))

    def test_a_bad_record_is_dropped(self):
        result = wise_plan.put_upcoming({"items": [{"id": "a", "title": "x", "date": "soon"}, {"id": "b", "title": "x", "date": "2026-12-05", "end": "2026-12-01"}]}, "2026-11-05")
        self.assertEqual((result["saved"], result["dropped"]), (0, 2))

    def test_the_runway_with_what_is_coming(self):
        wise_plan.put_upcoming({"items": [{"id": "a", "title": "x", "date": "2026-12-01", "due": {"value": 200, "currency": "USD"}}]}, "2026-11-05")
        self.assertEqual(wise_plan.ahead([], "2026-11-05", {"balance": 1000}, {"usd": 400})["runwayAfter"], 2)

    def good_tip(self, **more):
        return {"topic": "t1", "title": "題", "body": "本文", "source": "統計", "url": "https://example.org/a", "year": 2024, **more}

    def test_a_tip_needs_a_source_and_a_year(self):
        self.assertFalse(wise_plan.put_tip(self.good_tip(url="nope"), "2026-11-05")["saved"])
        self.assertFalse(wise_plan.put_tip(self.good_tip(year="2024"), "2026-11-05")["saved"])
        self.assertTrue(wise_plan.put_tip(self.good_tip(), "2026-11-05")["saved"])

    def test_a_tip_is_not_repeated_and_does_not_blame(self):
        wise_plan.put_tip(self.good_tip(), "2026-11-05")
        self.assertFalse(wise_plan.put_tip(self.good_tip(url="https://example.org/b"), "2026-11-06")["saved"])
        self.assertFalse(wise_plan.put_tip(self.good_tip(topic="t2", url="https://example.org/c", body="使いすぎです"), "2026-11-06")["saved"])

    def test_an_old_tip_leaves_the_front_page(self):
        wise_plan.put_tip(self.good_tip(), "2026-11-01")
        self.assertIsNotNone(wise_plan.today_tip("2026-11-03"))
        self.assertIsNone(wise_plan.today_tip("2026-11-04"))

    def test_age(self):
        self.assertEqual(wise_plan.age_on("2000-03-14", "2026-03-13"), 25)
        self.assertEqual(wise_plan.age_on("2000-03-14", "2026-03-14"), 26)


class World(SandboxCase, Base):
    def setUp(self):
        SandboxCase.setUp(self)
        Base.setUp(self)

    def fake(self, path):
        if path.startswith("/country"):
            return [{}, [{"id": "AAA", "iso2Code": "AA", "region": {"value": "Asia"}}, {"id": "BBB", "iso2Code": "BB", "region": {"value": "Asia"}}, {"id": "WLD", "iso2Code": "1W", "region": {"value": "Aggregates"}}]]
        series = path.split("/series/")[1].split("/")[0]
        row = lambda code, v: {"value": v, "variable": [{"concept": "Country", "id": code}]}
        values = {"9020000": [("AAA", 1200), ("WLD", 9999), ("BBB", None)], "1107000": [("AAA", 120)], "1103000": [("AAA", 60)], "1105000": []}
        return {"source": {"data": [row(c, v) for c, v in values.get(series, [])]}}

    def test_only_countries_with_a_value_are_kept_and_shares_are_of_the_total(self):
        countries = wise_world.fetch(self.fake)
        self.assertEqual(list(countries), ["AAA"])
        self.assertEqual((countries["AAA"]["month"], countries["AAA"]["uses"]["transport"]), (100, 10))

    def test_a_use_made_of_two_series_needs_both(self):
        self.assertNotIn("goods", wise_world.fetch(self.fake)["AAA"]["uses"])

    def test_a_failed_fetch_keeps_the_old_table(self):
        wise_sync.write_json("world.json", {"at": 1, "countries": {"AAA": {}}})
        def down(path):
            raise OSError("no network")
        self.assertFalse(wise_world.update(down))
        self.assertEqual(list(wise_sync.read_json("world.json", {})["countries"]), ["AAA"])

    def test_my_row_uses_finished_months_and_names_what_it_cannot_place(self):
        rows = [{"date": "2026-10-01", "kind": "spend", "usd": 60, "category": "交通"}, {"date": "2026-10-02", "kind": "spend", "usd": 60, "category": "現金"},
                {"date": "2026-11-01", "kind": "spend", "usd": 500, "category": "交通"}, {"date": "2024-01-01", "kind": "spend", "usd": 500, "category": "交通"}]
        me = wise_world.mine(rows, "2026-11-05")
        self.assertEqual((me["month"], me["uses"]["transport"], me["unknownShare"]), (10, 50, 50))

    def test_a_payment_straight_from_the_bank_leaves_the_balance_check_but_stays_spending(self):
        wise_sync.write_json("balances.json", {"2026-11-05": [{"currency": "USD", "value": 100, "usd": 100}]})
        rows = lambda: wise_analysis.enrich([{"id": "i", "date": "2026-10-01", "flow": "in", "type": "TRANSFER", "title": "Pay", "usd": 100, "value": 100, "currency": "USD"},
                                             {"id": "o", "date": "2026-10-02", "flow": "out", "type": "TRANSFER", "title": "School", "usd": 900, "value": 900, "currency": "USD"}], lambda a, o: "学び")
        wise_sync.write_json("flows.json", {"out:school": "spend"})
        self.assertEqual(wise_analysis.analyze(rows(), "2026-11-05")["bridge"]["gap"], 900)
        self.assertEqual(server.wise_settings(["direct"], {"title": "School", "on": True})[0], 200)
        z = wise_analysis.analyze(rows(), "2026-11-05")
        self.assertEqual(z["bridge"]["gap"], 0)
        self.assertEqual(z["months"][-2]["spent"], 900)


class Wages(SandboxCase, Base):
    PAGE = "<table><tr><td>Header</td></tr><tr><td>Vietnam<sup>1</sup></td><td>note</td><td>2,400</td><td></td><td>40</td><td>1.3</td><td></td><td></td><td>1 Jan 2026</td></tr><tr><td>Nowhere</td><td>n</td><td>100</td><td>1</td><td>40</td><td>1</td><td>1</td><td>1%</td><td>2020</td></tr></table>"

    def test_a_row_becomes_a_monthly_amount_with_its_date(self):
        got = wise_world.parse_wages(self.PAGE, {"VNM": "Viet Nam"})
        self.assertEqual(got, {"VNM": {"month": 200, "monthPpp": None, "date": "1 Jan 2026"}})

    def test_a_failed_wage_fetch_keeps_the_old_wages(self):
        def fake(path):
            if path.startswith("/country"):
                return [{}, [{"id": "AAA", "iso2Code": "AA", "name": "Aland", "region": {"value": "Asia"}}]]
            return {"source": {"data": [{"value": 1200, "variable": [{"concept": "Country", "id": "AAA"}]}]}}
        wise_sync.write_json("world.json", {"at": 1, "minwage": {"AAA": {"month": 1}}})
        def down(places):
            raise OSError("no")
        self.assertTrue(wise_world.update(fake, down))
        self.assertEqual(wise_sync.read_json("world.json", {})["minwage"], {"AAA": {"month": 1}})


class Goals(SandboxCase, Base):
    def setUp(self):
        SandboxCase.setUp(self)
        Base.setUp(self)

    def row(self, day, usd, category="交通", kind="spend"):
        return {"date": day, "kind": kind, "usd": usd, "category": category, "group": "交通費", "currency": "USD", "value": usd, "flow": "out", "title": "x", "amountText": ""}

    def test_any_period_is_compared_with_the_one_before(self):
        rows = [self.row("2026-05-10", 100), self.row("2026-08-10", 30), self.row("2026-10-10", 20)]
        z = wise_analysis.analyze(rows, "2026-11-05", None, "2026-08-06", "2026-11-05", ("2026-05-06", "2026-08-05"), "直近3か月")
        self.assertEqual((z["scope"], z["soFar"], z["lastSame"]), ("直近3か月", 50, 100))
        self.assertEqual(z["categories"][0]["change"], -50)

    def test_the_whole_history_has_no_before_but_has_a_month_average(self):
        rows = [self.row("2026-01-10", 100), self.row("2026-03-10", 200)]
        z = wise_analysis.analyze(rows, "2026-03-31", None, "2026-01-10", "2026-03-31", None, "計測の最初から")
        self.assertEqual((z["soFar"], z["lastSame"], z["perMonth"]), (300, 0, 100))
        self.assertEqual([m["month"] for m in z["months"]], ["2026-01", "2026-02", "2026-03"])

    def test_this_weeks_step_does_not_change_with_the_period(self):
        rows = [self.row(f"2026-{m:02d}-05", 9) for m in range(7, 12)]
        for m in rows:
            m["title"] = "Tool"
        month = wise_analysis.analyze(rows, "2026-11-06")["action"]
        wide = wise_analysis.analyze(rows, "2026-11-06", None, "2026-07-01", "2026-11-06", None, "x")["action"]
        self.assertEqual(month, wide)

    def test_budget_room_and_a_past_overrun(self):
        wise_goals.put_budget({"total": 100, "cats": {"交通": 40}}, ("交通",))
        b = wise_goals.budget([self.row("2026-11-02", 60), self.row("2026-10-02", 130)], "2026-11-21")
        self.assertEqual((b["now"]["left"], b["now"]["daysLeft"], b["now"]["perDay"]), (40, 10, 4))
        self.assertEqual(b["now"]["cats"][0]["left"], -20)
        self.assertEqual(b["history"][0]["over"], 30)

    def test_a_months_own_budget_wins(self):
        wise_goals.put_budget({"total": 100}, ())
        wise_goals.put_budget({"month": "2026-11", "total": 500}, ())
        self.assertEqual(wise_goals.budget([], "2026-11-21")["now"]["total"], 500)
        self.assertEqual(wise_goals.budget([], "2026-11-21")["history"][0]["budget"], 100)

    def test_no_budget_means_no_verdict(self):
        b = wise_goals.budget([self.row("2026-11-02", 60)], "2026-11-21")
        self.assertIsNone(b["now"]["left"])
        self.assertIsNone(b["history"][0]["over"])

    def trip(self, **more):
        return {"name": "試し", "country": "試し国", "start": "2026-12-01", "end": "2026-12-10", "currency": "USD", "budget": 1000, "items": [{"name": "宿", "plan": 400, "actual": 300}], **more}

    def test_a_trip_is_kept_apart_from_the_bank(self):
        wise_goals.put_trip("t1", self.trip())
        t = wise_goals.trips([])[0]
        self.assertEqual((t["days"], t["left"], t["perDay"], t["budgetUsd"]), (10, 700, 100, 1000))
        self.assertEqual(wise_plan.ahead([], "2026-11-05")["items"], [])

    def test_a_chosen_trip_counts_towards_what_is_coming(self):
        wise_goals.put_trip("t1", self.trip(ahead=True))
        self.assertEqual(wise_plan.ahead([], "2026-11-05")["due"], 700)

    def test_a_deleted_trip_stays_on_disk_only(self):
        wise_goals.put_trip("t1", self.trip(deleted=True))
        self.assertEqual(wise_goals.trips([]), [])
        self.assertIn("t1", wise_sync.read_json("trips.json", {}))

    def test_a_bad_trip_is_refused(self):
        self.assertEqual(server.wise_settings(["trips", "t1"], self.trip(end="2026-01-01"))[0], 400)
        self.assertEqual(server.wise_settings(["trips", "t1"], self.trip(start="soon"))[0], 400)
        self.assertEqual(server.wise_settings(["budget"], {"total": -1})[0], 400)

    def test_a_goal_says_what_it_needs_and_when_it_arrives(self):
        wise_goals.put_goals({"monthly": 100, "items": [{"id": "a", "name": "車", "amount": 1200, "saved": 200, "due": "2027-11-01"}]})
        g = wise_goals.saving([], "2026-11-05")
        self.assertEqual((g["goals"][0]["left"], g["goals"][0]["needed"], g["goals"][0]["byTarget"]), (1000, 83.33, "2027-09-01"))
        self.assertIsNone(g["goals"][0]["byPace"])

    def test_no_target_means_no_target_line(self):
        self.assertIsNone(wise_goals.saving([], "2026-11-05")["forecast"][0]["target"])

    def test_the_forecast_starts_from_the_balance(self):
        wise_sync.write_json("balances.json", {"2026-11-05": [{"currency": "USD", "value": 50, "usd": 50}]})
        wise_goals.put_goals({"monthly": 10})
        f = wise_goals.saving([], "2026-11-05")["forecast"]
        self.assertEqual((f[0]["target"], f[12]["target"]), (50, 170))

    def test_rates_are_not_filled_in(self):
        wise_goals.put_sims({"invest": {"start": 100, "monthly": 10, "years": 5}})
        self.assertIsNone(wise_sync.read_json("sims.json", {})["invest"]["rate1"])

    def lesson(self, **more):
        return {"field": "ライフプランニング", "topic": "t", "title": "題", "body": "本文", "source": "出典", "url": "https://example.org", "year": 2025, "quiz": {"q": "問", "choices": ["a", "b"], "answer": 1, "why": "理由"}, **more}

    def test_a_lesson_needs_a_source_and_a_sound_quiz(self):
        self.assertFalse(wise_goals.put_lesson(self.lesson(url="x"), "2026-11-05")["saved"])
        self.assertFalse(wise_goals.put_lesson(self.lesson(quiz={"q": "問", "choices": ["a", "b"], "answer": 5}), "2026-11-05")["saved"])
        self.assertFalse(wise_goals.put_lesson(self.lesson(field="占い"), "2026-11-05")["saved"])
        self.assertTrue(wise_goals.put_lesson(self.lesson(), "2026-11-05")["saved"])
        self.assertFalse(wise_goals.put_lesson(self.lesson(), "2026-11-06")["saved"])

    def test_the_first_answer_counts(self):
        wise_goals.put_lesson(self.lesson(), "2026-11-05")
        lesson_id = wise_goals.lessons()[0]["id"]
        wise_goals.answer_lesson(lesson_id, 0)
        wise_goals.answer_lesson(lesson_id, 1)
        p = wise_goals.learn("2026-11-05")["progress"]["ライフプランニング"]
        self.assertEqual((p["read"], p["answered"], p["correct"]), (1, 1, 0))

    def test_fields_go_round_and_a_report_comes_every_fifth(self):
        order = []
        for i in range(6):
            field = wise_goals.next_field(wise_goals.lessons())
            order.append(field)
            wise_goals.put_lesson(self.lesson(field=field, topic=str(i)), "2026-11-05")
        self.assertEqual(order[:4], list(wise_goals.FIELDS[:4]))
        self.assertEqual(order[4], wise_goals.REPORT)
        self.assertEqual(order[5], wise_goals.FIELDS[4])
