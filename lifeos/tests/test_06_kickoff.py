import contextlib
import io
import json
import os
import time
from datetime import datetime
from unittest import mock

from lifeos.lib import logs, paths, tasks, tools
from lifeos.lib.tools import load_tool
from lifeos.tests.sandbox import SandboxCase

DAY_MS = 86400000
TODAY = "2026-11-04"


def ms_at(day):
    return int(datetime.strptime(day + " 12:00", "%Y-%m-%d %H:%M").replace(tzinfo=paths.HCMC).timestamp() * 1000)


class Base(SandboxCase):
    def setUp(self):
        super().setUp()
        self.core = load_tool("06-kickoff", "core")

    def task(self, title, days_ago=0, **kw):
        """A task last touched `days_ago` days before TODAY."""
        fields = {k: kw.pop(k) for k in ("status", "note", "source") if k in kw}
        t = tasks.add(title, **fields)
        stamp = ms_at(TODAY) - days_ago * DAY_MS
        rec = {**t, "updatedAt": stamp, "createdAt": stamp, **kw}
        tasks._write_file(rec)
        return rec

    def made(self, name="draft.md", text="本文"):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p


class TestStale(Base):
    def test_seven_days_is_stalled_six_is_not(self):
        self.task("七日", 7)
        self.task("六日", 6)
        self.assertEqual([s["title"] for s in self.core.stale(TODAY)], ["七日"])

    def test_longest_first_with_days(self):
        self.task("八日", 8)
        self.task("三十日", 30)
        self.assertEqual([(s["title"], s["idleDays"]) for s in self.core.stale(TODAY)], [("三十日", 30), ("八日", 8)])

    def test_doing_counts_closed_and_deleted_do_not(self):
        self.task("進行中", 9, status="doing")
        self.task("済み", 9, status="done")
        self.task("やめた", 9, status="dropped")
        self.task("消した", 9, deleted=True)
        self.assertEqual([s["title"] for s in self.core.stale(TODAY)], ["進行中"])

    def test_old_record_without_updated_at_uses_created_at(self):
        t = self.task("古い記録", 10)
        t.pop("updatedAt")
        tasks._write_file(t)
        self.assertEqual(self.core.stale(TODAY)[0]["idleDays"], 10)

    def test_no_usable_time_is_not_called_stalled(self):
        t = self.task("時刻なし", 10)
        t.pop("updatedAt")
        t["createdAt"] = "いつか"
        tasks._write_file(t)
        self.assertEqual(self.core.stale(TODAY), [])

    def test_recurring_and_dated_sources_are_left_out(self):
        for s in ("daily", "claude", "calendar"):
            self.task(s, 20, source=s)
        self.task("手で足した", 20, source="manual")
        self.task("メール由来", 20, source="mail")
        self.assertEqual({s["title"] for s in self.core.stale(TODAY)}, {"手で足した", "メール由来"})

    def test_what_a_tool_asked_the_owner_to_do_is_left_out(self):
        self.task("Life OS 02：接続する", 20, source="lifeos-02")
        self.task("Telegram から足した", 20, source="lifeos-03")
        self.assertEqual([s["title"] for s in self.core.stale(TODAY)], ["Telegram から足した"])

    def test_title_is_kept_to_one_line(self):
        self.task("改行\nあり", 8)
        self.assertEqual(self.core.stale(TODAY)[0]["title"], "改行 あり")


class TestBrief(Base):
    def brief(self):
        return load_tool("06-kickoff", "brief")

    def test_nothing_stalled_means_no_lines(self):
        self.task("新しい", 1)
        self.assertEqual(self.brief().lines(TODAY), [])

    def test_line_asks_and_shows_the_command(self):
        self.task("資料を送る", 12)
        self.assertEqual(self.brief().lines(TODAY), ["初動を任せますか？ /kickoff 資料を送る（12日動きなし）"])

    def test_never_more_than_three(self):
        for i in range(5):
            self.task(f"作業{i}", 10 + i)
        got = self.brief().lines(TODAY)
        self.assertEqual(len(got), 3)
        self.assertIn("作業4", got[0])

    def test_reaches_the_morning_brief_through_brief_parts(self):
        self.task("資料を送る", 12)
        parts = dict(tools.brief_parts(TODAY))
        self.assertIn("資料を送る", parts["06-kickoff"][0])

    def test_reading_the_brief_changes_no_task(self):
        t = self.task("資料を送る", 12)
        self.brief().lines(TODAY)
        self.assertEqual(tasks.get(t["id"])["updatedAt"], t["updatedAt"])

    def test_skip_hides_the_task_until_the_date(self):
        t = self.task("今は無理", 400)
        until = self.core.skip(t["id"], days=3)
        self.assertEqual(self.brief().lines(paths.today()), [])
        self.assertEqual(tasks.get(t["id"])["status"], "notyet")
        self.assertEqual(len(self.brief().lines(until)), 1)

    def test_long_title_is_cut_but_still_finds_the_task(self):
        t = self.task("あ" * 60, 12)
        line = self.brief().lines(TODAY)[0]
        short = line.split("/kickoff ")[1].split("（")[0]
        self.assertEqual(len(short), 30)
        self.assertEqual(self.core.resolve(short)["task"]["id"], t["id"])

    def test_skip_needs_a_real_task(self):
        with self.assertRaises(KeyError):
            self.core.skip("ない")

    def test_proposals_can_be_turned_off_and_on(self):
        self.task("資料を送る", 12)
        self.core.set_suggesting(False)
        self.assertEqual(self.brief().lines(TODAY), [])
        self.core.set_suggesting(True)
        self.assertEqual(len(self.brief().lines(TODAY)), 1)


class TestResolve(Base):
    def test_exact_title_ignores_spaces_and_case(self):
        t = self.task("Report を 書く")
        self.assertEqual(self.core.resolve("report を書く")["task"]["id"], t["id"])

    def test_id_works(self):
        t = self.task("資料")
        self.assertEqual(self.core.resolve(t["id"])["task"]["id"], t["id"])

    def test_exact_beats_longer_titles(self):
        t = self.task("資料")
        self.task("資料を送る")
        self.assertEqual(self.core.resolve("資料")["task"]["id"], t["id"])

    def test_unique_part_of_a_title_is_enough(self):
        t = self.task("確定申告の資料を集める")
        self.assertEqual(self.core.resolve("確定申告")["task"]["id"], t["id"])

    def test_two_matches_ask(self):
        self.task("資料を送る")
        self.task("資料を直す")
        got = self.core.resolve("資料")
        self.assertEqual((got["match"], len(got["candidates"])), ("many", 2))

    def test_near_name_asks_instead_of_guessing(self):
        self.task("資料を送る")
        self.assertEqual(self.core.resolve("資料送る")["match"], "many")

    def test_unrelated_name_is_new(self):
        self.task("資料を送る")
        self.assertEqual(self.core.resolve("庭の草むしり")["match"], "none")

    def test_closed_task_is_not_matched(self):
        self.task("資料を送る", status="done")
        self.assertEqual(self.core.resolve("資料を送る")["match"], "none")

    def test_empty_name_is_refused(self):
        with self.assertRaises(ValueError):
            self.core.resolve("  ")


class TestBegin(Base):
    def test_writes_nothing_to_tasks_or_logs(self):
        self.core.begin("新しい作業")
        self.assertEqual(tasks.all_tasks(), [])
        self.assertEqual(logs.read("kickoff"), [])

    def test_work_dir_is_inside_the_data_folder(self):
        got = self.core.begin("../../外へ/出る")
        self.assertTrue(got["workDir"].startswith(paths.data_dir() + os.sep))

    def test_shows_existing_files_so_they_are_not_overwritten(self):
        t = self.task("資料を送る")
        folder = self.core.begin("資料を送る")["workDir"]
        with open(os.path.join(folder, "draft.md"), "w") as f:
            f.write("x")
        got = self.core.begin("資料を送る")
        self.assertEqual((got["task"]["id"], got["existingFiles"]), (t["id"], ["draft.md"]))

    def test_second_run_sees_the_last_one(self):
        t = self.task("資料を送る")
        self.core.finish("骨組み", self.made(), "見出しを直す", task_id=t["id"])
        self.assertEqual(self.core.begin("資料を送る")["lastRun"]["next"], "見出しを直す")


class TestFinish(Base):
    def test_records_on_the_task_and_moves_it_to_doing(self):
        t = self.task("資料を送る", 12)
        p = self.made()
        got = self.core.finish("骨組みを書いた", p, "1段落目を読む", task_id=t["id"])
        cur = tasks.get(t["id"])
        self.assertEqual(cur["status"], "doing")
        self.assertIn("骨組みを書いた", cur["note"])
        self.assertIn(p, cur["note"])
        self.assertIn("次の5分：1段落目を読む", cur["note"])
        self.assertEqual(got["next"], "1段落目を読む")
        self.assertEqual(logs.read("kickoff")[-1]["taskId"], t["id"])

    def test_finished_task_stops_being_proposed(self):
        t = self.task("資料を送る", 12)
        self.core.finish("骨組み", self.made(), "読む", task_id=t["id"])
        self.assertEqual(self.core.stale(paths.today()), [])

    def test_own_note_stays_and_runs_do_not_pile_up(self):
        t = self.task("資料を送る", note="相手の希望：月曜")
        self.core.finish("一回目", self.made(), "読む", task_id=t["id"])
        self.core.finish("二回目", self.made(), "直す", task_id=t["id"])
        note = tasks.get(t["id"])["note"]
        self.assertTrue(note.startswith("相手の希望：月曜"))
        self.assertNotIn("一回目", note)
        self.assertEqual(note.count("次の5分"), 1)

    def test_new_work_adds_a_task(self):
        got = self.core.finish("雛形", self.made(), "開く", title="庭の計画")
        t = tasks.get(got["task"]["id"])
        self.assertEqual((t["title"], t["status"], t["source"]), ("庭の計画", "doing", "lifeos-06"))

    def test_title_of_an_open_task_is_not_added_twice(self):
        t = self.task("庭の計画")
        got = self.core.finish("雛形", self.made(), "開く", title="庭の計画")
        self.assertEqual(got["task"]["id"], t["id"])
        self.assertEqual(len(tasks.all_tasks()), 1)

    def test_nothing_made_is_refused_and_nothing_recorded(self):
        t = self.task("資料を送る")
        cases = ([], os.path.join(self.tmp, "ない.md"), self.made("空.md", ""))
        for made in cases:
            with self.assertRaises(ValueError):
                self.core.finish("やった", made, "読む", task_id=t["id"])
        self.assertEqual(tasks.get(t["id"])["status"], "notyet")
        self.assertEqual(logs.read("kickoff"), [])

    def test_an_old_file_does_not_count_as_made(self):
        t = self.task("資料を送る")
        p = self.made()
        old = time.time() - 7 * 3600
        os.utime(p, (old, old))
        with self.assertRaises(ValueError):
            self.core.finish("やった", p, "読む", task_id=t["id"])

    def test_a_folder_counts_when_a_file_in_it_is_new(self):
        t = self.task("資料を送る")
        folder = os.path.join(self.tmp, "雛形")
        os.mkdir(folder)
        with self.assertRaises(ValueError):
            self.core.finish("やった", folder, "読む", task_id=t["id"])
        with open(os.path.join(folder, "a.py"), "w") as f:
            f.write("x = 1\n")
        self.assertEqual(self.core.finish("やった", folder, "読む", task_id=t["id"])["made"], [folder])

    def test_next_step_must_be_one_short_line(self):
        t = self.task("資料を送る")
        for bad in ("", "読む\n直す", "あ" * 101):
            with self.assertRaises(ValueError):
                self.core.finish("やった", self.made(), bad, task_id=t["id"])

    def test_did_is_required(self):
        t = self.task("資料を送る")
        with self.assertRaises(ValueError):
            self.core.finish("  ", self.made(), "読む", task_id=t["id"])

    def test_unknown_or_deleted_task_is_refused(self):
        with self.assertRaises(KeyError):
            self.core.finish("やった", self.made(), "読む", task_id="ない")
        t = self.task("消した", deleted=True)
        with self.assertRaises(KeyError):
            self.core.finish("やった", self.made(), "読む", task_id=t["id"])

    def test_similar_names_are_not_guessed(self):
        self.task("資料を送る")
        with self.assertRaises(ValueError):
            self.core.finish("やった", self.made(), "読む", title="資料送る")

    def test_does_not_push_a_fourth_task_into_doing(self):
        for i in range(3):
            self.task(f"進行中{i}", status="doing")
        t = self.task("資料を送る")
        got = self.core.finish("骨組み", self.made(), "読む", task_id=t["id"])
        self.assertEqual(got["task"]["status"], "notyet")
        self.assertIn("次の5分", tasks.get(t["id"])["note"])

    def test_doing_task_stays_doing(self):
        t = self.task("資料を送る", status="doing")
        self.assertEqual(self.core.finish("骨組み", self.made(), "読む", task_id=t["id"])["task"]["status"], "doing")


class TestCli(Base):
    def run_cli(self, *argv):
        cli = load_tool("06-kickoff", "cli")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(list(argv))
        return code, json.loads(out.getvalue())

    def test_whole_run_begin_make_finish(self):
        t = self.task("資料を送る", 12)
        code, got = self.run_cli("begin", "資料を送る")
        self.assertEqual((code, got["match"]), (0, "one"))
        draft = os.path.join(got["workDir"], "draft.md")
        with open(draft, "w", encoding="utf-8") as f:
            f.write("# 骨組み\n")
        code, got = self.run_cli("finish", "--task", t["id"], "--did", "骨組みを書いた", "--made", draft, "--next", "見出しを読む")
        self.assertEqual((code, got["task"]["status"], got["next"]), (0, "doing", "見出しを読む"))

    def test_refusal_is_reported_with_exit_1(self):
        t = self.task("資料を送る")
        code, got = self.run_cli("finish", "--task", t["id"], "--did", "やった", "--next", "読む")
        self.assertEqual(code, 1)
        self.assertIn("error", got)

    def test_stale_lists_three_at_most(self):
        for i in range(5):
            self.task(f"作業{i}", 400)
        self.assertEqual(len(self.run_cli("stale")[1]["tasks"]), 3)

    def test_skip_and_suggest(self):
        t = self.task("資料を送る", 400)
        self.assertEqual(self.run_cli("skip", t["id"])[0], 0)
        self.assertEqual(self.run_cli("stale")[1]["tasks"], [])
        self.assertEqual(self.run_cli("suggest", "off")[1], {"suggest": False})
        self.assertEqual(self.run_cli("skip", "ない")[0], 1)


class TestStopped(Base):
    def test_stopped_tool_proposes_and_runs_nothing(self):
        t = self.task("資料を送る", 12)
        cli = load_tool("06-kickoff", "cli")
        out = io.StringIO()
        with mock.patch.object(self.core, "stopped", lambda: True), contextlib.redirect_stdout(out):
            self.assertEqual(self.core.brief_lines(TODAY), [])
            cli.main(["begin", "資料を送る"])
        self.assertTrue(json.loads(out.getvalue())["stopped"])
        self.assertEqual(tasks.get(t["id"])["status"], "notyet")
