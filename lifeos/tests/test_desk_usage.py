"""使い方: only totals are kept, nothing personal gets in, and the weekly arrangement moves little and loses nothing."""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desk"))
import usage as u

VIEWS = [{"id": i, "g": g} for i, g in (
    ("home", "today"), ("tasks", "today"), ("calendar", "today"), ("habits", "today"), ("bodysleep", "body"),
    ("bodymeal", "body"), ("wins", "in"), ("news", "read"), ("heritage", "read"), ("notes", "write"), ("netspeed", "write"), ("pc", "write"))]
IDS = [v["id"] for v in VIEWS]
CARDS = ["triage", "focus", "pc", "body", "today", "heritage", "claude", "panel", "wins", "inflow", "notes", "tools"]
START = date(2026, 11, 6)   # a Friday: counting starts
MON = date(2026, 11, 23)    # the first Monday with 14 days behind it


def days_of(opened, cards=None, home=20, start=START, until=MON):
    """One record per day from start to the day before until. opened: id -> days it was opened on; cards: id -> visits with a press."""
    out, n = {}, (until - start).days
    for k in range(n):
        day = {"views": {"home": {"n": max(1, home // n + (1 if k < home % n else 0)), "sec": 60, "via": {"other": 1}}}}
        for vid, on in opened.items():
            if k >= n - on:  # the latest days, so they fall in the 14 days looked at
                day["views"][vid] = {"n": 2, "sec": 100 + IDS.index(vid), "via": {"nav": 2}}
        out[(start + timedelta(days=k)).isoformat()] = day
    out[(until - timedelta(days=1)).isoformat()]["cards"] = {c: {"n": v, "v": v} for c, v in (cards or {}).items()}
    return out


def layout(**more):
    known = {"nav": {i: START.isoformat() for i in IDS}, "home": {c: START.isoformat() for c in CARDS}}
    return u.normal({"known": known, **more})


class Temp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        p = mock.patch.object(u, "DIR", self.dir.name)
        p.start()
        self.addCleanup(p.stop)


class Record(Temp):
    def test_a_visit_adds_one_open_and_its_seconds(self):
        self.assertTrue(u.record({"view": "tasks", "sec": 61.4, "via": "nav"}, START))
        self.assertTrue(u.record({"view": "tasks", "sec": 30, "cont": True}, START))
        v = u.read_days()[START.isoformat()]["views"]["tasks"]
        self.assertEqual(v, {"n": 1, "sec": 91, "via": {"nav": 1}})

    def test_a_press_counts_once_per_visit_for_the_rate(self):
        u.record({"view": "home", "sec": 5, "via": "other", "hits": {"home|#soundBtn": 3}, "new": ["home|#soundBtn"],
                  "cards": {"inflow": 3}, "newCards": ["inflow"]}, START)
        u.record({"view": "home", "sec": 5, "cont": True, "hits": {"home|#soundBtn": 1}, "cards": {"inflow": 1}}, START)
        day = u.read_days()[START.isoformat()]
        self.assertEqual(day["hits"]["home|#soundBtn"], {"n": 4, "v": 1})
        self.assertEqual(day["cards"]["inflow"], {"n": 4, "v": 1})

    def test_text_from_the_screen_never_gets_in(self):
        bad = {"view": "tasks", "sec": 1, "hits": {"tasks|請求書を 山田さん に送る": 1, "tasks|data-id=abc 123": 1, "山田|#x": 1, "tasks|#ok": 1},
               "cards": {"inflow": 1}}
        u.record(bad, START)
        self.assertFalse(u.record({"view": "山田さん", "sec": 1}, START))
        self.assertFalse(u.record({"view": "_bar", "sec": 1}, START))
        text = json.dumps(u.read_days(), ensure_ascii=False)
        self.assertNotIn("山田", text)
        self.assertNotIn("abc", text)
        self.assertIn("tasks|#ok", text)
        self.assertNotIn("inflow", text)  # a card press belongs to Home only

    def test_one_summary_cannot_add_hours_without_end(self):
        u.record({"view": "notes", "sec": 10 ** 9, "via": "made-up", "hits": {"notes|#a": 10 ** 9}}, START)
        day = u.read_days()[START.isoformat()]
        self.assertEqual(day["views"]["notes"], {"n": 1, "sec": u.MAX_SEC, "via": {"other": 1}})
        self.assertNotIn("hits", day)


class View(Temp):
    def test_totals_follow_the_period_and_the_rate_can_be_worked_out(self):
        for k in range(20):
            u.record({"view": "home", "sec": 10, "via": "other", "cards": {"inflow": 1}, "newCards": ["inflow"] if k % 2 else []},
                     START + timedelta(days=k))
        u.record({"view": "tasks", "sec": 60, "via": "home", "hits": {"tasks|#old": 1}, "new": ["tasks|#old"]}, START)
        today = START + timedelta(days=19)
        v = u.view("7", today)
        self.assertEqual((v["views"]["home"]["n"], v["views"]["home"]["days"], v["homeN"], v["counted"]), (7, 7, 7, 7))
        self.assertNotIn("tasks", v["views"])
        self.assertEqual(v["cards"]["inflow"]["n"], 7)
        full = u.view("all", today)
        self.assertEqual(full["views"]["tasks"], {"n": 1, "days": 1, "sec": 60, "via": {"home": 1}, "last": START.isoformat()})
        self.assertEqual(full["cards"]["inflow"]["v"], 10)
        self.assertEqual(full["stale"], [{"view": "tasks", "key": "#old", "last": START.isoformat()}])

    def test_the_first_arrangement_waits_for_a_monday_with_14_days_behind_it(self):
        u.record({"view": "home", "sec": 1}, START)
        self.assertEqual(u.first_run(START), MON)
        v = u.view("14", date(2026, 11, 16))
        self.assertEqual((v["ready"], v["nextRun"]), (False, MON.isoformat()))
        v = u.view("14", MON + timedelta(days=2))
        self.assertEqual((v["ready"], v["nextRun"]), (True, (MON + timedelta(days=7)).isoformat()))


class Plan(unittest.TestCase):
    def test_nothing_moves_before_14_days_or_twice_in_a_week_or_when_switched_off(self):
        days = days_of({"tasks": 14, "netspeed": 0})
        self.assertIsNone(u.plan(days, layout(), VIEWS, CARDS, date(2026, 11, 16))[1])
        self.assertIsNone(u.plan(days, layout(auto=False), VIEWS, CARDS, MON)[1])
        lay, moves = u.plan(days, layout(), VIEWS, CARDS, MON + timedelta(days=3))
        self.assertTrue(moves)
        self.assertEqual(lay["week"], MON.isoformat())
        self.assertIsNone(u.plan(days, lay, VIEWS, CARDS, MON + timedelta(days=4))[1])

    def test_often_opened_screens_go_up_and_at_most_three_move(self):
        opened = {i: 14 for i in IDS if i != "home"}
        opened.update(tasks=14, notes=12, bodysleep=9, calendar=5, wins=3, netspeed=0, pc=0)
        lay, moves = u.plan(days_of(opened | {"habits": 3, "bodymeal": 3, "news": 3, "heritage": 3}), layout(), VIEWS, CARDS, MON)
        self.assertEqual(lay["nav"], {"fav": ["tasks", "notes", "bodysleep"], "rare": []})
        self.assertEqual([m for m in moves if m["kind"] == "nav"], [{"kind": "nav", "id": i, "to": "fav"} for i in ("tasks", "notes", "bodysleep")])
        self.assertEqual(lay["notice"]["moves"], moves)
        self.assertEqual(lay["history"][-1]["before"]["nav"], {"fav": [], "rare": []})

    def test_unopened_screens_go_down_and_come_back_when_opened(self):
        quiet = {i: 2 for i in IDS if i not in ("home", "netspeed", "pc")}
        lay, moves = u.plan(days_of(quiet), layout(), VIEWS, CARDS, MON)
        self.assertEqual(lay["nav"], {"fav": [], "rare": ["netspeed", "pc"]})
        later = MON + timedelta(days=14)
        lay, moves = u.plan(days_of(quiet | {"pc": 1}, start=MON, until=later) | days_of(quiet), lay, VIEWS, CARDS, later)
        self.assertEqual(lay["nav"]["rare"], ["netspeed"])
        self.assertIn({"kind": "nav", "id": "pc", "to": "group"}, moves)

    def test_home_itself_a_pinned_one_a_new_one_and_one_just_moved_stay(self):
        opened = {"tasks": 14, "notes": 13, "calendar": 12}
        lay = layout(pins={"nav": ["tasks", "netspeed"], "home": []})
        lay["known"]["nav"]["calendar"] = (MON - timedelta(days=3)).isoformat()
        lay["moved"]["nav"]["notes"] = (MON - timedelta(days=7)).isoformat()
        got, moves = u.plan(days_of(opened | {i: 2 for i in IDS if i not in ("home", "netspeed", "pc")}), lay, VIEWS, CARDS, MON)
        self.assertEqual(got["nav"], {"fav": [], "rare": ["pc"]})
        self.assertEqual(moves, [{"kind": "nav", "id": "pc", "to": "rare"}])

    def test_a_full_top_group_swaps_out_the_least_opened(self):
        lay = layout(nav={"fav": ["habits", "wins", "news", "heritage", "pc"], "rare": []})
        opened = {i: 2 for i in IDS if i != "home"}
        opened.update(habits=9, wins=8, news=7, heritage=6, pc=2, tasks=14)
        got, moves = u.plan(days_of(opened), lay, VIEWS, CARDS, MON)
        self.assertEqual(got["nav"]["fav"], ["habits", "wins", "news", "heritage", "tasks"])
        self.assertEqual(moves[:2], [{"kind": "nav", "id": "pc", "to": "group"}, {"kind": "nav", "id": "tasks", "to": "fav"}])

    def test_cards_that_get_pressed_go_up_three_at_most_and_none_is_lost(self):
        opened = {i: 2 for i in IDS if i != "home"}
        cards = {"inflow": 12, "tools": 9, "wins": 6, "notes": 5, "today": 1}
        lay, moves = u.plan(days_of(opened, cards, home=40), layout(), VIEWS, CARDS, MON)
        home = [m for m in moves if m["kind"] == "home"]
        self.assertEqual(len(home), 3)
        self.assertEqual(sorted(lay["home"]), sorted(CARDS))
        self.assertEqual(lay["home"][:4], ["inflow", "tools", "today", "triage"])
        self.assertTrue(all(m["dir"] == "up" for m in home))
        self.assertTrue(u.sound(lay, IDS, CARDS))

    def test_a_pinned_card_keeps_its_place_and_few_home_opens_move_nothing(self):
        opened = {i: 2 for i in IDS if i != "home"}
        lay = layout(pins={"nav": [], "home": ["focus"]})
        got, _ = u.plan(days_of(opened, {"inflow": 12, "tools": 9}, home=40), lay, VIEWS, CARDS, MON)
        self.assertEqual(got["home"].index("focus"), CARDS.index("focus"))
        self.assertEqual(got["home"][0], "inflow")
        got, moves = u.plan(days_of(opened, {"inflow": 3}, home=5, start=MON - timedelta(days=5)) | {START.isoformat(): {"views": {}}},
                            layout(), VIEWS, CARDS, MON)
        self.assertEqual((got["home"], moves), ([], []))

    def test_a_card_the_page_gained_lands_beside_its_neighbour(self):
        self.assertEqual(u.merge_order(["c", "a"], ["a", "b", "c", "d"]), ["c", "d", "a", "b"])
        self.assertEqual(u.merge_order(["gone", "b"], ["a", "b"]), ["a", "b"])


class Change(Temp):
    def sync(self, today):
        return u.change({"op": "sync", "views": VIEWS, "cards": CARDS}, True, today)

    def write_days(self, days):
        u._write("days.json", days)

    def test_the_week_runs_once_and_undo_brings_last_week_back(self):
        self.assertEqual(self.sync(START)["nav"], {"fav": [], "rare": []})
        self.write_days(days_of({"tasks": 14, "notes": 2, "calendar": 2, "habits": 2, "bodysleep": 2, "bodymeal": 2, "wins": 2, "news": 2,
                                 "heritage": 2, "pc": 2}))
        got = self.sync(MON)
        self.assertEqual(got["nav"], {"fav": ["tasks"], "rare": ["netspeed"]})
        self.assertEqual([m["id"] for m in got["notice"]["moves"]], ["tasks", "netspeed"])
        self.assertTrue(got["canUndo"])
        back = u.change({"op": "undo"}, True, MON)
        self.assertEqual((back["nav"], back["notice"], back["canUndo"]), ({"fav": [], "rare": []}, None, False))
        self.assertTrue(back["history"][0]["undone"])
        self.assertEqual(self.sync(MON + timedelta(days=1))["nav"], {"fav": [], "rare": []})  # not again this week

    def test_pin_auto_seen_and_reset(self):
        self.sync(START)
        self.write_days(days_of({"tasks": 14} | {i: 2 for i in IDS if i not in ("home", "tasks", "netspeed")}))
        self.assertEqual(u.change({"op": "pin", "kind": "nav", "id": "tasks", "on": True}, True, START)["pins"]["nav"], ["tasks"])
        self.assertFalse(u.change({"op": "auto", "on": False}, True, START)["auto"])
        self.assertIsNone(self.sync(MON)["week"])
        u.change({"op": "auto", "on": True}, True, MON)
        got = self.sync(MON)
        self.assertEqual(got["nav"], {"fav": [], "rare": ["netspeed"]})
        self.assertIsNone(u.change({"op": "seen"}, True, MON)["notice"])
        got = u.change({"op": "reset"}, True, MON)
        self.assertEqual((got["nav"], got["changed"], got["pins"]["nav"]), ({"fav": [], "rare": []}, False, ["tasks"]))
        self.assertTrue(got["history"][0]["reset"])

    def test_a_test_server_changes_nothing(self):
        self.sync(START)
        def text():
            with open(os.path.join(self.dir.name, "layout.json")) as f:
                return f.read()
        before = text()
        got = u.change({"op": "pin", "kind": "nav", "id": "tasks", "on": True}, False, START)
        self.assertEqual((got["on"], got["pins"]["nav"]), (False, []))
        self.assertEqual(text(), before)
        self.assertFalse(u.enabled(4791))
        self.assertTrue(u.enabled(4747))

    def test_a_broken_file_reads_as_the_first_arrangement(self):
        with open(os.path.join(self.dir.name, "layout.json"), "w") as f:
            f.write('{"nav": {"fav": ["home", "x y", "tasks", "tasks"], "rare": "no"}, "home": 3, "pins": []')
        self.assertEqual(u.read_layout()["nav"], {"fav": [], "rare": []})
        self.assertEqual(u.normal({"nav": {"fav": ["home", "x y", "tasks", "tasks"], "rare": ["tasks", "pc"]}})["nav"], {"fav": ["tasks"], "rare": ["pc"]})


if __name__ == "__main__":
    unittest.main()
