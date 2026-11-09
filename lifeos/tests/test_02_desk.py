import json
import os
import re
from datetime import timedelta

from lifeos.lib import paths, tasks
from lifeos.lib.tools import load_tool
from lifeos.tests.desk_import import server, sync
from lifeos.tests.sandbox import SandboxCase


class DeskCase(SandboxCase):
    def setUp(self):
        super().setUp()
        self.core = load_tool("02-followup", "core")
        self.now = paths.now()
        self.day = paths.today()

    def thread(self, tid="a", days=15, last="me"):
        at = (self.now - timedelta(days=days)).isoformat(timespec="seconds")
        return {"threadId": tid, "subject": "件名" + tid, "lastFrom": last, "lastAt": at, "to": ["someone@example.com"],
                "messageId": "m", "viewUrl": "https://mail.example.com/" + tid}

    def morning(self, *threads):
        with open(paths.data_path("tools", "02-followup", "snapshot.json"), "w", encoding="utf-8") as f:
            json.dump({"day": self.day, "threads": list(threads)}, f, ensure_ascii=False)
        self.core.morning()
        for r in self.core.pending_drafts():
            self.core.record_draft(r["no"], "d", "https://mail.example.com/draft")


class TestView(DeskCase):
    def test_lists_without_mail_text(self):
        self.morning(self.thread())
        item = self.core.desk_view()["items"][0]
        self.assertTrue(item["asking"])
        self.assertEqual(item["draftUrl"], "https://mail.example.com/draft")
        self.assertNotIn("body", item)
        self.assertNotIn("snippet", item)

    def test_not_asking_before_fourteen_days_or_without_a_draft(self):
        self.morning(self.thread(days=13))
        self.assertFalse(self.core.desk_view()["items"][0]["asking"])
        with open(paths.data_path("tools", "02-followup", "snapshot.json"), "w", encoding="utf-8") as f:
            json.dump({"day": self.day, "threads": [self.thread(tid="b", days=20)]}, f)
        self.core.morning()
        self.assertFalse([i for i in self.core.desk_view()["items"] if i["no"] == 2][0]["asking"])

    def test_replied_threads_are_gone_and_dropped_stay_for_undo(self):
        self.morning(self.thread(tid="a"), self.thread(tid="b"))
        self.core.answer(2, "捨てる")
        self.morning(self.thread(tid="a", days=0, last="them"))
        self.assertEqual([(i["no"], i["state"]) for i in self.core.desk_view()["items"]], [(2, "dropped")])

    def test_stop_shows(self):
        self.core.set_enabled(False)
        self.assertFalse(self.core.desk_view()["enabled"])


class TestServer(DeskCase):
    def test_answer_goes_through_the_rules(self):
        self.morning(self.thread())
        code, out = server.followup_answer("1", {"choice": "送る"})
        self.assertEqual(code, 200)
        self.assertIn("Nothing was sent", out["message"])
        self.assertEqual(out["view"]["items"][0]["state"], "send")
        self.assertEqual(len(tasks.open_tasks()), 1)

    def test_bad_answers_are_refused(self):
        self.morning(self.thread())
        for no, rec in (("1", {"choice": "送信"}), ("1", {}), ("x", {"choice": "保留"}), ("-1", {"choice": "保留"})):
            self.assertEqual(server.followup_answer(no, rec)[0], 400)
        self.assertEqual(self.core.desk_view()["items"][0]["state"], "waiting")

    def test_followup_command_is_allowed_and_exists(self):
        self.assertIn("/followup", server.CLAUDE_CMDS)
        self.assertTrue(os.path.exists(os.path.join(paths.REPO, ".claude", "commands", "followup.md")))


class TestOnline(DeskCase):
    def test_first_sync_sends_the_list_and_meta(self):
        self.morning(self.thread())
        push = {d["id"]: d for d in self.core.sync_online([])}
        self.assertEqual(push["f1"]["subject"], "件名a")
        self.assertEqual(push["_meta"]["lastRun"], self.day)

    def test_no_change_pushes_nothing(self):
        self.morning(self.thread())
        self.assertEqual(self.core.sync_online(self.core.sync_online([])), [])

    def test_phone_answer_is_applied_once_and_cleared(self):
        self.morning(self.thread())
        online = self.core.sync_online([])
        for d in online:
            if d["id"] == "f1":
                d.update(answer="保留", answeredAt=1)
        push = {d["id"]: d for d in self.core.sync_online(online)}
        self.assertEqual(self.core.desk_view()["items"][0]["state"], "held")
        self.assertEqual(push["f1"]["state"], "held")
        self.assertNotIn("answer", push["f1"])

    def test_phone_send_answer_only_adds_a_task(self):
        self.morning(self.thread())
        online = self.core.sync_online([])
        online[0].update(answer="送る", answeredAt=1)
        self.core.sync_online(online)
        self.assertEqual([t["source"] for t in tasks.open_tasks()], ["lifeos-02"])

    def test_unknown_answer_is_ignored(self):
        self.morning(self.thread())
        online = self.core.sync_online([])
        online[0].update(answer="全部消す")
        self.core.sync_online(online)
        self.assertEqual(self.core.desk_view()["items"][0]["state"], "waiting")

    def test_replied_thread_is_deleted_online(self):
        self.morning(self.thread())
        online = self.core.sync_online([])
        self.morning(self.thread(days=0, last="them"))
        push = {d["id"]: d for d in self.core.sync_online(online)}
        self.assertEqual(push["f1"], {"id": "f1", "deleted": True})

    def test_broken_state_does_not_stop_the_hourly_sync(self):
        with open(paths.data_path("tools", "02-followup", "state.json"), "w") as f:
            f.write("{")
        self.assertEqual(sync.followups_push([]), [])


class TestPage(SandboxCase):
    def source(self):
        with open(os.path.join(paths.REPO, "desk", "index.html"), encoding="utf-8") as f:
            return f.read()

    def test_view_has_no_way_to_send_mail(self):
        html = self.source()
        block = html[html.index("/* Awaiting Reply: Life OS 02"):html.index("/* Life OS: one section per tool")]
        self.assertTrue(block)
        self.assertIsNone(re.search(r"send_message|callTool|mcp\.", block))

    def test_view_is_in_the_menu_next_to_mail(self):
        self.assertIn('{id:"followups",e:"⏳",label:"Awaiting Reply",g:"in"}', self.source())
