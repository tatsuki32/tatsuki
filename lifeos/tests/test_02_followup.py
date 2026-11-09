import glob
import io
import json
import os
import re
from contextlib import redirect_stdout
from datetime import timedelta

from lifeos.bot import router
from lifeos.lib import logs, paths, tasks, tools
from lifeos.lib.tools import load_tool
from lifeos.tests.sandbox import SandboxCase

TOOL = os.path.join(paths.REPO, "lifeos", "02-followup")


class FollowupCase(SandboxCase):
    def setUp(self):
        super().setUp()
        self.core = load_tool("02-followup", "core")
        self.conn = load_tool("02-followup", "connect")
        self.now = paths.now()
        self.day = paths.today()

    def thread(self, tid="a", days=5, last="me", to=("someone@example.com",), subject="見積の件", lang="ja"):
        at = (self.now - timedelta(days=days)).isoformat(timespec="seconds")
        return {"threadId": tid, "subject": subject, "lastFrom": last, "lastAt": at, "to": list(to),
                "messageId": "m-" + tid, "lang": lang, "viewUrl": "https://mail.example.com/" + tid}

    def snap(self, *threads, day=None):
        with open(paths.data_path("tools", "02-followup", "snapshot.json"), "w", encoding="utf-8") as f:
            json.dump({"day": day or self.day, "threads": list(threads)}, f, ensure_ascii=False)

    def run_with_drafts(self, *threads):
        """A morning, then the Claude step that creates what was queued."""
        self.snap(*threads)
        result = self.core.morning()
        for r in self.core.pending_drafts():
            self.core.record_draft(r["no"], "draft-" + str(r["no"]), "https://mail.example.com/d")
        return result


class TestRules(FollowupCase):
    def test_under_three_days_makes_no_draft(self):
        self.snap(self.thread(days=2))
        self.core.morning()
        self.assertEqual(self.core.pending_drafts(), [])

    def test_three_days_queues_one_draft_to_the_counterpart(self):
        self.snap(self.thread(days=3))
        self.assertEqual(self.core.morning()["pendingDrafts"], 1)
        r = self.core.pending_drafts()[0]
        self.assertEqual(r["to"], ["someone@example.com"])
        self.assertEqual(r["subject"], "Re: 見積の件")
        self.assertEqual(r["replyToMessageId"], "m-a")
        self.assertIn("その後いかが", r["body"])

    def test_draft_is_not_made_twice(self):
        self.run_with_drafts(self.thread(days=4))
        self.snap(self.thread(days=4))
        self.core.morning()
        self.assertEqual(self.core.pending_drafts(), [])

    def test_queue_is_kept_until_the_draft_exists(self):
        self.snap(self.thread(days=4))
        self.core.morning()
        self.core.morning()
        self.assertEqual(len(self.core.pending_drafts()), 1)

    def test_reply_from_them_closes_it(self):
        self.run_with_drafts(self.thread(days=5))
        self.snap(self.thread(days=1, last="them"))
        self.core.morning()
        self.assertEqual(self.core.open_items(), [])

    def test_our_new_message_restarts_the_clock(self):
        self.run_with_drafts(self.thread(days=20))
        self.snap(self.thread(days=1))
        self.core.morning()
        item = self.core.open_items()[0]
        self.assertIsNone(item["draft"])
        self.assertEqual(self.core.age_days(item, self.day), 1)

    def test_automated_addresses_are_ignored(self):
        self.snap(self.thread(to=("noreply@example.com",)), self.thread(tid="b", to=()))
        self.core.morning()
        self.assertEqual(self.core.open_items(), [])

    def test_old_snapshot_does_nothing(self):
        self.snap(self.thread(days=9), day="2020-01-01")
        self.assertEqual(self.core.morning(), {"stale": True})
        self.assertEqual(self.core.pending_drafts(), [])

    def test_first_run_is_capped(self):
        self.snap(*[self.thread(tid="t%d" % i, days=4) for i in range(9)])
        self.core.morning()
        self.assertEqual(len(self.core.pending_drafts()), self.core.DEFAULTS["maxDraftsPerRun"])

    def test_broken_state_is_not_overwritten(self):
        p = paths.data_path("tools", "02-followup", "state.json")
        with open(p, "w") as f:
            f.write("{")
        self.snap(self.thread())
        with self.assertRaises(self.core.StateError):
            self.core.morning()
        with open(p) as f:
            self.assertEqual(f.read(), "{")

    def test_bad_time_is_skipped(self):
        bad = dict(self.thread(), lastAt="昨日")
        self.snap(bad, self.thread(tid="b"))
        self.core.morning()
        self.assertEqual([i["threadId"] for i in self.core.open_items()], ["b"])


class TestAsk(FollowupCase):
    def asked(self):
        self.run_with_drafts(self.thread(days=15))
        self.snap(self.thread(days=15))
        self.core.morning()
        return self.core.open_items()[0]

    def test_thirteen_days_does_not_ask(self):
        self.run_with_drafts(self.thread(days=13))
        self.snap(self.thread(days=13))
        self.assertEqual(self.core.morning()["asked"], 0)
        self.assertEqual(logs.read("outbox"), [])

    def test_fourteen_days_asks_with_three_choices(self):
        self.run_with_drafts(self.thread(days=14))
        self.snap(self.thread(days=14))
        self.assertEqual(self.core.morning()["asked"], 1)
        text = logs.read("outbox")[0]["text"]
        for word in ("催促 送る 1", "催促 保留 1", "催促 捨てる 1"):
            self.assertIn(word, text)

    def test_never_asks_before_a_draft_exists(self):
        self.snap(self.thread(days=30))
        self.assertEqual(self.core.morning()["asked"], 0)

    def test_outbox_is_not_filled_every_morning(self):
        self.asked()
        self.snap(self.thread(days=15))
        self.core.morning()
        self.assertEqual(len(logs.read("outbox")), 1)

    def test_delivered_question_is_recorded(self):
        self.run_with_drafts(self.thread(days=15))
        self.conn.ask, saved = (lambda text: True), self.conn.ask
        self.addCleanup(setattr, self.conn, "ask", saved)
        self.snap(self.thread(days=15))
        self.core.morning()
        self.assertEqual(self.core.open_items()[0]["askedOn"], self.day)

    def test_send_answer_adds_a_task_and_sends_nothing(self):
        item = self.asked()
        reply = self.core.answer(item["no"], "送る")
        self.assertIn("送信はしていません", reply)
        self.assertEqual(self.core.open_items()[0]["state"], "send")
        self.assertEqual([t["source"] for t in tasks.open_tasks()], ["lifeos-02"])
        self.core.answer(item["no"], "送る")
        self.assertEqual(len(tasks.open_tasks()), 1)

    def test_task_closes_when_they_reply(self):
        item = self.asked()
        self.core.answer(item["no"], "送る")
        self.snap(self.thread(days=0, last="them"))
        self.core.morning()
        self.assertEqual(tasks.open_tasks(), [])

    def test_changing_your_mind_removes_the_send_task(self):
        item = self.asked()
        self.core.answer(item["no"], "送る")
        self.core.answer(item["no"], "捨てる")
        self.assertEqual(tasks.open_tasks(), [])

    def test_details_rename_the_task_and_reach_the_desk(self):
        self.snap(self.thread(days=4))
        self.core.morning()
        no = self.core.needs_detail()[0]["no"]
        self.assertEqual(self.core.save_details([{"no": no, "topic": "見積もり", "summary": "見積もりの確認を頼んだ。"}]), 1)
        self.assertEqual(tasks.open_tasks()[0]["title"], "Email nudge: 見積もり (someone@example.com)")
        self.assertEqual(self.core.needs_detail(), [])
        view = self.core.desk_view()["items"][0]
        self.assertEqual((view["taskId"], view["summary"]), (tasks.open_tasks()[0]["id"], "見積もりの確認を頼んだ。"))

    def test_send_answer_needs_a_draft(self):
        self.snap(self.thread(days=15))
        self.core.morning()
        self.assertIn("下書きがまだ", self.core.answer(1, "送る"))
        self.assertEqual([t["title"] for t in tasks.open_tasks()], [self.core.task_title(self.core.read_state()["threads"]["a"])])

    def test_three_days_makes_one_task_for_today_and_a_reply_closes_it(self):
        self.snap(self.thread(days=2), self.thread("b", days=4, to=("山田 <someone@example.com>",)))
        self.core.morning()
        self.core.morning()
        task = tasks.open_tasks()
        self.assertEqual([t["title"] for t in task], [self.core.task_title(self.core.read_state()["threads"]["b"])])
        self.assertTrue(task[0]["title"].startswith("Email nudge"))
        self.assertTrue(task[0]["title"].endswith("について（山田）"))
        self.assertEqual(task[0]["planDate"], self.day)
        self.snap(self.thread("b", days=0, last="them"))
        self.core.morning()
        self.assertEqual(tasks.open_tasks(), [])

    def test_first_run_adds_five_tasks_at_most(self):
        self.snap(*[self.thread(str(i), days=5) for i in range(8)])
        self.core.morning()
        self.assertEqual(len(tasks.open_tasks()), 5)

    def test_chat_gets_a_task_and_no_mail_draft(self):
        chat = {**self.thread("wa1", days=4, to=("友達",), subject="友達"), "source": "whatsapp"}
        self.assertEqual(self.core.apply_chats([chat]), {"tasks": 1})
        self.assertEqual(self.core.pending_drafts(), [])
        item = self.core.desk_view()["items"][0]
        self.assertEqual((item["source"], item["hasDraft"]), ("whatsapp", False))
        self.assertTrue(item["copyText"])
        self.core.apply_chats([{**chat, "lastFrom": "them", "lastAt": self.now.isoformat(timespec="seconds")}])
        self.assertEqual(tasks.open_tasks(), [])

    def test_hold_asks_again_after_seven_days(self):
        item = self.asked()
        self.core.answer(item["no"], "保留")
        self.snap(self.thread(days=15))
        self.assertEqual(self.core.morning()["asked"], 0)
        later = (self.now + timedelta(days=7)).strftime("%Y-%m-%d")
        self.snap(self.thread(days=15), day=later)
        self.assertEqual(self.core.morning(later)["asked"], 1)

    def test_drop_stops_and_can_be_undone(self):
        item = self.asked()
        self.assertIn("消していません", self.core.answer(item["no"], "捨てる"))
        self.assertEqual(self.core.open_items(), [])
        self.core.answer(item["no"], "戻す")
        self.assertEqual(len(self.core.open_items()), 1)

    def test_answer_after_reply_changes_nothing(self):
        item = self.asked()
        self.snap(self.thread(days=0, last="them"))
        self.core.morning()
        self.assertIn("返信が来ています", self.core.answer(item["no"], "送る"))
        self.assertEqual(tasks.open_tasks(), [])


class TestHandler(FollowupCase):
    def test_match(self):
        h = load_tool("02-followup", "handler")
        for ok in ("催促 送る 3", "催促　保留　３", "催促捨てる3番", "催促 一覧", "催促 送る"):
            self.assertTrue(h.match(ok), ok)
        for no in ("催促してほしい", "送る 3", "催促 送る 3 と 4", "task: 催促 送る 3"):
            self.assertFalse(h.match(no), no)

    def test_route_and_reply(self):
        self.run_with_drafts(self.thread(days=15))
        self.snap(self.thread(days=15))
        self.core.morning()
        found = router.discover()
        self.assertEqual(router.which("催促 保留 1", found.routes).slug, "02-followup")
        self.assertIn("保留", router.dispatch("催促 保留 1", router.Ctx(), found.routes))
        self.assertIn("1番", router.dispatch("催促 一覧", router.Ctx(), found.routes))

    def test_no_number_works_for_a_single_question_only(self):
        self.run_with_drafts(self.thread(days=15))
        self.snap(self.thread(days=15))
        self.core.morning()
        h = load_tool("02-followup", "handler")
        self.assertIn("保留", h.handle("催促 保留", router.Ctx()))
        self.assertIn("番号", h.handle("催促 保留", router.Ctx()))


class TestBrief(FollowupCase):
    def desk_file(self, name, value, var):
        os.makedirs(paths.desk_data_dir(), exist_ok=True)
        with open(os.path.join(paths.desk_data_dir(), name), "w", encoding="utf-8") as f:
            f.write(f"window.{var}={json.dumps(value, ensure_ascii=False)};")

    def part(self):
        return dict(tools.brief_parts(self.day)).get("02-followup") or []

    def test_uses_desk_calendar_and_morning_mail(self):
        start = self.now.replace(hour=23, minute=58, second=0, microsecond=0)
        self.desk_file("calendar.js", {"events": [{"title": "打合せ", "start": start.isoformat(), "end": (start + timedelta(minutes=1)).isoformat()}]}, "CAL")
        self.desk_file("mail.js", [{"id": self.day, "data": {"items": [
            {"key": "k1", "priority": "Action needed", "subject": "返す件", "from": "相手"},
            {"key": "k2", "priority": "FYI", "subject": "参考の件", "from": "相手"},
            {"key": "k3", "priority": "Reply drafted", "subject": "送った件", "from": "相手", "status": "sent"}]}}], "MAIL")
        text = "\n".join(self.part())
        self.assertIn("23:58 打合せ", text)
        self.assertIn("こちらが返すもの：1件　返す件", text)
        self.assertNotIn("参考の件", text)
        self.assertNotIn("送った件", text)

    def test_missing_desk_data_is_said_plainly(self):
        text = "\n".join(self.part())
        self.assertIn("今日の予定：まだ届いていません", text)
        self.assertIn("メールの確認がまだ", text)

    def test_waiting_is_listed_with_at_most_three(self):
        self.snap(*[self.thread(tid="t%d" % i, days=1, subject="件%d" % i) for i in range(5)])
        self.core.morning()
        line = [x for x in self.part() if x.startswith("相手の返信待ち")][0]
        self.assertIn("5件", line)
        self.assertIn("ほか2件", line)
        self.assertEqual(line.count("（1日）"), 3)

    def test_says_when_this_morning_did_not_run(self):
        self.snap(self.thread(days=1))
        self.core.morning()
        with self.core.state() as s:
            s["lastRun"] = "2020-01-01"
        self.assertIn("まだです", [x for x in self.part() if x.startswith("相手")][0])

    def test_dm_source_can_be_swapped_in(self):
        self.conn.read_dms, saved = (lambda day: [{"title": "DMの件", "from": "相手"}]), self.conn.read_dms
        self.addCleanup(setattr, self.conn, "read_dms", saved)
        self.assertIn("DMの件", "\n".join(self.part()))

    def test_draft_connection_can_be_swapped_in(self):
        self.conn.make_draft, saved = (lambda r: {"id": "x", "viewUrl": ""}), self.conn.make_draft
        self.addCleanup(setattr, self.conn, "make_draft", saved)
        self.snap(self.thread(days=3))
        self.assertEqual(self.core.morning()["drafted"], 1)
        self.assertEqual(self.core.pending_drafts(), [])

    def test_stop_silences_brief_and_morning(self):
        self.core.set_enabled(False)
        self.snap(self.thread(days=4))
        self.assertEqual(self.core.morning(), {"stopped": True})
        self.assertEqual(self.part(), [])
        self.core.set_enabled(True)
        self.assertEqual(self.core.morning()["pendingDrafts"], 1)


class TestNeverSends(FollowupCase):
    def test_code_has_no_way_to_send_mail(self):
        pat = re.compile(r"send_message|send_mail|smtplib|sendmail|drafts?/send|messages/send|create_reply|\bforward\b|gmail\.googleapis")
        files = glob.glob(os.path.join(TOOL, "*.py"))
        self.assertTrue(files)
        for path in files:
            with open(path, encoding="utf-8") as f:
                self.assertIsNone(pat.search(f.read()), path)

    def test_procedure_allows_only_the_draft_tool_for_writing(self):
        with open(os.path.join(TOOL, "MORNING_RUN.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("create_draft", text)
        self.assertIn("使ってはいけない", text)

    def test_snapshot_command_stamps_today(self):
        run = load_tool("02-followup", "run")
        p = os.path.join(self.tmp, "in.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"threads": [self.thread(days=3)]}, f)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(run.main(["snapshot", p]), 0)
        self.assertEqual(self.core.morning()["pendingDrafts"], 1)

    def test_demo_leaves_no_trace(self):
        run = load_tool("02-followup", "run")
        before = os.environ["LIFEOS_DATA"]
        self.addCleanup(os.environ.__setitem__, "LIFEOS_DATA", before)
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(run.main(["demo"]), 0)
        self.assertNotEqual(os.environ["LIFEOS_DATA"], before)
        self.assertFalse(os.path.exists(os.environ["LIFEOS_DATA"]))
        self.assertIn("送信はしていません", buf.getvalue())
