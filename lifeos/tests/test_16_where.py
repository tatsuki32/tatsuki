import contextlib
import io
import json
import os
import re
import sys
import time
from unittest import mock

from lifeos.lib import logs, paths, tasks
from lifeos.lib.tools import load_tool
from lifeos.tests.sandbox import SandboxCase


def core():
    return load_tool("16-where", "core")


def draft():
    return load_tool("16-where", "draft")


def run(*argv, stdin=None):
    cli = load_tool("16-where", "where")
    out = io.StringIO()
    with contextlib.redirect_stdout(out), mock.patch.object(sys, "stdin", io.StringIO(stdin or "")):
        cli.main(list(argv))
    return out.getvalue()


class TestGather(SandboxCase):
    def test_finds_by_title_and_note_with_width_and_case_ignored(self):
        a = tasks.add("ＡＢＣ資料を直す", source="t")
        b = tasks.add("見積もり", note="案件:abc資料", source="t")
        tasks.add("関係ないもの", source="t")
        tasks.done(b["id"])
        found = core().find_tasks("abc資料")
        self.assertEqual([t["id"] for t in found["open"]], [a["id"]])
        self.assertEqual([t["id"] for t in found["done"]], [b["id"]])

    def test_all_words_must_match(self):
        tasks.add("試し 案件 資料", source="t")
        self.assertEqual(len(core().find_tasks("試し 資料")["open"]), 1)
        self.assertEqual(core().find_tasks("試し 別物")["open"], [])

    def test_deleted_and_dropped_are_left_out(self):
        tasks.delete(tasks.add("試し案件A", source="t")["id"])
        tasks.update(tasks.add("試し案件B", source="t")["id"], status="dropped")
        found = core().find_tasks("試し案件")
        self.assertEqual(found, {"done": [], "open": []})

    def test_default_readers_report_not_connected(self):
        g = core().gather("試し案件")
        self.assertEqual(g["drive"]["status"], "未接続")
        self.assertEqual(g["gmail"]["status"], "未接続")

    def test_readers_can_be_swapped_and_failure_stays_local(self):
        tasks.add("試し案件の作業", source="t")

        def broken(name):
            raise OSError("secret detail")

        g = core().gather("試し案件", {"drive": lambda n: [{"title": "資料", "date": "2026-10-01"}], "gmail": broken})
        self.assertEqual(g["drive"]["status"], "ok")
        self.assertEqual(g["gmail"], {"status": "失敗", "items": []})
        self.assertEqual(len(g["tasks"]["open"]), 1)
        self.assertNotIn("secret detail", json.dumps(g))

    def test_broken_task_store_does_not_stop_it(self):
        os.makedirs(paths.desk_data_dir(), exist_ok=True)
        with open(os.path.join(paths.desk_data_dir(), "tasks.json"), "w") as f:
            f.write("{broken")
        self.assertEqual(core().gather("試し案件")["tasks"]["status"], "失敗")

    def test_search_words_are_escaped(self):
        c = core().connectors
        self.assertEqual(c.drive_query("a'b c"), "fullText contains 'a\\'b' and fullText contains 'c'")
        self.assertEqual(c.gmail_query('"a" b'), "a b newer_than:1y")


class TestFiveLines(SandboxCase):
    def test_always_exactly_five_lines(self):
        for fields in ({}, {"name": "a\nb\nc", "done": "x\n\ny", "next": "長い" * 200, "remaining": "\n"},
                       {"name": "n", "last_date": "こわれた日付", "sources": {"gmail": {"status": "失敗"}}}):
            text = core().render(fields)
            lines = text.split("\n")
            self.assertEqual(len(lines), 5, text)
            self.assertTrue(all(len(x) <= core().LINE_MAX for x in lines))

    def test_says_what_each_line_is(self):
        lines = core().render({"name": "試し", "done": "D", "remaining": "R", "next": "N"}).split("\n")
        for line, want in zip(lines, ("試し", "どこまで：D", "残り：R", "次の一手：N", "根拠：")):
            self.assertIn(want, line)

    def test_unknown_stays_unknown(self):
        f = core().summarize(core().gather("何もない案件"))
        self.assertEqual((f["done"], f["remaining"], f["next"]), ("記録なし", "記録なし", "記録なし"))

    def test_sources_and_time_are_shown(self):
        f = {"name": "n", "started_ms": paths.now_ms() - 90000,
             "sources": {"tasks": {"status": "ok", "count": 3}, "drive": {"status": "ok", "count": 2}, "gmail": {"status": "未接続"}}}
        last = core().render(f).split("\n")[4]
        self.assertIn("タスク3件・Drive2件・Gmail 未接続", last)
        self.assertIn("所要1分30秒", last)

    def test_next_prefers_what_is_in_progress(self):
        tasks.add("試し案件の古い作業", source="t")
        doing = tasks.add("試し案件の途中の作業", source="t")
        tasks.update(doing["id"], status="doing")
        f = core().summarize(core().gather("試し案件"))
        self.assertEqual(f["next"], "「試し案件の途中の作業」を開く")
        self.assertIn("2件", f["remaining"])

    def test_previous_result_fills_in_when_tasks_are_empty(self):
        core().remember({"name": "試し案件", "done": "前のD", "remaining": "前のR", "next": "前のN", "last_date": "2026-10-01"})
        f = core().summarize(core().gather("試し案件"))
        self.assertEqual((f["done"], f["remaining"], f["next"]), ("前のD", "前のR", "前のN"))

    def test_local_part_is_well_under_five_minutes(self):
        for i in range(200):
            tasks._write_file({"id": "t%d" % i, "title": "試し案件%d" % i, "status": "notyet", "updatedAt": i})
        start = time.monotonic()
        self.assertEqual(len(core().render(core().summarize(core().gather("試し案件"))).split("\n")), 5)
        self.assertLess(time.monotonic() - start, 5)


class TestStalled(SandboxCase):
    def test_at_most_three_longest_untouched_first(self):
        for i, n in enumerate(("a", "b", "c", "d")):
            tasks._write_file({"id": "t%d" % i, "title": "x", "status": "notyet", "note": "案件:" + n, "updatedAt": (i + 1) * 86400000})
        tasks.add("案件の指定なし", source="t")
        self.assertEqual([x["name"] for x in core().stalled()], ["a", "b", "c"])


class TestParse(SandboxCase):
    def test_ok_words(self):
        for text in ("OK", "ok", "ＯＫ", "OK！", " おっけー ", "はい。"):
            self.assertEqual(draft().parse(text)[0], "ok", text)

    def test_unclear_text_never_approves(self):
        for text in ("OKじゃない", "はいじゃない", "OK?だめかも", "いいえ", "ok だけど宛先を変えて", "送って"):
            self.assertEqual(draft().parse(text)[0], "fix", text)

    def test_fix_keeps_the_words(self):
        self.assertEqual(draft().parse("直す もっと短く"), ("fix", "もっと短く"))
        self.assertEqual(draft().parse("もっと短く"), ("fix", "もっと短く"))

    def test_stop(self):
        self.assertEqual(draft().parse("やめる")[0], "stop")

    def test_empty_is_refused(self):
        with self.assertRaises(draft().DraftError):
            draft().parse("  ！ ")


class TestApproval(SandboxCase):
    def test_nothing_happens_before_ok(self):
        d = draft().create("task", "本文", project="試し", title="次の一歩")
        self.assertEqual(tasks.open_tasks(), [])
        draft().react(d["id"], "直す 短く")
        self.assertEqual(tasks.open_tasks(), [])

    def test_ok_adds_the_task_once(self):
        d = draft().create("task", "本文", project="試し", title="次の一歩")
        out = draft().react(d["id"], "OK")
        self.assertTrue(out["added"])
        t = tasks.open_tasks()
        self.assertEqual([x["title"] for x in t], ["次の一歩"])
        self.assertIn("案件:試し", t[0]["note"])
        self.assertEqual(t[0]["source"], "lifeos-16")
        with self.assertRaises(draft().DraftError):
            draft().react(d["id"], "OK")
        self.assertEqual(len(tasks.open_tasks()), 1)

    def test_same_open_task_is_not_added_twice(self):
        tasks.add("次の一歩", source="t")
        out = draft().react(draft().create("task", "本文", title="次の一歩")["id"], "OK")
        self.assertFalse(out["added"])
        self.assertEqual(len(tasks.open_tasks()), 1)

    def test_ok_after_fix_needs_the_new_text(self):
        d = draft().create("note", "一版")
        self.assertEqual(draft().react(d["id"], "直す 短く")["feedback"], "短く")
        with self.assertRaises(draft().DraftError):
            draft().react(d["id"], "OK")
        self.assertEqual(draft().redraft(d["id"], "二版")["rev"], 2)
        out = draft().react(d["id"], "OK")
        with open(out["saved"], encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "二版")

    def test_stop_closes_it(self):
        d = draft().create("note", "一版")
        self.assertEqual(draft().react(d["id"], "やめる")["result"], "stop")
        with self.assertRaises(draft().DraftError):
            draft().react(d["id"], "OK")
        with self.assertRaises(draft().DraftError):
            draft().redraft(d["id"], "二版")

    def test_failed_step_leaves_it_waiting(self):
        d = draft().create("task", "本文", title="次の一歩")
        with mock.patch.object(tasks, "add", side_effect=OSError("書けない")):
            with self.assertRaises(OSError):
                draft().react(d["id"], "OK")
        self.assertEqual(draft().get(d["id"])["status"], "pending")

    def test_bad_drafts_are_refused(self):
        for args in (("send", "本文"), ("note", " "), ("task", "本文"), ("mail", "本文")):
            with self.assertRaises(draft().DraftError):
                draft().create(*args)

    def test_id_cannot_leave_the_folder(self):
        with self.assertRaises(draft().DraftError):
            draft().get("../../secrets/x")


class TestMail(SandboxCase):
    def mail(self):
        return draft().create("mail", "本文", subject="件名", to=["a@example.com"])

    def test_mail_stops_at_a_draft(self):
        d = self.mail()
        self.assertEqual(draft().react(d["id"], "直す 短く")["do"], "redraft")
        draft().redraft(d["id"], "二版")
        out = draft().react(d["id"], "OK")
        self.assertEqual(out["do"], "gmail_create_draft")
        self.assertEqual(out["body"], "二版")
        self.assertEqual(draft().drafted(d["id"])["status"], "gmail_drafted")
        with self.assertRaises(draft().DraftError):
            draft().react(d["id"], "OK")

    def test_cannot_be_marked_before_ok(self):
        with self.assertRaises(draft().DraftError):
            draft().drafted(self.mail()["id"])

    def test_code_has_no_way_to_send(self):
        folder = os.path.join(paths.REPO, "lifeos", "16-where")
        for name in os.listdir(folder):
            if name.endswith(".py"):
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    text = f.read()
                self.assertIsNone(re.search(r"send_message|smtplib|forward\(|\.reply\(", text), name)

    def test_commands_forbid_sending(self):
        with open(os.path.join(paths.REPO, ".claude", "commands", "lowenergy.md"), encoding="utf-8") as f:
            self.assertIn("メールを送らない", f.read())
        with open(os.path.join(paths.REPO, ".claude", "commands", "where.md"), encoding="utf-8") as f:
            self.assertIn("読むだけ", f.read())


class TestCli(SandboxCase):
    def test_show_writes_nothing(self):
        tasks.add("試し案件の作業", source="t")
        self.assertEqual(len(run("show", "試し案件").strip().split("\n")), 5)
        self.assertEqual(logs.read("where"), [])

    def test_render_prints_five_lines_and_remembers(self):
        f = {"name": "試し案件", "done": "D", "remaining": "R", "next": "N", "last_date": paths.today()}
        out = run("render", stdin=json.dumps(f)).strip().split("\n")
        self.assertEqual(len(out), 5)
        self.assertIn("今日", out[0])
        self.assertEqual(json.loads(run("gather", "試し案件"))["previous"]["next"], "N")

    def test_draft_then_react(self):
        d = json.loads(run("draft", stdin=json.dumps({"kind": "task", "title": "一歩", "body": "本文"})))
        self.assertEqual(json.loads(run("react", d["id"], "OK"))["result"], "ok")
        self.assertEqual(len(tasks.open_tasks()), 1)

    def test_errors_are_short_messages(self):
        with self.assertRaises(SystemExit) as e:
            run("react", "d00", "OK")
        self.assertEqual(str(e.exception), "その下書きはありません")
        with self.assertRaises(SystemExit):
            run("render", stdin="こわれた入力")

    def test_can_be_stopped(self):
        cli = load_tool("16-where", "where")
        with mock.patch.object(cli, "info", return_value={"status": "stopped"}):
            self.assertIn("止めています", run("show", "試し案件"))


class TestDeskButtons(SandboxCase):
    def test_server_allows_both_commands(self):
        from lifeos.tests import desk_import
        for c in ("/where", "/lowenergy"):
            self.assertIn(c, desk_import.server.CLAUDE_CMDS)

    def test_page_lists_both_and_lowenergy_asks_nothing(self):
        with open(os.path.join(paths.REPO, "desk", "index.html"), encoding="utf-8") as f:
            page = f.read()
        where = re.search(r'\{id:"where".*\}', page).group(0)
        low = re.search(r'\{id:"lowenergy".*\}', page).group(0)
        self.assertIn('cmd:"/where"', where)
        self.assertIn('cmd:"/lowenergy"', low)
        self.assertNotIn("ask:", low)


class TestToolJson(SandboxCase):
    def test_tool_json_has_no_gap(self):
        with open(os.path.join(paths.REPO, "lifeos", "16-where", "tool.json"), encoding="utf-8") as f:
            t = json.load(f)
        self.assertEqual(t["commands"], ["/where", "/lowenergy"])
        self.assertIn(t["status"], ("planned", "building", "working", "blocked", "stopped"))
        for c in t["commands"]:
            self.assertTrue(os.path.exists(os.path.join(paths.REPO, ".claude", "commands", c[1:] + ".md")))
