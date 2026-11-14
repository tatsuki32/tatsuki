"""Automation ideas: what may be saved, and that repeats are found without names or numbers."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desk"))
import automation as au  # noqa: E402

OK = {"title": "Monthly invoice drafts", "repeat": "Invoice by hand", "tool": "Claude scheduled task", "effort": "Medium", "notAuto": "Sending"}


class Check(unittest.TestCase):
    def test_three_at_most_and_every_field(self):
        self.assertTrue(au.check({"items": [OK] * 4}, [])[0])
        errs, _ = au.check({"items": [{**OK, "notAuto": ""}]}, [])
        self.assertIn("Item 1: notAuto is missing", errs)
        self.assertEqual(au.check({"items": [OK]}, [])[0], [])

    def test_no_blame_words_and_no_repeat_of_earlier(self):
        self.assertTrue(au.check({"items": [{**OK, "why": "You must do this"}]}, [])[0])
        self.assertTrue(au.check({"items": [OK]}, [{"title": "Monthly invoice drafts", "status": "passed"}])[0])

    def test_repeats_drop_names_and_numbers(self):
        ts = [{"title": f"Email nudge: Esperanto group ({i}@x.de)", "createdAt": 10 ** 13} for i in range(3)]
        self.assertEqual(au.repeated(ts, 10 ** 13), [{"shape": "email nudge esperanto group", "count": 3}])


if __name__ == "__main__":
    unittest.main()
