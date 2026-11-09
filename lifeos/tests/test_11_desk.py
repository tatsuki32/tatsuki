import json
import os

from lifeos.lib import paths
from lifeos.lib.tools import load_tool
from lifeos.tests.desk_import import server
from lifeos.tests.sandbox import SandboxCase

core = load_tool("11-notes", "core")
memos = load_tool("11-notes", "memos")


class TestMemolink(SandboxCase):
    def seed(self):
        memos.add("単語帳アプリを作りたい #語学")
        memos.add("単語帳アプリの復習を調べた #語学")

    def test_view_before_any_run(self):
        v = core.desk_view()
        self.assertEqual((v["memos"], v["lastRun"], v["cosense"], v["reader"]), (0, None, False, False))

    def test_run_answers_counts_only(self):
        self.seed()
        code, v = server.memolink_run()
        self.assertEqual(code, 200)
        self.assertEqual((v["memos"], v["linked"], v["unread"]), (2, 2, 0))
        self.assertGreater(v["written"], 0)
        self.assertTrue(v["lastRun"])
        self.assertNotIn("単語帳", json.dumps(v, ensure_ascii=False))

    def test_written_is_zero_on_a_second_run(self):
        self.seed()
        server.memolink_run()
        self.assertEqual(server.memolink_run()[1]["written"], 0)

    def test_two_at_once_are_refused(self):
        self.assertTrue(server.MEMOLINK_LOCK.acquire(blocking=False))
        self.addCleanup(server.MEMOLINK_LOCK.release)
        self.assertEqual(server.memolink_run()[0], 409)

    def test_lock_is_released_after_a_failure(self):
        self.seed()
        os.makedirs(paths.desk_data_dir(), exist_ok=True)
        path = os.path.join(paths.desk_data_dir(), "notes.json")
        with open(path, "w") as f:
            f.write("こわれている")
        code, out = server.memolink_run()
        self.assertEqual(code, 500)
        self.assertIn("notes.json", out["error"])
        os.remove(path)
        self.assertEqual(server.memolink_run()[0], 200)

    def test_failure_does_not_show_the_memo(self):
        self.seed()
        real = core.run
        self.addCleanup(setattr, core, "run", real)

        def boom():
            raise RuntimeError("単語帳アプリ")
        core.run = boom
        code, out = server.memolink_run()
        self.assertEqual(code, 500)
        self.assertNotIn("単語帳", out["error"])

    def test_stopped_changes_nothing(self):
        self.seed()
        core.save_settings(enabled=False)
        code, v = server.memolink_run()
        self.assertEqual(code, 200)
        self.assertFalse(v["enabled"])
        self.assertEqual(v["memos"], 0)


class TestWiring(SandboxCase):
    def read(self, *parts):
        with open(os.path.join(paths.REPO, *parts), encoding="utf-8") as f:
            return f.read()

    def test_page_has_the_view(self):
        html = self.read("desk", "index.html")
        for word in ('id:"memolink"', "function mlView", "/api/memolink/run", "#mlRun"):
            self.assertIn(word, html)

    def test_scheduled_tasks_run_it(self):
        self.assertIn("lifeos/11-notes/cli.py", self.read("desk", "sync", "SCHEDULED_TASKS.md"))
