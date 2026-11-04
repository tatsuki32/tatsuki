import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from unittest import mock

from lifeos.lib import logs, paths
from lifeos.lib.tools import load_tool
from lifeos.tests.sandbox import SandboxCase

core = load_tool("05-parallel", "core")
SCRIPT = os.path.join(paths.REPO, "lifeos", "05-parallel", "lines.py")
NOW = datetime(2026, 11, 4, 14, 0, tzinfo=paths.HCMC)  # a Wednesday


def _at(hour, minute=0, day=4):
    return datetime(2026, 11, day, hour, minute, tzinfo=paths.HCMC)


class LinesCase(SandboxCase):
    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main")
        self.git("commit", "-q", "--allow-empty", "-m", "first")
        os.environ["LIFEOS_LINES_REPO"] = self.repo

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com"] + list(args), cwd=cwd or self.repo,
                              check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True).stdout

    def begin(self, name="a", until="18:00", now=NOW, **kw):
        return core.start(name, until, kw.pop("what", "作業"), now=now, **kw)


class TestUntil(LinesCase):
    def test_clock_and_length(self):
        self.assertEqual(core.parse_until("18:30", NOW), _at(18, 30))
        self.assertEqual(core.parse_until("18：30", NOW), _at(18, 30))
        self.assertEqual(core.parse_until("90m", NOW), _at(15, 30))
        self.assertEqual(core.parse_until("2時間", NOW), _at(16))
        self.assertEqual(core.parse_until("1h30m", NOW), _at(15, 30))

    def test_past_clock_means_after_midnight(self):
        late = _at(23)
        self.assertEqual(core.parse_until("01:00", late), _at(1, 0, 5))

    def test_refuses_too_far_and_nonsense(self):
        for bad in ("13:59", "13h", "25:00", "90", "そのうち", "0m", ""):
            with self.assertRaises(core.LinesError, msg=bad):
                core.parse_until(bad, NOW)


class TestStart(LinesCase):
    def test_one_work_one_branch_one_worktree(self):
        line = self.begin("notes", what="メモの整理")
        self.assertEqual(line["branch"], "lines/notes")
        self.assertTrue(os.path.isdir(line["path"]))
        self.assertIn("lines/notes", self.git("worktree", "list"))
        self.assertEqual(self.git("branch", "--show-current", cwd=line["path"]).strip(), "lines/notes")

    def test_fourth_is_refused_and_makes_nothing(self):
        for n in "abc":
            self.begin(n)
        with self.assertRaises(core.LinesError) as e:
            self.begin("d")
        self.assertIn("3つまで", str(e.exception))
        self.assertNotIn("lines/d", self.git("worktree", "list"))
        self.assertEqual(len(core.read_lines()), 3)

    def test_paused_still_counts_ended_frees_a_place(self):
        for n in "abc":
            self.begin(n)
        core.pause("a", "待ち", now=_at(15))
        with self.assertRaises(core.LinesError):
            self.begin("d", now=_at(15))
        core.end("a", now=_at(15))
        self.begin("d", now=_at(15))

    def test_end_time_and_description_are_required(self):
        with self.assertRaises(core.LinesError):
            core.start("a", None, "作業", now=NOW)
        with self.assertRaises(core.LinesError):
            core.start("a", "18:00", "  ", now=NOW)
        self.assertEqual(core.read_lines(), [])

    def test_cli_needs_until(self):
        r = subprocess.run([sys.executable, SCRIPT, "start", "a", "--what", "x"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(r.returncode, 2)

    def test_refuses_bad_names_and_same_branch(self):
        for bad in ("-x", "a/b", "../x", "a b"):
            with self.assertRaises(core.LinesError, msg=bad):
                self.begin(bad)
        self.begin("a")
        with self.assertRaises(core.LinesError):
            self.begin("a")
        with self.assertRaises(core.LinesError):
            self.begin("b", branch="lines/a")

    def test_refuses_flag_like_branch_and_main_checkout(self):
        with self.assertRaises(core.LinesError):
            self.begin("a", branch="--force")
        with self.assertRaises(core.LinesError):
            self.begin("a", branch="main")

    def test_attaches_to_an_existing_worktree(self):
        other = os.path.join(self.tmp, "other")
        self.git("worktree", "add", "-q", "-b", "work/x", other)
        line = self.begin("x", branch="work/x")
        self.assertTrue(core._same_path(line["path"], other))
        self.assertFalse(line["created_worktree"])

    def test_limit_setting_has_a_ceiling(self):
        self.assertEqual(core.max_lines(), 3)
        core.set_max_lines(4)
        self.assertEqual(core.max_lines(), 4)
        with self.assertRaises(core.LinesError):
            core.set_max_lines(core.HARD_MAX + 1)


class TestList(LinesCase):
    def test_one_screen_answers_who_what_where(self):
        for n in "abc":
            self.begin(n, what=f"{n}の作業 " + "長い説明" * 40)
        core.note("a", "テストが通った", now=_at(14, 20))
        core.pause("b", "返事待ち", now=_at(14, 25))
        line = core.read_lines()[2]
        with open(os.path.join(line["path"], "x.txt"), "w") as f:
            f.write("x")
        text = core.render_list(_at(14, 30))
        rows = text.splitlines()
        self.assertLessEqual(len(rows), 24)
        self.assertTrue(all(core._width(r) <= 100 for r in rows), max(rows, key=core._width))
        self.assertIn("作りかけ 3/3", text)
        self.assertIn("テストが通った（10分前）", text)
        self.assertIn("止めている", text)
        self.assertIn("止めた理由：返事待ち", text)
        self.assertIn("未コミット1件", text)
        self.assertIn("18:00まで", text)
        self.assertIn("   やること：", text)
        self.assertIn("   lines/a・", text)

    def test_no_commit_yet_is_not_shown_as_progress(self):
        self.begin("a")
        text = core.render_list(NOW)
        self.assertIn("このラインのコミットはまだありません", text)
        self.assertNotIn("first", text)

    def test_quiet_line_and_missing_folder_are_called_out(self):
        now = paths.now()  # the commit in the worktree carries the real clock
        line = self.begin("a", until="3h", now=now)
        self.assertIn("動いている", core.render_list(now + timedelta(minutes=5)))
        self.assertIn("動きなし", core.render_list(now + timedelta(hours=1)))
        self.git("worktree", "remove", line["path"])
        self.assertIn("作業フォルダなし", core.render_list(now + timedelta(hours=1)))

    def test_unregistered_worktrees_are_one_row_and_all_shows_them(self):
        for i in range(10):
            self.git("worktree", "add", "-q", "-b", f"w{i}", os.path.join(self.tmp, f"w{i}"))
        text = core.render_list(NOW)
        self.assertIn("ラインに入れていない worktree：10本", text)
        self.assertNotIn("w3", text)
        self.assertIn("w3", core.render_all(NOW))

    def test_empty_list_says_how_to_start(self):
        self.assertIn("/lines start", core.render_list(NOW))

    def test_broken_state_is_not_overwritten(self):
        self.begin("a")
        with open(core._file("lines.json"), "w") as f:
            f.write("{broken")
        with self.assertRaises(core.LinesError):
            self.begin("b")
        with open(core._file("lines.json")) as f:
            self.assertEqual(f.read(), "{broken")

    def test_snapshot_is_json(self):
        self.begin("a")
        snap = json.loads(json.dumps(core.snapshot(NOW), ensure_ascii=False))
        self.assertEqual(snap["lines"][0]["name"], "a")
        self.assertEqual(snap["limit"], 3)


class TestAlert(LinesCase):
    def setUp(self):
        super().setUp()
        self.tg = mock.patch.object(core, "send_telegram", return_value=False)
        self.screen = mock.patch.object(core, "show_on_screen", return_value=True)
        self.send_tg, self.send_screen = self.tg.start(), self.screen.start()
        self.addCleanup(self.tg.stop)
        self.addCleanup(self.screen.stop)

    def test_quiet_before_the_end_time(self):
        self.begin("a")
        self.assertIsNone(core.check(_at(17, 59)))
        self.send_tg.assert_not_called()
        self.assertEqual(core.banner_lines(core.read_lines(), _at(17, 59)), [])

    def test_alerts_once_then_every_30_minutes_then_only_the_screen(self):
        self.begin("a")
        self.assertIn("a（18:00）", core.check(_at(18, 1))["text"])
        self.assertIsNone(core.check(_at(18, 2)))
        self.assertIsNotNone(core.check(_at(18, 31)))
        self.assertIsNotNone(core.check(_at(19, 1)))
        self.assertIsNone(core.check(_at(19, 40)))
        self.assertEqual(self.send_tg.call_count, 3)
        self.assertEqual(self.send_screen.call_count, 3)
        self.assertTrue(core.banner_lines(core.read_lines(), _at(23)))

    def test_without_telegram_the_list_still_shows_it(self):
        self.begin("a")
        text = core.render_list(_at(18, 25))
        self.assertIn("終了時刻を過ぎました：a（18:00）", text)
        self.assertIn("Telegram は未接続", text)
        self.assertIn("時間切れ", text)

    def test_one_channel_failing_does_not_stop_the_other(self):
        self.send_tg.side_effect = RuntimeError("down")
        self.begin("a")
        sent = core.check(_at(18, 1))
        self.assertFalse(sent["telegram"])
        self.assertTrue(sent["screen"])

    def test_extend_clears_and_counts(self):
        self.begin("a")
        core.check(_at(18, 1))
        line = core.extend("a", "30m", now=_at(18, 5))
        self.assertEqual(line["extended"], 1)
        self.assertEqual(line["alerts"], 0)
        self.assertIsNone(core.check(_at(18, 10)))
        self.assertIn("延長1回", core.render_list(_at(18, 10)))
        self.assertIsNotNone(core.check(_at(18, 36)))

    def test_paused_and_ended_lines_do_not_alert(self):
        self.begin("a")
        self.begin("b")
        core.pause("a", now=_at(17))
        core.end("b", now=_at(17))
        self.assertIsNone(core.check(_at(19)))

    def test_resume_needs_a_new_end_time(self):
        self.begin("a")
        core.pause("a", now=_at(15))
        with self.assertRaises(core.LinesError):
            core.resume("a", None, now=_at(16))
        self.assertEqual(core.resume("a", "20:00", now=_at(16))["until"], core._iso(_at(20)))

    def test_alert_log_records_what_happened(self):
        self.begin("a")
        core.check(_at(18, 1))
        rec = logs.read("lines")[-1]
        self.assertEqual((rec["event"], rec["lines"], rec["telegram"], rec["screen"]), ("alert", ["a"], False, True))


class TestRealChannels(LinesCase):
    def test_sandbox_alert_goes_to_outbox_and_no_notification(self):
        self.begin("a", until="18:00", now=_at(17, 0))
        with mock.patch.object(subprocess, "run", wraps=subprocess.run) as run:
            sent = core.check(_at(18, 1))
        self.assertFalse(sent["telegram"])
        self.assertFalse(sent["screen"])
        self.assertFalse(any(c.args and c.args[0][0] == "osascript" for c in run.call_args_list))
        self.assertIn("終了時刻", logs.read("outbox")[-1]["text"])

    def test_screen_text_is_an_argument_not_script(self):
        os.environ["LIFEOS_DATA"] = paths.DEFAULT_DATA  # looks like real data; subprocess is replaced
        with mock.patch.object(core.subprocess, "run") as run, mock.patch.object(core.sys, "platform", "darwin"):
            run.return_value.returncode = 0
            self.assertTrue(core.show_on_screen('"; do shell script "x'))
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[-2:], ["--", '"; do shell script "x'])
        self.assertFalse(any("do shell" in x for x in cmd[:-1]))


class TestWatcher(LinesCase):
    def test_not_started_in_a_sandbox(self):
        self.begin("a")
        with mock.patch.object(core.subprocess, "Popen") as popen:
            self.assertFalse(core.ensure_watcher(SCRIPT))
        popen.assert_not_called()

    def test_started_once_when_a_line_needs_it(self):
        self.begin("a")
        os.environ["LIFEOS_LINES_WATCHER"] = "1"
        with mock.patch.object(core.subprocess, "Popen") as popen:
            self.assertTrue(core.ensure_watcher(SCRIPT))
        popen.assert_called_once()

    def test_not_started_with_nothing_running(self):
        os.environ["LIFEOS_LINES_WATCHER"] = "1"
        self.begin("a")
        core.end("a", now=_at(15))
        with mock.patch.object(core.subprocess, "Popen") as popen:
            self.assertFalse(core.ensure_watcher(SCRIPT))
        popen.assert_not_called()

    def test_watch_alerts_and_ends_when_the_line_ends(self):
        self.begin("a", until="1m", now=paths.now() - timedelta(minutes=5))
        calls = []

        def sleep(_):
            calls.append(1)
            core.end("a")

        self.assertTrue(core.watch(sleep, 0))
        self.assertEqual(len(calls), 1)
        self.assertIn("a（", logs.read("outbox")[-1]["text"])

    def test_second_watcher_steps_aside(self):
        self.begin("a")
        seen = []

        def sleep(_):
            seen.append(core.watcher_held())
            seen.append(core.watch(lambda _: None, 0))
            core.end("a")

        core.watch(sleep, 0)
        self.assertEqual(seen, [True, False])


class TestHours(LinesCase):
    def test_stretch_is_logged_with_a_correctable_end(self):
        self.begin("a")
        core.end("a", at="16:30", now=_at(23))
        rec = logs.read("work")[-1]
        self.assertEqual((rec["line"], rec["minutes"]), ("a", 150))

    def test_at_before_start_is_refused(self):
        self.begin("a")
        with self.assertRaises(core.LinesError):
            core.end("a", at="13:00", now=_at(15))

    def test_overlaps_count_once(self):
        self.begin("a")
        self.begin("b", now=_at(15))
        core.end("a", now=_at(16))
        core.end("b", now=_at(17))
        self.assertEqual(core.minutes_by_day(_at(18))["2026-11-04"], 180)

    def test_pause_time_is_not_worked_time(self):
        self.begin("a")
        core.pause("a", now=_at(15))
        core.resume("a", "20:00", now=_at(17))
        core.end("a", now=_at(18))
        self.assertEqual(core.minutes_by_day(_at(19))["2026-11-04"], 120)

    def test_running_line_counts_until_now_and_midnight_splits(self):
        self.begin("a", until="01:00", now=_at(23))
        days = core.minutes_by_day(_at(0, 30, day=5))
        self.assertEqual(days, {"2026-11-04": 60, "2026-11-05": 30})

    def test_week_view(self):
        self.begin("a", now=_at(9, 0, day=3), until="15:00")
        core.end("a", now=_at(11, 30, day=3))
        self.begin("b", now=_at(10, 0, day=4), until="15:00")
        core.end("b", now=_at(11, 0, day=4))
        logs.append("work", {"line": "old", "start": core._iso(_at(9, 0, day=2) - timedelta(days=7)),
                             "end": core._iso(_at(10, 0, day=2) - timedelta(days=7)), "minutes": 60})
        text = core.render_week(_at(12, 0, day=4))
        self.assertIn("火 11/03  2時間30分", text)
        self.assertIn("水 11/04  1時間", text)
        self.assertIn("合計 3時間30分", text)
        self.assertIn("10/26 の週  1時間", text)
        self.assertRegex(text, r"木 11/05  -")

    def test_damaged_log_lines_are_skipped(self):
        logs.append("work", {"line": "x", "start": "ゆうべ", "end": "朝"})
        self.assertEqual(core.minutes_by_day(NOW), {})


class TestRemove(LinesCase):
    def test_needs_approval_and_keeps_the_branch(self):
        line = self.begin("a")
        core.end("a", now=_at(15))
        plan = core.remove_worktree("a")
        self.assertFalse(plan["approved"])
        self.assertTrue(os.path.isdir(line["path"]))
        core.remove_worktree("a", approved=True)
        self.assertFalse(os.path.isdir(line["path"]))
        self.assertIn("lines/a", self.git("branch"))

    def test_uncommitted_work_stops_it(self):
        line = self.begin("a")
        with open(os.path.join(line["path"], "x.txt"), "w") as f:
            f.write("x")
        core.end("a", now=_at(15))
        with self.assertRaises(core.LinesError):
            core.remove_worktree("a", approved=True)
        self.assertTrue(os.path.exists(os.path.join(line["path"], "x.txt")))

    def test_open_lines_and_borrowed_worktrees_are_left_alone(self):
        line = self.begin("a")
        with self.assertRaises(core.LinesError):
            core.remove_worktree("a", approved=True)
        other = os.path.join(self.tmp, "other")
        self.git("worktree", "add", "-q", "-b", "work/x", other)
        self.begin("x", branch="work/x")
        core.end("x", now=_at(15))
        with self.assertRaises(core.LinesError):
            core.remove_worktree("x", approved=True)
        self.assertTrue(os.path.isdir(other) and os.path.isdir(line["path"]))


class TestCommand(LinesCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, SCRIPT] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, encoding="utf-8")

    def test_whole_flow(self):
        r = self.run_cli("start", "a", "--until", "90m", "--what", "試し")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("lines/a", r.stdout)
        self.assertEqual(self.run_cli("note", "a", "途中", "まで").returncode, 0)
        self.assertIn("途中 まで", self.run_cli().stdout)
        self.assertEqual(json.loads(self.run_cli("json").stdout)["open"], 1)
        self.assertIn("今週の稼働", self.run_cli("week").stdout)
        self.assertEqual(self.run_cli("end", "a").returncode, 0)
        self.assertEqual(self.run_cli("banner").stdout, "")

    def test_fourth_is_refused_on_the_command_line(self):
        for n in "abc":
            self.run_cli("start", n, "--until", "90m", "--what", "試し")
        r = self.run_cli("start", "d", "--until", "90m", "--what", "試し")
        self.assertEqual(r.returncode, 1)
        self.assertIn("3つまで", r.stderr)

    def test_command_file_exists_and_matches_tool_json(self):
        with open(os.path.join(paths.REPO, "lifeos", "05-parallel", "tool.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["commands"], ["/lines"])
        self.assertTrue(os.path.exists(os.path.join(paths.REPO, ".claude", "commands", "lines.md")))
