import os
import subprocess
import sys
import textwrap

from lifeos.bot import router, run, telegram, voice
from lifeos.lib import logs, paths, tools
from lifeos.tests.sandbox import SandboxCase


class Fake:
    """Stands in for telegram.Client."""

    def __init__(self):
        self.sent = []
        self.updates = []

    def send_message(self, chat, text):
        self.sent.append((chat, text))

    def download(self, file_id, dest):
        with open(dest, "w") as f:
            f.write("audio")

    def get_updates(self, offset=None):
        return self.updates


class BotCase(SandboxCase):
    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.tmp, "tools")
        os.environ["LIFEOS_TOOLS_ROOT"] = self.root
        self.addCleanup(voice.set_transcriber, None)

    def tool(self, slug, code, status="building"):
        d = os.path.join(self.root, slug)
        os.makedirs(d)
        with open(os.path.join(d, "handler.py"), "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(code))
        with open(os.path.join(d, "tool.json"), "w", encoding="utf-8") as f:
            f.write('{"name":"%s","status":"%s"}' % (slug, status))


HI = """
    def match(text): return text.startswith("hi")
    def handle(text, ctx): return "hi from A"
"""


class TestRouter(BotCase):
    def test_loads_handlers_by_itself(self):
        self.tool("05-a", HI)
        found = router.discover()
        self.assertEqual([r.slug for r in found.routes], ["05-a"])
        self.assertEqual(router.dispatch("hi there", router.Ctx(), found.routes), "hi from A")

    def test_order_is_priority_then_number(self):
        self.tool("01-late", "PRIORITY=90\n" + textwrap.dedent(HI))
        self.tool("03-b", HI)
        self.tool("02-a", HI)
        self.tool("09-first", "PRIORITY=10\n" + textwrap.dedent(HI))
        routes = router.discover().routes
        self.assertEqual([r.slug for r in routes], ["09-first", "02-a", "03-b", "01-late"])
        self.assertEqual(router.which("hi", routes).slug, "09-first")

    def test_a_broken_tool_does_not_stop_the_others(self):
        self.tool("01-syntax", "def match(:")
        self.tool("02-missing", "def match(t): return True")
        self.tool("03-raises", "raise RuntimeError('x')")
        self.tool("04-ok", HI)
        found = router.discover()
        self.assertEqual([r.slug for r in found.routes], ["04-ok"])
        self.assertEqual([e[0] for e in found.errors], ["01-syntax", "02-missing", "03-raises"])

    def test_a_stopped_tool_is_not_loaded(self):
        self.tool("01-off", HI, status="stopped")
        self.assertEqual(router.discover().routes, [])

    def test_match_error_falls_to_the_next_tool(self):
        self.tool("01-bad", "def match(t): raise ValueError()\ndef handle(t,c): return 'no'")
        self.tool("02-ok", HI)
        self.assertEqual(router.dispatch("hi", router.Ctx()), "hi from A")
        self.assertEqual(logs.read("bot-errors")[0]["tool"], "01-bad")

    def test_handle_error_keeps_the_words(self):
        self.tool("01-bad", "def match(t): return True\ndef handle(t,c): raise ValueError('boom')")
        reply = router.dispatch("大事なメモ", router.Ctx())
        self.assertIn("エラー", reply)
        self.assertEqual(logs.read("unrouted")[0]["text"], "大事なメモ")

    def test_nothing_matched_is_kept(self):
        self.assertEqual(router.dispatch("だれも受けない", router.Ctx()), router.UNROUTED)
        self.assertEqual(logs.read("unrouted")[0]["text"], "だれも受けない")
        self.assertEqual(router.dispatch("   ", router.Ctx()), router.EMPTY)

    def test_ctx_gives_tasks(self):
        self.tool("03-task", """
            def match(t): return t.startswith("task:")
            def handle(t, ctx): return ctx.tasks.add(t[5:], source="lifeos-03")["title"]
        """)
        self.assertEqual(router.dispatch("task: 買う", router.Ctx()), "買う")

    def test_load_tool_and_brief_parts(self):
        d = os.path.join(self.root, "04-x")
        os.makedirs(d)
        for name, code in {"core.py": "V=1", "brief.py": "def lines(day): return ['1行:'+day]"}.items():
            with open(os.path.join(d, name), "w") as f:
                f.write(code)
        e = os.path.join(self.root, "05-bad")
        os.makedirs(e)
        with open(os.path.join(e, "brief.py"), "w") as f:
            f.write("raise RuntimeError()")
        self.assertEqual(tools.load_tool("04-x", "core").V, 1)
        self.assertEqual(tools.brief_parts("2026-01-01"), [("04-x", ["1行:2026-01-01"])])


class TestCli(BotCase):
    def cli(self, *args):
        r = subprocess.run([sys.executable, os.path.join(paths.REPO, "lifeos", "bot", "cli.py"), *args],
                           capture_output=True, text=True, env=os.environ, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_cli(self):
        self.tool("05-a", HI)
        self.assertEqual(self.cli("hi"), "hi from A")
        self.assertEqual(self.cli("--which", "hi"), "05-a")
        self.assertIn("05-a", self.cli("--list"))
        self.assertEqual(self.cli("ほか"), router.UNROUTED)
        self.assertEqual(self.cli("--voice", "x.ogg"), "文字起こし未接続")


class TestUpdates(BotCase):
    def msg(self, chat=7, **kw):
        return {"update_id": 1, "message": {"chat": {"id": chat}, **kw}}

    def test_owner_only(self):
        self.tool("05-a", HI)
        c = Fake()
        run.handle_update(self.msg(text="hi"), c, "7")
        self.assertEqual(c.sent, [(7, "hi from A")])
        c.sent.clear()
        run.handle_update(self.msg(chat=8, text="hi"), c, "7")
        self.assertEqual(c.sent, [])
        self.assertEqual(logs.read("unrouted"), [])

    def test_without_a_chat_id_only_tells_the_id(self):
        self.tool("03-task", "def match(t): return True\ndef handle(t,c): return c.tasks.add(t)['id']")
        c = Fake()
        run.handle_update(self.msg(text="task"), c, None)
        self.assertIn("7", c.sent[0][1])
        from lifeos.lib import tasks
        self.assertEqual(tasks.all_tasks(), [])

    def test_voice_without_a_transcriber(self):
        c = Fake()
        run.handle_update(self.msg(voice={"file_id": "f"}), c, "7")
        self.assertEqual(c.sent, [(7, "文字起こし未接続")])

    def test_voice_with_a_swapped_transcriber(self):
        self.tool("05-v", "def match(t): return True\ndef handle(t,c): return ('音声' if c.is_voice else '文字')+':'+t")
        voice.set_transcriber(lambda path: "しゃべった内容")
        c = Fake()
        run.handle_update(self.msg(voice={"file_id": "f"}), c, "7")
        self.assertEqual(c.sent, [(7, "音声:しゃべった内容")])

    def test_stickers_and_the_like(self):
        c = Fake()
        run.handle_update(self.msg(sticker={}), c, "7")
        self.assertEqual(c.sent, [(7, run.NOT_TEXT)])
        run.handle_update({"update_id": 2}, c, "7")
        self.assertEqual(len(c.sent), 1)

    def test_poll_moves_on_after_a_bad_message(self):
        c = Fake()
        c.send_message = lambda *a: (_ for _ in ()).throw(RuntimeError("x"))
        c.updates = [{"update_id": 5, "message": {"chat": {"id": 7}, "text": "a"}}]
        self.assertEqual(run.poll_once(c, "7", None, []), 6)
        self.assertEqual(run.read_offset(), 6)
        self.assertEqual(logs.read("bot-errors")[0]["where"], "update")


class TestTelegramSetup(BotCase):
    def test_no_token_means_no_start(self):
        r = subprocess.run([sys.executable, os.path.join(paths.REPO, "lifeos", "bot", "run.py")],
                           capture_output=True, text=True, env=os.environ, timeout=30)
        self.assertEqual(r.returncode, 2)
        self.assertIn("トークンがないので起動しません", r.stdout)

    def test_notify_in_a_sandbox_never_sends(self):
        os.environ["LIFEOS_TELEGRAM_TOKEN"] = "fake"
        os.environ["LIFEOS_TELEGRAM_CHAT_ID"] = "1"
        self.assertFalse(telegram.notify("週のまとめ"))
        self.assertEqual(logs.read("outbox")[0]["text"], "週のまとめ")

    def test_errors_do_not_contain_the_token(self):
        c = telegram.Client("SECRET-TOKEN")
        telegram.API = "http://127.0.0.1:9"
        self.addCleanup(setattr, telegram, "API", "https://api.telegram.org")
        with self.assertRaises(telegram.TelegramError) as e:
            c.call("getMe", timeout=1)
        self.assertNotIn("SECRET", str(e.exception))

    def test_long_text_is_split(self):
        self.assertEqual([len(x) for x in telegram.split_text("a" * 8500)], [4000, 4000, 500])
