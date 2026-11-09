"""08 2-minute tidy: rotation, the no-score rule, keeping the data safe."""
import json as _json
import os as _os
import unittest as _unittest

with open(_os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "08-tidy", "tool.json"), encoding="utf-8") as _f:
    if _json.load(_f).get("status") == "stopped":  # the owner stopped this tool; its tests come back with it
        raise _unittest.SkipTest("08-tidy は停止中")

import json
import os
import re
import shutil
from unittest import mock

from lifeos.bot import router
from lifeos.lib import logs, paths, tools
from lifeos.lib.tools import load_tool
from lifeos.tests.sandbox import SandboxCase

SCORING = re.compile(r"散らかり|評価|点数|スコア|ビフォー|アフター|前回|日ぶり|連続|サボ|さぼ|遅れ|[0-9]|"
                     r"(?i:mess|clutter|score|rating|before|after|last time|days? ago|since|streak|behind|late|!)")


class Base(SandboxCase):
    def setUp(self):
        super().setUp()
        self.h = load_tool("08-tidy", "handler")
        self.replies = []

    def say(self, text):
        reply = self.h.handle(text, router.Ctx())
        self.replies.append(reply)
        return reply

    def on_day(self, day):
        return mock.patch.object(paths, "today", return_value=day)

    def state(self):
        with open(self.h._file(), encoding="utf-8") as f:
            return json.load(f)

    def place(self, name):
        return next(p for p in self.state()["places"] if p["name"] == name)


class TestMatch(Base):
    def test_takes_only_the_clear_signals(self):
        for t in ("片付け", "片づけ", "かたづけ", "片付け。", " 片付け ", "済", "済み", "済。",
                  "片付け場所", "片付け追加 Desk", "片付け追加　寝室　枕元", "片付け外す Desk", "片付け追加"):
            self.assertTrue(self.h.match(t), t)
        for t in ("片付けをした", "部屋の片付けについてのメモ", "返済", "済んだ話", "task: 片付け", ""):
            self.assertFalse(self.h.match(t), t)

    def test_match_writes_nothing(self):
        self.h.match("片付け")
        self.assertFalse(os.path.exists(paths.data_dir()))

    def test_router_gives_it_to_this_tool_first(self):
        routes = router.discover().routes
        self.assertEqual(self.h.PRIORITY, 10)
        self.assertEqual(router.which("片付け", routes).slug, "08-tidy")
        self.assertEqual(router.which("済", routes).slug, "08-tidy")

    def test_stopped_tool_is_not_loaded(self):
        root = os.path.join(self.tmp, "tools")
        shutil.copytree(os.path.join(tools.root_dir(), "08-tidy"), os.path.join(root, "08-tidy"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        path = os.path.join(root, "08-tidy", "tool.json")
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
        meta["status"] = "stopped"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f)
        self.assertEqual(router.discover(root).routes, [])


class TestRotation(Base):
    def test_gives_exactly_one_place(self):
        reply = self.say("片付け")
        self.assertIn("Two minutes today: Desk", reply)
        self.assertNotIn("Kitchen", reply)
        self.assertNotIn("Bathroom", reply)

    def test_same_place_until_done(self):
        self.assertEqual(self.say("片付け"), self.say("片付け"))

    def test_done_then_a_different_place(self):
        self.say("片付け")
        self.say("済")
        self.assertIn("Kitchen", self.say("片付け"))

    def test_goes_round_all_places_then_repeats(self):
        got = []
        for _ in range(4):
            self.say("片付け")
            got.append(self.h._label(self.h._current(self.h._read())))
            self.say("済")
        self.assertEqual(got, ["Desk", "Kitchen", "Bathroom", "Desk"])

    def test_oldest_last_date_comes_first(self):
        for day, name in (("2026-11-01", "Desk"), ("2026-11-02", "Kitchen"), ("2026-11-03", "Bathroom")):
            with self.on_day(day):
                self.say("片付け")
                self.assertIn(name, self.replies[-1])
                self.say("済")
        self.assertEqual(self.place("Desk")["last"], "2026-11-01")
        with self.on_day("2026-11-04"):
            self.assertIn("Desk", self.say("片付け"))

    def test_hand_edited_dates_decide_the_order(self):
        self.say("片付け場所")
        s = self.state()
        s["places"] = ["Desk", {"name": "棚", "last": "2026-01-01"}, {"name": "床", "last": "2025-01-01"}]
        with open(self.h._file(), "w", encoding="utf-8") as f:
            json.dump(s, f)
        self.assertIn("Two minutes today: Desk", self.say("片付け"))  # never tidied goes first
        self.say("済")
        self.assertIn("Two minutes today: 床", self.say("片付け"))

    def test_new_day_without_done_stays_quiet_and_picks_again(self):
        with self.on_day("2026-11-01"):
            self.say("片付け")
        with self.on_day("2026-11-09"):
            reply = self.say("片付け")
        self.assertEqual(reply, 'Two minutes today: Desk\nSend "done" when you finish.')
        self.assertEqual(self.state()["current"]["day"], "2026-11-09")

    def test_done_next_morning_still_counts(self):
        with self.on_day("2026-11-01"):
            self.say("片付け")
        with self.on_day("2026-11-02"):
            self.say("済")
        self.assertEqual(self.place("Desk")["last"], "2026-11-02")

    def test_done_with_nothing_out_changes_nothing(self):
        self.assertIn("No place is out", self.say("済"))
        self.assertEqual(logs.read("tidy"), [])
        self.say("片付け")
        self.say("済")
        self.assertIn("No place is out", self.say("済"))
        self.assertEqual(len(logs.read("tidy")), 1)

    def test_data_is_only_in_the_data_folder(self):
        self.say("片付け")
        self.say("済")
        self.assertTrue(self.h._file().startswith(paths.data_dir()))
        self.assertTrue(os.path.exists(paths.data_path("tools", "08-tidy", "places.json")))
        self.assertEqual(logs.read("tidy")[0]["place"], "Desk")


class TestPlaces(Base):
    def test_add_with_a_room(self):
        self.assertIn("Added 寝室 枕元.", self.say("片付け追加　寝室 枕元"))
        self.assertEqual(self.place("枕元")["room"], "寝室")

    def test_new_place_comes_before_tidied_ones(self):
        for _ in range(3):
            self.say("片付け")
            self.say("済")
        self.say("片付け追加 棚")
        self.assertIn("棚", self.say("片付け"))

    def test_add_twice_is_one_place(self):
        self.say("片付け追加 棚")
        self.assertIn("already on the list", self.say("片付け追加 棚"))
        self.assertEqual(sum(p["name"] == "棚" for p in self.state()["places"]), 1)

    def test_remove_keeps_the_date_and_add_brings_it_back(self):
        with self.on_day("2026-11-01"):
            self.say("片付け")
            self.say("済")
        self.assertIn("to bring it back", self.say("片付け外す Desk"))
        self.assertTrue(self.place("Desk")["off"])
        self.assertNotIn("Desk", self.say("片付け場所").split("\n")[0])
        self.assertIn("back on the list", self.say("片付け追加 Desk"))
        self.assertEqual(self.place("Desk")["last"], "2026-11-01")

    def test_removed_place_is_not_given(self):
        self.say("片付け外す Desk")
        self.assertIn("Kitchen", self.say("片付け"))

    def test_removing_the_place_out_now(self):
        self.say("片付け")
        self.say("片付け外す Desk")
        self.assertIsNone(self.state()["current"])
        self.assertIn("No place is out", self.say("済"))

    def test_same_name_in_two_rooms_asks_which(self):
        self.say("片付け追加 寝室 棚")
        self.say("片付け追加 居間 棚")
        self.assertIn("more than one", self.say("片付け外す 棚"))
        self.say("片付け外す 居間 棚")
        self.assertFalse(next(p for p in self.state()["places"] if p["room"] == "寝室")["off"])

    def test_no_places_left_says_what_to_do(self):
        for n in ("Desk", "Kitchen", "Bathroom"):
            self.say("片付け外す " + n)
        self.assertIn("tidy add", self.say("片付け"))

    def test_missing_names(self):
        self.assertIn("tidy add", self.say("片付け追加"))
        self.assertIn("tidy remove", self.say("片付け外す"))
        self.assertIn("not on the list", self.say("片付け外す 宇宙"))

    def test_list_shows_order_without_dates(self):
        with self.on_day("2026-11-01"):
            self.say("片付け")
            self.say("済")
        self.assertIn("Kitchen, Bathroom, Desk", self.say("片付け場所"))


class TestEnglish(Base):
    def test_takes_the_english_commands(self):
        for t in ("tidy", "Tidy", "tidy up", "done", "Done.", "tidied", "tidy places", "tidy list",
                  "tidy add desk", "Tidy add Bedroom Nightstand", "tidy remove desk", "tidy add"):
            self.assertTrue(self.h.match(t), t)
        for t in ("tidy the desk later", "done with that", "untidy", "task: tidy"):
            self.assertFalse(self.h.match(t), t)

    def test_a_round_in_english(self):
        self.assertEqual(self.say("tidy"), 'Two minutes today: Desk\nSend "done" when you finish.')
        self.assertIn("Desk is recorded", self.say("done"))
        self.assertIn("Kitchen", self.say("tidy"))
        self.assertEqual(self.say("tidy add Bedroom Nightstand"), "Added Bedroom Nightstand.")
        self.assertIn("already on the list", self.say("tidy add desk"))
        self.assertIn('"tidy add Bedroom Nightstand"', self.say("tidy remove nightstand"))
        self.assertTrue(self.place("Nightstand")["off"])
        self.assertIn("Removed: Bedroom Nightstand", self.say("tidy places"))

    def test_an_existing_japanese_file_is_untouched(self):
        with open(self.h._file(), "w", encoding="utf-8") as f:
            json.dump({"places": [{"room": "", "name": "机", "last": "", "seq": 0, "off": False}], "seq": 0, "current": None}, f)
        self.assertIn("机", self.say("tidy"))
        self.assertIn("机 is recorded", self.say("済"))
        self.assertEqual([p["name"] for p in self.state()["places"]], ["机"])


class TestNoScore(Base):
    def test_no_reply_scores_or_compares(self):
        for day in ("2026-11-01", "2026-11-20"):
            with self.on_day(day):
                for t in ("片付け", "片付け", "済", "済", "片付け", "片付け場所", "片付け追加 棚",
                          "片付け外す 棚", "片付け追加 棚", "片付け追加", "tidy", "done", "done", "tidy places",
                          "tidy add Shelf", "tidy remove Shelf", "tidy remove", "tidy remove Moon"):
                    self.say(t)
        for reply in self.replies:
            found = SCORING.search(reply)
            self.assertIsNone(found, reply)

    def test_nothing_is_recorded_about_clutter(self):
        self.say("片付け")
        self.say("済")
        self.assertEqual(set(logs.read("tidy")[0]), {"at", "place"})
        self.assertEqual(set(self.place("Desk")), {"room", "name", "last", "seq", "off"})


class TestSafety(Base):
    def test_broken_file_is_not_overwritten(self):
        path = self.h._file()
        with open(path, "w", encoding="utf-8") as f:
            f.write("{こわれた")
        with self.assertRaises(self.h.PlacesError):
            self.h.handle("片付け", router.Ctx())
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{こわれた")

    def test_broken_file_keeps_the_words_through_the_router(self):
        with open(self.h._file(), "w", encoding="utf-8") as f:
            f.write("[]")
        reply = router.dispatch("片付け", router.Ctx(), router.discover().routes)
        self.assertIn("hit an error", reply)
        self.assertEqual(logs.read("unrouted")[0]["text"], "片付け")

    def test_tool_json_is_filled_in(self):
        meta = tools.info(os.path.join(tools.root_dir(), "08-tidy"))
        self.assertEqual(meta["no"], "08")
        self.assertIn(meta["status"], ("working", "blocked"))
        for key in ("usage", "howToCheck"):
            self.assertTrue(meta[key])
        for b in meta["blockers"]:
            self.assertTrue(b["what"] and b["why"] and b["needs"])
