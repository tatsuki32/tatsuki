"""Not doing and labels: what the nightly run may write, and what it must leave alone."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desk"))
import task_skip as ts  # noqa: E402
import server  # noqa: E402

NOW = 1_800_000_000_000
TYPES = ts.all_types([])


def task(i, **kw):
    return {"id": i, "title": "t" + i, "status": "notyet", "source": "manual", "due": None, "createdAt": 1, **kw}


class Types(unittest.TestCase):
    def test_about_45_in_8_groups(self):
        self.assertEqual(len(ts.TYPE_GROUPS), 8)
        self.assertEqual(len(TYPES), 45)

    def test_added_types_come_after(self):
        out = ts.all_types([{"name": "Gardening", "group": "Yours"}, {"name": "research"}])
        self.assertEqual(out[-1], ("Hold", "Gardening"))
        self.assertEqual(len(out), 46)


class Pending(unittest.TestCase):
    def test_replies_and_claude_work_are_skipped(self):
        items = [task("a"), task("b", source="mail"), task("c", source="lifeos-02"), task("d", by="claude"), task("e", status="done")]
        self.assertEqual([x["id"] for x in ts.pending(items, NOW)], ["a"])

    def test_done_ones_wait_30_days_after_keep(self):
        full = {"cat": "Research", "genre": "g-x"}
        fresh = task("a", skip={"ifNot": "x", "by": "claude", "at": NOW - 5, "kept": NOW - 10 * ts.DAY_MS}, **full)
        old = task("b", skip={"ifNot": "x", "by": "claude", "at": NOW - 40 * ts.DAY_MS, "kept": NOW - 31 * ts.DAY_MS}, **full)
        mine = task("c", skip={"ifNot": "x", "by": "me", "kept": NOW - 99 * ts.DAY_MS}, tagBy="me")
        self.assertEqual([x["id"] for x in ts.pending([fresh, old, mine], NOW)], ["b"])


class Apply(unittest.TestCase):
    def run_rows(self, items, rows, genres=()):
        return ts.apply_rows(items, rows, NOW, list(genres), TYPES)

    def test_writes_skip_type_and_a_new_genre(self):
        out, new = self.run_rows([task("a")], [{"id": "a", "ifNot": "Nothing  breaks.", "minor": True, "why": "No one waits.",
                                                 "cat": "research", "genre": "Georgia company"}])
        r = out[0]
        self.assertEqual(r["skip"], {"ifNot": "Nothing breaks.", "minor": True, "why": "No one waits.", "by": "claude", "at": NOW})
        self.assertEqual(r["cat"], "Research")
        self.assertEqual(new[0]["name"], "Georgia company")
        self.assertEqual(r["genre"], new[0]["id"])
        self.assertNotIn("updatedAt", r)

    def test_due_dates_and_paperwork_are_never_minor(self):
        out, _ = self.run_rows([task("a", due=NOW + 1), task("b"), task("c")],
                               [{"id": "a", "ifNot": "x", "minor": True}, {"id": "b", "ifNot": "x", "minor": True, "cat": "Pay a bill"},
                                {"id": "c", "ifNot": "x", "minor": True, "cat": "Health and appointments"}])
        self.assertEqual([r["skip"]["minor"] for r in out], [False, False, False])

    def test_what_the_person_wrote_stays(self):
        t = task("a", skip={"ifNot": "mine", "minor": False, "by": "me"}, cat="Plan", genre="g-1", tagBy="me")
        out, new = self.run_rows([t], [{"id": "a", "ifNot": "theirs", "minor": True, "cat": "Read", "genre": "Other"}])
        self.assertEqual(out, [])
        self.assertEqual(new, [])

    def test_existing_genre_is_reused_and_100_is_the_cap(self):
        gs = [{"id": f"g-{i}", "name": f"G{i}"} for i in range(100)]
        out, new = self.run_rows([task("a"), task("b")], [{"id": "a", "ifNot": "x", "genre": "g5"}, {"id": "b", "ifNot": "x", "genre": "Brand new"}], gs)
        self.assertEqual(out[0]["genre"], "g-5")
        self.assertNotIn("genre", out[1])
        self.assertEqual(new, [])

    def test_unknown_type_is_not_written(self):
        out, _ = self.run_rows([task("a")], [{"id": "a", "ifNot": "x", "cat": "Juggling"}])
        self.assertNotIn("cat", out[0])


class ServerKeeps(unittest.TestCase):
    def test_an_old_page_does_not_erase_the_night_run(self):
        old = {"skip": {"ifNot": "night", "at": 5}, "cat": "Read", "genre": "g", "tagBy": "claude", "tagAt": 5}
        new = {"title": "x"}
        server.keep_labels(old, new)
        self.assertEqual((new["skip"]["ifNot"], new["cat"], new["genre"]), ("night", "Read", "g"))

    def test_a_newer_edit_wins(self):
        old = {"skip": {"ifNot": "night", "at": 5}, "cat": "Read", "tagAt": 5}
        new = {"skip": {"ifNot": "mine", "at": 9}, "cat": "Plan", "tagAt": 9}
        server.keep_labels(old, new)
        self.assertEqual((new["skip"]["ifNot"], new["cat"]), ("mine", "Plan"))


if __name__ == "__main__":
    unittest.main()
