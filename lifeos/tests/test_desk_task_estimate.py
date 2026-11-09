"""Claude's guesses for Desk tasks: what gets a guess, and what a guess may never change."""
import importlib.util
import os
import sys
import unittest
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desk"))
spec = importlib.util.spec_from_file_location("task_estimate", os.path.join(ROOT, "desk", "task_estimate.py"))
te = importlib.util.module_from_spec(spec)
spec.loader.exec_module(te)

TODAY = date(2026, 11, 5)  # a Thursday


def task(**more):
    return {"id": "t1", "title": "書く", "status": "notyet", "createdAt": 1, "updatedAt": 2, **more}


class Periods(unittest.TestCase):
    def test_each_period_ends_on_its_last_day(self):
        self.assertEqual(te.plan_date("today", TODAY), "2026-11-05")
        self.assertEqual(te.plan_date("week", TODAY), "2026-11-08")
        self.assertEqual(te.plan_date("month", TODAY), "2026-11-30")
        self.assertEqual(te.plan_date("next", TODAY), "2026-12-31")
        self.assertEqual(te.plan_date("next2", TODAY), "2027-01-31")
        self.assertEqual(te.plan_date("y2027", TODAY), "2027-12-31")
        self.assertEqual(te.plan_date("later", TODAY), "9999-12-31")

    def test_december_rolls_into_the_next_year(self):
        self.assertEqual(te.plan_date("next2", date(2026, 12, 15)), "2027-02-28")

    def test_sunday_is_the_last_day_of_its_own_week(self):
        self.assertEqual(te.plan_date("week", date(2026, 11, 8)), "2026-11-08")

    def test_unknown_keys_and_past_years_are_not_periods(self):
        for key in ("soon", "y2025", "y20x7", None, 7):
            self.assertIsNone(te.plan_date(key, TODAY))


class Pending(unittest.TestCase):
    def test_a_new_task_needs_both(self):
        self.assertEqual(te.pending([task()], TODAY)[0]["needs"], ["minutes", "plan"])

    def test_a_task_with_a_due_date_needs_no_plan(self):
        self.assertEqual(te.pending([task(due=1)], TODAY)[0]["needs"], ["minutes"])

    def test_own_choices_and_closed_tasks_are_left_out(self):
        items = [task(estMin=10, planDate="2026-11-30"), task(status="done"), task(deleted=True)]
        self.assertEqual(te.pending(items, TODAY), [])

    def test_an_old_plan_guess_is_asked_again(self):
        self.assertEqual(te.pending([task(estAi=10, planAi="2026-11-01")], TODAY)[0]["needs"], ["plan"])

    def test_a_long_note_is_cut(self):
        self.assertEqual(len(te.pending([task(note="あ" * 500)], TODAY)[0]["note"]), te.NOTE_MAX)


class Apply(unittest.TestCase):
    def test_a_guess_fills_only_the_guess_fields(self):
        rec = te.apply_rows([task()], [{"id": "t1", "minutes": 15, "plan": "week"}], TODAY)[0]
        self.assertEqual((rec["estAi"], rec["planAi"]), (15, "2026-11-08"))
        self.assertEqual(rec["updatedAt"], 2)
        self.assertNotIn("planDate", rec)
        self.assertNotIn("estMin", rec)

    def test_own_choices_win(self):
        items = [task(estMin=30, planDate="2027-12-31")]
        self.assertEqual(te.apply_rows(items, [{"id": "t1", "minutes": 15, "plan": "week"}], TODAY), [])

    def test_bad_rows_are_skipped(self):
        rows = [{"id": "x", "minutes": 5}, "nope", {"id": "t1", "minutes": -1}, {"id": "t1", "minutes": True},
                {"id": "t1", "minutes": 99999}, {"id": "t1", "plan": "soon"}]
        self.assertEqual(te.apply_rows([task()], rows, TODAY), [])

    def test_a_finished_task_gets_nothing(self):
        self.assertEqual(te.apply_rows([task(status="done")], [{"id": "t1", "minutes": 15}], TODAY), [])


if __name__ == "__main__":
    unittest.main()
