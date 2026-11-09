"""LINE: an opened chat is never lost, nothing happens while someone is at the Mac, and only a chat waiting for an answer becomes a task."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "desk"))
os.environ.setdefault("LIFEOS_DATA", tempfile.mkdtemp(prefix="lifeos-line-"))
import line_sync as ln  # noqa: E402
import lifelog  # noqa: E402
from lifeos.lib import paths, tasks  # noqa: E402


def at(h, m=0):
    return datetime(2026, 11, 6, h, m, tzinfo=paths.HCMC).timestamp()


READING = {"name": "相手", "topic": "明日の予定について", "last_is_mine": False, "needs_reply": True, "texts": ["明日どう？"], "summary": "予定を聞かれている", "body": "行けるよ", "body_ja": ""}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for k, v in {"LIFELOG_DATA": self.tmp, "LIFEOS_DATA": self.tmp}.items():
            p = mock.patch.dict(os.environ, {k: v})
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(paths, "desk_data_dir", lambda: self.tmp)
        p.start()
        self.addCleanup(p.stop)
        self.store = {}
        for name, fn in {"_put": lambda r: self.store.__setitem__(r["id"], r), "_load": lambda: list(self.store.values()), "get": self.store.get}.items():
            p = mock.patch.object(tasks, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def queue_one(self, kind="friend", name="相手"):
        os.makedirs(ln.lp("shots"), exist_ok=True)
        with open(ln.lp("shots", "e-0.png"), "wb") as f:
            f.write(b"x")
        ln.write_json(ln.lp("queue.json"), {"e": {"kind": kind, "name": name, "shots": ["e-0.png"], "at": 100, "tries": 0}})


class Timing(Base):
    def test_day_starts_at_three(self):
        self.assertEqual(ln.period(at(2, 59)), "2026-11-05")
        self.assertEqual(ln.period(at(3)), "2026-11-06")

    def test_night_window(self):
        self.assertTrue(ln.in_night(at(21)))
        self.assertTrue(ln.in_night(at(3, 29)))
        self.assertFalse(ln.in_night(at(3, 30)))
        self.assertFalse(ln.in_night(at(20, 59)))


class Tick(Base):
    def run_tick(self, status, now=None):
        def hand(cmd, *a):
            return status if cmd == "status" else self.fail("must not touch LINE: " + cmd)
        with mock.patch.object(ln, "hand", hand):
            return ln.tick(now or at(3))

    def test_locked_waits_without_a_problem(self):
        self.assertEqual(self.run_tick({"screen": True, "access": True, "locked": True, "idle": 999}), "locked")
        self.assertFalse(ln.read_json(ln.lp("status.json"), {}).get("problem"))

    def test_someone_at_the_mac_waits(self):
        self.assertEqual(self.run_tick({"screen": True, "access": True, "locked": False, "idle": 5}), "busy")

    def test_missing_permission_is_shown(self):
        self.assertEqual(self.run_tick({"screen": True, "access": False, "locked": False, "idle": 999}), "permission")
        self.assertTrue(ln.read_json(ln.lp("status.json"), {}).get("problem"))

    def test_once_a_day(self):
        ln.set_status(doneFor=ln.period(at(4)))
        self.assertEqual(self.run_tick({}, at(4)), "done")

    def test_next_day_runs_again(self):
        ln.set_status(doneFor=ln.period(at(2)))
        self.assertEqual(self.run_tick({"screen": True, "access": True, "locked": True, "idle": 0}, at(3)), "locked")

    def test_touched_keeps_what_was_opened(self):
        self.queue_one()
        with mock.patch.object(ln, "hand", lambda c, *a: {"screen": True, "access": True, "locked": False, "idle": 999}), \
                mock.patch.object(ln, "look", side_effect=ln.Touched()):
            self.assertEqual(ln.tick(at(3)), "touched")
        self.assertIn("e", ln.read_json(ln.lp("queue.json"), {}))
        self.assertNotEqual(ln.read_json(ln.lp("status.json"), {}).get("doneFor"), ln.period(at(3)))


class Look(Base):
    def script(self, list_reply, idle=999):
        calls = []

        def hand(cmd, *a):
            calls.append((cmd, *a))
            if cmd == "status":
                return {"locked": False, "idle": idle}
            if cmd == "open":
                return {"front": True, "launched": True, "previous": 7, "w": 1440, "h": 900}
            if cmd == "shot":
                with open(a[0], "wb") as f:
                    f.write(b"x")
                return {"w": 1440, "h": 900, "pxw": 2880, "pxh": 1800}
            return {"ok": True}
        return calls, mock.patch.multiple(ln, hand=hand, ask=lambda *a, **k: list_reply, crop_list=lambda s, *a: s)

    def test_clicks_stay_in_the_list_and_the_screen_comes_back(self):
        calls, patch = self.script({"screen": "list", "rows": [{"name": "相手", "y": 0.17}], "total_unread": 0})
        with patch:
            self.assertEqual(ln.look(pause=lambda s: None, recent=True), 2)  # one row in each tab
        clicks = [c for c in calls if c[0] == "click"]
        self.assertTrue(all(c[1] <= ln.LIST_W for c in clicks))
        self.assertEqual(calls[-1], ("restore", 1, 7))
        self.assertEqual(len(ln.read_json(ln.lp("queue.json"), {})), 2)
        self.assertNotIn("list.png", os.listdir(ln.lp("shots")))

    def test_goes_down_until_the_list_stops_changing(self):
        calls, patch = self.script({"screen": "list", "rows": [], "total_unread": 3})
        with patch:
            ln.look(pause=lambda s: None, recent=True)
        self.assertEqual(ln.read_json(ln.lp("status.json"), {})["leftUnread"], 3)
        self.assertGreaterEqual(len([c for c in calls if c[0] == "scroll" and c[3] < 0]), 2)

    def test_a_chat_seen_on_two_screens_opens_once(self):
        calls, patch = self.script({"screen": "list", "rows": [{"name": "相手", "y": 0.3}], "total_unread": 1})
        n = [0]
        real = ln.file_id

        def changing(p):
            n[0] += 1
            return real(p) + str(min(n[0], 2))
        with patch, mock.patch.object(ln, "file_id", changing):
            self.assertEqual(ln.visit_tab("friend", None, time.time(), {}, 20, pause=lambda s: None)[0], 1)

    def test_only_status_click_scroll_shot_open_restore(self):
        calls, patch = self.script({"screen": "list", "rows": [{"name": "相手", "y": 0.17}], "total_unread": 1})
        with patch:
            ln.look(pause=lambda s: None, recent=True)
        self.assertLessEqual({c[0] for c in calls}, {"status", "open", "click", "scroll", "shot", "restore"})

    def test_login_screen_stops_and_gives_the_screen_back(self):
        calls, patch = self.script({"screen": "login", "rows": [], "total_unread": 0})
        with patch, self.assertRaises(ln.Trouble):
            ln.look(pause=lambda s: None, recent=True)
        self.assertEqual(calls[-1][0], "restore")

    def test_person_stops_before_the_next_chat(self):
        calls, patch = self.script({"screen": "list", "rows": [{"name": "相手", "y": 0.17}], "total_unread": 0}, idle=-1)
        with patch, self.assertRaises(ln.Touched):
            ln.look(pause=lambda s: None, recent=True)
        self.assertEqual(calls[-1][0], "restore")
        self.assertFalse(ln.read_json(ln.lp("queue.json"), {}))

    def test_row_grid(self):
        self.assertEqual(ln.row_point(160 / 900, 900, False), (ln.ROW_X, 148))
        self.assertEqual(ln.row_point(225 / 900, 900, False), (ln.ROW_X, 219))
        self.assertIsNone(ln.row_point(0.99, 900, False))
        self.assertIsNone(ln.row_point(0.02, 900, False))  # the tabs and the search box are above the first row


def png(path, w, h, paint):
    """A small real PNG, every line stored with a different filter kind so the reader's unfiltering gets tested."""
    import struct
    import zlib

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    raw, prev = bytearray(), bytearray(w * 4)
    for y in range(h):
        line = bytearray()
        for x in range(w):
            line += bytes((*paint(x, y), 255))
        kind = y % 5
        out = bytearray(line)
        for i in range(len(line)):
            a, b, c = line[i - 4] if i >= 4 else 0, prev[i], prev[i - 4] if i >= 4 else 0
            pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
            guess = {0: 0, 1: a, 2: b, 3: (a + b) // 2, 4: a if pa <= pb and pa <= pc else b if pb <= pc else c}[kind]
            out[i] = (line[i] - guess) & 255
        raw += bytes((kind,)) + out
        prev = line
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))


def window(red=True, circles=(219,), signed_in=True):
    def paint(x, y):
        if x < 40 and signed_in:
            if red and 88 <= y < 108 and 36 <= x:
                return (240, 50, 60)
            return (31, 42, 70)
        if 330 <= x < 348 and any(abs(y - c) < 10 for c in circles):
            return (6, 199, 85)
        if 297 <= x < 349 and 620 - 26 <= y < 620 + 26:
            return (6, 199, 85)  # the floating button
        return (255, 255, 255)
    return paint


class Colours(Base):
    def see(self, **kw):
        path = os.path.join(self.tmp, "w.png")
        png(path, 400, 666, window(**kw))
        return ln.badges(path, 400, 666)

    def test_red_and_circles(self):
        got = self.see(circles=(219, 361))
        self.assertTrue(got["signed_in"] and got["red"])
        self.assertEqual([round(y) for y in got["green"]], [219, 361])

    def test_nothing_unread(self):
        got = self.see(red=False, circles=())
        self.assertFalse(got["red"])
        self.assertEqual(got["green"], [])

    def test_the_floating_button_is_no_circle(self):
        self.assertEqual(self.see(circles=())["green"], [])

    def test_login_screen_is_not_taken_for_nothing_unread(self):
        self.assertFalse(self.see(signed_in=False, red=False, circles=())["signed_in"])


class Sweep(Base):
    def run_sweep(self, screens, limit=20):
        """screens: what each look at the window finds. The last one repeats."""
        calls, seq = [], list(screens)

        def hand(cmd, *a):
            calls.append((cmd, *a))
            if cmd == "status":
                return {"locked": False, "idle": 999}
            if cmd == "shot":
                with open(a[0], "wb") as f:
                    f.write(b"x")
                return {"w": 1440, "h": 900, "pxw": 2880, "pxh": 1800}
            return {"ok": True}

        def look():
            s = seq.pop(0) if len(seq) > 1 else seq[0]
            return s[0], {"w": 1440, "h": 900}, s[1]
        queue = {}
        with mock.patch.multiple(ln, hand=hand, look_at_window=look):
            result = ln.sweep_tab("group", time.time(), queue, limit, pause=lambda s: None)
        return result, calls, queue

    def test_red_gone_means_no_scrolling_and_no_reading(self):
        (done, left), calls, queue = self.run_sweep([({"signed_in": True, "red": False, "green": []}, "a")])
        self.assertEqual((done, left, queue), (0, False, {}))
        self.assertFalse([c for c in calls if c[0] == "scroll" and c[3] < 0])

    def test_scrolls_to_the_circle_opens_it_and_stops_when_red_goes(self):
        (done, left), calls, queue = self.run_sweep([
            ({"signed_in": True, "red": True, "green": []}, "a"),
            ({"signed_in": True, "red": True, "green": [300.0]}, "b"),
            ({"signed_in": True, "red": False, "green": []}, "c")])
        self.assertEqual((done, left, len(queue)), (1, False, 1))
        self.assertIn(("click", ln.ROW_X, 300.0), calls)
        self.assertEqual(len([c for c in calls if c[0] == "scroll" and c[1] == ln.ROW_X and c[3] < 0]), 1)

    def test_end_of_the_list_with_red_left_says_so(self):
        (done, left), _, _ = self.run_sweep([({"signed_in": True, "red": True, "green": []}, "a")])
        self.assertEqual((done, left), (0, True))

    def test_a_circle_that_will_not_go_stops(self):
        with self.assertRaises(ln.Trouble):
            self.run_sweep([({"signed_in": True, "red": True, "green": [300.0]}, "a")])

    def test_login_screen_stops(self):
        with self.assertRaises(ln.Trouble):
            self.run_sweep([({"signed_in": False, "red": False, "green": []}, "a")])


class Queue(Base):
    def test_friend_becomes_a_task_and_the_picture_goes(self):
        self.queue_one()
        self.assertEqual(ln.read_queue(reader=lambda *a: READING), 1)
        task = next(iter(self.store.values()))
        self.assertEqual((task["source"], task["title"], task["status"]), ("line", "LINE reply: 相手 — 明日の予定", "notyet"))
        self.assertNotIn("明日どう", json.dumps(task, ensure_ascii=False))
        self.assertEqual(ln.load_items()[task["id"]]["body"], "行けるよ")
        self.assertEqual(os.listdir(ln.lp("shots")), [])
        self.assertEqual(ln.read_json(ln.lp("queue.json"), {}), {})

    def test_every_unread_chat_gets_a_task_even_with_nothing_to_answer(self):
        self.queue_one()
        ln.read_queue(reader=lambda *a: {**READING, "needs_reply": False, "body": ""})
        self.assertEqual(len(self.store), 1)
        self.assertIs(next(iter(ln.load_items().values()))["needsReply"], False)
        self.assertEqual(os.listdir(ln.lp("shots")), [])

    def test_unread_chat_with_your_last_word_still_gets_a_task(self):
        self.queue_one()
        ln.read_queue(reader=lambda *a: {**READING, "last_is_mine": True})
        self.assertEqual(len(self.store), 1)

    def test_recent_chat_you_answered_gets_none(self):
        self.queue_one()
        q = ln.read_json(ln.lp("queue.json"), {})
        q["e"]["recent"] = True
        ln.write_json(ln.lp("queue.json"), q)
        ln.read_queue(reader=lambda *a: {**READING, "last_is_mine": True})
        self.assertEqual(self.store, {})
        self.assertEqual(ln.read_json(ln.lp("queue.json"), {}), {})

    def test_group_rule_goes_to_the_reader(self):
        self.queue_one("group")
        ln.write_json(ln.lp("settings.json"), {"myNames": ["ほんにん"]})
        seen = {}

        def reader(images, text, system, schema, key):
            seen["system"] = system
            return READING
        ln.read_queue(reader=reader)
        self.assertIn("ほんにん", seen["system"])
        self.assertNotIn("{rule}", seen["system"])
        self.assertNotIn("{who}", seen["system"])

    def test_api_down_keeps_the_picture(self):
        self.queue_one()

        def reader(*a):
            raise lifelog.Stop("混み合っています")
        with self.assertRaises(lifelog.Stop):
            ln.read_queue(reader=reader)
        self.assertIn("e", ln.read_json(ln.lp("queue.json"), {}))
        self.assertTrue(os.path.exists(ln.lp("shots", "e-0.png")))

    def test_unreadable_becomes_a_task_after_three_tries(self):
        self.queue_one()

        def reader(*a):
            raise lifelog.Refused("読めず")
        for _ in range(2):
            ln.read_queue(reader=reader)
            self.assertEqual(self.store, {})
        ln.read_queue(reader=reader)
        self.assertEqual(next(iter(self.store.values()))["title"], "LINE reply: 相手")
        self.assertEqual(os.listdir(ln.lp("shots")), [])

    def test_new_messages_join_the_open_task(self):
        self.queue_one()
        ln.read_queue(reader=lambda *a: READING)
        self.queue_one()
        ln.read_queue(reader=lambda *a: {**READING, "texts": ["どう？"], "body": "行くよ"})
        self.assertEqual(len(self.store), 1)
        item = next(iter(ln.load_items().values()))
        self.assertEqual(item["texts"], ["明日どう？", "どう？"])
        self.assertEqual(item["body"], "行くよ")

    def test_same_message_read_twice_is_kept_once_and_title_follows(self):
        self.queue_one()
        ln.read_queue(reader=lambda *a: READING)
        self.queue_one()
        ln.read_queue(reader=lambda *a: {**READING, "topic": "集合場所について"})
        self.assertEqual(next(iter(ln.load_items().values()))["texts"], ["明日どう？"])
        self.assertEqual(next(iter(self.store.values()))["title"], "LINE reply: 相手 — 集合場所")

    def test_finished_task_takes_its_text(self):
        self.queue_one()
        ln.read_queue(reader=lambda *a: READING)
        tid = next(iter(self.store))
        self.store[tid] = {**self.store[tid], "status": "done"}
        ln.read_queue(reader=lambda *a: READING)
        self.assertNotIn(tid, ln.load_items())


class Safety(unittest.TestCase):
    def test_the_helper_cannot_press_a_key(self):
        with open(os.path.join(ROOT, "desk", "app", "linewatch.swift"), encoding="utf-8") as f:
            code = f.read()
        self.assertNotIn("keyboardEvent", code)
        self.assertNotIn("virtualKey", code)


if __name__ == "__main__":
    unittest.main()
