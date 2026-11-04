import contextlib
import io
import json
import os
import subprocess
import sys
import time

from lifeos.bot import router
from lifeos.lib import logs, paths, tasks
from lifeos.lib.tools import load_tool
from lifeos.tests.sandbox import SandboxCase

CLI = os.path.join(paths.REPO, "lifeos", "03-task", "cli.py")


class Base(SandboxCase):
    def setUp(self):
        super().setUp()
        self.core = load_tool("03-task", "core")


class TestParse(Base):
    def test_automatic(self):
        self.assertEqual(self.core.parse("資料を作る")[1:], ("中", "未分類"))
        self.assertEqual(self.core.parse("至急 見積もりを送る")[1], "高")
        self.assertEqual(self.core.parse("いつか本棚を直す")[1], "低")

    def test_explicit_marks_leave_the_title(self):
        self.assertEqual(self.core.parse("資料を作る !低 #案件A"), ("資料を作る", "低", "案件A"))
        self.assertEqual(self.core.parse("優先度:高 案件:案件B 資料"), ("資料", "高", "案件B"))

    def test_url_fragment_is_not_a_project(self):
        self.assertEqual(self.core.parse("https://example.com/#top を読む")[2], "未分類")

    def test_known_project_longest_wins(self):
        self.assertEqual(self.core.parse("案件AB の資料", {"案件A", "案件AB"})[2], "案件AB")


class TestAdd(Base):
    def test_note_and_project_learned(self):
        t = self.core.add("資料を作る #案件A")
        self.assertEqual(t["title"], "資料を作る")
        self.assertEqual(t["note"], "優先度:中 案件:案件A")
        self.assertEqual(self.core.add("案件A の請求")["note"], "優先度:中 案件:案件A")

    def test_only_desk_fields(self):
        t = self.core.add("資料")
        self.assertEqual(set(t), {"id", *tasks.FIELDS, "updatedAt"})

    def test_empty_refused_and_not_logged(self):
        with self.assertRaises(ValueError):
            self.core.add("  #案件A !高 ")
        self.assertEqual(logs.read("task-history"), [])

    def test_history_kept(self):
        t = self.core.add("資料")
        self.assertEqual(logs.read("task-history")[-1]["id"], t["id"])

    def test_within_five_seconds_as_a_command(self):
        start = time.time()
        r = subprocess.run([sys.executable, CLI, "add", "試し"], capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(time.time() - start, 5)
        self.assertIn("試し", [t["title"] for t in tasks.open_tasks()])


class TestNext(Base):
    def test_empty(self):
        self.assertIsNone(self.core.next_task())

    def test_priority_decides_among_equals(self):
        self.core.add("普通")
        self.core.add("大事 !高")
        self.assertEqual(self.core.next_task()["title"], "大事")

    def test_due_today_and_doing_come_before_priority(self):
        self.core.add("大事 !高")
        doing = self.core.add("途中 !低")
        tasks.update(doing["id"], status="doing")
        self.assertEqual(self.core.next_task()["title"], "途中")
        soon = tasks.add("今日締切", due=paths.now_ms() + 1000, note="優先度:低")
        self.assertEqual(self.core.next_task()["id"], soon["id"])

    def test_changes_no_task(self):
        t = self.core.add("普通")
        self.core.next_task()
        self.assertEqual(tasks.get(t["id"])["status"], "notyet")

    def test_high_priority_past_kickoffs_first_15(self):
        for i in range(20):
            self.core.add(f"普通{i}")
        self.core.add("大事 !高")
        self.assertEqual(self.core.next_task()["title"], "大事")

    def test_listed_mail_or_google_task_is_included(self):
        os.makedirs(paths.desk_data_dir(), exist_ok=True)
        with open(os.path.join(paths.desk_data_dir(), "gtasks.js"), "w") as f:
            f.write("window.GTASKS=" + json.dumps({"tasks": [{"id": "g1", "title": "外のタスク"}]}) + ";")
        self.assertEqual(self.core.next_task()["id"], "tgg1")
        t, _ = self.core.done()
        self.assertEqual(t["status"], "done")
        self.assertEqual(tasks.get("tgg1")["status"], "done")
        self.assertIsNone(self.core.next_task())


class TestDone(Base):
    def test_finishes_the_one_from_next(self):
        self.core.add("一つ目 !高")
        self.core.add("二つ目")
        self.core.next_task()
        t, _ = self.core.done()
        self.assertEqual((t["title"], t["status"]), ("一つ目", "done"))
        self.assertEqual(self.core.next_task()["title"], "二つ目")

    def test_words(self):
        self.core.add("資料を作る")
        self.core.add("電話する")
        self.assertEqual(self.core.done("電話")[0]["title"], "電話する")

    def test_ambiguous_finishes_nothing(self):
        self.core.add("資料A")
        self.core.add("資料B")
        t, options = self.core.done("資料")
        self.assertIsNone(t)
        self.assertEqual(len(options), 2)
        self.assertEqual(len(tasks.open_tasks()), 2)

    def test_no_words_no_current_finishes_nothing(self):
        self.core.add("資料A")
        self.assertIsNone(self.core.done()[0])
        self.assertEqual(len(tasks.open_tasks()), 1)

    def test_history(self):
        self.core.add("資料")
        self.core.done("資料")
        self.assertEqual([r["action"] for r in logs.read("task-history")], ["add", "done"])


class TestSave(Base):
    def test_saved_with_current_task(self):
        self.core.add("資料")
        self.core.next_task()
        self.core.save("途中まで", "続きを書く")
        r = logs.read("work")[-1]
        self.assertEqual((r["kind"], r["did"], r["next"], r["task"]), ("save", "途中まで", "続きを書く", "資料"))

    def test_cli_saved(self):
        cli = load_tool("03-task", "cli")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["save", "途中まで", "--next", "続き"]), 0)
            self.assertEqual(cli.main(["saved"]), 0)
        self.assertIn("次の一手：続き", out.getvalue())


class TestTelegram(Base):
    def test_routes_through_the_bot(self):
        found = router.discover()
        for text in ("task: 資料", "Task：資料", "タスク: 資料"):
            self.assertEqual(router.which(text, found.routes).slug, "03-task")
        self.assertIsNone(router.which("タスクを考える", [r for r in found.routes if r.slug == "03-task"]))
        reply = router.dispatch("task: 資料を送る", router.Ctx(), found.routes)
        self.assertIn("資料を送る", reply)
        self.assertEqual(len(reply.splitlines()), 1)
        self.assertEqual(tasks.open_tasks()[0]["title"], "資料を送る")

    def test_empty(self):
        found = router.discover()
        self.assertIn("内容がありません", router.dispatch("task:", router.Ctx(), found.routes))
        self.assertEqual(tasks.open_tasks(), [])
