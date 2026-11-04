import contextlib
import io
import json
import os
import re
import threading
from http.server import ThreadingHTTPServer

from lifeos.bot import router
from lifeos.lib import logs, paths
from lifeos.lib.tools import load_tool
from lifeos.tests.sandbox import SandboxCase

HERE = os.path.join(paths.REPO, "lifeos", "07-habits")
# words that blame, warn or point at missed days. None may appear in anything the tool says
HARSH = ("サボ", "さぼ", "途切", "連続", "ストリーク", "リセット", "記録なし", "記録がない", "未達", "失敗", "できなかった",
         "やらなかった", "してない", "していません", "忘れ", "残念", "怠", "遅れ", "枯れ", "しおれ", "減り", "減っ",
         "ゼロ", "空い", "ぶり", "⚠", "❌", "🥀", "🔴", "もっと", "べき", "頑張")


def ctx():
    return router.Ctx(send=lambda t: None)


class Base(SandboxCase):
    def setUp(self):
        super().setUp()
        self.core = load_tool("07-habits", "core")
        self.h = load_tool("07-habits", "handler")
        self.weekly = load_tool("07-habits", "weekly")

    def desk(self):
        with open(os.path.join(paths.desk_data_dir(), "habits.json"), encoding="utf-8") as f:
            return json.load(f)

    def seed(self, *habits):
        os.makedirs(paths.desk_data_dir(), exist_ok=True)
        with open(os.path.join(paths.desk_data_dir(), "habits.json"), "w", encoding="utf-8") as f:
            json.dump(list(habits), f, ensure_ascii=False)

    def days(self, n, end="2026-11-04", step=1):
        return [self.core.day_before(end, i * step) for i in range(n)]


class TestPlant(Base):
    def test_grows_at_ten_and_thirty(self):
        names = {n: self.core.stage(n)[2] for n in (0, 9, 10, 29, 30, 99, 100, 500)}
        self.assertEqual(names, {0: "種", 9: "種", 10: "芽", 29: "芽", 30: "花", 99: "花", 100: "木", 500: "木"})

    def test_status_line(self):
        self.assertEqual(self.core.status_line(12), "🌱 芽 · これまで 12回（花まであと18回）")
        self.assertEqual(self.core.status_line(120), "🌳 木 · これまで 120回")

    def test_desk_uses_the_same_table_and_shows_it_on_the_card(self):
        with open(os.path.join(paths.REPO, "desk", "index.html"), encoding="utf-8") as f:
            text = f.read()
        table = re.search(r"const PLANTS=\[(.*?)\];", text).group(1)
        rows = re.findall(r'\[(\d+),"(.+?)","(.+?)"\]', table)
        self.assertEqual(sorted((int(a), b, c) for a, b, c in rows), [s[:3] for s in self.core.STAGES])
        self.assertIn('<div class="sub">${esc(plantLine(st.total))}</div>', text)


class TestMatch(Base):
    def test_takes_reports_of_doing_it(self):
        for t in ("筋トレした", "読書30分", "読書を30分しました", "読書 30分", "読書３０分", "筋トレやった！", "今日は筋トレした",
                  "昨日筋トレした", "筋トレしてきました。", "ランニング5km", "筋トレした💪", "習慣: 5分歩く", "習慣：水を飲む"):
            self.assertTrue(self.h.match(t), t)

    def test_leaves_everything_else_for_other_tools(self):
        for t in ("筋トレしたい", "筋トレしなかった", "筋トレした？", "筋トレする", "読書30分したら連絡する", "読書1000円", "読書",
                  "資料を送った", "会議30分", "電話した", "task: 筋トレした", "習慣:", ""):
            self.assertFalse(self.h.match(t), t)

    def test_a_desk_habit_is_known_by_its_name(self):
        self.assertFalse(self.h.match("水を1杯した"))
        self.seed({"id": "h1", "name": "水を1杯", "done": []})
        self.assertTrue(self.h.match("水を1杯した"))
        self.assertTrue(self.h.match("水を1杯"))

    def test_match_changes_nothing_and_survives_a_broken_file(self):
        self.h.match("筋トレした")
        self.assertFalse(os.path.exists(os.path.join(paths.desk_data_dir(), "habits.json")))
        os.makedirs(paths.desk_data_dir(), exist_ok=True)
        with open(os.path.join(paths.desk_data_dir(), "habits.json"), "w") as f:
            f.write("{壊れた")
        self.assertTrue(self.h.match("筋トレした"))

    def test_the_bot_routes_here(self):
        routes = router.discover().routes
        self.assertEqual(router.which("読書30分", routes).slug, "07-habits")
        self.assertEqual(router.which("読書30分", routes).priority, 30)


class TestRecord(Base):
    def test_writes_a_desk_habit_and_no_new_log(self):
        reply = self.h.handle("読書30分", ctx())
        self.assertIn("読書 30分、記録しました", reply)
        rec = self.desk()[0]
        self.assertEqual(rec["name"], "読書")
        self.assertEqual(rec["done"], [paths.today()])
        self.assertEqual(sorted(rec), ["createdAt", "deleted", "done", "emoji", "id", "name", "updatedAt"])
        self.assertFalse(os.path.exists(paths.data_path("logs", "habits.md")))
        self.assertEqual(logs.read("habits"), [])

    def test_same_day_counts_once(self):
        self.h.handle("読書30分", ctx())
        reply = self.h.handle("読書した", ctx())
        self.assertIn("もう入っています", reply)
        self.assertEqual(len(self.desk()[0]["done"]), 1)

    def test_yesterday_goes_on_yesterday(self):
        reply = self.h.handle("昨日筋トレした", ctx())
        self.assertIn("昨日の分", reply)
        self.assertEqual(self.desk()[0]["done"], [self.core.day_before(paths.today(), 1)])

    def test_uses_the_existing_habit_and_keeps_its_fields(self):
        self.seed({"id": "h1", "name": "読書", "emoji": "📚", "done": ["2026-01-01"], "createdAt": 5, "updatedAt": 5, "deleted": False})
        self.h.handle("読書した", ctx())
        rec = self.desk()[0]
        self.assertEqual((len(self.desk()), rec["id"], rec["emoji"], rec["createdAt"]), (1, "h1", "📚", 5))
        self.assertEqual(rec["done"], ["2026-01-01", paths.today()])
        self.assertGreater(rec["updatedAt"], 5)

    def test_a_long_gap_keeps_the_count(self):
        old = self.days(12, "2020-03-01")
        self.seed({"id": "h1", "name": "読書", "done": old})
        reply = self.h.handle("読書した", ctx())
        self.assertIn("これまで 13回", reply)
        self.assertEqual(set(self.desk()[0]["done"]), set(old) | {paths.today()})

    def test_says_when_the_plant_grows(self):
        self.seed({"id": "h1", "name": "読書", "done": self.days(9, "2020-03-01")})
        self.assertIn("🌱 読書：芽が出ました", self.h.handle("読書した", ctx()))
        self.seed({"id": "h1", "name": "読書", "done": self.days(29, "2020-03-01")})
        self.assertIn("🌸 読書：花が咲きました", self.h.handle("読書した", ctx()))

    def test_a_stopped_habit_comes_back_with_its_days(self):
        self.seed({"id": "h1", "name": "読書", "done": ["2026-01-01"], "deleted": True})
        self.h.handle("読書した", ctx())
        rec = self.desk()[0]
        self.assertFalse(rec["deleted"])
        self.assertEqual(rec["done"], ["2026-01-01", paths.today()])

    def test_explicit_form_adds_any_habit(self):
        self.assertIn("新しい習慣", self.h.handle("習慣: 5分歩く", ctx()))
        self.assertEqual(self.desk()[0]["name"], "5分歩く")
        self.assertTrue(self.h.match("5分歩くした"))

    def test_a_broken_file_is_not_overwritten(self):
        self.seed()
        path = os.path.join(paths.desk_data_dir(), "habits.json")
        with open(path, "w") as f:
            f.write("{壊れた")
        reply = router.dispatch("筋トレした", ctx())
        self.assertIn("未整理に残しました", reply)
        with open(path) as f:
            self.assertEqual(f.read(), "{壊れた")
        self.assertEqual(logs.read("unrouted")[0]["failedTool"], "07-habits")


class TestServer(Base):
    def setUp(self):
        super().setUp()
        from lifeos.tests.desk_import import server
        self.server = server
        folder = os.path.join(self.tmp, "desk-server")
        os.makedirs(folder)
        old = server.DATA
        server.DATA = folder
        self.addCleanup(setattr, server, "DATA", old)
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        except PermissionError:
            self.skipTest("この環境ではローカルのポートを開けません")
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        os.environ["LIFEOS_DESK_API"] = f"http://127.0.0.1:{srv.server_address[1]}"

    def test_goes_through_the_desk_server(self):
        self.h.handle("読書した", ctx())
        self.h.handle("読書した", ctx())
        rec = self.server.load("habits")[0]
        self.assertEqual((rec["name"], rec["done"]), ("読書", [paths.today()]))
        self.assertTrue(rec["updatedAt"])
        self.assertFalse(os.path.exists(os.path.join(paths.desk_data_dir(), "habits.json")))


class TestWeekly(Base):
    DAY = "2026-11-04"

    def test_report_shows_growth_and_this_week(self):
        self.seed({"id": "a", "name": "読書", "createdAt": 1, "done": self.days(12, self.DAY)},
                  {"id": "b", "name": "筋トレ", "createdAt": 2, "done": self.days(3, "2026-01-01")},
                  {"id": "c", "name": "やめた", "done": ["2026-01-01"], "deleted": True})
        text = self.core.week_report(self.DAY)
        self.assertIn("読書：🌱 芽 · これまで 12回（花まであと18回） · この7日で +7", text)
        self.assertIn("筋トレ：🌰 種 · これまで 3回（芽まであと7回）", text)
        self.assertNotIn("筋トレ：🌰 種 · これまで 3回（芽まであと7回） ·", text)
        self.assertNotIn("やめた", text)

    def test_sends_once_a_week_into_the_outbox_without_telegram(self):
        self.seed({"id": "a", "name": "読書", "done": [self.DAY]})
        self.assertEqual(self.weekly.run(self.DAY), "outbox")
        self.assertEqual(self.weekly.run(self.DAY), "already")
        self.assertEqual(len(logs.read("outbox")), 1)
        self.assertIn("今週の育ち具合", logs.read("outbox")[0]["text"])
        self.assertEqual(self.weekly.run(self.DAY, force=True), "outbox")
        self.assertEqual(self.weekly.run("2026-11-09"), "outbox")
        self.assertEqual(len(logs.read("outbox")), 3)

    def test_nothing_to_send_without_habits(self):
        self.assertEqual(self.weekly.run(self.DAY), "empty")
        self.assertEqual(logs.read("outbox"), [])

    def test_stopped_sends_nothing(self):
        self.seed({"id": "a", "name": "読書", "done": [self.DAY]})
        root = os.path.join(self.tmp, "stopped-tool")
        os.makedirs(root)
        with open(os.path.join(root, "tool.json"), "w") as f:
            json.dump({"status": "stopped"}, f)
        self.assertEqual(self.weekly.run(self.DAY, root=root), "stopped")
        self.assertEqual(logs.read("outbox"), [])

    def test_preview_sends_nothing(self):
        self.seed({"id": "a", "name": "読書", "done": [paths.today()]})
        with contextlib.redirect_stdout(io.StringIO()):
            self.weekly.main(["--preview"])
        self.assertEqual(logs.read("outbox"), [])
        self.assertFalse(os.path.exists(paths.data_path("tools", "07-habits", "weekly.json")))


class TestNeverBlames(Base):
    """The completion condition: days without a record never produce a harsh word."""

    def assertGentle(self, text):
        for w in HARSH:
            self.assertNotIn(w, text)
        self.assertNotRegex(text, r"(?<!\d)0回")

    def test_every_message_after_missed_days(self):
        day = "2026-11-04"
        cases = ([], ["2019-01-01"], self.days(9, "2020-01-01"), self.days(29, "2020-01-01", step=30), self.days(10, day), self.days(150, day))
        for done in cases:
            self.seed({"id": "a", "name": "読書", "createdAt": 1, "done": list(done)},
                      {"id": "b", "name": "瞑想", "createdAt": 2, "done": []})
            if done:
                self.assertGentle(self.h.handle("読書した", ctx()))
                self.assertGentle(self.h.handle("読書した", ctx()))
                self.assertGentle(self.h.handle("昨日読書した", ctx()))
            self.assertGentle(self.core.week_report(day))
            self.assertGentle(self.core.week_report("2030-01-01"))
        for n in range(0, 130):
            self.assertGentle(self.core.status_line(n))
        for s in self.core.STAGES:
            self.assertGentle(s[3])
        for m in self.weekly.MESSAGES.values():
            self.assertGentle(m)

    def test_the_week_with_nothing_new_reads_the_same_as_any_other(self):
        self.seed({"id": "a", "name": "読書", "done": self.days(12, "2020-01-01")})
        lines = self.core.week_report("2026-11-04").split("\n")
        self.assertEqual(lines, ["🌿 今週の育ち具合", "読書：🌱 芽 · これまで 12回（花まであと18回）", "育った分はこのまま残ります。"])

    def test_tool_screen_text_is_gentle(self):
        with open(os.path.join(HERE, "tool.json"), encoding="utf-8") as f:
            t = json.load(f)
        self.assertGentle(" ".join([t["oneline"]] + t["usage"]))
