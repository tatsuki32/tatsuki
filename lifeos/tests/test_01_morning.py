"""/morning: the pick order, the 3-item limit, the brief file, Telegram, and the time limit."""
import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from unittest import mock

from lifeos.lib import logs, md, paths
from lifeos.lib.tools import load_tool
from lifeos.tests.sandbox import SandboxCase


def task(tid, title=None, status="notyet", **kw):
    return dict({"id": tid, "title": title or tid, "status": status, "kind": "集中"}, **kw)


def cand(**kw):
    ages = {"tasks.json": 0.1, "calendar.js": 0.1, "mail.js": 0.1, "gtasks.js": 0.1}
    return dict({"now": "11/04 09:00", "weekday": "水", "mode": "集中", "tasks": [], "nextSteps": [], "events": [],
                 "nextEvent": None, "freeMinutes": None, "short": False, "dataAgeHours": ages}, **kw)


class Base(SandboxCase):
    def setUp(self):
        super().setUp()
        self.m = load_tool("01-morning", "morning")

    def run_with(self, c, parts=(), send=None, **kw):
        out = io.StringIO()
        send = send or mock.Mock(return_value=True)
        with mock.patch.object(self.m, "gather", return_value=c), \
                mock.patch.object(self.m, "collect_parts", return_value=list(parts)), \
                mock.patch.object(self.m, "send_telegram", send):
            code = self.m.run(out=out, **kw)
        self.send = send
        return code, out.getvalue()


class TestPick(Base):
    def titles(self, c):
        return [i["title"] for i in self.m.pick(c)]

    def test_order_matches_kickoff(self):
        c = cand(tasks=[task("rest"), task("doing", status="doing"), task("due", dueToday=True, dueText="11/04 18:00")],
                 nextSteps=[{"text": "step", "file": "x"}])
        self.assertEqual(self.titles(c), ["due", "step", "doing"])

    def test_never_more_than_three(self):
        c = cand(tasks=[task(f"t{i}", dueToday=True) for i in range(10)], nextSteps=[{"text": "s"}])
        self.assertEqual(self.titles(c), ["t0", "t1", "t2"])

    def test_only_first_step_before_tasks(self):
        c = cand(tasks=[task("doing", status="doing"), task("rest")], nextSteps=[{"text": "s1"}, {"text": "s2"}])
        self.assertEqual(self.titles(c), ["s1", "doing", "rest"])

    def test_steps_fill_when_tasks_are_few(self):
        c = cand(nextSteps=[{"text": "s1"}, {"text": "s2"}, {"text": "s3"}, {"text": "s4"}])
        self.assertEqual(self.titles(c), ["s1", "s2", "s3"])

    def test_step_same_as_task_counts_once(self):
        c = cand(tasks=[task("t1", "資料を送る", status="doing"), task("t2", "別の仕事")], nextSteps=[{"text": "資料を送る"}])
        items = self.m.pick(c)
        self.assertEqual([i["title"] for i in items], ["資料を送る", "別の仕事"])
        self.assertIn("次の一手", items[0]["reason"])

    def test_fewer_than_three_are_not_padded(self):
        self.assertEqual(self.titles(cand(tasks=[task("only")])), ["only"])
        self.assertEqual(self.titles(cand()), [])

    def test_every_item_has_one_line_reason(self):
        c = cand(tasks=[task("a\nb", overdue=True), task("c", dueToday=True), task("d", status="doing")])
        for i in self.m.pick(c):
            self.assertTrue(i["reason"])
            self.assertNotIn("\n", i["reason"] + i["title"])

    def test_overdue_is_not_blamed(self):
        reason = self.m.pick(cand(tasks=[task("x", overdue=True, dueText="11/01 10:00")]))[0]["reason"]
        self.assertEqual(reason, "今日片づけると楽になる")

    def test_short_time_only_changes_the_first_reason(self):
        items = self.m.pick(cand(tasks=[task("a"), task("b")], short=True, freeMinutes=20))
        self.assertEqual([i["title"] for i in items], ["a", "b"])
        self.assertIn("15分", items[0]["reason"])
        self.assertNotIn("15分", items[1]["reason"])


class TestKickoffData(SandboxCase):
    """The real desk/kickoff.py, reading a fake Desk folder inside the sandbox."""

    def test_reads_tasks_and_calendar(self):
        desk = paths.desk_data_dir()
        os.makedirs(desk)
        now = paths.now_ms()
        with open(os.path.join(desk, "tasks.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "a", "title": "進行中の仕事", "status": "doing"},
                       {"id": "b", "title": "消した仕事", "status": "notyet", "deleted": True},
                       {"id": "c", "title": "終わった仕事", "status": "done"}], f, ensure_ascii=False)
        start = datetime.fromtimestamp(now / 1000 + 60, paths.HCMC).isoformat()
        with open(os.path.join(desk, "calendar.js"), "w", encoding="utf-8") as f:
            f.write("window.CALENDAR=" + json.dumps({"events": [{"title": "予定", "start": start, "end": start}]}) + ";")
        m = load_tool("01-morning", "morning")
        c = m.gather()
        self.assertEqual([t["title"] for t in c["tasks"]], ["進行中の仕事"])
        self.assertEqual(c["nextEvent"]["title"], "予定")
        self.assertIn("進行中の仕事", [i["title"] for i in m.pick(c)])


class TestRender(Base):
    def test_missing_calendar_is_not_called_empty(self):
        ages = {"tasks.json": 0.1, "calendar.js": None}
        text = self.m.render(cand(dataAgeHours=ages), [], [])
        self.assertIn("予定は未確認", text)
        self.assertNotIn("予定なし", text)
        self.assertIn("予定を見ずに", text)

    def test_stale_sync_is_mentioned(self):
        ages = {"tasks.json": 5.5, "calendar.js": 0.1}
        self.assertIn("tasks.json が5.5時間前", self.m.render(cand(dataAgeHours=ages), [], []))

    def test_other_tools_lines_are_not_numbered(self):
        items = [{"title": "a", "reason": "r"}]
        text = self.m.render(cand(), items, [("99-none", ["返信待ち：2件"])])
        self.assertIn("・99-none：返信待ち：2件", text)
        self.assertEqual([l for l in text.splitlines() if l[:2] in ("1.", "2.", "3.", "4.")], ["1. a"])

    def test_collects_lines_from_other_tools(self):
        root = os.path.join(self.tmp, "tools")
        for slug, status in (("98-on", "working"), ("99-off", "stopped")):
            os.makedirs(os.path.join(root, slug))
            with open(os.path.join(root, slug, "tool.json"), "w", encoding="utf-8") as f:
                json.dump({"name": "試し" + slug, "status": status}, f, ensure_ascii=False)
            with open(os.path.join(root, slug, "brief.py"), "w", encoding="utf-8") as f:
                f.write("def lines(day):\n    return ['返信待ち：2件']\n")
        os.environ["LIFEOS_TOOLS_ROOT"] = root
        text = self.m.render(cand(), [], self.m.collect_parts(paths.today()))
        self.assertIn("・試し98-on：返信待ち：2件", text)
        self.assertNotIn("99-off", text)

    def test_empty_day(self):
        self.assertIn("今日はタスクが見つかりませんでした", self.m.render(cand(), [], []))


class TestBriefFile(Base):
    def test_saves_todays_file(self):
        code, out = self.run_with(cand(tasks=[task("仕事")]))
        self.assertEqual(code, 0)
        saved = md.read(f"briefs/{paths.today()}.md")
        self.assertIn("1. 仕事", saved)
        self.assertIn("1. 仕事", out)

    def test_keeps_what_another_tool_wrote(self):
        rel = f"briefs/{paths.today()}.md"
        md.save(rel, "# 今日のまとめ\n夜に書いた\n")
        self.run_with(cand(tasks=[task("一回目")]))
        self.run_with(cand(tasks=[task("二回目")]))
        saved = md.read(rel)
        self.assertIn("夜に書いた", saved)
        self.assertIn("二回目", saved)
        self.assertNotIn("一回目", saved)
        self.assertEqual(saved.count("<!-- morning -->"), 1)

    def test_title_cannot_break_the_block(self):
        rel = f"briefs/{paths.today()}.md"
        self.run_with(cand(tasks=[task("x <!-- /morning --> y")]))
        self.run_with(cand(tasks=[task("次")]))
        self.assertNotIn("/morning", md.read(rel).replace("<!-- /morning -->", ""))

    def test_save_failure_still_shows_the_items(self):
        with mock.patch.object(self.m.md, "save", side_effect=OSError("disk")):
            code, out = self.run_with(cand(tasks=[task("仕事")]))
        self.assertEqual(code, 1)
        self.assertIn("1. 仕事", out)
        self.assertIn("保存できませんでした", out)


class TestTelegram(Base):
    def test_sends_same_text_as_screen(self):
        _, out = self.run_with(cand(tasks=[task("仕事")]))
        sent = self.send.call_args[0][0]
        self.assertIn("1. 仕事", sent)
        self.assertTrue(out.startswith(sent))
        self.assertIn("送りました", out)

    def test_same_content_is_sent_once_a_day(self):
        self.run_with(cand(tasks=[task("仕事")]))
        _, out = self.run_with(cand(tasks=[task("仕事")], now="11/04 10:00"))
        self.send.assert_not_called()
        self.assertIn("すでに送った", out)

    def test_changed_content_is_sent_again(self):
        self.run_with(cand(tasks=[task("仕事")]))
        self.run_with(cand(tasks=[task("別の仕事")]))
        self.send.assert_called_once()

    def test_no_send(self):
        self.run_with(cand(tasks=[task("仕事")]), no_send=True)
        self.send.assert_not_called()

    def test_can_be_stopped_by_config(self):
        with open(paths.data_path("tools", "01-morning", "config.json"), "w") as f:
            json.dump({"telegram": False}, f)
        _, out = self.run_with(cand(tasks=[task("仕事")]))
        self.send.assert_not_called()
        self.assertIn("止めています", out)

    def test_not_connected_leaves_a_copy_and_can_send_later(self):
        code, out = self.run_with(cand(tasks=[task("仕事")]), send=self.m.telegram.notify)
        self.assertEqual(code, 0)
        self.assertIn("まだ送れません", out)
        self.assertIn("仕事", logs.read("outbox")[0]["text"])
        self.run_with(cand(tasks=[task("仕事")]))
        self.send.assert_called_once()

    def test_send_error_does_not_lose_the_brief(self):
        code, out = self.run_with(cand(tasks=[task("仕事")]), send=mock.Mock(side_effect=OSError("x")))
        self.assertEqual(code, 0)
        self.assertIn("1. 仕事", out)
        self.assertIn("送れませんでした", out)
        self.assertEqual(len(logs.read("outbox")), 1)


class TestTimeLimit(Base):
    def test_hung_telegram_does_not_hold_the_answer(self):
        t0 = time.time()
        code, out = self.run_with(cand(tasks=[task("仕事")]), send=lambda _: time.sleep(5), send_limit=0.1)
        self.assertLess(time.time() - t0, 2)
        self.assertEqual(code, 0)
        self.assertIn("確かめられません", out)

    def test_slow_tools_do_not_hold_the_answer(self):
        out = io.StringIO()
        with mock.patch.object(self.m, "gather", return_value=cand(tasks=[task("仕事")])), \
                mock.patch.object(self.m, "collect_parts", side_effect=lambda _: time.sleep(5)):
            t0 = time.time()
            self.m.run(no_send=True, out=out, parts_limit=0.1)
        self.assertLess(time.time() - t0, 2)
        self.assertIn("1. 仕事", out.getvalue())
        self.assertIn("省きました", out.getvalue())

    def test_limits_add_up_to_under_thirty_seconds(self):
        self.assertLess(self.m.BRIEF_LIMIT + self.m.SEND_LIMIT, 25)

    def test_whole_command_is_fast_with_many_tasks(self):
        desk = paths.desk_data_dir()
        os.makedirs(desk)
        with open(os.path.join(desk, "tasks.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": f"t{i}", "title": f"仕事{i}", "status": "notyet"} for i in range(5000)], f)
        script = os.path.join(paths.REPO, "lifeos", "01-morning", "morning.py")
        t0 = time.time()
        r = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=30, env=os.environ)
        self.assertLess(time.time() - t0, 30)
        self.assertEqual(r.returncode, 0, r.stderr)
        numbered = [l for l in r.stdout.splitlines() if l[:2].rstrip(".").isdigit() and l[1:2] == "."]
        self.assertEqual(len(numbered), 3)


class TestRepo(SandboxCase):
    def test_no_resume_command_is_added(self):
        cmds = load_tool("01-morning", "morning").tools.info(os.path.join(paths.REPO, "lifeos", "01-morning"))["commands"]
        self.assertEqual(cmds, ["/morning"])
        self.assertFalse(os.path.exists(os.path.join(paths.REPO, ".claude", "commands", "resume.md")))
