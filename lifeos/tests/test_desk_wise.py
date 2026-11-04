"""Wise on the Desk: what is read, what is counted, and that statements stay out of the private copy."""
import json
import os
import subprocess
import tempfile
import unittest

from lifeos.tests.desk_import import server  # noqa: F401  (puts desk/ on the import path)
from lifeos.tests.sandbox import SandboxCase

import backup_data  # noqa: E402
import wise_sync  # noqa: E402


def act(id_, when, title="<strong>Shop</strong>", amount="10.00 USD", type_="CARD_PAYMENT", status="COMPLETED", second=""):
    return {"id": id_, "type": type_, "status": status, "title": title, "description": "", "primaryAmount": amount,
            "secondaryAmount": second, "createdOn": when, "updatedOn": when}


class FakeWise:
    def __init__(self, pages):
        self.pages, self.paths = pages, []

    def __call__(self, path):
        self.paths.append(path)
        if path == "/v2/profiles":
            return [{"id": 1, "type": "PERSONAL"}]
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
        self.assertEqual(len(fake.paths), 2)  # profiles and one page

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

    def test_every_completed_movement_counts(self):
        s = self.put(
            act("a", "2026-11-02T01:00:00Z", amount="10.00 USD"),
            act("b", "2026-11-03T01:00:00Z", amount="5.00 USD", status="CANCELLED"),
            act("c", "2026-11-04T01:00:00Z", amount="0.00 USD", type_="CARD_CHECK", status="CANCELLED"),
            act("d", "2026-11-05T01:00:00Z", amount="100.00 USD", type_="TRANSFER"),
            act("e", "2026-11-06T01:00:00Z", amount="<positive>+ 3.00 USD</positive>"),
            act("f", "2026-11-07T01:00:00Z", amount="20.00 USD", type_="CASH_WITHDRAWAL"),
            act("g", "2026-11-08T01:00:00Z", amount="<positive>+ 500 JPY</positive>", type_="BALANCE_DEPOSIT"),
        )
        usd = s["totals"]["USD"]
        self.assertEqual((usd["in"], usd["out"], usd["net"], usd["count"]), (3.0, 130.0, -127.0, 4))
        self.assertEqual(usd["types"], {"out:CARD_PAYMENT": 10.0, "out:TRANSFER": 100.0, "in:CARD_PAYMENT": 3.0, "out:CASH_WITHDRAWAL": 20.0})
        self.assertEqual((s["totals"]["JPY"]["in"], s["totals"]["JPY"]["out"]), (500.0, 0))
        self.assertEqual(s["items"][0]["id"], "g")
        self.assertEqual(len(s["items"]), 7)

    def test_the_total_uses_what_the_balance_paid(self):
        s = self.put(act("a", "2026-11-02T01:00:00Z", amount="320,000 VND", second="1,900 JPY"))
        self.assertEqual(list(s["totals"]), ["JPY"])
        self.assertEqual(s["totals"]["JPY"]["out"], 1900.0)

    def test_a_fee_in_the_same_currency_counts(self):
        s = self.put(act("a", "2026-11-02T01:00:00Z", amount="10,000 JPY", second="10,250 JPY", type_="CASH_WITHDRAWAL"))
        self.assertEqual(s["totals"]["JPY"]["out"], 10250.0)

    def test_a_split_payment_falls_back_to_the_first_amount(self):
        s = self.put(act("a", "2026-11-02T01:00:00Z", amount="50 AUD", second="JPY, USD"))
        self.assertEqual(s["totals"]["AUD"]["out"], 50.0)

    def test_moving_between_own_balances_is_not_income_or_spending(self):
        s = self.put(act("a", "2026-11-02T01:00:00Z", amount="100 USD", second="15,000 JPY", type_="INTERBALANCE"))
        self.assertEqual(s["totals"], {})
        self.assertEqual(len(s["items"]), 1)

    def test_another_month_stays_out(self):
        s = self.put(act("a", "2026-10-02T01:00:00Z"))
        self.assertEqual((s["totals"], s["items"]), ({}, []))
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
        self.assertEqual(server.wise_view("2026-13")[0], 400)
        self.assertEqual(server.wise_view("../x")[0], 400)

    def test_a_candidate_reaches_life_os_18_with_the_statement_amount(self):
        code, view = server.wise_view()
        self.assertEqual(code, 200)
        self.assertFalse(view["recurring"][0]["inSubs"])
        key = view["recurring"][0]["key"]
        code, view = server.wise_to_subs({"key": key, "amount": 1})
        self.assertEqual(code, 200)
        self.assertTrue(view["recurring"][0]["inSubs"])
        sub = list(server.subs_core().load().values())[0]
        self.assertEqual((sub["manual"]["amount"], sub["manual"]["currency"]), (12.0, "USD"))

    def test_an_unknown_candidate_adds_nothing(self):
        self.assertEqual(server.wise_to_subs({"key": "nope|USD"})[0], 404)
        self.assertEqual(server.subs_core().load(), {})


if __name__ == "__main__":
    unittest.main()
