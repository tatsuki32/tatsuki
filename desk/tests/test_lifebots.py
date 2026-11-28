"""python3 -m unittest desk.tests.test_lifebots — the spreadsheet is a fake; nothing leaves the machine."""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import lifebots  # noqa: E402
import lifelog  # noqa: E402


class Sheet:
    def __init__(self, sleep=(), meals=()):
        self.rows = {"sleep": list(sleep), "meals": list(meals)}
        self.calls = []

    def __call__(self, url, k, op, **p):
        self.calls.append((op, p))
        if k != "secret":
            raise PermissionError("The script did not accept the key")
        if op == "tz":
            return {"ok": True, "tz": p["tz"], "from": "mac"}
        if op == "export":
            return {"ok": True, "tz": "Asia/Tokyo", **{k2: [r for r in v if not r.get("imported_at")] for k2, v in self.rows.items()}}
        if op == "ack":
            ids = p["ids"].split(",")
            for v in self.rows.values():
                for r in v:
                    if r["id"] in ids:
                        r["imported_at"] = "now"
            return {"ok": True}


class LifebotsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"LIFELOG_DATA": self.tmp.name, "LIFEBOTS_KEY": "secret", "LIFEBOTS_TZ": "Asia/Tokyo"})
        self.env.start()
        os.makedirs(lifebots.folder())
        lifelog.write_json(lifebots.folder("config.json"), {"url": "https://script.google.com/macros/s/x/exec"})
        self.today = lifelog.today()
        self.yday = (date.fromisoformat(self.today) - timedelta(days=1)).isoformat()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def body(self):
        return lifelog.read_json(lifelog.bp("records.json"), {})

    def test_rows_become_records_once(self):
        sheet = Sheet(sleep=[{"id": "r1", "date": self.today, "kind": "sleep", "sleep_min": 450, "bedtime": "22:00", "wake_time": "05:30", "text": "7h30"},
                             {"id": "r2", "date": self.yday, "kind": "steps", "steps": 8200}],
                      meals=[{"id": "r3", "date": self.yday, "meal": "lunch", "items": "Curry rice (1 plate); Salad", "kcal": 800, "protein_g": 22, "fat_g": 26, "carb_g": 115, "salt_g": 3.5}])
        with mock.patch.object(lifebots, "_get", sheet):
            s = lifebots.run(force=True)
            self.assertEqual(s["lastAdded"], 3)
            lifebots.run(force=True)
        b = self.body()
        self.assertEqual(len(b), 3)
        self.assertEqual(b["mb-r1"]["values"], {"total_min": 450, "bedtime": "22:00", "wake_time": "05:30"})
        self.assertEqual(b["mb-r1"]["status"], "confirmed")
        self.assertEqual(b["mb-r2"]["values"], {"steps": 8200})
        self.assertEqual(b["mb-r3"]["kind"], "meal_photo")
        self.assertEqual(b["mb-r3"]["values"]["energy_kcal"], 800)
        self.assertEqual(b["mb-r3"]["items"], ["Curry rice (1 plate)", "Salad"])
        self.assertTrue(b["mb-r3"]["estimated"])
        self.assertEqual(b["mb-r3"]["label"], "Lunch: Curry rice (1 plate), Salad")
        habits = {h["id"]: h for h in lifelog.read_json(os.path.join(self.tmp.name, "habits.json"), [])}
        self.assertIn(self.today, habits["log-sleep"]["done"])
        self.assertIn(self.yday, habits["log-meals"]["done"])

    def test_all_nutrients_come_in_and_targets_go_out(self):
        lifelog.write_json(lifelog.bp("targets.json"), {"protein_g": {"target": 130, "dir": "above"}, "steps": {"target": 15000, "dir": "above"},
                                                       "weight_kg": {"target": 62, "dir": "near"}})
        row = {"id": "r1", "date": self.yday, "meal": "dinner", "items": "Pho (1 bowl)", "kcal": 450, "protein_g": 25, "fat_g": 8, "carb_g": 60, "salt_g": 4,
               "fiber_g": 2.5, "vitamin_d_ug": 0, "vitamin_b1_mg": 0.2, "iron_mg": "", "saturated_fat_g": 2}
        sheet = Sheet(meals=[row])
        with mock.patch.object(lifebots, "_get", sheet):
            lifebots.run(force=True)
            lifebots.run(force=True)
        v = self.body()["mb-r1"]["values"]
        self.assertEqual((v["fiber_g"], v["vitamin_d_ug"], v["vitamin_b1_mg"], v["saturated_fat_g"]), (2.5, 0, 0.2, 2))
        self.assertNotIn("iron_mg", v)
        tz_calls = [p for op, p in sheet.calls if op == "tz"]
        self.assertEqual(len(tz_calls), 1)  # sent once; the same targets the same day are not sent again
        self.assertEqual(json.loads(tz_calls[0]["targets"]), {"protein_g": [130, "above"], "steps": [15000, "above"]})

    def test_sleep_stages_and_step_details_come_in(self):
        sheet = Sheet(sleep=[{"id": "r1", "date": self.today, "kind": "sleep", "sleep_min": 420, "deep_min": 80, "rem_min": 95, "awake_min": "", "hrv_avg": 48, "spo2_avg": 96},
                             {"id": "r2", "date": self.yday, "kind": "steps", "steps": 9000, "distance_km": 6.4, "floors": 3}])
        with mock.patch.object(lifebots, "_get", sheet):
            lifebots.run(force=True)
        b = self.body()
        self.assertEqual(b["mb-r1"]["values"], {"total_min": 420, "deep_min": 80, "rem_min": 95, "hrv_avg": 48, "spo2_avg": 96})
        self.assertEqual(b["mb-r2"]["values"], {"steps": 9000, "distance_km": 6.4, "floors": 3})

    def test_undo_after_import_discards(self):
        sheet = Sheet(sleep=[{"id": "r1", "date": self.today, "kind": "sleep", "sleep_min": 420}])
        with mock.patch.object(lifebots, "_get", sheet):
            lifebots.run(force=True)
            sheet.rows["sleep"][0].update(deleted=True, imported_at="")
            s = lifebots.run(force=True)
        self.assertEqual(s["lastGone"], 1)
        self.assertEqual(self.body()["mb-r1"]["status"], "discarded")

    def test_out_of_range_waits_for_review(self):
        sheet = Sheet(sleep=[{"id": "r1", "date": self.yday, "kind": "steps", "steps": 999999}])
        with mock.patch.object(lifebots, "_get", sheet):
            lifebots.run(force=True)
        self.assertEqual(self.body()["mb-r1"]["status"], "review")

    def test_once_a_day_after_five_and_tz(self):
        sheet = Sheet()
        with mock.patch.object(lifebots, "_get", sheet), mock.patch.object(lifebots, "datetime") as dt:
            import datetime as real
            dt.now.return_value = real.datetime(2026, 11, 29, 4, 30)
            lifebots.run()
            self.assertEqual([c[0] for c in sheet.calls], ["tz"])
            dt.now.return_value = real.datetime(2026, 11, 29, 5, 10)
            lifebots.run()
            lifebots.run()
            self.assertEqual([c[0] for c in sheet.calls], ["tz", "export"])
        self.assertEqual(lifebots.view()["tz"], "Asia/Tokyo")

    def test_wrong_key_and_no_setup(self):
        with mock.patch.dict(os.environ, {"LIFEBOTS_KEY": "wrong"}), mock.patch.object(lifebots, "_get", Sheet()):
            self.assertIn("did not accept", lifebots.run(force=True)["error"])
        os.remove(lifebots.folder("config.json"))
        self.assertFalse(lifebots.view()["setup"])


if __name__ == "__main__":
    unittest.main()
