import json
import os
import subprocess
import sys
from datetime import datetime

from lifeos.lib import paths, tasks
from lifeos.lib.tools import load_tool
from lifeos.tests.sandbox import SandboxCase

ME = "Me <me@example.com>"
CLI = os.path.join(paths.REPO, "lifeos", "19-daily-note", "cli.py")


def at(day, hour=16, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=paths.HCMC)


class Base(SandboxCase):
    def setUp(self):
        super().setUp()
        self.core = load_tool("19-daily-note", "core")

    def send(self, day=5):
        out = self.core.prepare(at(day))
        self.assertTrue(out["send"])
        self.core.record_sent(out["date"], f"th{day}", f"m{day}", ME, at(day))
        return out["date"]

    def card(self, date):
        return next(t for t in tasks.all_tasks() if t.get("source") == self.core.SOURCE and t["sourceRef"] == date)

    def reply(self, date, text, mid="r1", sender=ME, labels=("SENT", "INBOX")):
        return self.core.save_reply(date, mid, sender, list(labels), text)["status"]


class TestSend(Base):
    def test_first_run_gives_the_mail_and_one_open_card(self):
        out = self.core.prepare(at(5))
        self.assertEqual(out["subject"], "3min Daily Note 2026-11-05")
        self.assertIn("9. Time Liberation", out["body"])
        card = self.card("2026-11-05")
        self.assertEqual(card["status"], "notyet")
        self.assertEqual(card["due"], int(datetime(2026, 11, 5, 23, 59, tzinfo=paths.HCMC).timestamp() * 1000))

    def test_second_run_adds_nothing(self):
        self.send()
        self.assertFalse(self.core.prepare(at(5, 17))["send"])
        self.assertEqual(len([t for t in tasks.all_tasks() if t.get("source") == self.core.SOURCE]), 1)

    def test_failed_send_is_retried_without_a_second_card(self):
        self.assertTrue(self.core.prepare(at(5))["send"])
        self.assertTrue(self.core.prepare(at(5, 17))["send"])
        self.assertEqual(len([t for t in tasks.all_tasks() if t.get("source") == self.core.SOURCE]), 1)

    def test_morning_catch_up_does_not_send(self):
        out = self.core.prepare(at(6, 9))
        self.assertFalse(out["send"])
        self.assertEqual([t for t in tasks.all_tasks() if t.get("source") == self.core.SOURCE], [])


class TestFormat(Base):
    def test_numbers_become_item_names(self):
        got = self.core.format_reply("1. 7時に起きた\n朝はパン\n3) 早く寝ると楽\n")
        self.assertEqual(got, "【1. Daily Log】\n7時に起きた\n朝はパン\n\n【3. Insights】\n早く寝ると楽")

    def test_full_width_numbers(self):
        self.assertIn("【2. Today's Wins】\n歩いた", self.core.format_reply("２．歩いた"))

    def test_no_numbers_stay_as_they_are(self):
        self.assertEqual(self.core.format_reply("今日は疲れた\nでも歩いた"), "今日は疲れた\nでも歩いた")

    def test_kept_template_wording_is_removed_and_blank_items_skipped(self):
        got = self.core.format_reply("1. Daily Log: Wake-up time, 3meals 7時\n2. Today's Wins: What made me happy and what I achieved for presence Tatsuki")
        self.assertEqual(got, "【1. Daily Log】\n7時")

    def test_quotes_are_removed(self):
        for head in ("2026年11月5日(木) 16:00 Me <me@example.com>:", "On Thu, Nov 5, 2026 at 4:00 PM Me <me@example.com> wrote:",
                     "2026/11/05 16:00、Me <me@example.com>のメール:"):
            self.assertEqual(self.core.format_reply(f"歩いた\n\n{head}\n\n> 1. Daily Log\n元の文"), "歩いた")
        self.assertEqual(self.core.format_reply("歩いた\n> 引用\nSent from my iPhone"), "歩いた")

    def test_only_quote_is_empty(self):
        self.assertEqual(self.core.format_reply("> 1. Daily Log: Wake-up time, 3meals"), "")


class TestReply(Base):
    def test_reply_saves_and_completes(self):
        date = self.send()
        self.assertEqual(self.reply(date, "1. 7時"), "saved")
        card = self.card(date)
        self.assertEqual(card["status"], "done")
        self.assertEqual(card["note"], "【1. Daily Log】\n7時")

    def test_same_reply_twice_changes_nothing_and_keeps_hand_edits(self):
        date = self.send()
        self.reply(date, "歩いた")
        tasks.update(self.card(date)["id"], note="手で直した")
        self.assertEqual(self.reply(date, "歩いた"), "already-saved")
        self.assertEqual(self.card(date)["note"], "手で直した")

    def test_second_reply_is_added(self):
        date = self.send()
        self.reply(date, "歩いた")
        self.assertEqual(self.reply(date, "読んだ", mid="r2"), "saved")
        self.assertEqual(self.card(date)["note"], "歩いた\n\n---\n\n読んだ")

    def test_original_mail_is_not_a_reply(self):
        date = self.send()
        self.assertEqual(self.reply(date, "x", mid="m5"), "already-saved")
        self.assertEqual(self.card(date)["status"], "notyet")

    def test_someone_else_is_refused(self):
        date = self.send()
        self.assertEqual(self.reply(date, "x", sender="Other <o@example.com>"), "not-from-owner")
        self.assertEqual(self.reply(date, "x", labels=("INBOX",)), "not-from-owner")  # a forged From has no SENT
        self.assertEqual(self.card(date)["status"], "notyet")

    def test_empty_reply_does_not_complete(self):
        date = self.send()
        self.assertEqual(self.reply(date, "> quote only"), "empty")
        self.assertEqual(self.card(date)["status"], "notyet")

    def test_unknown_day(self):
        self.assertEqual(self.reply("2026-01-01", "x"), "unknown-day")


class TestNextDay(Base):
    def test_unanswered_card_is_archived_and_a_late_reply_completes_it(self):
        date = self.send(5)
        self.core.pending(at(6, 1))
        self.assertEqual(self.card(date)["status"], "archived")
        self.assertEqual(self.reply(date, "遅れた"), "saved")
        self.assertEqual(self.card(date)["status"], "done")

    def test_done_card_stays_done(self):
        date = self.send(5)
        self.reply(date, "歩いた")
        self.core.prepare(at(6))
        self.assertEqual(self.card(date)["status"], "done")

    def test_pending_looks_three_days_back(self):
        for day in (3, 4, 5, 6):
            self.send(day)
        got = self.core.pending(at(6, 17))["threads"]
        self.assertEqual([t["date"] for t in got], ["2026-11-04", "2026-11-05", "2026-11-06"])
        self.assertEqual(got[0]["skipMessageIds"], ["m4"])


class TestCli(Base):
    def test_commands_print_json(self):
        r = subprocess.run([sys.executable, CLI, "pending"], capture_output=True, text=True, env=os.environ)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["threads"], [])
