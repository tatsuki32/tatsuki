"""写真の取り込み: the same photo is one record, an odd value waits for review, and a failure never loses a photo."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desk"))
sys.path.insert(0, ROOT)
import lifelog as ll

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000" + "1f15c4890000000a49444154789c6300010000050001" + "0d0a2db40000000049454e44ae426082")


def sleep_record(total=430, day=None):
    return {"kind": "sleep", "date": day or ll.today(), "label": "", "items": [], "uncertain": [],
            "values": [{"key": "total_min", "number": total, "text": None}, {"key": "bedtime", "number": None, "text": "22:15"}]}


USAGE = {"model": ll.MODEL, "input": 2500, "output": 1000}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.environ["LIFELOG_DATA"] = self.tmp
        self.addCleanup(os.environ.pop, "LIFELOG_DATA", None)
        self.addCleanup(os.environ.pop, "ANTHROPIC_API_KEY", None)
        self.src = os.path.join(self.tmp, "in.png")
        with open(self.src, "wb") as f:
            f.write(PNG)

    def put(self, source="telegram", ref=None):
        return ll.add(self.src, source, ref)


class Queue(Base):
    def test_same_bytes_from_both_doors_are_one_photo(self):
        _, first = self.put("telegram")
        entry, second = self.put("drive", "drive-1")
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(entry["refs"], ["drive-1"])
        self.assertEqual(ll.known_refs(), ["drive-1"])

    def test_drive_download_is_queued_with_its_id(self):
        import base64
        saved = os.path.join(self.tmp, "saved.json")
        with open(saved, "w") as f:
            json.dump({"content": base64.b64encode(PNG).decode()}, f)
        self.assertEqual(ll.main(["add-json", "--ref", "drive-9", saved]), 0)
        self.assertEqual(ll.known_refs(), ["drive-9"])
        self.assertEqual(ll.status()["waiting"], 1)

    def test_photo_path_takes_only_our_names(self):
        self.assertIsNone(ll.photo_path("../secrets.json"))
        self.assertIsNone(ll.photo_path("a" * 32 + ".exe"))


class Reading(Base):
    def test_reads_and_saves_once(self):
        self.put()
        r = ll.process(call=lambda p, n: ([sleep_record()], USAGE))
        self.assertEqual(len(r["read"]), 1)
        self.assertEqual(r["read"][0]["status"], "confirmed")
        self.assertEqual(ll.process(call=lambda p, n: self.fail("read twice"))["read"], [])

    def test_no_key_keeps_photo_waiting(self):
        self.put()
        r = ll.process()
        self.assertIn("API キー", r["stopped"])
        self.assertEqual(ll.status()["waiting"], 1)

    def test_refusal_is_recorded_not_retried(self):
        self.put()

        def refuse(p, n):
            raise ll.Refused("断られました")
        r = ll.process(call=refuse)
        self.assertEqual(r["failed"], 1)
        self.assertEqual(ll.status()["failed"], 1)
        self.assertEqual(ll.status()["waiting"], 0)

    def test_monthly_limit_stops_before_spending(self):
        self.put()
        ll.set_settings({"monthlyLimitUsd": 0})
        r = ll.process(call=lambda p, n: self.fail("spent over the limit"))
        self.assertIn("上限", r["stopped"])
        self.assertEqual(ll.status()["waiting"], 1)

    def test_usage_is_counted(self):
        self.put()
        ll.process(call=lambda p, n: ([sleep_record()], USAGE))
        self.assertEqual(ll.month_usage(), {"photos": 1, "usd": 0.03})

    def test_nothing_readable_is_not_a_record(self):
        self.put()
        r = ll.process(call=lambda p, n: ([], USAGE))
        self.assertEqual(r["empty"], 1)
        self.assertEqual(ll.read_json(ll.bp("records.json"), {}), {})


class Check(Base):
    def test_out_of_range_goes_to_review(self):
        self.assertEqual(ll.check(sleep_record(total=9000), ll.today())["status"], "review")

    def test_bad_time_goes_to_review(self):
        rec = sleep_record()
        rec["values"][1]["text"] = "25:99"
        self.assertEqual(ll.check(rec, ll.today())["doubts"], ["bedtime"])

    def test_future_date_goes_to_review(self):
        self.assertIn("date", ll.check(sleep_record(day="2099-01-01"), ll.today())["doubts"])

    def test_unknown_keys_and_kinds_are_dropped(self):
        rec = sleep_record()
        rec["values"].append({"key": "run_this", "number": 1, "text": None})
        self.assertNotIn("run_this", ll.check(rec, ll.today())["values"])
        self.assertIsNone(ll.check({"kind": "shell", "values": []}, ll.today()))

    def test_meal_photo_is_marked_estimated(self):
        rec = {"kind": "meal_photo", "date": None, "label": "カレー", "items": [], "uncertain": [], "values": [{"key": "energy_kcal", "number": 700, "text": None}]}
        got = ll.check(rec, "2026-11-01")
        self.assertTrue(got["estimated"])
        self.assertEqual(got["date"], "2026-11-01")

    def test_receipt_needs_date_amount_and_currency(self):
        rec = {"kind": "receipt", "date": None, "label": "店", "items": [], "uncertain": [], "values": [{"key": "amount", "number": 10, "text": None}]}
        self.assertEqual(ll.check(rec, ll.today())["doubts"], ["currency", "date"])


def stored(day, kind, values, status="confirmed", at=1, **more):
    return {"id": f"{kind}-{day}-{at}", "kind": kind, "date": day, "values": values, "status": status, "at": at, "label": "", **more}


class View(Base):
    def save(self, *recs):
        ll.write_json(ll.bp("records.json"), {r["id"]: r for r in recs})

    def row(self, key, day="2026-11-05"):
        for g in ll.view(day)["groups"]:
            for r in g["rows"]:
                if r["key"] == key:
                    return r
        return None

    def test_latest_change_rate_and_average(self):
        self.save(stored("2026-11-03", "body", {"weight_kg": 66}), stored("2026-11-05", "body", {"weight_kg": 65}))
        ll.set_target("weight_kg", 62, "below")
        r = self.row("weight_kg")
        self.assertEqual((r["value"], r["change"], r["rate"], r["met"]), (65, -1, 104.8, False))
        self.assertEqual(r["avg"]["3"], 65.5)
        self.assertIsNone(self.row("steps"))

    def test_review_stays_out_of_totals(self):
        self.save(stored("2026-11-05", "steps", {"steps": 99999}, status="review"))
        self.assertIsNone(self.row("steps"))
        self.assertEqual(len(ll.view("2026-11-05")["review"]), 1)

    def test_day_screen_wins_over_photos_and_photos_add_up(self):
        self.save(stored("2026-11-05", "meal_photo", {"energy_kcal": 500}, at=1), stored("2026-11-05", "meal_photo", {"energy_kcal": 300}, at=2),
                  stored("2026-11-04", "meal_photo", {"energy_kcal": 100}, at=3), stored("2026-11-04", "meal_screen", {"energy_kcal": 1800}, at=4))
        v = ll.view("2026-11-05")
        hist = dict(self.row("energy_kcal")["history"])
        self.assertEqual(hist, {"2026-11-04": 1800, "2026-11-05": 800})
        self.assertEqual(v["estimatedDays"], ["2026-11-05"])

    def test_edit_checks_range_then_confirm(self):
        self.save(stored("2026-11-05", "steps", {"steps": 999999}, status="review", doubts=["steps"]))
        rid = "steps-2026-11-05-1"
        self.assertIn("error", ll.update_record(rid, "confirm", {"values": {"steps": 999999}}))
        out = ll.update_record(rid, "confirm", {"values": {"steps": 9000}})
        self.assertEqual((out["status"], out["values"]["steps"]), ("confirmed", 9000))


class Detail(View):
    def test_all_sleep_keys_pass_the_check(self):
        vals = {"total_min": 294, "deep_min": 40, "light_min": 200, "rem_min": 54, "wake_count": 6, "sleep_score": 62, "hrv_avg": 45, "spo2_avg": 96, "hr_avg": 58, "breath_rate": 15, "breath_quality": 80, "nap_min": 0, "night_min": 294}
        rec = {"kind": "sleep", "date": ll.today(), "label": "", "items": [], "uncertain": [], "values": [{"key": k, "number": v, "text": None} for k, v in vals.items()]}
        got = ll.check(rec, ll.today())
        self.assertEqual(got["values"], vals)
        self.assertEqual(got["status"], "confirmed")

    def test_two_photos_of_one_night_join_by_metric(self):
        self.save(stored("2026-11-05", "sleep", {"total_min": 294}, at=1), stored("2026-11-05", "sleep", {"hrv_avg": 45}, at=2))
        self.assertEqual(self.row("total_min")["value"], 294)
        self.assertEqual(self.row("hrv_avg")["value"], 45)

    def test_two_workouts_in_a_day_add_up_and_keep_exercises(self):
        rec = {"kind": "workout", "date": ll.today(), "label": "", "items": ["ベンチプレス 60kg x 10 x 3"], "uncertain": [], "values": [{"key": "workout_min", "number": 45, "text": None}]}
        self.assertEqual(ll.check(rec, ll.today())["items"], ["ベンチプレス 60kg x 10 x 3"])
        self.save(stored("2026-11-05", "workout", {"workout_min": 45}, at=1), stored("2026-11-05", "workout", {"workout_min": 20}, at=2))
        self.assertEqual(self.row("workout_min")["value"], 65)

    def test_step_screen_and_study_details_reach_the_page(self):
        self.save(stored("2026-11-05", "steps", {"steps": 9000, "distance_km": 6.2, "active_kcal": 300}), stored("2026-11-05", "screen_time", {"screen_min": 252, "top_genre": "SNS"}),
                  stored("2026-11-05", "study", {"minutes": 30}, at=1), stored("2026-11-05", "study", {"minutes": 15}, at=2))
        self.assertEqual(self.row("distance_km")["value"], 6.2)
        self.assertEqual(self.row("active_kcal")["value"], 300)
        self.assertEqual(self.row("screen_min")["value"], 252)
        self.assertEqual(self.row("minutes")["value"], 45)
        rec = {"kind": "screen_time", "date": ll.today(), "label": "", "items": ["LINE 40"], "uncertain": [], "values": [{"key": "screen_min", "number": 252, "text": None}, {"key": "peak_hour", "number": None, "text": "17:00-18:00"}]}
        got = ll.check(rec, ll.today())
        self.assertEqual((got["items"], got["values"]["peak_hour"]), (["LINE 40"], "17:00-18:00"))

    def test_the_answer_lists_every_value(self):
        vals = {k: 1 for k in ll.NUTRIENTS}
        text = ll.describe({"kind": "meal_photo", "date": "2026-11-05", "label": "", "values": vals, "items": ["ごはん 約150g"], "estimated": True, "photo": "a.jpg", "status": "confirmed"})
        for rule in ll.NUTRIENTS.values():
            self.assertIn(rule["label"], text)
        self.assertIn("ごはん 約150g", text)
        self.assertNotIn("ほか", text)

    def test_meal_score_follows_the_sheet(self):
        ll.write_json(ll.bp("targets.json"), {"energy_kcal": {"target": 2000}, "protein_g": {"target": 100}})
        self.save(stored("2026-11-05", "meal_screen", {"energy_kcal": 1000, "protein_g": 150}))
        self.assertEqual(self.row("meal_score")["value"], 75.0)

    def test_reading_again_replaces_unless_the_owner_corrected(self):
        self.put()
        ll.process(call=lambda p, n: ([sleep_record(total=100)], USAGE))
        pid = list(ll.read_json(ll.lp("queue.json"), {}))[0]
        ll.reread([pid])
        out = ll.process(call=lambda p, n: ([sleep_record(total=200)], USAGE))
        self.assertEqual(out["read"][0]["values"]["total_min"], 200)
        ll.update_record(out["read"][0]["id"], "confirm", {"values": {"total_min": 300}})
        ll.reread([pid])
        out = ll.process(call=lambda p, n: ([sleep_record(total=400)], USAGE))
        self.assertEqual(out["read"][0]["values"]["total_min"], 300)
        self.assertEqual(ll.status()["done"], 1)


class Typed(Base):
    def setUp(self):
        super().setUp()
        os.environ["LIFEOS_DATA"] = os.path.join(self.tmp, "lifeos")
        self.addCleanup(os.environ.pop, "LIFEOS_DATA", None)
        from lifeos.lib import tools
        self.h = tools.load_file(os.path.join(ROOT, "lifeos", "20-lifelog"), "handler")

    def test_records_match_and_ideas_do_not(self):
        for yes in ("体重65.2", "昼 カレー大盛り食べた", "ベンチ60kg 10回3セット", "昨日7時間寝た", "歩数 12000", "筋トレ：胸", "記録：水2L", "朝食 おにぎり"):
            self.assertTrue(self.h.match(yes), yes)
        for no in ("筋トレした", "読書30分", "朝食を見直したい", "体重を減らす方法は？", "新しいアプリのアイデア", "/start", "task: 買い物"):
            self.assertFalse(self.h.match(no), no)

    def test_typed_record_needs_no_photo_and_keeps_the_words(self):
        ll.add_text("体重65.2", "telegram")
        seen = {}

        def call(p, n):
            seen.update(path=p, note=n)
            return [{"kind": "body", "date": None, "label": "", "items": [], "uncertain": [], "values": [{"key": "weight_kg", "number": 65.2, "text": None}]}], USAGE
        r = ll.process(call=call)
        self.assertEqual(seen, {"path": None, "note": "体重65.2"})
        self.assertEqual((r["read"][0]["photo"], r["read"][0]["status"], r["read"][0]["date"]), (None, "confirmed", ll.today()))

    def test_same_words_twice_are_two_records(self):
        ll.add_text("朝食 おにぎり", "telegram")
        ll.add_text("朝食 おにぎり", "telegram")
        self.assertEqual(ll.status()["waiting"], 2)

    def test_typed_receipt_takes_todays_date_but_still_needs_a_currency(self):
        rec = {"kind": "receipt", "date": None, "label": "カフェ", "items": [], "uncertain": [], "values": [{"key": "amount", "number": 5, "text": None}]}
        self.assertEqual(ll.check(rec, ll.today(), typed=True)["doubts"], ["currency"])

    def test_no_key_keeps_the_words_and_says_so(self):
        from lifeos.bot import router
        out = self.h.handle("体重65.2", router.Ctx(source="telegram"))
        self.assertIn("API キー", out)
        self.assertEqual(ll.status()["waiting"], 1)

    def test_not_a_record_goes_on_to_the_other_tools(self):
        from unittest import mock
        from lifeos.bot import router
        with mock.patch.object(ll, "call_claude", lambda p, n: ([], USAGE)), mock.patch.object(ll.process, "__defaults__", (ll.BATCH, lambda p, n: ([], USAGE))):
            with mock.patch.object(router, "dispatch", lambda text, ctx, routes: "他のツール") as d:
                self.assertEqual(self.h.handle("歩数計の話をした 3つ", router.Ctx(source="telegram")), "他のツール")


class Receipts(Base):
    def setup(self, activities):
        ll.write_json(os.path.join(self.tmp, "wise", "activities.json"), {a["id"]: a for a in activities})
        rec = stored("2026-11-05", "receipt", {"amount": 12.5, "currency": "AUD", "category": "食費"})
        ll.write_json(ll.receipts_path(), {rec["id"]: rec})
        return rec["id"]

    def act(self, id_, **more):
        return {"id": id_, "value": 12.5, "currency": "AUD", "incoming": False, "createdOn": "2026-11-04T10:00:00Z", "status": "COMPLETED", "title": "<b>Coles</b>", **more}

    def state(self):
        return ll.receipts_view("2026-11-05"[:7])

    def test_one_match_is_not_new_spending(self):
        self.setup([self.act("a")])
        v = self.state()
        self.assertEqual(v["rows"][0]["matchState"], "matched")
        self.assertEqual(v["rows"][0]["candidates"][0]["title"], "Coles")
        self.assertEqual(v["totals"]["AUD"]["new"], 0)

    def test_two_matches_ask_and_none_is_a_suggestion(self):
        self.setup([self.act("a"), self.act("b")])
        self.assertEqual(self.state()["rows"][0]["matchState"], "choose")
        self.setup([self.act("a", incoming=True), self.act("b", currency="USD"), self.act("c", createdOn="2026-10-01T00:00:00Z")])
        v = self.state()
        self.assertEqual(v["rows"][0]["matchState"], "new")
        self.assertEqual(v["totals"]["AUD"]["new"], 12.5)

    def test_owner_decision_stands(self):
        rid = self.setup([])
        ll.settle_receipt(rid, "added")
        v = self.state()
        self.assertEqual(v["rows"][0]["matchState"], "added")
        self.assertEqual(v["totals"]["AUD"]["added"], 12.5)


class Bot(Base):
    def setUp(self):
        super().setUp()
        os.environ["LIFEOS_DATA"] = os.path.join(self.tmp, "lifeos")
        self.addCleanup(os.environ.pop, "LIFEOS_DATA", None)
        from lifeos.bot import run
        self.run_mod = run
        self.sent = []
        src = self.src

        class Client:
            def __init__(s):
                s.sent = self.sent

            def send_message(s, chat, text):
                s.sent.append((chat, text))

            def download(s, file_id, dest):
                shutil.copyfile(src, dest)
                return dest
        self.client = Client()

    def test_photo_from_owner_is_queued_and_reported(self):
        self.run_mod.handle_update({"message": {"chat": {"id": 7}, "photo": [{"file_id": "s"}, {"file_id": "L"}], "caption": "朝食"}}, self.client, "7")
        self.assertEqual(ll.status()["waiting"], 1)
        self.assertIn("API キー", self.sent[-1][1])
        self.assertEqual(list(ll.read_json(ll.lp("queue.json"), {}).values())[0]["note"], "朝食")

    def test_photo_from_a_stranger_is_ignored(self):
        self.run_mod.handle_update({"message": {"chat": {"id": 9}, "photo": [{"file_id": "s"}]}}, self.client, "7")
        self.assertEqual(self.sent, [])
        self.assertEqual(ll.status()["waiting"], 0)

    def test_same_photo_twice_says_so(self):
        msg = {"message": {"chat": {"id": 7}, "photo": [{"file_id": "s"}]}}
        self.run_mod.handle_update(msg, self.client, "7")
        self.run_mod.handle_update(msg, self.client, "7")
        self.assertIn("取り込み済み", self.sent[-1][1])

    def test_long_polling_sends_telegrams_timeout_and_waits_longer(self):
        from unittest import mock
        from lifeos.bot import telegram
        seen = {}

        class Answer:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def read(s):
                return b'{"ok": true, "result": []}'

        def fake(url, data=None, timeout=None):
            seen.update(data=data.decode(), timeout=timeout)
            return Answer()
        with mock.patch("urllib.request.urlopen", fake):
            self.assertEqual(telegram.Client("t").get_updates(5), [])
        self.assertEqual(seen["timeout"], 60)
        self.assertIn("timeout=50", seen["data"])
        self.assertIn("offset=5", seen["data"])

    def test_non_image_document_is_not_a_photo(self):
        self.assertIsNone(self.run_mod.photo_file({"document": {"file_id": "x", "mime_type": "application/zip"}}))
        self.assertEqual(self.run_mod.photo_file({"document": {"file_id": "x", "mime_type": "application/pdf"}}), "x")


if __name__ == "__main__":
    unittest.main()
