import contextlib
import glob
import io
import json
import os

from lifeos.lib import paths
from lifeos.tests.desk_import import server, sync
from lifeos.tests.sandbox import SandboxCase

SLUGS = ("01-morning 02-followup 03-task 04-voice 05-parallel 06-kickoff 07-habits 08-tidy 09-toolshop "
         "10-autostart 11-notes 12-chatlog 13-brain 14-lifemap 15-resume 16-where 17-secretary 18-subs").split()
STATUSES = {"planned", "building", "working", "blocked", "stopped"}


class TestToolJson(SandboxCase):
    def test_all_eighteen_are_well_formed(self):
        found = sorted(os.path.basename(os.path.dirname(p))
                       for p in glob.glob(os.path.join(paths.REPO, "lifeos", "*", "tool.json")))
        self.assertEqual(found, SLUGS)
        commands = []
        for slug in SLUGS:
            with open(os.path.join(paths.REPO, "lifeos", slug, "tool.json"), encoding="utf-8") as f:
                t = json.load(f)
            self.assertEqual(t["no"], slug[:2])
            self.assertIn(t["status"], STATUSES)
            for k in ("name", "oneline", "updated"):
                self.assertTrue(t[k], slug + " " + k)
            for k in ("usage", "howToCheck"):
                self.assertTrue(isinstance(t[k], list) and t[k], slug + " " + k)
            for b in t["blockers"]:
                self.assertEqual(set(b), {"what", "why", "needs"}, slug)
            commands += t["commands"]
        self.assertEqual(len(commands), len(set(commands)), "a command must have one owner")


class TestServer(SandboxCase):
    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.tmp, "repo")
        self.write("01-a", '{"no":"01","name":"あ","status":"planned"}')
        old = server.REPO
        server.REPO = self.repo
        self.addCleanup(setattr, server, "REPO", old)
        old_data = server.DATA, sync.DATA
        server.DATA = sync.DATA = os.path.join(self.tmp, "desk-data")
        os.makedirs(server.DATA)
        self.addCleanup(lambda: (setattr(server, "DATA", old_data[0]), setattr(sync, "DATA", old_data[1])))

    def write(self, slug, text):
        d = os.path.join(self.repo, "lifeos", slug)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "tool.json"), "w", encoding="utf-8") as f:
            f.write(text)

    def merge(self, remote):
        path = os.path.join(self.tmp, "remote.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(remote, f)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sync.cmd_merge(path)
        return json.loads(out.getvalue())["write"]

    def test_reads_fresh_every_time(self):
        self.assertEqual(server.lifeos_tools()[0]["id"], "01-a")
        self.write("02-b", '{"no":"02","name":"い","status":"working"}')
        self.assertEqual([t["id"] for t in server.lifeos_tools()], ["01-a", "02-b"])

    def test_a_broken_file_is_shown_not_fatal(self):
        self.write("02-b", "{broken")
        t = server.lifeos_tools()[1]
        self.assertEqual((t["id"], t["status"]), ("02-b", "blocked"))

    def test_page_carries_window_lifeos(self):
        self.write("02-b", '{"no":"02","name":"</script><b>","status":"planned"}')
        html = server.page()
        self.assertIn("window.LIFEOS=", html)
        self.assertNotIn('"</script>', html)

    def test_sync_is_one_way_mac_to_online(self):
        first = self.merge({"lifeos": []})["lifeos"]
        self.assertEqual([d["id"] for d in first], ["01-a"])
        self.assertEqual(self.merge({"lifeos": first}), {})  # same: nothing to write
        changed = dict(first[0], status="working")  # online edited by hand: the repo wins
        self.assertEqual(self.merge({"lifeos": [changed]})["lifeos"], first)
        self.assertFalse(os.path.exists(os.path.join(server.DATA, "lifeos.json")))

    def test_sync_retires_what_left_the_repo(self):
        write = self.merge({"lifeos": server.lifeos_tools() + [{"id": "99-old", "name": "x"}]})
        self.assertEqual(write["lifeos"], [{"id": "99-old", "deleted": True}])
        self.assertEqual(self.merge({"lifeos": server.lifeos_tools() + [{"id": "99-old", "deleted": True}]}), {})

    def test_sync_skips_when_online_was_not_read(self):
        self.assertEqual(self.merge({}), {})

    def test_old_checkout_does_not_hide_online(self):
        server.REPO = os.path.join(self.tmp, "empty")
        self.assertEqual(self.merge({"lifeos": [{"id": "01-a", "name": "x"}]}), {})
