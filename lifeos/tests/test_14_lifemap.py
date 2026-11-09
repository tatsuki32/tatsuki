"""Life OS 14: at most one question a day, answers go to the area's file, no reminders, patterns."""
import json
import os
import random
from datetime import datetime, timedelta

from lifeos.bot import router
from lifeos.lib import logs, md, paths, tasks
from lifeos.lib.tools import load_tool
from lifeos.tests.desk_import import server, sync
from lifeos.tests.sandbox import SandboxCase

core = load_tool("14-lifemap", "core")
handler = load_tool("14-lifemap", "handler")


def at(day, hhmm):
    return datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H:%M").replace(tzinfo=paths.HCMC)


class Sent:
    def __init__(self, ok=True):
        self.texts, self.ok = [], ok

    def __call__(self, text):
        self.texts.append(text)
        return self.ok


def run_day(day, send, rng=None):
    """Ticks every 15 minutes through the whole day."""
    t = at(day, "00:00")
    while t.strftime("%Y-%m-%d") == day:
        core.tick(t, send, rng)
        t += timedelta(minutes=15)


def ask(send=None, day="2026-11-04", hhmm="10:00"):
    return core.ask_now(at(day, hhmm), send or Sent())


def ask_just_now():
    """A question asked this minute, for the handler, which reads the real clock."""
    ask()
    s = core.load_state()
    s["askedAt"] = paths.now().isoformat(timespec="seconds")
    core._save_state(s)
    return s


def answer(area, day, text, q="問い"):
    rel = core.area_file(area)
    md.append(rel, f"\n## {day} 10:00\n> {q}\n\n{text}\n")


class TestOneQuestionADay(SandboxCase):
    def test_a_full_day_of_ticks_sends_one_question(self):
        send = Sent()
        run_day("2026-11-04", send)
        self.assertEqual(len(send.texts), 1)

    def test_every_planned_time_gets_a_tick(self):
        for seed in range(40):
            send = Sent()
            os.environ["LIFEOS_DATA"] = os.path.join(self.tmp, str(seed))
            run_day("2026-11-04", send, random.Random(seed))
            self.assertEqual(len(send.texts), 1, seed)

    def test_a_new_question_comes_on_the_next_day(self):
        send = Sent()
        run_day("2026-11-04", send)
        run_day("2026-11-05", send)
        self.assertEqual(len(send.texts), 2)

    def test_asking_by_hand_is_also_limited(self):
        send = Sent()
        self.assertTrue(ask(send))
        self.assertIsNone(ask(send, hhmm="11:00"))
        run_day("2026-11-04", send)
        self.assertEqual(len(send.texts), 1)

    def test_a_lost_state_file_does_not_ask_twice(self):
        send = Sent()
        ask(send)
        os.remove(core._state_path())
        run_day("2026-11-04", send)
        self.assertEqual(len(send.texts), 1)

    def test_a_failed_send_is_not_retried_and_does_not_catch_the_next_message(self):
        send = Sent(ok=False)
        run_day("2026-11-04", send)
        self.assertEqual(len(send.texts), 1)
        self.assertIsNone(core.pending(now=at("2026-11-04", "21:00")))

    def test_the_time_is_random_and_inside_the_window(self):
        times = set()
        for seed in range(30):
            t = core._plan(at("2026-11-04", "00:00"), random.Random(seed))
            self.assertTrue("09:00" <= t < "21:00", t)
            times.add(t)
        self.assertGreater(len(times), 10)

    def test_nothing_is_asked_late_at_night(self):
        send = Sent()
        core.tick(at("2026-11-04", "22:00"), send)
        core.tick(at("2026-11-04", "23:45"), send)
        self.assertEqual(send.texts, [])

    def test_waking_up_after_the_planned_time_still_asks(self):
        send = Sent()
        core.tick(at("2026-11-04", "09:00"), send, random.Random(1))
        planned = core.load_state()["planned"]
        core.tick(at("2026-11-04", "20:59"), send)
        self.assertLessEqual(planned, "20:59")
        self.assertEqual(len(send.texts), 1)

    def test_without_telegram_the_question_stays_in_the_outbox(self):
        self.assertTrue(core.ask_now(at("2026-11-04", "10:00")))
        self.assertEqual(len(logs.read("outbox")), 1)

    def test_recent_areas_are_not_repeated(self):
        send = Sent()
        for i in range(12):
            core.ask_now(at(f"2026-11-{i + 10}", "10:00"), send)
        asked = [r["area"] for r in logs.read("lifemap") if r["kind"] == "ask"]
        self.assertEqual(len(asked), 12)
        self.assertEqual(set(asked) - set(core.AREAS), set())
        for i in range(len(asked) - core.RECENT_AREAS):
            window = asked[i:i + core.RECENT_AREAS + 1]
            self.assertEqual(len(set(window)), len(window))

    def test_every_area_has_questions(self):
        self.assertEqual(set(core.QUESTIONS), set(core.AREAS))
        self.assertEqual(len(core.AREAS), 6)


class TestAnswer(SandboxCase):
    def test_the_answer_goes_to_the_file_of_the_area(self):
        ask()
        s = core.load_state()
        self.assertEqual(core.add_answer("よく眠れた", at("2026-11-04", "10:30")), s["area"])
        text = md.read(f"life/{s['area']}.md")
        self.assertIn(f"# {core.AREAS[s['area']]}", text)
        self.assertIn(s["question"], text)
        self.assertIn("よく眠れた", text)

    def test_the_files_are_in_the_data_folder(self):
        ask()
        core.add_answer("答え", at("2026-11-04", "10:30"))
        area = core.load_state()["area"]
        self.assertTrue(os.path.exists(os.path.join(paths.data_dir(), "life", area + ".md")))

    def test_answers_are_appended(self):
        send = Sent()
        ask(send)
        area = core.load_state()["area"]
        core.add_answer("一つ目", at("2026-11-04", "10:30"))
        answer(area, "2026-11-05", "二つ目")
        text = md.read(f"life/{area}.md")
        self.assertIn("一つ目", text)
        self.assertIn("二つ目", text)
        self.assertEqual(text.count(f"# {core.AREAS[area]}\n"), 1)

    def test_only_one_answer_per_question(self):
        ask()
        self.assertTrue(core.add_answer("答え", at("2026-11-04", "10:30")))
        self.assertIsNone(core.add_answer("次のメモ", at("2026-11-04", "10:31")))
        self.assertIsNone(core.pending(now=at("2026-11-04", "10:32")))

    def test_the_window_closes(self):
        ask()
        self.assertTrue(core.pending(now=at("2026-11-04", "12:00")))
        self.assertIsNone(core.pending(now=at("2026-11-04", "12:01")))

    def test_handler_takes_a_message_only_while_a_question_is_open(self):
        os.environ["LIFEOS_TOOLS_ROOT"] = self.tmp  # no other tool gets a say
        self.assertFalse(handler.match("今日のメモ"))
        s = ask_just_now()
        self.assertTrue(handler.match("今日のメモ"))
        self.assertFalse(handler.match("/help"))
        route = router.Route("14-lifemap", "14", "人生マップ", handler.PRIORITY, handler.match, handler.handle)
        reply = router.dispatch("よく眠れた", router.Ctx(), [route])
        self.assertIn(core.AREAS_EN[s["area"]], reply)
        self.assertFalse(handler.match("次のメモ"))

    def test_skip_in_english(self):
        s = ask_just_now()
        self.assertEqual(router.dispatch("Skip", router.Ctx(), [router.Route("14-lifemap", "14", "x", handler.PRIORITY, handler.match, handler.handle)]),
                         "OK. No answer needed.")
        self.assertIsNone(md.read(f"life/{s['area']}.md"))
        self.assertIsNone(core.pending())

    def test_skipping_writes_nothing(self):
        s = ask_just_now()
        self.assertEqual(handler.handle("パス", router.Ctx()), "OK. No answer needed.")
        self.assertIsNone(md.read(f"life/{s['area']}.md"))
        self.assertIsNone(core.pending())

    def test_another_tool_keeps_its_own_messages(self):
        root = os.path.join(self.tmp, "tools", "30-habit")
        os.makedirs(root)
        with open(os.path.join(root, "handler.py"), "w", encoding="utf-8") as f:
            f.write("PRIORITY = 30\ndef match(t):\n    return t.startswith('読書')\ndef handle(t, c):\n    return 'ok'\n")
        os.environ["LIFEOS_TOOLS_ROOT"] = os.path.dirname(root)
        ask_just_now()
        self.assertFalse(handler.match("読書 30分"))
        self.assertTrue(handler.match("楽しかった"))


class TestNoNagging(SandboxCase):
    def test_no_answer_means_no_further_message(self):
        send = Sent()
        run_day("2026-11-04", send)
        run_day("2026-11-05", send)
        run_day("2026-11-06", send)
        self.assertEqual(len(send.texts), 3)
        plain = {core.format_question(a, q) for a in core.AREAS for q in core.QUESTIONS[a]}
        self.assertEqual(set(send.texts) - plain, set())  # the bare question, nothing about yesterday

    def test_questions_look_the_same_after_silence(self):
        for t in [core.format_question(a, core.QUESTIONS[a][0]) for a in core.AREAS]:
            self.assertIn("It is fine not to answer", t)

    def test_no_task_is_ever_created(self):
        run_day("2026-11-04", Sent())
        core.add_answer("答え", at("2026-11-04", "20:00"))
        self.assertEqual(tasks.all_tasks(), [])


class TestStop(SandboxCase):
    def test_pause_stops_questions_and_answers(self):
        ask()
        core.set_paused(True)
        self.assertIsNone(core.pending(now=at("2026-11-04", "10:30")))
        send = Sent()
        run_day("2026-11-05", send)
        self.assertEqual(send.texts, [])
        core.set_paused(False)
        run_day("2026-11-06", send)
        self.assertEqual(len(send.texts), 1)

    def test_stopped_in_tool_json_sends_nothing(self):
        send = Sent()
        real = core.tools.info
        core.tools.info = lambda d: {"status": "stopped"}
        self.addCleanup(setattr, core.tools, "info", real)
        run_day("2026-11-04", send)
        self.assertEqual(send.texts, [])

    def test_tool_json_is_valid(self):
        with open(os.path.join(core.HERE, "tool.json"), encoding="utf-8") as f:
            v = json.load(f)
        self.assertEqual(v["no"], "14")
        self.assertIn(v["status"], ("working", "blocked"))
        for b in v["blockers"]:
            self.assertEqual(set(b), {"what", "why", "needs"})


def desk_answer(text):
    """What the Desk page does: saves the answer on today's record."""
    doc = next(d for d in core.desk_docs() if d["id"].startswith("q-"))
    core._desk_put({**doc, "answer": text, "answeredAt": 1793800000000, "answeredVia": "desk"})
    return doc


class TestDesk(SandboxCase):
    def test_the_question_reaches_the_desk(self):
        ask()
        d = core.desk_docs()[0]
        self.assertEqual(d["id"], "q-2026-11-04")
        self.assertEqual((d["area"], d["question"]), (core.load_state()["area"], core.load_state()["question"]))
        self.assertNotIn("answer", d)

    def test_a_question_that_failed_to_send_can_still_be_answered_on_the_desk(self):
        ask(Sent(ok=False))
        self.assertEqual(len(core.desk_docs()), 1)

    def test_a_desk_answer_reaches_the_file_once(self):
        ask()
        doc = desk_answer("机で答えた")
        core.tick(at("2026-11-04", "10:15"), Sent())
        core.tick(at("2026-11-04", "10:30"), Sent())
        text = md.read(core.area_file(doc["area"]))
        self.assertEqual(text.count("机で答えた"), 1)
        self.assertIn(doc["question"], text)

    def test_a_desk_answer_closes_the_bot_window(self):
        ask()
        desk_answer("机で答えた")
        self.assertIsNone(core.pending(now=at("2026-11-04", "10:30")))
        self.assertIsNone(core.add_answer("あとから", at("2026-11-04", "10:31")))

    def test_a_bot_answer_reaches_the_desk_and_is_not_written_again(self):
        ask()
        area = core.load_state()["area"]
        core.add_answer("ボットで答えた", at("2026-11-04", "10:30"))
        d = core.desk_docs()[0]
        self.assertEqual((d["answer"], d["answeredVia"]), ("ボットで答えた", "telegram"))
        core.tick(at("2026-11-04", "10:45"), Sent())
        self.assertEqual(md.read(core.area_file(area)).count("ボットで答えた"), 1)

    def test_two_answers_before_a_sync_both_stay_in_the_file(self):
        ask()
        area = core.load_state()["area"]
        core.add_answer("ボットの答え", at("2026-11-04", "10:30"))
        desk_answer("オンラインの答え")
        core.tick(at("2026-11-04", "10:45"), Sent())
        text = md.read(core.area_file(area))
        self.assertIn("ボットの答え", text)
        self.assertIn("オンラインの答え", text)

    def test_pass_on_the_desk_writes_nothing_and_closes_the_bot_window(self):
        ask()
        area = core.load_state()["area"]
        core._desk_put({**core.desk_docs()[0], "skipped": True})
        core.tick(at("2026-11-04", "10:15"), Sent())
        self.assertIsNone(md.read(core.area_file(area)))
        self.assertIsNone(core.pending(now=at("2026-11-04", "10:30")))

    def test_pass_on_the_bot_clears_the_desk(self):
        s = ask_just_now()
        handler.handle("パス", router.Ctx())
        self.assertTrue(core.desk_docs()[0]["skipped"])
        self.assertIsNone(md.read(core.area_file(s["area"])))

    def test_pause_clears_todays_question_from_the_desk(self):
        ask()
        core.set_paused(True)
        self.assertTrue(core.desk_docs()[0]["skipped"])

    def test_answers_still_arrive_while_paused(self):
        ask()
        doc = desk_answer("止める前の答え")
        core.set_paused(True)
        core.tick(at("2026-11-04", "11:00"), Sent())
        self.assertIn("止める前の答え", md.read(core.area_file(doc["area"])))

    def test_each_day_has_its_own_record(self):
        run_day("2026-11-04", Sent())
        run_day("2026-11-05", Sent())
        self.assertEqual(sorted(d["id"] for d in core.desk_docs()), ["q-2026-11-04", "q-2026-11-05"])

    def test_the_monthly_insight_reaches_the_desk(self):
        for day in ("2026-10-05", "2026-10-13"):
            answer("work", day, "ストレス")
            answer("money", day, "不安")
        core.tick(at("2026-11-01", "09:00"), Sent(), random.Random(1))
        d = next(x for x in core.desk_docs() if x["id"] == "insight-2026-10")
        self.assertIn("stress", d["text"])

    def test_a_broken_answer_time_does_not_lose_the_answer(self):
        ask()
        doc = core.desk_docs()[0]
        core._desk_put({**doc, "answer": "時刻なし", "answeredAt": "?"})
        core.tick(at("2026-11-04", "10:15"), Sent())
        self.assertIn("時刻なし", md.read(core.area_file(doc["area"])))

    def test_the_desk_knows_the_collection(self):
        self.assertIn("lifemap", server.COLLECTIONS)
        self.assertIn("lifemap", sync.TWO_WAY)

    def test_the_page_has_no_counts_of_what_was_not_answered(self):
        with open(os.path.join(paths.REPO, "desk", "index.html"), encoding="utf-8") as f:
            html = f.read()
        panel = html[html.index("function lifemapPanel"):html.index("function wireLifemap")]
        self.assertIn("data-noauto", panel)  # answers stay out of the automatic links
        for word in ("未回答", "連続", "催促"):
            self.assertNotIn(word, panel)


class TestInsight(SandboxCase):
    def fill(self):
        for day in ("2026-10-05", "2026-10-13"):
            answer("work", day, "締切のストレスが大きい")
            answer("money", day, "支払いの不安がある")
        answer("home", "2026-10-20", "締切の前に引っ越しは無理")

    def test_finds_the_cross_area_pattern(self):
        self.fill()
        text = core.insight("2026-10")
        self.assertIn("Work (stress) and money (worry)", text)
        self.assertIn("same week 2 times", text)

    def test_english_answers_count_too(self):
        for day in ("2026-10-05", "2026-10-13"):
            answer("work", day, "Deadlines are stressful")
            answer("money", day, "I worry about rent")
        self.assertIn("Work (stress) and money (worry)", core.insight("2026-10"))

    def test_shared_words_across_areas(self):
        self.fill()
        self.assertIn('"締切"', core.insight("2026-10"))

    def test_words_of_the_question_do_not_count(self):
        answer("work", "2026-10-05", "晴れ", q="不安はありますか")
        answer("money", "2026-10-05", "雨", q="不安はありますか")
        answer("work", "2026-10-13", "晴れ", q="不安はありますか")
        answer("money", "2026-10-13", "雨", q="不安はありますか")
        self.assertIsNone(core.insight("2026-10"))

    def test_one_shared_week_is_not_a_pattern(self):
        answer("work", "2026-10-05", "ストレス")
        answer("money", "2026-10-05", "不安")
        self.assertIsNone(core.insight("2026-10"))

    def test_other_months_are_ignored(self):
        self.fill()
        self.assertIsNone(core.insight("2026-09"))

    def test_at_most_three_lines(self):
        for a in core.AREAS:
            for w in ("共通語彙", "別語彙", "第三語彙", "第四語彙"):
                answer(a, "2026-10-05", w)
        self.assertEqual(len(core.find_patterns(core.entries("2026-10"))), core.MAX_PATTERNS)
        self.assertEqual(core.insight("2026-10", finder=lambda items: ["a", "b", "c", "d"]).count("\n- "), 3)

    def test_the_finder_can_be_replaced(self):
        answer("work", "2026-10-05", "何か")
        self.assertIn("差し替え", core.insight("2026-10", finder=lambda items: [f"差し替え{len(items)}"]))

    def test_sent_once_a_month_and_saved(self):
        self.fill()
        send = Sent()
        run_day("2026-11-01", send)
        run_day("2026-11-02", send)
        monthly = [t for t in send.texts if "Life map" in t]
        self.assertEqual(len(monthly), 1)
        self.assertIn("2026-10", md.read("life/insights/2026-10.md"))
        self.assertEqual(len([t for t in send.texts if t.startswith("About ")]), 2)

    def test_a_quiet_month_sends_nothing(self):
        send = Sent()
        core.tick(at("2026-11-01", "09:00"), send, random.Random(3))
        self.assertEqual([t for t in send.texts if "Life map" in t], [])
        self.assertIsNone(md.read("life/insights/2026-10.md"))
