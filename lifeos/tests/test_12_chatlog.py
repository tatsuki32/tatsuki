import contextlib
import io
import json
import os
import shutil
from datetime import datetime
from unittest import mock

from lifeos.bot import router
from lifeos.lib import logs, md, paths, tasks
from lifeos.lib.tools import load_tool
from lifeos.tests.sandbox import SandboxCase

parse = load_tool("12-chatlog", "parse")
core = load_tool("12-chatlog", "core")
gcal = core.gcal
handler = load_tool("12-chatlog", "handler")
brief = load_tool("12-chatlog", "brief")
chatlog = load_tool("12-chatlog", "chatlog")

NOW = datetime(2026, 11, 4, 14, 0, tzinfo=parse.HCMC)  # a Wednesday


def ms(y, mo, d, h=23, mi=59):
    return int(datetime(y, mo, d, h, mi, tzinfo=parse.HCMC).timestamp() * 1000)


class TestExpenseReading(SandboxCase):
    def read(self, text):
        kind, found = parse.classify(text, NOW)
        self.assertEqual(kind, "expense", text)
        return found["item"], found["amount"], found["currency"], found["guessed"]

    def test_units(self):
        cases = {
            "ランチ 3万ドン": ("ランチ", 30000, "VND", False),
            "ランチ 3万円": ("ランチ", 30000, "JPY", False),
            "コーヒー 3万5千ドン": ("コーヒー", 35000, "VND", False),
            "コーヒー 3.5万ドン": ("コーヒー", 35000, "VND", False),
            "本 1,500円": ("本", 1500, "JPY", False),
            "本 ¥1500": ("本", 1500, "JPY", False),
            "本 ￥１５００": ("本", 1500, "JPY", False),
            "昼 30.000đ": ("昼", 30000, "VND", False),
            "昼 200000 VND": ("昼", 200000, "VND", False),
            "昼 50kドン": ("昼", 50000, "VND", False),
            "本に三千円": ("本", 3000, "JPY", False),
            "3万ドン": ("品目なし", 30000, "VND", False),
            "ランチ 3万ドン 払った": ("ランチ", 30000, "VND", False),
            "3万ドン ランチ": ("ランチ", 30000, "VND", False),
        }
        for text, want in cases.items():
            self.assertEqual(self.read(text), want, text)

    def test_no_unit_is_a_guess_by_size(self):
        self.assertEqual(self.read("ランチ 50000"), ("ランチ", 50000, "VND", True))
        self.assertEqual(self.read("ランチ 1500"), ("ランチ", 1500, "JPY", True))
        self.assertEqual(self.read("ランチ 3万"), ("ランチ", 30000, "VND", True))

    def test_not_an_expense(self):
        for text in ("歩数 8000", "給料 20万円 入った", "3万円もらった", "3万円の請求書を送る", "3万円ってどう？",
                     "ランチ 5ドル", "ランチ 3万ドン コーヒー 5万ドン", "メモ 3万円のカバン", "こんにちは", "",
                     "ランチ 3万ドン\n夜 5万ドン"):
            self.assertEqual(parse.classify(text, NOW), (None, None), text)


class TestDeadlineReading(SandboxCase):
    def read(self, text):
        kind, found = parse.classify(text, NOW)
        self.assertEqual(kind, "deadline", text)
        return found["title"], found["due"]

    def test_dates(self):
        cases = {
            "明日 資料 締切": ms(2026, 11, 5),
            "今日 資料 締切": ms(2026, 11, 4),
            "明後日 資料 締切": ms(2026, 11, 6),
            "3日後 資料 締切": ms(2026, 11, 7),
            "2週間後 資料 締切": ms(2026, 11, 18),
            "金曜 資料 締切": ms(2026, 11, 6),
            "水曜 資料 締切": ms(2026, 11, 4),
            "来週金曜 資料 締切": ms(2026, 11, 13),
            "11/10 資料 締切": ms(2026, 11, 10),
            "11月10日 資料 締切": ms(2026, 11, 10),
            "11/1 資料 締切": ms(2027, 11, 1),  # already past: next year
            "2027/1/2 資料 締切": ms(2027, 1, 2),
            "15日 資料 締切": ms(2026, 11, 15),
            "3日 資料 締切": ms(2026, 12, 3),
            "月末 資料 締切": ms(2026, 11, 30),
            "来月末 資料 締切": ms(2026, 12, 31),
            "明日 15時 資料 締切": ms(2026, 11, 5, 15, 0),
            "明日 午後3時半 資料 締切": ms(2026, 11, 5, 15, 30),
            "11/10 17:00 資料 締切": ms(2026, 11, 10, 17, 0),
        }
        for text, due in cases.items():
            self.assertEqual(self.read(text), ("資料", due), text)

    def test_word_order_and_words(self):
        for text in ("資料 明日 締切", "明日の資料の締切", "資料の締切は明日", "資料 〆切 明日", "明日までに資料 締め切り",
                     "資料 明日 期限", "締切: 明日 資料", "資料 明日 deadline"):
            self.assertEqual(self.read(text), ("資料", ms(2026, 11, 5)), text)

    def test_deadline_wins_over_money(self):
        self.assertEqual(self.read("家賃 500万ドン 明日 締切"), ("家賃 500万ドン", ms(2026, 11, 5)))

    def test_no_date_keeps_the_words(self):
        self.assertEqual(self.read("資料 締切"), ("資料", None))
        self.assertEqual(self.read("2/30 資料 締切"), ("2/30 資料", None))

    def test_not_a_deadline(self):
        for text in ("締切を延ばしてもらう 明日", "資料の締切が近い", "期限切れの牛乳", "締切いつ？", "明日 締切"):
            self.assertEqual(parse.classify(text, NOW), (None, None), text)

    def test_list_words(self):
        for text in ("締切", "締切一覧", "〆切", "締め切りは?"):
            self.assertEqual(parse.classify(text, NOW)[0], "list", text)

    def test_countdown(self):
        self.assertEqual(parse.countdown(ms(2026, 11, 7), NOW), "あと3日")
        self.assertEqual(parse.countdown(ms(2026, 11, 4), NOW), "今日まで")
        self.assertEqual(parse.countdown(ms(2026, 11, 2), NOW), "2日過ぎ")
        self.assertEqual(parse.when(ms(2026, 11, 5)), "11/5(木)")
        self.assertEqual(parse.when(ms(2026, 11, 5, 15, 0)), "11/5(木) 15:00")


class Chat(SandboxCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(paths, "now", return_value=NOW)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(gcal.set_writer, None)
        # only this tool, whatever else is in the checkout
        self.root = os.path.join(self.tmp, "tools")
        os.makedirs(self.root)
        os.environ["LIFEOS_TOOLS_ROOT"] = self.root

    def say(self, text):
        self.assertTrue(handler.match(text), text)
        return handler.handle(text, router.Ctx())

    def habit_tool(self, status="working"):
        d = os.path.join(self.root, "07-habits")
        os.makedirs(d)
        with open(os.path.join(d, "tool.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "habits", "status": status}, f)
        with open(os.path.join(d, "handler.py"), "w", encoding="utf-8") as f:
            f.write('def match(t): return "読書" in t\ndef handle(t, ctx): return "07:" + t\n')


class TestRecording(Chat):
    def test_expense_goes_to_log_and_markdown_in_one_line(self):
        reply = self.say("ランチ 3万ドン")
        self.assertEqual(reply, "支出：ランチ 30,000ドン")
        rec = logs.read("expenses")[0]
        self.assertEqual((rec["item"], rec["amount"], rec["currency"]), ("ランチ", 30000, "VND"))
        self.assertIn("ランチ 30,000ドン", md.read("logs/expenses.md"))
        self.assertTrue(paths.data_path("logs", "expenses.md").startswith(os.environ["LIFEOS_DATA"]))

    def test_guess_is_said_out_loud(self):
        self.assertEqual(self.say("ランチ 1500"), "支出：ランチ 1,500円（通貨は推定）")

    def test_deadline_is_a_desk_task_with_due(self):
        reply = self.say("明日 資料 締切")
        self.assertEqual(reply, "締切：資料 11/5(木) あと1日・カレンダー待ち")
        self.assertNotIn("\n", reply)
        t = tasks.open_tasks()[0]
        self.assertEqual((t["title"], t["due"], t["status"], t["source"]), ("資料", ms(2026, 11, 5), "notyet", "lifeos-12"))

    def test_same_deadline_twice_is_one_task(self):
        self.say("明日 資料 締切")
        self.assertIn("登録済み", self.say("明日 資料 締切"))
        self.assertEqual(len(tasks.open_tasks()), 1)
        self.assertEqual(len(gcal.pending()), 1)

    def test_no_date_is_kept_and_said(self):
        self.assertIn("日付なし", self.say("資料 締切"))
        self.assertIsNone(tasks.open_tasks()[0]["due"])
        self.assertEqual(gcal.pending(), [])

    def test_list_counts_down(self):
        self.assertEqual(self.say("締切"), "締切はありません")
        self.say("3日後 資料 締切")
        self.say("明日 見積 締切")
        self.assertEqual(self.say("締切一覧"), "11/5(木) あと1日　見積\n11/7(土) あと3日　資料")

    def test_brief_shows_three_within_a_week(self):
        self.assertEqual(brief.lines("2026-11-04"), [])
        for i, title in enumerate(("甲", "乙", "丙", "丁"), 1):
            self.say(f"{i}日後 {title} 締切")
        self.say("11/30 遠い 締切")
        self.assertEqual(brief.lines("2026-11-04"), ["締切：甲 あと1日", "締切：乙 あと2日", "締切：丙 あと3日"])

    def test_other_lines_are_left_alone(self):
        self.assertFalse(handler.match("今日はいい天気"))
        self.assertIsNone(core.record("今日はいい天気", router.Ctx()))
        self.assertEqual(logs.read("expenses"), [])


class TestCalendar(Chat):
    def test_waits_when_not_connected(self):
        self.say("明日 資料 締切")
        waiting = gcal.pending()
        self.assertEqual((waiting[0]["title"], waiting[0]["due"]), ("資料", ms(2026, 11, 5)))

    def test_swapped_in_writer_gets_old_and_new(self):
        self.say("明日 古い 締切")
        calls = []
        gcal.set_writer(lambda title, due, ref: calls.append((title, due, ref)))
        self.assertEqual(self.say("明日 資料 締切"), "締切：資料 11/5(木) あと1日・カレンダー記入済み")
        self.assertEqual([c[0] for c in calls], ["古い", "資料"])
        self.assertEqual(gcal.pending(), [])
        self.assertEqual(calls[1][2], [t for t in tasks.open_tasks() if t["title"] == "資料"][0]["id"])

    def test_failed_write_keeps_the_task_and_the_queue(self):
        def boom(*a):
            raise OSError("no network")
        gcal.set_writer(boom)
        self.assertIn("カレンダー待ち", self.say("明日 資料 締切"))
        self.assertEqual(len(tasks.open_tasks()), 1)
        self.assertEqual(len(gcal.pending()), 1)

    def test_writer_from_environment(self):
        os.environ["LIFEOS_CALENDAR_WRITER"] = "builtins:print"
        self.assertTrue(gcal.connected())
        del os.environ["LIFEOS_CALENDAR_WRITER"]
        self.assertFalse(gcal.connected())

    def test_outside_scheduler_can_mark_done(self):
        self.say("明日 資料 締切")
        gcal.mark_done(gcal.pending()[0]["ref"])
        self.assertEqual(gcal.pending(), [])


class TestHabits(Chat):
    def test_held_while_07_is_absent(self):
        self.assertIn("預かりました", self.say("読書30分"))
        self.assertEqual(logs.read("habits-pending")[0]["text"], "読書30分")
        self.assertEqual(logs.read("habits"), [])

    def test_left_to_07_by_the_router(self):
        self.habit_tool()
        self.assertFalse(handler.match("読書30分"))
        self.assertTrue(handler.match("ランチ 3万ドン"))

    def test_held_while_07_is_stopped(self):
        self.habit_tool(status="stopped")
        self.assertTrue(handler.match("読書30分"))

    def test_handed_over_when_called_directly(self):
        self.habit_tool()
        self.assertEqual(core.record("読書30分", router.Ctx()), "07:読書30分")

    def test_never_mistaken_for_money(self):
        self.assertEqual(parse.classify("読書30分", NOW)[0], "habit")
        self.assertEqual(parse.classify("ジョギング 5km", NOW)[0], "habit")


class TestStandalone(Chat):
    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = chatlog.main(list(argv))
        return code, out.getvalue().strip()

    def test_cli(self):
        self.assertEqual(self.run_cli("ランチ", "3万ドン"), (0, "支出：ランチ 30,000ドン"))
        self.assertEqual(self.run_cli("今日はいい天気")[1], "記録する形として読めませんでした。何も記録していません")
        self.run_cli("3日後 資料 締切")
        self.assertIn("あと3日　資料", self.run_cli("--list")[1])
        ref = json.loads(self.run_cli("--calendar-pending")[1])[0]["ref"]
        self.run_cli("--calendar-done", ref)
        self.assertEqual(json.loads(self.run_cli("--calendar-pending")[1]), [])
        self.run_cli("読書30分")
        self.assertIn("読書30分", self.run_cli("--habits")[1])

    def test_through_the_router_and_stopping(self):
        d = os.path.join(self.root, "12-chatlog")
        os.symlink(os.path.join(paths.REPO, "lifeos", "12-chatlog"), d)
        found = router.discover()
        self.assertEqual([r.slug for r in found.routes], ["12-chatlog"])
        self.assertEqual(found.routes[0].priority, 40)
        self.assertEqual(router.dispatch("ランチ 3万ドン", router.Ctx(), found.routes), "支出：ランチ 30,000ドン")
        self.assertEqual(router.dispatch("今日はいい天気", router.Ctx(), found.routes), router.UNROUTED)

    def test_stopped_tool_is_not_loaded(self):
        d = os.path.join(self.root, "12-chatlog")
        shutil.copytree(os.path.join(paths.REPO, "lifeos", "12-chatlog"), d, ignore=shutil.ignore_patterns("__pycache__"))
        with open(os.path.join(d, "tool.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "チャット記録", "status": "stopped"}, f)
        self.assertEqual(router.discover().routes, [])


class TestSpokenMemo(SandboxCase):
    def test_one_line_is_still_taken(self):
        self.assertTrue(load_tool("12-chatlog", "handler").match("ランチ 3万ドン"))

    def test_several_sentences_are_left_for_the_voice_memo_tool(self):
        h = load_tool("12-chatlog", "handler")
        self.assertFalse(h.match("牛乳を買う。ランチは3万ドンだった。"))
        self.assertFalse(h.match("ランチ 3万ドン\n明日 資料 締切"))
