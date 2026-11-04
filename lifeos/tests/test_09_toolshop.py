import ast
import contextlib
import io
import json
import os
import unittest
from datetime import datetime
from unittest import mock

from lifeos.lib import logs, paths, tools
from lifeos.lib.tools import load_file
from lifeos.tests.sandbox import SandboxCase

REAL_DIR = os.path.join(paths.REPO, "lifeos", "09-toolshop")
shop = load_file(REAL_DIR, "toolshop")


class ShopCase(SandboxCase):
    """A fake lifeos/ in the temp folder, so nothing is scaffolded into the repository. Noon by default."""

    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.tmp, "repo", "lifeos")
        os.makedirs(self.root)
        os.environ["LIFEOS_TOOLS_ROOT"] = self.root
        self.at("12:00")

    def at(self, hhmm):
        h, m = map(int, hhmm.split(":"))
        p = mock.patch.object(paths, "now", return_value=datetime(2026, 11, 4, h, m, tzinfo=paths.HCMC))
        p.start()
        self.addCleanup(p.stop)

    def seed(self, no, status, name=None):
        d = os.path.join(self.root, f"{no}-seed")
        os.makedirs(d)
        with open(os.path.join(d, "tool.json"), "w", encoding="utf-8") as f:
            json.dump({"no": no, "name": name or f"既存{no}", "status": status}, f, ensure_ascii=False)
        return d

    def rec(self, folder):
        with open(os.path.join(self.root, folder, "tool.json"), encoding="utf-8") as f:
            return json.load(f)

    def folders(self):
        return [os.path.basename(d) for d in tools.tool_dirs()]

    def fill_in(self, folder):
        for name in ("README.md", "core.py", "tool.json"):
            path = os.path.join(self.root, folder, name)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            with open(path, "w", encoding="utf-8") as f:
                f.write(text.replace(shop.TODO, "書いた"))


class TestNew(ShopCase):
    def test_makes_readme_code_and_tool_json(self):
        t = shop.new("領収書箱", "領収書を月ごとにまとめる", slug="receipt-box")
        self.assertEqual(t["slug"], "01-receipt-box")
        rec = self.rec("01-receipt-box")
        self.assertEqual((rec["no"], rec["name"], rec["status"]), ("01", "領収書箱", "building"))
        self.assertEqual(set(rec), {"no", "name", "oneline", "status", "usage", "howToCheck", "blockers",
                                    "commands", "updated"})
        with open(os.path.join(self.root, "01-receipt-box", "README.md"), encoding="utf-8") as f:
            readme = f.read()
        for heading in ("## 目的", "## 使い方", "## 止め方", "領収書を月ごとにまとめる", "set 01 stopped"):
            self.assertIn(heading, readme)

    def test_scaffolded_code_runs_and_its_test_passes(self):
        shop.new("試し", "試す", slug="try-it")
        core = load_file(os.path.join(self.root, "01-try-it"), "core")
        self.assertIn("あ", core.run("あ"))
        mod = load_file(os.path.join(self.root, "tests"), "test_01_try_it")
        result = unittest.TestResult()
        unittest.defaultTestLoader.loadTestsFromModule(mod).run(result)
        self.assertEqual((result.testsRun, result.errors, result.failures), (1, [], []))

    def test_appears_on_the_desk_without_touching_it(self):
        from lifeos.tests.desk_import import server
        with mock.patch.object(server, "REPO", os.path.dirname(self.root)):
            self.assertEqual(server.lifeos_tools(), [])
            shop.new("新入り", "試す")
            got = server.lifeos_tools()
        self.assertEqual([(t["id"], t["name"], t["status"]) for t in got], [("01-tool", "新入り", "building")])

    def test_numbers_continue_from_the_largest(self):
        self.seed("18", "working")
        self.assertEqual(shop.new("次", "試す", slug="next")["slug"], "19-next")
        with self.assertRaises(shop.Stop):
            shop.new("次", "同じ名前")
        self.assertEqual(self.folders(), ["18-seed", "19-next"])

    def test_refuses_empty_and_full(self):
        for name, purpose in (("", "x"), ("x", ""), ("   ", "x")):
            with self.assertRaises(shop.Stop):
                shop.new(name, purpose)
        self.seed("99", "working")
        with self.assertRaises(shop.Stop):
            shop.new("a", "b")

    def test_user_text_cannot_break_out(self):
        evil = 'x"""\nimport os; os.system("x") $HOME {folder} {no}'
        t = shop.new(evil[:40], evil, slug="../../evil")
        self.assertTrue(t["dir"].startswith(self.root + os.sep))
        self.assertEqual(os.path.dirname(t["dir"]), self.root)
        for path in (os.path.join(t["dir"], "core.py"),
                     os.path.join(self.root, "tests", f"test_{t['no']}_{t['slug'][3:].replace('-', '_')}.py")):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            ast.parse(text)
            self.assertNotIn("os.system", text)

    def test_japanese_name_gets_a_plain_folder(self):
        self.assertEqual(shop.new("家計メモ", "試す")["slug"], "01-tool")
        self.assertEqual(shop.make_slug("Sub Check!"), "sub-check")


class TestLimit(ShopCase):
    def test_third_is_allowed_and_fourth_stops(self):
        self.seed("01", "building")
        self.seed("02", "building")
        shop.new("三つ目", "試す", slug="third")
        before = self.folders()
        with self.assertRaises(shop.Stop) as e:
            shop.new("四つ目", "あとでやる", slug="fourth")
        self.assertEqual(self.folders(), before)
        self.assertIn("先に1つ仕上げましょう", str(e.exception))
        self.assertIn("三つ目", str(e.exception))

    def test_the_idea_is_kept_and_leaves_once_built(self):
        for no in ("01", "02", "03"):
            self.seed(no, "building")
        with self.assertRaises(shop.Stop):
            shop.new("四つ目", "あとでやる")
        self.assertEqual([x["purpose"] for x in shop.ideas()], ["あとでやる"])
        shop.set_status("01", "stopped")
        shop.new("四つ目", "あとでやる")
        self.assertEqual(shop.ideas(), [])

    def test_only_building_counts(self):
        for no, status in (("01", "planned"), ("02", "blocked"), ("03", "working"), ("04", "stopped"),
                           ("05", "building"), ("06", "building")):
            self.seed(no, status)
        self.assertEqual(shop.new("通る", "試す", slug="ok")["slug"], "07-ok")

    def test_going_back_to_building_respects_the_limit(self):
        for no in ("01", "02", "03"):
            self.seed(no, "building")
        self.seed("04", "stopped")
        with self.assertRaises(shop.Stop):
            shop.set_status("04", "building")
        self.assertEqual(self.rec("04-seed")["status"], "stopped")


class TestEndTime(ShopCase):
    def test_default_hours(self):
        for hhmm, want in (("22:59", False), ("23:00", True), ("02:00", True), ("04:59", True), ("05:00", False)):
            self.at(hhmm)
            self.assertEqual(shop.resting(), want, hhmm)

    def test_refusal_keeps_the_idea(self):
        self.at("23:30")
        with self.assertRaises(shop.Stop) as e:
            shop.new("夜の案", "朝にやる")
        self.assertIn("05:00", str(e.exception))
        self.assertEqual(self.folders(), [])
        self.assertEqual(shop.ideas()[0]["why"], "time")

    def write_config(self, text):
        with open(paths.data_path("tools", shop.SELF, "config.json"), "w", encoding="utf-8") as f:
            f.write(text)

    def test_settings_change_the_hours(self):
        self.write_config('{"endTime": "01:00", "resumeTime": "06:00"}')
        for hhmm, want in (("23:30", False), ("00:59", False), ("01:00", True), ("05:59", True), ("06:00", False)):
            self.at(hhmm)
            self.assertEqual(shop.resting(), want, hhmm)

    def test_broken_settings_mean_the_defaults(self):
        for text in ("{broken", "[]", '{"endTime": "25:00", "resumeTime": 5}'):
            self.write_config(text)
            self.assertEqual(shop.hours(), ("23:00", "05:00"))


class TestStatus(ShopCase):
    def test_list_shows_name_and_the_three_states(self):
        self.seed("01", "building", "作りかけの道具")
        self.seed("02", "working", "動く道具")
        self.seed("03", "stopped", "止めた道具")
        lines = shop.show().splitlines()
        self.assertIn("作りかけ 1 / 3", lines[0])
        for line, name, label in zip(lines[1:], ("作りかけの道具", "動く道具", "止めた道具"), ("作成中", "稼働中", "停止")):
            self.assertIn(name, line)
            self.assertIn(label, line)

    def test_finish_refuses_what_is_not_written(self):
        t = shop.new("途中", "試す", slug="half")
        with self.assertRaises(shop.Stop):
            shop.set_status(t["no"], "working")
        self.assertEqual(self.rec("01-half")["status"], "building")
        self.fill_in("01-half")
        self.assertEqual(shop.set_status("01", "working")["label"], "稼働中")

    def test_stopping_keeps_every_file(self):
        t = shop.new("止める", "試す", slug="pause")
        before = sorted(os.listdir(t["dir"]))
        self.assertEqual(shop.set_status("止める", "停止")["status"], "stopped")
        self.assertEqual(sorted(os.listdir(t["dir"])), before)

    def test_broken_tool_json_is_listed_and_not_overwritten(self):
        d = os.path.join(self.root, "05-bad")
        os.makedirs(d)
        with open(os.path.join(d, "tool.json"), "w") as f:
            f.write("{broken")
        self.assertIn("読めません", shop.show())
        with self.assertRaises(shop.Stop):
            shop.set_status("05", "stopped")
        with open(os.path.join(d, "tool.json")) as f:
            self.assertEqual(f.read(), "{broken")

    def test_unknown_tool_and_state(self):
        self.seed("01", "working")
        for args in (("99", "stopped"), ("01", "done")):
            with self.assertRaises(shop.Stop):
                shop.set_status(*args)

    def test_the_workshop_itself_can_be_stopped(self):
        d = os.path.join(self.root, "09-toolshop")
        os.makedirs(d)
        with open(os.path.join(d, "tool.json"), "w") as f:
            json.dump({"no": "09", "name": "ツール工房", "status": "stopped"}, f)
        with self.assertRaises(shop.Stop):
            shop.new("a", "b")
        self.assertIn("ツール工房", shop.show())


class TestCommandLine(ShopCase):
    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = shop.main(list(argv))
        return code, out.getvalue()

    def test_new_list_finish_stop(self):
        code, text = self.run_main("new", "道具", "試す", "ため", "--slug", "kit")
        self.assertEqual(code, 0)
        self.assertIn("lifeos/01-kit/", text)
        self.assertEqual(self.rec("01-kit")["oneline"], "試す ため")
        self.assertIn("作成中", self.run_main("list")[1])
        self.assertEqual(json.loads(self.run_main("list", "--json")[1])[0]["status"], "building")
        self.assertEqual(self.run_main("finish", "01")[0], 2)
        self.fill_in("01-kit")
        self.assertIn("稼働中", self.run_main("finish", "01")[1])
        self.assertIn("停止", self.run_main("set", "kit", "stopped")[1])

    def test_refusal_exits_with_2_and_says_why(self):
        self.at("23:30")
        code, text = self.run_main("new", "夜", "試す")
        self.assertEqual(code, 2)
        self.assertIn("案は残しました", text)
        self.assertIn("夜", self.run_main("ideas")[1])

    def test_log_stays_in_the_data_folder(self):
        self.run_main("new", "道具", "試す")
        self.assertEqual(logs.read(shop.LOG)[0]["event"], "made")
        self.assertTrue(paths.data_path("logs", "toolshop.jsonl").startswith(self.tmp))


class TestRepositoryFiles(unittest.TestCase):
    def test_own_tool_json_and_readme(self):
        with open(os.path.join(REAL_DIR, "tool.json"), encoding="utf-8") as f:
            rec = json.load(f)
        self.assertEqual((rec["no"], rec["commands"]), ("09", ["/newtool"]))
        with open(os.path.join(REAL_DIR, "README.md"), encoding="utf-8") as f:
            readme = f.read()
        for heading in ("## 目的", "## 使い方", "## 止め方"):
            self.assertIn(heading, readme)

    def test_the_workshop_can_be_restarted_after_stopping(self):
        self.assertEqual(shop.todo_left(REAL_DIR), [])

    def test_the_command_runs_this_script(self):
        with open(os.path.join(paths.REPO, ".claude", "commands", "newtool.md"), encoding="utf-8") as f:
            self.assertIn("lifeos/09-toolshop/toolshop.py", f.read())
