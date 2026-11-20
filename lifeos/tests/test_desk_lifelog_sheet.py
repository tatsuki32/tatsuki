"""The old Life Dashboard sheet comes in once: dates fixed, 0 is no record, the later row wins, slid columns put back."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desk"))
import lifelog as ll  # noqa: E402
import lifelog_sheet as ls  # noqa: E402


class Sheet(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.environ["LIFELOG_DATA"] = self.tmp
        self.addCleanup(os.environ.pop, "LIFELOG_DATA", None)

    def csv(self, name, text):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def test_dates(self):
        self.assertEqual(ls.parse_day("2026-08-09"), "2026-08-09")
        self.assertEqual(ls.parse_day("2026/08/09"), "2026-08-09")
        self.assertEqual(ls.parse_day("26/08/2026"), "2026-08-26")
        self.assertEqual(ls.parse_day("12/08/2026 17:00:00"), "2026-08-13")
        self.assertIsNone(ls.parse_day("x"))

    def test_sleep_zero_and_slid_columns(self):
        p = self.csv("s.csv", "日付,総睡眠時間(分),夜間睡眠時間(分),昼寝時間(分),呼吸品質(点),平均心拍(bpm),平均HRV(ms),平均SpO2(%),平均呼吸数(brpm),就寝時刻,起床時刻,メモ\n"
                              "2026-08-08,461,0,0,0,0,0,0,0,,,\n2026-09-05,390,21:30,4:40,,,,,,,,\n")
        recs = ls.records([p])
        a = recs["sheet-sleep-2026-08-08"]["values"]
        self.assertEqual(a, {"total_min": 461})
        b = recs["sheet-sleep-2026-09-05"]["values"]
        self.assertEqual((b.get("bedtime"), b.get("wake_time"), b.get("night_min")), ("21:30", "04:40", None))

    def test_later_row_wins_and_write_is_idempotent(self):
        p = self.csv("st.csv", "日付,歩数,メモ\n2026-08-09,8200,\n2026/08/09,5884,\n2026-08-10,0,\n")
        recs = ls.records([p])
        self.assertEqual(list(recs), ["sheet-steps-2026-08-09"])
        self.assertEqual(recs["sheet-steps-2026-08-09"]["values"]["steps"], 5884)
        ls.write(recs)
        ls.write(ls.records([p]))
        with open(ll.bp("records.json"), encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)), 1)

    def test_meal_tab_with_target_rows_above(self):
        p = self.csv("m.csv", ",項目,エネルギー(kcal),飽和脂肪酸(g)\n,目標値,1850,1\n,種別(以上/未満),未満,未満\n日付,完璧度(%),エネルギー(kcal),飽和脂肪酸(g)\n"
                              "12/08/2026 17:00:00,81%,2530,12\n")
        recs = ls.records([p])
        r = recs["sheet-meal-2026-08-13"]
        self.assertEqual((r["kind"], r["values"]["energy_kcal"], r["values"]["saturated_fat_g"]), ("meal_screen", 2530, 12))
        ls.write(recs)
        with open(os.path.join(self.tmp, "habits.json"), encoding="utf-8") as f:
            self.assertIn("2026-08-13", {h["id"]: h for h in json.load(f)}["log-meals"]["done"])


if __name__ == "__main__":
    unittest.main()
