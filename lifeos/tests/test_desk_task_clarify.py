"""Making vague tasks easier to start: what gets picked, and what a result may never change."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import date

from lifeos.tests.desk_import import server

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desk"))
spec = importlib.util.spec_from_file_location("task_clarify", os.path.join(ROOT, "desk", "task_clarify.py"))
tc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tc)

TODAY = date(2026, 11, 5)  # a Thursday


def task(id="t1", **more):
    return {"id": id, "title": "納税", "status": "notyet", "source": "manual", "createdAt": 1, "updatedAt": 2, **more}


def row(id="t1", **more):
    return {"id": id, "title": "納税の手順を確かめる", "first": "公式の案内を開く", **more}


class Pending(unittest.TestCase):
    def ids(self, items, **kw):
        return [t["id"] for t in tc.pending(items, TODAY, **kw)]

    def test_answers_then_today_then_this_week_then_the_oldest(self):
        items = [task("old", createdAt=1), task("new", createdAt=9), task("week", planDate="2026-11-08"),
                 task("today", planDate="2026-11-05"), task("answered", clear={"redo": True, "title": "x"})]
        self.assertEqual(self.ids(items), ["answered", "today", "week", "old", "new"])

    def test_a_passed_date_counts_as_today(self):
        self.assertEqual(self.ids([task("a", createdAt=0), task("b", planDate="2026-10-01")]), ["b", "a"])

    def test_the_limit_holds(self):
        items = [task(f"t{i}", createdAt=i) for i in range(40)]
        self.assertEqual(len(self.ids(items)), tc.DEFAULT_LIMIT)

    def test_closed_deleted_mail_and_finished_ones_are_left_out(self):
        items = [task("a", status="done"), task("b", status="paused"), task("c", status="archived"), task("d", deleted=True),
                 task("e", source="mail"), task("f", clear={"title": "x", "first": "y"})]
        self.assertEqual(self.ids(items), [])

    def test_the_button_can_redo_a_finished_one(self):
        self.assertEqual(self.ids([task(clear={"title": "x", "first": "y"})], only="t1"), ["t1"])
        self.assertEqual(self.ids([task(status="done")], only="t1"), [])


class Apply(unittest.TestCase):
    def one(self, t, r):
        got = tc.apply_rows([t], [r], 100)
        return got[0] if got else None

    def test_only_clear_changes(self):
        t = task(note="本人のメモ", due=5, planDate="2027-12-31", estMin=30)
        rec = self.one(t, row())
        self.assertEqual({k: v for k, v in rec.items() if k != "clear"}, t)
        self.assertEqual(rec["clear"]["title"], "納税の手順を確かめる")
        self.assertEqual(rec["clear"]["at"], 100)
        self.assertFalse(rec["clear"]["redo"])

    def test_a_finished_task_gets_nothing(self):
        self.assertIsNone(self.one(task(status="done"), row()))

    def test_nothing_to_show_is_skipped(self):
        self.assertIsNone(self.one(task(), {"id": "t1", "title": "題だけ"}))
        self.assertIsNone(self.one(task(), {"id": "t1", "first": "最初だけ"}))

    def test_questions_alone_are_enough(self):
        rec = self.one(task(), {"id": "t1", "title": "何の納税か確かめる", "questions": ["どの税ですか"]})
        self.assertEqual(rec["clear"]["questions"][0]["state"], "open")

    def test_only_https_sources_pass(self):
        rec = self.one(task(), row(sources=[{"label": "公式", "url": "https://example.go.jp/a"}, {"url": "javascript:alert(1)"}, {"url": "http://x"}, "no"]))
        self.assertEqual([s["url"] for s in rec["clear"]["sources"]], ["https://example.go.jp/a"])

    def test_answers_stay_and_are_not_asked_again(self):
        old = {"questions": [{"id": "q1", "q": "どの税ですか", "a": "住民税", "state": "answered"}, {"id": "q2", "q": "期限は", "a": None, "state": "unknown"}], "redo": True}
        qs = self.one(task(clear=old), row(questions=["どの税ですか", "期限は", "どこに住んでいますか"]))["clear"]["questions"]
        self.assertEqual([q["q"] for q in qs], ["どの税ですか", "期限は", "どこに住んでいますか"])
        self.assertEqual(qs[0]["a"], "住民税")
        self.assertEqual(qs[2]["state"], "open")

    def test_at_most_two_open_questions(self):
        qs = self.one(task(), row(questions=["一", "二", "三"]))["clear"]["questions"]
        self.assertEqual(len(qs), 2)

    def test_going_back_to_the_own_title_lasts(self):
        self.assertTrue(self.one(task(clear={"titleOff": True}), row())["clear"]["titleOff"])

    def test_lengths_and_counts_are_cut(self):
        c = self.one(task(), row(title="あ" * 200, steps=["手順"] * 30, sources=[{"url": f"https://a/{i}"} for i in range(9)]))["clear"]
        self.assertEqual((len(c["title"]), len(c["steps"]), len(c["sources"])), (tc.TITLE_MAX, tc.MAX_STEPS, tc.MAX_SOURCES))

    def test_only_the_three_kinds_of_asks_pass(self):
        c = self.one(task(), row(asks={"teach": "教えて", "pay": "払う", "help": 3}))["clear"]
        self.assertEqual(c["asks"], {"teach": "教えて"})


class ServerKeepsClear(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        old = server.DATA
        server.DATA = self.td.name
        self.addCleanup(lambda: setattr(server, "DATA", old))
        server.upsert("tasks", "t1", {"title": "納税", "clear": {"title": "新", "at": 200}}, merge=False)

    def now(self):
        with open(os.path.join(self.td.name, "tasks.json"), encoding="utf-8") as f:
            return json.load(f)[0]

    def test_an_old_page_without_it_does_not_erase_it(self):
        server.upsert("tasks", "t1", {"title": "納税", "status": "done"}, merge=False)
        self.assertEqual((self.now()["status"], self.now()["clear"]["title"]), ("done", "新"))

    def test_an_older_copy_loses(self):
        server.upsert("tasks", "t1", {"title": "納税", "clear": {"title": "古", "at": 100}}, merge=False)
        self.assertEqual(self.now()["clear"]["title"], "新")

    def test_the_same_copy_with_an_answer_wins(self):
        server.upsert("tasks", "t1", {"title": "納税", "clear": {"title": "新", "at": 200, "redo": True}}, merge=False)
        self.assertTrue(self.now()["clear"]["redo"])

    def test_other_collections_are_left_alone(self):
        server.upsert("notes", "n1", {"clear": {"at": 9}}, merge=False)
        server.upsert("notes", "n1", {"title": "x"}, merge=False)
        self.assertEqual(server.load("notes")[0], {"title": "x", "id": "n1"})


if __name__ == "__main__":
    unittest.main()
