import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta

from lifeos.lib import logs, paths, tasks
from lifeos.lib.tools import load_tool
from lifeos.tests.sandbox import SandboxCase

TOOL = os.path.join(paths.REPO, "lifeos", "15-resume")
SESSION = {"file": "memory/episodic/sessions/2026-11-01-x.md", "title": "x",
           "sections": {"ひとこと": ["いま：画面ができた", "次：スマホで見る"], "次の一手": ["長い説明"]}}


class Base(SandboxCase):
    def setUp(self):
        super().setUp()
        self.core = load_tool("15-resume", "core")
        self.real_memory = self.core.memory_doc
        self.addCleanup(setattr, self.core, "memory_doc", self.real_memory)
        self.core.memory_doc = lambda: {}

    def old(self, days):
        return (paths.now() - timedelta(days=days)).isoformat(timespec="seconds")


class TestTsuzuki(Base):
    def test_three_lines_from_the_work_log(self):
        logs.append("work", {"session": "a", "done": "土台を作った", "next": "テストを足す"})
        out = self.core.lines()
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0], "前回：土台を作った（今日）")
        self.assertEqual(out[1], "途中：進行中のタスクなし")
        self.assertEqual(out[2], "次の一手：テストを足す（作業ログ・今日）")

    def test_any_gap_still_gives_the_next_move(self):
        for days, word in ((1, "昨日"), (3, "3日前"), (90, "90日前")):
            logs.append("work", {"at": self.old(days), "session": str(days), "done": "やった", "next": "続きを書く"})
            out = self.core.lines()
            self.assertIn(word, out[0])
            self.assertTrue(out[2].startswith("次の一手：続きを書く"))

    def test_newest_record_wins(self):
        logs.append("work", {"at": self.old(5), "session": "a", "done": "古い", "next": "古い手"})
        logs.append("work", {"session": "b", "done": "新しい", "next": "新しい手"})
        self.assertIn("新しい手", self.core.lines()[2])

    def test_falls_back_in_order(self):
        # memory only: no hook has run yet
        self.core.memory_doc = lambda: {"sessions": [SESSION]}
        out = self.core.lines()
        self.assertIn("画面ができた", out[0])
        self.assertTrue(out[2].startswith("次の一手：スマホで見る（セッション記録）"))
        # a task in progress is more concrete than the record
        tasks.add("見出しを書く", status="doing")
        self.assertTrue(self.core.lines()[2].startswith("次の一手：見出しを書く（進行中のタスク）"))

    def test_open_task_when_nothing_else(self):
        tasks.add("資料を開く")
        self.assertIn("資料を開く（Desk のタスク）", self.core.lines()[2])

    def test_empty_everything_still_answers(self):
        out = self.core.lines()
        self.assertEqual(out[0], "前回：記録なし")
        self.assertIn("やることを1つ決めて", out[2])

    def test_next_move_already_done_is_not_repeated(self):
        t = tasks.add("送る前に読む")
        logs.append("work", {"session": "a", "done": "書いた", "next": "送る前に読む"})
        tasks.done(t["id"])
        tasks.add("別の作業")
        self.assertIn("別の作業", self.core.lines()[2])

    def test_only_one_next_move_and_many_doing_are_folded(self):
        for n in range(5):
            tasks.add(f"作業{n}", status="doing")
        out = self.core.lines()
        self.assertEqual(len(out), 3)
        self.assertIn("（ほか4件）", out[1])

    def test_broken_tasks_file_does_not_stop_it(self):
        tasks.add("仮")
        with open(tasks._file(), "w", encoding="utf-8") as f:
            f.write("{こわれた")
        logs.append("work", {"session": "a", "done": "やった", "next": "続ける"})
        self.assertIn("続ける", self.core.lines()[2])

    def test_reads_only(self):
        tasks.add("そのまま")
        before = tasks.all_tasks()
        self.core.lines()
        self.assertEqual(tasks.all_tasks(), before)
        self.assertEqual(logs.read("work"), [])

    def test_cli_all_lists_each_session_once(self):
        logs.append("work", {"session": "a", "branch": "x", "done": "一", "next": "二"})
        logs.append("work", {"session": "a", "branch": "x", "done": "三", "next": "四"})
        logs.append("work", {"session": "b", "branch": "y", "done": "五", "next": ""})
        buf = io.StringIO()
        with redirect_stdout(buf):
            load_tool("15-resume", "tsuzuki").main(["--all"])
        rows = buf.getvalue().strip().splitlines()
        self.assertEqual(len(rows), 2)
        self.assertIn("次の一手なし", rows[0])
        self.assertIn("三 → 四", rows[1])

    def test_real_memory_folder_gives_a_next_move(self):
        self.core.memory_doc = self.real_memory  # the repo's own memory/: reads only, no personal data
        out = self.core.lines()
        self.assertNotEqual(out[0], "前回：記録なし")
        self.assertTrue(out[2].endswith("（セッション記録）"), out[2])


class TestSplit(Base):
    def setUp(self):
        super().setUp()
        self.s = load_tool("15-resume", "split")

    def test_adds_steps_to_desk_tasks(self):
        r = self.s.split("企画書を書く", ["5分 ひな形を開く", "15分 見出しを書く"])
        self.assertEqual(len(r["added"]), 2)
        self.assertEqual(r["first"]["minutes"], 5)
        items = tasks.open_tasks()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "ひな形を開く（1/2 企画書を書く）")
        self.assertEqual(items[0]["source"], "lifeos-15")
        self.assertIn("目安：5分", items[0]["note"])

    def test_over_fifteen_minutes_saves_nothing(self):
        with self.assertRaises(self.s.StepError):
            self.s.split("大きい", ["5分 開く", "16分 書く"])
        self.assertEqual(tasks.open_tasks(), [])

    def test_step_without_minutes_saves_nothing(self):
        with self.assertRaises(self.s.StepError):
            self.s.split("大きい", ["5分 開く", "書く"])
        self.assertEqual(tasks.open_tasks(), [])

    def test_too_many_steps_saves_nothing(self):
        with self.assertRaises(self.s.StepError):
            self.s.split("大きい", [f"5分 手順{n}" for n in range(13)])
        self.assertEqual(tasks.open_tasks(), [])

    def test_repeat_adds_nothing(self):
        self.s.split("企画書", ["5分 開く", "5分 書く"])
        r = self.s.split("企画書", ["5分 開く", "5分 書く"])
        self.assertEqual(r["added"], [])
        self.assertEqual(len(r["skipped"]), 2)
        self.assertEqual(len(tasks.open_tasks()), 2)

    def test_finished_step_is_not_added_again(self):
        self.s.split("企画書", ["5分 開く", "5分 書く"])
        tasks.done(tasks.open_tasks()[0]["id"])
        r = self.s.split("企画書", ["5分 開く", "5分 書く", "5分 読む"])
        self.assertEqual([a["title"][:2] for a in r["added"]], ["読む"])

    def test_links_to_the_existing_parent_and_leaves_it_open(self):
        parent = tasks.add("企画書を書く", status="doing")
        r = self.s.split("企画書", ["5分 開く"])
        self.assertTrue(r["linked"])
        self.assertEqual(tasks.get(parent["id"])["status"], "doing")
        step = next(t for t in tasks.open_tasks() if t["id"] != parent["id"])
        self.assertTrue(step["sourceRef"].startswith("split:" + parent["id"]))

    def test_step_one_sorts_first_in_both_desk_orders(self):
        self.s.split("企画書", ["5分 一番", "5分 二番", "5分 三番"])
        stored = tasks.open_tasks()
        self.assertTrue(stored[0]["title"].startswith("一番"))  # the Home list keeps stored order
        by_new = sorted(stored, key=lambda t: -t["createdAt"])  # the Tasks view: newest first
        self.assertEqual([t["title"][:2] for t in by_new], ["一番", "二番", "三番"])

    def test_dry_run_writes_nothing(self):
        r = self.s.split("企画書", ["5分 開く"], dry_run=True)
        self.assertEqual(len(r["added"]), 1)
        self.assertEqual(tasks.open_tasks(), [])

    def test_only_fields_desk_keeps(self):
        self.s.split("企画書", ["5分 開く"])
        self.assertLessEqual(set(tasks.open_tasks()[0]), set(tasks.FIELDS) | {"id", "updatedAt"})

    def test_long_parent_keeps_the_step_readable(self):
        self.s.split("長" * 200, ["5分 開く"])
        title = tasks.open_tasks()[0]["title"]
        self.assertTrue(title.startswith("開く（1/1 "))
        self.assertLessEqual(len(title), tasks.TITLE_MAX)

    def test_cli_exit_codes(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(self.s.main(["企画書", "5分 開く"]), 0)
        self.assertIn("まず：開く", buf.getvalue())
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(self.s.main(["企画書", "20分 書く"]), 2)
        self.assertIn("保存していません", err.getvalue())


def line(kind, content, **extra):
    return {"type": kind, "gitBranch": "work/x", "message": {"role": kind, "content": content}, **extra}


class TestSplitThenTsuzuki(Base):
    def setUp(self):
        super().setUp()
        self.s = load_tool("15-resume", "split")

    def test_first_step_is_the_next_move_then_the_second(self):
        self.core.memory_doc = lambda: {"sessions": [SESSION]}
        self.s.split("企画書", ["5分 開く", "5分 書く"])
        self.assertIn("開く（1/2 企画書）（分けたステップ）", self.core.lines()[2])
        first = next(t for t in tasks.open_tasks() if t["title"].startswith("開く"))
        tasks.done(first["id"])
        self.assertIn("書く（2/2 企画書）", self.core.lines()[2])

    def test_split_after_the_log_wins_and_an_older_split_does_not(self):
        logs.append("work", {"at": self.old(2), "session": "a", "done": "やった", "next": "古い手"})
        self.s.split("企画書", ["5分 開く"])
        self.assertIn("開く", self.core.lines()[2])
        later = (paths.now() + timedelta(minutes=1)).isoformat()
        logs.append("work", {"at": later, "session": "b", "done": "やった", "next": "新しい手"})
        self.assertIn("新しい手", self.core.lines()[2])

    def test_a_started_step_comes_before_the_unstarted_ones(self):
        self.s.split("企画書", ["5分 開く", "5分 書く"])
        second = next(t for t in tasks.open_tasks() if t["title"].startswith("書く"))
        tasks.update(second["id"], status="doing")
        self.assertIn("書く", self.core.lines()[2])
        self.assertIn("進行中のタスク", self.core.lines()[2])


class TestHook(Base):
    def setUp(self):
        super().setUp()
        self.h = load_tool("15-resume", "hook")
        self.path = os.path.join(self.tmp, "t.jsonl")

    def write(self, *rows, junk=False):
        with open(self.path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            if junk:
                f.write("{こわれた行\n")

    def fire(self, name="SessionEnd"):
        return self.h.run({"session_id": "s1", "transcript_path": self.path, "cwd": "/w", "hook_event_name": name})

    def commit_rows(self, subject="画面を直した"):
        return [line("assistant", [{"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "git add . && git commit -m x"}}]),
                line("user", [{"type": "tool_result", "tool_use_id": "b1", "content": f"[work/x abc1234] {subject}\n 1 file changed"}])]

    def test_commit_and_next_line(self):
        self.write(line("user", "<system-reminder>消す</system-reminder>画面を直して"), *self.commit_rows(),
                   line("assistant", [{"type": "text", "text": "直しました。\n\n👉 次の一手：スマホで見る"}]))
        r = self.fire()
        self.assertEqual(r["done"], "画面を直した")
        self.assertEqual(r["next"], "スマホで見る")
        self.assertEqual(r["branch"], "work/x")
        self.assertEqual(logs.read("work")[-1]["next"], "スマホで見る")

    def test_tsuzuki_reads_what_the_hook_wrote(self):
        self.write(*self.commit_rows(), line("assistant", [{"type": "text", "text": "**次の一手**：スマホで見る"}]))
        self.fire()
        self.assertIn("スマホで見る", self.core.lines()[2])
        self.assertIn("画面を直した", self.core.lines()[0])

    def test_read_only_session_writes_nothing_and_keeps_the_old_next(self):
        logs.append("work", {"session": "old", "done": "やった", "next": "続ける"})
        self.write(line("user", "これは何？"), line("assistant", [{"type": "text", "text": "説明です。"}]))
        self.assertIsNone(self.fire())
        self.assertIn("続ける", self.core.lines()[2])

    def test_edits_without_commit(self):
        self.write(line("user", [{"type": "text", "text": "題を直して"}]),
                   line("assistant", [{"type": "tool_use", "id": "e", "name": "Edit", "input": {"file_path": "/w/a.py"}}]))
        r = self.fire()
        self.assertEqual(r["done"], "題を直して（1個のファイルを変更）")
        self.assertEqual(r["next"], "")
        self.assertEqual(r["files"], ["a.py"])

    def test_session_record_gives_plain_words(self):
        self.core.memory_doc = lambda: {"sessions": [SESSION]}
        self.write(line("assistant", [{"type": "tool_use", "id": "w", "name": "Write", "input": {"file_path": "/w/" + SESSION["file"]}}]))
        r = self.fire()
        self.assertEqual((r["done"], r["next"]), ("画面ができた", "スマホで見る"))

    def test_prose_is_not_taken_as_next(self):
        self.assertEqual(self.h.explicit_next("次は画面を直します。次回：未定"), "")
        self.assertEqual(self.h.explicit_next("## 次の一手\n\n- 画面を直す"), "画面を直す")
        self.assertEqual(self.h.explicit_next("- 次：見る\n- 次の一手：書く"), "書く")

    def test_commit_text_in_other_output_is_ignored(self):
        self.write(line("assistant", [{"type": "tool_use", "id": "c", "name": "Bash", "input": {"command": "cat old.log"}}]),
                   line("user", [{"type": "tool_result", "tool_use_id": "c", "content": "[main abc1234] 昔の話"}]))
        self.assertIsNone(self.fire())

    def test_subagent_lines_are_skipped(self):
        self.write(line("assistant", [{"type": "text", "text": "次の一手：子の話"}], isSidechain=True))
        self.assertIsNone(self.fire())

    def test_every_turn_does_not_repeat_the_same_record(self):
        self.write(*self.commit_rows())
        self.assertIsNotNone(self.fire("Stop"))
        self.assertIsNone(self.fire("Stop"))
        self.assertIsNone(self.fire("SessionEnd"))
        self.assertEqual(len(logs.read("work")), 1)

    def test_secrets_are_masked(self):
        fake = "sk-" + "a" * 24
        self.write(*self.commit_rows(), line("assistant", [{"type": "text", "text": f"次の一手：{fake} を入れる"}]))
        r = self.fire()
        self.assertNotIn(fake, json.dumps(r))
        self.assertIn("伏せ字", r["next"])

    def test_broken_or_missing_transcript_is_harmless(self):
        self.write(*self.commit_rows(), junk=True)
        self.assertIsNotNone(self.fire())
        self.assertIsNone(self.h.run({"session_id": "s", "transcript_path": self.tmp + "/none.jsonl"}))
        self.assertIsNone(self.h.run({}))

    def test_stopped_tool_writes_nothing(self):
        self.write(*self.commit_rows())
        real = self.h.tools.info
        self.addCleanup(setattr, self.h.tools, "info", real)
        self.h.tools.info = lambda d: {"status": "stopped"}
        self.assertIsNone(self.fire())

    def run_script(self, stdin):
        return subprocess.run([sys.executable, os.path.join(TOOL, "hook.py")], input=stdin,
                              capture_output=True, text=True, timeout=30)

    def test_script_is_silent_and_exits_zero(self):
        self.write(*self.commit_rows())
        ok = self.run_script(json.dumps({"session_id": "s", "transcript_path": self.path}))
        self.assertEqual((ok.returncode, ok.stdout, ok.stderr), (0, "", ""))
        self.assertEqual(logs.read("work")[-1]["done"], "画面を直した")
        for bad in ("", "こわれた", "[1,2]"):
            r = self.run_script(bad)
            self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(logs.read("work_errors")[-1]["error"], "AttributeError")


class TestIntegration(SandboxCase):
    def test_registered_hooks_never_exit_two(self):
        with open(os.path.join(paths.REPO, ".claude", "settings.json"), encoding="utf-8") as f:
            hooks = json.load(f)["hooks"]
        for event in ("Stop", "SessionEnd"):
            cmds = [h["command"] for group in hooks[event] for h in group["hooks"] if "15-resume/hook.py" in h["command"]]
            self.assertEqual(len(cmds), 1, event)
            # a branch without the script must not block the session: exit 2 would
            env = {**os.environ, "CLAUDE_PROJECT_DIR": self.tmp}
            r = subprocess.run(["sh", "-c", cmds[0]], input="{}", capture_output=True, text=True, env=env)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
