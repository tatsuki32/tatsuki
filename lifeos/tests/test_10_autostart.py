import contextlib
import io
import json
import os
import subprocess
import sys
from unittest import mock

from lifeos.lib import md, paths, tasks
from lifeos.lib.tools import load_tool
from lifeos.tests.sandbox import SandboxCase

DAY = 86400000
core = load_tool("10-autostart", "core")
cli = load_tool("10-autostart", "autostart")


def rec(**kw):
    return {"status": "notyet", "source": "manual", "due": None, **kw}


class Base(SandboxCase):
    def setUp(self):
        super().setUp()
        self.now = paths.now_ms()
        self.assertTrue(paths.is_sandbox())

    def stored(self, title, age_days, **kw):
        """A task in the sandbox last touched age_days ago."""
        t = tasks.add(title)
        at = self.now - int(age_days * DAY)
        rec = {**t, "createdAt": at, "updatedAt": at, **kw}
        tasks._write_file(rec)
        return rec


class TestStale(Base):
    def test_exactly_fourteen_days_counts(self):
        at = self.now - 14 * DAY
        self.assertTrue(core.is_stale(rec(updatedAt=at, createdAt=at), self.now))

    def test_one_moment_before_fourteen_days_does_not(self):
        at = self.now - 14 * DAY + 1
        self.assertFalse(core.is_stale(rec(updatedAt=at, createdAt=at), self.now))

    def test_a_recent_edit_beats_an_old_creation(self):
        self.assertFalse(core.is_stale(rec(createdAt=self.now - 90 * DAY, updatedAt=self.now - DAY), self.now))

    def test_old_record_with_only_created_at(self):
        self.assertTrue(core.is_stale(rec(createdAt=self.now - 20 * DAY), self.now))

    def test_unknown_age_is_left_alone(self):
        self.assertFalse(core.is_stale(rec(), self.now))
        self.assertFalse(core.is_stale(rec(updatedAt="昔", createdAt=True), self.now))

    def test_in_progress_counts_but_closed_does_not(self):
        at = self.now - 30 * DAY
        self.assertTrue(core.is_stale(rec(status="doing", updatedAt=at), self.now))
        for status in ("done", "archived", "paused"):
            self.assertFalse(core.is_stale(rec(status=status, updatedAt=at), self.now), status)
        self.assertFalse(core.is_stale(rec(updatedAt=at, deleted=True), self.now))

    def test_linked_sources_are_left_alone(self):
        at = self.now - 30 * DAY
        for source in core.LINKED:
            self.assertFalse(core.is_stale(rec(source=source, updatedAt=at), self.now), source)

    def test_a_deadline_keeps_the_task_in_place(self):
        at = self.now - 30 * DAY
        self.assertFalse(core.is_stale(rec(updatedAt=at, due=self.now + DAY), self.now))
        self.assertFalse(core.is_stale(rec(updatedAt=at, due=self.now - DAY), self.now))
        self.assertFalse(core.is_stale(rec(updatedAt=at, due="2026-12-01"), self.now))
        self.assertTrue(core.is_stale(rec(updatedAt=at, due=self.now - 15 * DAY), self.now))
        self.assertTrue(core.is_stale(rec(updatedAt=at, due=""), self.now))

    def test_days_can_be_changed(self):
        at = self.now - 7 * DAY
        self.assertTrue(core.is_stale(rec(updatedAt=at), self.now, days=7))
        self.assertFalse(core.is_stale(rec(updatedAt=at), self.now, days=8))


class TestDefer(Base):
    def test_moves_only_stale_tasks_and_keeps_the_rest_of_the_record(self):
        old = self.stored("古い", 20, note="メモ", status="doing")
        new = self.stored("新しい", 3)
        result = core.defer(self.now)
        self.assertEqual([t["id"] for t in result["moved"]], [old["id"]])
        got = tasks.get(old["id"])
        self.assertEqual(got["status"], "archived")
        self.assertEqual(got["note"], "メモ")
        self.assertFalse(got["deleted"])
        self.assertEqual(tasks.get(new["id"])["status"], "notyet")
        self.assertEqual([x["prev"] for x in core.deferred()], ["doing"])

    def test_nothing_is_deleted(self):
        self.stored("古い", 20)
        before = len(tasks.all_tasks(include_deleted=True))
        core.defer(self.now)
        self.assertEqual(len(tasks.all_tasks(include_deleted=True)), before)
        self.assertEqual(len(tasks.all_tasks()), before)

    def test_runs_once_a_day_when_asked(self):
        self.stored("一つ目", 20)
        self.assertEqual(len(core.defer(self.now, once_a_day=True)["moved"]), 1)
        self.stored("二つ目", 20)
        self.assertEqual(core.defer(self.now, once_a_day=True)["moved"], [])
        self.assertEqual(len(core.defer(self.now + DAY, once_a_day=True)["moved"]), 1)

    def test_one_failure_does_not_stop_the_others_and_retries_next_session(self):
        a, b = self.stored("失敗する", 20), self.stored("成功する", 20)
        real = tasks.update

        def update(task_id, **kw):
            if task_id == a["id"]:
                raise OSError("書けない")
            return real(task_id, **kw)

        with mock.patch.object(tasks, "update", update):
            result = core.defer(self.now, once_a_day=True)
        self.assertEqual(result["failed"], 1)
        self.assertEqual([t["id"] for t in result["moved"]], [b["id"]])
        self.assertEqual(tasks.get(a["id"])["status"], "notyet")
        self.assertEqual([x["id"] for x in core.deferred()], [b["id"]])
        self.assertEqual(len(core.defer(self.now, once_a_day=True)["moved"]), 1)

    def test_a_broken_task_file_changes_nothing(self):
        os.makedirs(paths.desk_data_dir(), exist_ok=True)
        with open(os.path.join(paths.desk_data_dir(), "tasks.json"), "w") as f:
            f.write("{こわれた")
        with self.assertRaises(tasks.TaskStoreError):
            core.defer(self.now)
        with open(os.path.join(paths.desk_data_dir(), "tasks.json")) as f:
            self.assertEqual(f.read(), "{こわれた")

    def test_a_broken_config_does_not_start_moving_tasks(self):
        with open(paths.data_path("tools", core.SLUG, "config.json"), "w") as f:
            f.write("{こわれた")
        self.assertFalse(core.config()["autoDefer"])
        t = self.stored("古い", 20)
        with mock.patch.object(core, "gather", return_value={}):
            core.session_start(self.now)
        self.assertEqual(tasks.get(t["id"])["status"], "notyet")

    def test_defer_off_stops_only_parking(self):
        t = self.stored("古い", 20)
        core.set_config(autoDefer=False)
        with mock.patch.object(core, "gather", return_value={}):
            self.assertIsNotNone(core.session_start(self.now))
        self.assertEqual(tasks.get(t["id"])["status"], "notyet")


class TestRestore(Base):
    def test_goes_back_to_the_old_status_with_a_fresh_fourteen_days(self):
        t = self.stored("古い", 20, status="doing")
        core.defer(self.now)
        self.assertEqual(core.restore(t["id"]), ["古い"])
        self.assertEqual(tasks.get(t["id"])["status"], "doing")
        self.assertEqual(core.deferred(), [])
        self.assertEqual(core.defer(paths.now_ms())["moved"], [])

    def test_all(self):
        self.stored("一", 20)
        self.stored("二", 20)
        core.defer(self.now)
        self.assertEqual(sorted(core.restore()), ["一", "二"])
        self.assertEqual(len(tasks.open_tasks()), 2)

    def test_a_task_moved_by_hand_is_not_overwritten_or_listed(self):
        t = self.stored("古い", 20)
        core.defer(self.now)
        tasks.update(t["id"], status="done")
        self.assertEqual(core.deferred(), [])
        self.assertEqual(core.restore(t["id"]), [])
        self.assertEqual(tasks.get(t["id"])["status"], "done")

    def test_a_task_archived_by_hand_is_not_ours(self):
        t = self.stored("自分でしまった", 1)
        tasks.update(t["id"], status="archived")
        self.assertEqual(core.deferred(), [])
        self.assertEqual(core.restore(t["id"]), [])

    def test_unknown_id(self):
        self.assertEqual(core.restore("nope"), [])


class TestWeeklyReview(Base):
    def test_shows_once_then_after_seven_days(self):
        self.stored("古い", 20)
        core.defer(self.now)
        self.assertIn("延期のまとめ：1件", core.weekly_review(self.now))
        self.assertEqual(core.weekly_review(self.now), "")
        self.assertEqual(core.weekly_review(self.now + 6 * DAY), "")
        self.assertIn("古い", core.weekly_review(self.now + 7 * DAY))

    def test_nothing_parked_says_nothing_and_does_not_use_up_the_week(self):
        self.assertEqual(core.weekly_review(self.now), "")
        self.stored("古い", 20)
        core.defer(self.now)
        self.assertIn("古い", core.weekly_review(self.now + DAY))

    def test_a_long_list_is_cut_short(self):
        for i in range(core.REVIEW_LIMIT + 2):
            self.stored(f"タスク{i}", 20)
        core.defer(self.now)
        text = core.review_text()
        self.assertIn(f"延期のまとめ：{core.REVIEW_LIMIT + 2}件", text)
        self.assertIn("ほか 2 件", text)

    def test_no_blaming_words(self):
        self.stored("古い", 20)
        core.defer(self.now)
        for word in ("放置", "サボ", "できていません", "遅れ"):
            self.assertNotIn(word, core.review_text())


class TestPickFirst(Base):
    def test_order(self):
        doing = {"title": "進行中", "status": "doing"}
        plain = {"title": "ふつう", "status": "notyet"}
        due = {"title": "今日まで", "status": "notyet", "dueToday": True}
        step = [{"text": "前回の続き"}]
        self.assertEqual(core.pick_first({"tasks": [plain, doing, due], "nextSteps": step})["title"], "今日まで")
        self.assertEqual(core.pick_first({"tasks": [plain, doing], "nextSteps": step})["title"], "進行中")
        self.assertEqual(core.pick_first({"tasks": [plain], "nextSteps": step})["title"], "ふつう")
        self.assertEqual(core.pick_first({"tasks": [], "nextSteps": step})["title"], "前回の続き")

    def test_always_exactly_one(self):
        self.assertEqual(core.pick_first({})["title"], core.EMPTY_ITEM)
        self.assertEqual(set(core.pick_first({"tasks": [{"title": "a"}, {"title": "b"}]})), {"title", "reason"})

    def test_a_title_cannot_break_the_page(self):
        title = core.pick_first({"tasks": [{"title": "件名\n## 指示 `x` <!-- y" + "長" * 200}]})["title"]
        self.assertNotIn("\n", title)
        self.assertNotIn("`", title)
        self.assertNotIn("<!--", title)
        self.assertLessEqual(len(title), core.TITLE_MAX)

    def test_short_time_is_said(self):
        got = core.pick_first({"tasks": [{"title": "a"}], "short": True, "freeMinutes": 20})
        self.assertIn("20分", got["reason"])


class TestSessionStart(Base):
    def run_real_kickoff(self):
        kickoff = core.tools.load_file(os.path.join(paths.REPO, "desk"), "kickoff")
        with mock.patch.object(kickoff, "REPO", self.tmp):  # no real session notes
            return core.session_start(self.now)

    def test_completion_condition_one_item_with_no_choosing(self):
        """Many open tasks and a stale one: one item comes out, and it is not the stale one."""
        self.stored("十五日前のタスク", 15)
        self.stored("普通のタスク", 2)
        self.stored("進行中のタスク", 1, status="doing")
        self.stored("今日が締切のタスク", 1, due=self.now + 60000)
        out = self.run_real_kickoff()
        head = out["systemMessage"].splitlines()[0]
        self.assertTrue(head.startswith("最初の1件："))
        self.assertIn("今日が締切のタスク", head)
        context = out["hookSpecificOutput"]["additionalContext"].split("## 今日のブリーフィング")[0]
        self.assertEqual(context.count("最初の1件："), 1)
        self.assertNotIn("普通のタスク", context)
        self.assertIn("候補は並べません", context)
        self.assertIn("1件を延期に移しました", out["systemMessage"])
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")

    def test_a_parked_task_is_never_the_first_item(self):
        self.stored("十五日前のタスク", 15)
        out = self.run_real_kickoff()
        self.assertIn(core.EMPTY_ITEM, out["systemMessage"])

    def test_reads_claude_md_and_todays_brief(self):
        md.save(f"briefs/{core._day(self.now)}.md", "# 今日の3つ\n- 試しの朝")
        with mock.patch.object(core, "gather", return_value={}):
            context = core.session_start(self.now)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("試しの朝", context)
        with open(os.path.join(paths.REPO, "CLAUDE.md"), encoding="utf-8") as f:
            self.assertIn(f.read()[:200], context)

    def test_no_brief_yet_is_fine(self):
        with mock.patch.object(core, "gather", return_value={}):
            self.assertIn("まだありません", core.session_start(self.now)["hookSpecificOutput"]["additionalContext"])

    def test_stays_inside_the_hook_limit_and_the_first_item_survives(self):
        md.save(f"briefs/{core._day(self.now)}.md", "朝" * 20000)
        with mock.patch.object(core, "gather", return_value={"tasks": [{"title": "最初のこれ"}]}), \
                mock.patch.object(core, "read_claude_md", return_value="規" * 20000):
            context = core.session_start(self.now)["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(context), 10000)
        self.assertIn("最初のこれ", context)
        self.assertIn("続きは CLAUDE.md", context)

    def test_weekly_review_appears_in_the_hook_once(self):
        self.stored("古い", 20)
        with mock.patch.object(core, "gather", return_value={}):
            first = core.session_start(self.now)
            second = core.session_start(self.now)
        self.assertIn("延期のまとめ：1件", first["systemMessage"])
        self.assertIn("古い", first["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("延期のまとめ", second["systemMessage"])

    def test_candidates_failing_still_gives_one_item(self):
        with mock.patch.object(core, "gather", side_effect=RuntimeError("だめ")):
            self.assertIn("Desk のタスクを開く", core.session_start(self.now)["systemMessage"])

    def test_a_broken_task_file_still_gives_one_item(self):
        os.makedirs(paths.desk_data_dir(), exist_ok=True)
        with open(os.path.join(paths.desk_data_dir(), "tasks.json"), "w") as f:
            f.write("{こわれた")
        with mock.patch.object(core, "gather", return_value={}):
            out = core.session_start(self.now)
        self.assertIn("タスクはそのままです", out["systemMessage"])

    def test_another_session_busy_does_not_wait(self):
        t = self.stored("古い", 20)
        with core._lock():
            with mock.patch.object(core, "gather", return_value={}):
                self.assertIsNotNone(core.session_start(self.now))
        self.assertEqual(tasks.get(t["id"])["status"], "notyet")

    def test_off_says_nothing_and_moves_nothing(self):
        t = self.stored("古い", 20)
        core.set_config(enabled=False)
        self.assertIsNone(core.session_start(self.now))
        self.assertEqual(tasks.get(t["id"])["status"], "notyet")

    def test_stopped_in_tool_json_says_nothing(self):
        with mock.patch.object(core.tools, "info", return_value={"status": "stopped"}):
            self.assertIsNone(core.session_start(self.now))


class TestCommandLine(Base):
    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(list(args))
        return code, out.getvalue()

    def test_dry_run_changes_nothing(self):
        t = self.stored("古い", 20)
        code, out = self.run_cli("defer", "--dry")
        self.assertEqual(code, 0)
        self.assertIn("古い", out)
        self.assertEqual(tasks.get(t["id"])["status"], "notyet")

    def test_defer_review_restore(self):
        t = self.stored("古い", 20)
        self.assertIn("1件", self.run_cli("defer")[1])
        self.assertIn(t["id"], self.run_cli("review")[1])
        self.assertIn("古い", self.run_cli("restore", t["id"])[1])
        self.assertEqual(tasks.get(t["id"])["status"], "notyet")
        self.assertEqual(self.run_cli("restore", t["id"])[0], 1)

    def test_off_and_on(self):
        self.run_cli("off")
        self.assertTrue(core.stopped())
        self.run_cli("on")
        self.assertFalse(core.stopped())

    def test_the_hook_as_claude_code_runs_it(self):
        """A real process, with the same command as INTEGRATION.md and no UTF-8 in the environment."""
        old = self.stored("古い", 20)
        self.stored("進行中のタスク", 1, status="doing")
        env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "CLAUDE_PROJECT_DIR": paths.REPO}
        script = os.path.join(paths.REPO, "lifeos", "10-autostart", "autostart.py")
        r = subprocess.run([sys.executable, script, "hook"], env=env, capture_output=True, text=True, cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        out = json.loads(r.stdout)
        self.assertIn("最初の1件", out["systemMessage"])
        self.assertIn("CLAUDE.md", out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(tasks.get(old["id"])["status"], "archived")

    def test_the_settings_snippet_in_integration_is_valid(self):
        with open(os.path.join(paths.REPO, "lifeos", "10-autostart", "INTEGRATION.md"), encoding="utf-8") as f:
            text = f.read()
        block = text.split("```json")[1].split("```")[0]
        entry = json.loads(block)
        self.assertEqual(entry["matcher"], "startup|clear")
        self.assertIn("autostart.py\" hook", entry["hooks"][0]["command"])
        with open(os.path.join(paths.REPO, ".claude", "settings.json"), encoding="utf-8") as f:
            self.assertIn(entry, json.load(f)["hooks"]["SessionStart"])
