import json
import os
import threading
from http.server import ThreadingHTTPServer

from lifeos.lib import logs, md, paths, tasks
from lifeos.tests.sandbox import SandboxCase


class TestPaths(SandboxCase):
    def test_sandbox_keeps_everything_inside(self):
        self.assertTrue(paths.is_sandbox())
        self.assertTrue(paths.desk_data_dir().startswith(paths.data_dir()))
        self.assertIsNone(paths.desk_api())

    def test_default_points_at_the_main_checkout(self):
        del os.environ["LIFEOS_DATA"]
        self.assertFalse(paths.is_sandbox())
        self.assertEqual(paths.data_dir(), os.path.expanduser("~/TatsukiDesk/desk/data/lifeos"))
        self.assertEqual(paths.desk_data_dir(), os.path.expanduser("~/TatsukiDesk/desk/data"))
        self.assertEqual(paths.desk_api(), "http://localhost:4747")

    def test_cannot_leave_the_data_folder(self):
        with self.assertRaises(ValueError):
            paths.data_path("..", "x")
        with self.assertRaises(ValueError):
            md.save("../../x.md", "a")


class TestTasksFile(SandboxCase):
    def test_add_has_the_desk_shape(self):
        t = tasks.add("  試し   の  タスク ", source="lifeos-03")
        self.assertEqual(set(t), {"id", "updatedAt", *tasks.FIELDS})
        self.assertEqual((t["title"], t["status"], t["deleted"]), ("試し の タスク", "notyet", False))
        with open(os.path.join(paths.desk_data_dir(), "tasks.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), [t])

    def test_update_done_delete(self):
        t = tasks.add("a")
        b = tasks.add("b", status="doing")
        self.assertEqual({x["id"] for x in tasks.open_tasks()}, {t["id"], b["id"]})
        self.assertEqual(tasks.done(t["id"])["status"], "done")
        self.assertEqual([x["id"] for x in tasks.open_tasks()], [b["id"]])
        tasks.delete(b["id"])
        self.assertEqual([x["id"] for x in tasks.all_tasks()], [t["id"]])
        self.assertTrue(tasks.get(b["id"])["deleted"])  # kept, not removed

    def test_update_bumps_updated_at_and_keeps_fields(self):
        t = tasks.add("a", note="n")
        with open(os.path.join(paths.desk_data_dir(), "tasks.json"), "r+", encoding="utf-8") as f:
            items = json.load(f)
            items[0]["updatedAt"] = 1
            f.seek(0)
            f.truncate()
            json.dump(items, f)
        u = tasks.update(t["id"], status="doing")
        self.assertGreater(u["updatedAt"], 1)
        self.assertEqual(u["note"], "n")

    def test_refuses_what_the_desk_would_lose(self):
        t = tasks.add("a")
        with self.assertRaises(ValueError):
            tasks.update(t["id"], priority="高")
        with self.assertRaises(ValueError):
            tasks.add("a", status="todo")
        with self.assertRaises(ValueError):
            tasks.add("   ")
        with self.assertRaises(KeyError):
            tasks.update("nope", status="done")

    def test_a_broken_file_is_not_overwritten(self):
        os.makedirs(paths.desk_data_dir())
        path = os.path.join(paths.desk_data_dir(), "tasks.json")
        with open(path, "w") as f:
            f.write("{broken")
        with self.assertRaises(tasks.TaskStoreError):
            tasks.add("a")
        with open(path) as f:
            self.assertEqual(f.read(), "{broken")

    def test_concurrent_adds_lose_nothing(self):
        ts = [threading.Thread(target=tasks.add, args=(f"t{i}",)) for i in range(20)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(tasks.all_tasks()), 20)


class TestTasksApi(SandboxCase):
    """Against the real desk/server.py on a free port, with its data folder in the sandbox."""

    def setUp(self):
        super().setUp()
        from lifeos.tests.desk_import import server
        self.server_mod = server
        desk_data = os.path.join(self.tmp, "desk-server")
        os.makedirs(desk_data)
        old = server.DATA
        server.DATA = desk_data
        self.addCleanup(setattr, server, "DATA", old)
        try:
            self.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        except PermissionError:
            self.skipTest("この環境ではローカルのポートを開けません")
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        os.environ["LIFEOS_DESK_API"] = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def test_goes_through_the_server_when_it_is_up(self):
        t = tasks.add("API 経由")
        self.assertEqual(self.server_mod.load("tasks")[0]["title"], "API 経由")
        self.assertFalse(os.path.exists(os.path.join(paths.desk_data_dir(), "tasks.json")))
        u = tasks.update(t["id"], status="doing")
        self.assertEqual(self.server_mod.load("tasks")[0]["status"], "doing")
        self.assertEqual(self.server_mod.load("tasks")[0]["updatedAt"], u["updatedAt"])
        self.assertEqual(tasks.get(t["id"])["title"], "API 経由")

    def test_falls_back_to_the_file_when_it_is_down(self):
        os.environ["LIFEOS_DESK_API"] = "http://127.0.0.1:9"  # nothing listens here
        tasks.add("ファイルへ")
        self.assertEqual(self.server_mod.load("tasks"), [])
        self.assertEqual(tasks.all_tasks()[0]["title"], "ファイルへ")


class TestLogsAndMd(SandboxCase):
    def test_log_appends_and_reads(self):
        logs.append("habits", {"name": "読書"})
        logs.append("habits", {"name": "散歩"})
        self.assertEqual([x["name"] for x in logs.read("habits")], ["読書", "散歩"])
        self.assertEqual([x["name"] for x in logs.read("habits", limit=1)], ["散歩"])
        self.assertIn("at", logs.read("habits")[0])
        self.assertEqual(logs.read("none"), [])

    def test_log_skips_damaged_lines_and_bad_names(self):
        logs.append("a", {"n": 1})
        with open(paths.data_path("logs", "a.jsonl"), "a") as f:
            f.write("not json\n")
        self.assertEqual(len(logs.read("a")), 1)
        with self.assertRaises(ValueError):
            logs.append("../x", {})

    def test_markdown(self):
        self.assertIsNone(md.read("briefs/x.md"))
        md.save("briefs/x.md", "# 1")
        md.append("briefs/x.md", "- 2")
        self.assertEqual(md.read("briefs/x.md"), "# 1\n- 2\n")
        md.save("briefs/x.md", "new\n")
        self.assertEqual(md.read("briefs/x.md"), "new\n")
        with self.assertRaises(ValueError):
            md.save("a.txt", "x")
