"""Good news only: a row taken off never comes back, and a deposit counts only as income."""
import importlib.util
import json
import os
import sys

from lifeos.lib import paths, tasks
from lifeos.tests.sandbox import SandboxCase

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desk"))
spec = importlib.util.spec_from_file_location("presents", os.path.join(ROOT, "desk", "presents.py"))
presents = importlib.util.module_from_spec(spec)
spec.loader.exec_module(presents)
import wise_sync  # noqa: E402


class Presents(SandboxCase):
    def setUp(self):
        super().setUp()
        os.makedirs(paths.desk_data_dir(), exist_ok=True)
        old = wise_sync.DATA
        wise_sync.DATA = os.path.join(self.tmp, "wise-data")
        self.addCleanup(lambda: setattr(wise_sync, "DATA", old))
        os.makedirs(wise_sync.wise_dir())

    def row(self, **kw):
        return {"kind": "award", "title": "賞に選ばれました", "from": "主催者", "date": "2026-11-05", "source": "gmail", "ref": "t1", **kw}

    def test_add_is_once_per_source_and_ref(self):
        self.assertEqual(presents.add([self.row(), self.row()]), 1)
        self.assertEqual(presents.add([self.row()]), 0)
        self.assertEqual(presents.load()[0]["title"], "賞に選ばれました")

    def test_a_removed_row_does_not_come_back(self):
        presents.add([self.row()])
        presents._put({**presents.load()[0], "deleted": True})
        self.assertEqual(presents.add([self.row()]), 0)
        self.assertEqual(presents.removed()[0]["title"], "賞に選ばれました")

    def test_bad_rows_are_left_out(self):
        self.assertEqual(presents.add([self.row(kind="spam"), self.row(source="x"), self.row(title=" "), self.row(ref="")]), 0)

    def test_it_makes_no_task(self):
        presents.add([self.row()])
        self.assertEqual(tasks.open_tasks(), [])

    def test_only_income_deposits_count(self):
        rows = {str(i): {"id": str(i), "type": t, "status": "COMPLETED", "title": title, "value": 10.0, "currency": "USD",
                         "incoming": True, "amountText": "+ 10 USD", "createdOn": "2026-11-01T01:00:00Z"}
                for i, (t, title) in enumerate((("TRANSFER", "取引先"), ("BALANCE_DEPOSIT", "自分"), ("CARD_PAYMENT", "返金")))}
        wise_sync.write_json("activities.json", rows)
        wise_sync.write_json("flows.json", {"in:取引先": "income"})
        self.assertEqual(presents.add_wise(), 1)
        p = presents.load()[0]
        self.assertEqual((p["kind"], p["title"], p["from"], p["date"]), ("payment", "10 USD の入金", "取引先", "2026-11-01"))
        self.assertEqual(presents.add_wise(), 0)
