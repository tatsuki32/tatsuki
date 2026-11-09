"""WhatsApp chats waiting for a reply become Desk tasks, and answering closes them."""
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys

from lifeos.lib import tasks
from lifeos.tests.sandbox import SandboxCase

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
spec = importlib.util.spec_from_file_location("whatsapp_sync", os.path.join(ROOT, "desk", "whatsapp_sync.py"))
wa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wa)

NOW = 1793860000
GROUP, PERSONAL = 1, 0
GROUP_HOST, PERSON_HOST = "@g.us", "@s.whatsapp.net"  # how the store writes a chat id; not addresses


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript(
            "CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZSESSIONTYPE INT, ZREMOVED INT, ZCONTACTJID TEXT, ZPARTNERNAME TEXT);"
            "CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INT, ZISFROMME INT, ZMESSAGETYPE INT, ZTEXT TEXT, ZMESSAGEDATE REAL, ZSTANZAID TEXT);")
        self.n = 0

    def chat(self, pk, name, kind=PERSONAL, jid=None):
        self.db.execute("INSERT INTO ZWACHATSESSION VALUES (?,?,0,?,?)", (pk, kind, jid or f"8190{pk}@s.whatsapp.net", name))
        self.db.commit()

    def msg(self, chat, me, text="やあ", ago=3600, kind=0):
        self.n += 1
        self.db.execute("INSERT INTO ZWAMESSAGE (ZCHATSESSION,ZISFROMME,ZMESSAGETYPE,ZTEXT,ZMESSAGEDATE,ZSTANZAID) VALUES (?,?,?,?,?,?)",
                        (chat, int(me), kind, text, NOW - ago - wa.APPLE_EPOCH, f"m{self.n}"))
        self.db.commit()


class Scan(SandboxCase):
    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.tmp, "data", "_desk"), exist_ok=True)
        path = os.path.join(self.tmp, "store.sqlite")
        os.environ["WHATSAPP_STORE"] = path
        self.s = Store(path)
        self.addCleanup(self.s.db.close)

    def titles(self):
        return sorted(t["title"] for t in tasks.open_tasks())

    def test_their_last_message_makes_a_task_for_today(self):
        self.s.chat(1, "相手")
        self.s.msg(1, False, "元気？")
        r = wa.scan(NOW)
        t = tasks.open_tasks()[0]
        self.assertEqual(t["title"], "WhatsApp reply (相手)")
        self.assertEqual(t["source"], "whatsapp")
        self.assertEqual(t["planDate"], wa.today())
        self.assertEqual(r["drafts"][0]["messages"][-1]["text"], "元気？")

    def test_the_task_holds_no_message_text(self):
        self.s.chat(1, "相手")
        self.s.msg(1, False, "ひみつの本文")
        wa.scan(NOW)
        self.assertNotIn("ひみつの本文", json.dumps(tasks.open_tasks(), ensure_ascii=False))

    def test_your_last_message_makes_no_task(self):
        self.s.chat(1, "相手")
        self.s.msg(1, False, ago=7200)
        self.s.msg(1, True)
        wa.scan(NOW)
        self.assertEqual(self.titles(), [])

    def test_groups_and_the_official_account_make_no_task(self):
        self.s.chat(1, "グループ", kind=GROUP, jid="1" + GROUP_HOST)
        self.s.chat(2, "WhatsApp", jid="0" + PERSON_HOST)
        self.s.msg(1, False)
        self.s.msg(2, False)
        wa.scan(NOW)
        self.assertEqual(self.titles(), [])

    def test_a_last_sticker_makes_no_task(self):
        self.s.chat(1, "相手")
        self.s.msg(1, False, ago=7200)
        self.s.msg(1, False, text="", kind=15)
        wa.scan(NOW)
        self.assertEqual(self.titles(), [])

    def test_a_voice_message_makes_a_task_without_a_draft(self):
        self.s.chat(1, "相手")
        self.s.msg(1, False, text="", kind=3)
        r = wa.scan(NOW)
        self.assertEqual(self.titles(), ["WhatsApp reply (相手)"])
        self.assertEqual(r["drafts"], [])
        self.assertEqual(wa.load_items()[tasks.open_tasks()[0]["id"]]["kind"], "voice message")

    def test_an_old_message_makes_no_task(self):
        self.s.chat(1, "相手")
        self.s.msg(1, False, ago=wa.WINDOW + 60)
        wa.scan(NOW)
        self.assertEqual(self.titles(), [])

    def test_a_second_scan_adds_nothing_and_drafts_once(self):
        self.s.chat(1, "相手")
        self.s.msg(1, False)
        first = wa.scan(NOW)
        wa.save([{"ref": first["drafts"][0]["ref"], "summary": "挨拶", "body": "Hi", "body_ja": "やあ"}])
        again = wa.scan(NOW)
        self.assertEqual(again["created"], 0)
        self.assertEqual(again["drafts"], [])
        self.assertEqual(len(tasks.open_tasks()), 1)

    def test_their_next_message_redrafts_the_same_task(self):
        self.s.chat(1, "相手")
        self.s.msg(1, False, ago=7200)
        first = wa.scan(NOW)
        wa.save([{"ref": first["drafts"][0]["ref"], "body": "Hi"}])
        self.s.msg(1, False, "もう一つ")
        again = wa.scan(NOW)
        self.assertEqual(len(tasks.open_tasks()), 1)
        self.assertEqual(again["drafts"][0]["ref"], first["drafts"][0]["ref"])

    def test_replying_closes_the_task(self):
        self.s.chat(1, "相手")
        self.s.msg(1, False, ago=7200)
        tid = wa.scan(NOW)["drafts"][0]["ref"]
        self.s.msg(1, True, "返事")
        self.assertEqual(wa.scan(NOW)["closed"], 1)
        self.assertEqual(tasks.get(tid)["status"], "done")
        self.assertNotIn(tid, wa.load_items())

    def test_a_task_you_closed_stays_closed(self):
        self.s.chat(1, "相手")
        self.s.msg(1, False)
        tid = wa.scan(NOW)["drafts"][0]["ref"]
        tasks.done(tid)
        wa.scan(NOW)
        self.assertEqual(tasks.get(tid)["status"], "done")
        self.assertEqual(self.titles(), [])

    def test_after_a_reply_their_new_message_is_a_new_task(self):
        self.s.chat(1, "相手")
        self.s.msg(1, False, ago=7200)
        tid = wa.scan(NOW)["drafts"][0]["ref"]
        self.s.msg(1, True, ago=3600)
        self.s.msg(1, False, "次の話", ago=60)
        wa.scan(NOW)
        wa.scan(NOW)
        self.assertEqual(tasks.get(tid)["status"], "done")
        self.assertEqual(len(tasks.open_tasks()), 1)

    def test_an_unsaved_number_is_marked(self):
        self.s.chat(1, "‪+81 90 1234 5678‬")
        self.s.msg(1, False)
        self.assertTrue(wa.scan(NOW)["drafts"][0]["unknown"])

    def test_save_ignores_unknown_refs_and_empty_drafts(self):
        self.assertEqual(wa.save([{"ref": "nope", "body": "Hi"}]), 0)
        self.s.chat(1, "相手")
        self.s.msg(1, False)
        ref = wa.scan(NOW)["drafts"][0]["ref"]
        self.assertEqual(wa.save([{"ref": ref, "body": " "}]), 0)


class Titles(Scan):
    def test_the_topic_goes_into_the_title(self):
        self.s.chat(1, "相手")
        self.s.msg(1, False, "来週どう？")
        ref = wa.scan(NOW)["drafts"][0]["ref"]
        wa.save([{"ref": ref, "topic": "来週の予定", "summary": "予定を聞かれた", "body": "Hi"}])
        self.assertEqual(tasks.get(ref)["title"], "WhatsApp返信 来週の予定について（相手）")

    def nudge_task(self, jid="81901" + PERSON_HOST):
        ref = "wa" + hashlib.sha1(jid.encode()).hexdigest()[:16]
        return tasks.add("催促：相手", source="lifeos-02", source_ref=ref)

    def test_a_nudge_gets_the_talk_and_a_draft_and_your_last_word_does_not_close_it(self):
        self.s.chat(1, "相手")
        self.s.msg(1, False, "こんにちは", ago=7200)
        self.s.msg(1, True, "どうですか？")
        t = self.nudge_task()
        r = wa.nudges()
        self.assertEqual(r["drafts"][0]["ref"], t["id"])
        self.assertEqual(wa.load_items()[t["id"]]["texts"], ["相手: こんにちは", "Me: どうですか？"])
        wa.save([{"ref": t["id"], "topic": "前の件", "body": "その後どう？"}])
        self.assertEqual(tasks.get(t["id"])["title"], "WhatsApp催促 前の件について（相手）")
        self.assertEqual(wa.scan(NOW)["closed"], 0)
        self.assertEqual(tasks.get(t["id"])["status"], "notyet")
        self.assertEqual(wa.nudges()["drafts"], [])

    def test_a_closed_nudge_leaves_no_text_behind(self):
        self.s.chat(1, "相手")
        self.s.msg(1, True)
        t = self.nudge_task()
        wa.nudges()
        tasks.done(t["id"])
        wa.scan(NOW)
        self.assertNotIn(t["id"], wa.load_items())


class MailAdd(SandboxCase):
    def setUp(self):
        super().setUp()
        sys.path.insert(0, os.path.join(ROOT, "desk"))
        import sync
        self.merge = sync.merge_mail

    def test_the_afternoon_run_keeps_the_morning_items(self):
        out = self.merge({"items": [{"key": "a", "gmail_url": "u1"}]}, {"summary": "夕方", "items": [{"key": "b", "gmail_url": "u2"}]})
        self.assertEqual(sorted(i["key"] for i in out["items"]), ["a", "b"])
        self.assertEqual(out["summary"], "夕方")

    def test_the_same_mail_stays_one_item_with_its_status_and_draft(self):
        old = {"items": [{"key": "a", "gmail_url": "u1", "status": "sent", "draft": {"id": "d"}}]}
        out = self.merge(old, {"items": [{"key": "a2", "gmail_url": "u1", "status": "pending", "draft": None}]})
        self.assertEqual(len(out["items"]), 1)
        self.assertEqual(out["items"][0]["status"], "sent")
        self.assertEqual(out["items"][0]["draft"], {"id": "d"})


class NoReply(Scan):
    def test_a_message_needing_no_answer_is_put_away_and_stays_away(self):
        self.s.chat(1, "相手")
        self.s.msg(1, False, "またね")
        ref = wa.scan(NOW)["drafts"][0]["ref"]
        self.assertEqual(wa.save([{"ref": ref, "noreply": True}]), 1)
        self.assertEqual(tasks.get(ref)["status"], "archived")
        again = wa.scan(NOW)
        self.assertEqual((again["created"], again["drafts"]), (0, []))

    def test_chat_waiting_on_them_is_a_thread_without_text(self):
        self.s.chat(1, "相手")
        self.s.msg(1, True, "見積もりはどうなりましたか？", ago=4 * 86400)
        self.s.chat(2, "友達")
        self.s.msg(2, False, "返事きたよ")
        rows = {r["subject"]: r for r in wa.waiting_threads(NOW)}
        self.assertEqual((rows["相手"]["lastFrom"], rows["友達"]["lastFrom"]), ("me", "them"))
        self.assertEqual(rows["相手"]["lang"], "ja")
        self.assertNotIn("見積もり", json.dumps(rows, ensure_ascii=False))

    def test_a_closing_word_or_an_old_or_group_chat_is_not_waiting(self):
        self.s.chat(1, "相手")
        self.s.msg(1, True, "ありがとうございます！")
        self.s.chat(2, "昔の人")
        self.s.msg(2, True, "お元気ですか？", ago=40 * 86400)
        self.s.chat(3, "みんな", kind=GROUP, jid="1" + GROUP_HOST)
        self.s.msg(3, True, "集まりませんか？")
        self.assertEqual(wa.waiting_threads(NOW), [])


class EnglishTitles(SandboxCase):
    """New topics are read in English; old Japanese topics keep their Japanese title."""

    def test_english_topic_gets_an_english_title(self):
        self.assertEqual(wa.title_of({"name": "Ana", "topic": "next week"}), "WhatsApp reply: next week (Ana)")
        self.assertEqual(wa.title_of({"name": "Ana", "topic": "the invoice", "nudge": True}), "WhatsApp nudge: the invoice (Ana)")
        self.assertEqual(wa.title_of({"name": "Ana"}), "WhatsApp reply (Ana)")

    def test_japanese_topic_keeps_the_japanese_title(self):
        self.assertEqual(wa.title_of({"name": "相手", "topic": "来週の予定"}), "WhatsApp返信 来週の予定について（相手）")
