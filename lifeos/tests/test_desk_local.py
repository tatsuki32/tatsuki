"""The Mac-only Desk: docs on disk, the page's feeds, tool URLs and the backup scan."""
import json
import os
import subprocess
import tempfile
import unittest

from lifeos.tests.desk_import import server, sync

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "backup_data", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "desk", "backup_data.py")
)
backup_data = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backup_data)


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        old = server.DATA, sync.DATA, backup_data.DATA
        server.DATA = sync.DATA = backup_data.DATA = self.td.name
        self.addCleanup(lambda: (setattr(server, "DATA", old[0]), setattr(sync, "DATA", old[1]), setattr(backup_data, "DATA", old[2])))

    def page_value(self, name, var):
        with open(os.path.join(self.td.name, name), encoding="utf-8") as f:
            text = f.read().strip()
        return json.loads(text[len(f"window.{var}=") : -1])


class Feeds(Base):
    def test_a_doc_reaches_the_page_newest_first(self):
        server.put_doc("days", "2026-11-03", {"date": "2026-11-03", "items": []})
        server.put_doc("days", "2026-11-04", {"date": "2026-11-04", "items": []})
        server.rebuild_feed("days")
        self.assertEqual([d["id"] for d in self.page_value("mail.js", "MAIL")], ["2026-11-04", "2026-11-03"])

    def test_old_docs_stay_on_disk_when_the_page_keeps_the_newest(self):
        for n in range(1, 33):
            server.put_doc("days", f"2026-10-{n:02d}" if n < 32 else "2026-11-01", {"date": ""})
        server.rebuild_feed("days")
        self.assertEqual(len(self.page_value("mail.js", "MAIL")), 30)
        self.assertEqual(len(server.feed_docs("days")), 32)

    def test_one_doc_feed(self):
        server.put_doc("calendar", "upcoming", {"events": [1]})
        server.rebuild_feed("calendar")
        self.assertEqual(self.page_value("calendar.js", "CAL"), {"events": [1]})

    def test_a_doc_id_cannot_leave_its_folder(self):
        for bad in ("../x", "a/b", ".hidden", ""):
            with self.assertRaises(ValueError):
                server.put_doc("days", bad, {})

    def test_a_broken_doc_does_not_hide_the_rest(self):
        server.put_doc("watch", "2026-11-03", {"date": "2026-11-03"})
        with open(server.doc_path("watch", "bad"), "w") as f:
            f.write("{broken")
        server.rebuild_feed("watch")
        self.assertEqual(len(self.page_value("watch.js", "WATCH")), 1)


class Tools(Base):
    def test_only_an_editable_tool_and_only_https(self):
        server.put_doc("tools", "free", {"name": "a", "url": "https://a.example", "urlEditable": True})
        server.put_doc("tools", "fixed", {"name": "b", "url": "https://b.example"})
        self.assertEqual(server.set_tool_url("free", "https://new.example")["url"], "https://new.example")
        self.assertIsNone(server.set_tool_url("fixed", "https://new.example"))
        self.assertIsNone(server.set_tool_url("free", "javascript:alert(1)"))
        self.assertIsNone(server.set_tool_url("../free", "https://new.example"))
        self.assertEqual({t["id"]: t["url"] for t in self.page_value("tools.js", "TOOLS")}["free"], "https://new.example")


class Mail(Base):
    def test_the_draft_is_read_by_day_and_key(self):
        server.put_doc("days", "2026-11-04", {"items": [{"key": "k", "subject": "s", "draft": {"id": "d"}}, {"key": "x"}]})
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sync.cmd_mail("2026-11-04", "k")
        self.assertEqual(json.loads(out.getvalue())["draft"]["id"], "d")
        with self.assertRaises(SystemExit):
            sync.cmd_mail("2026-11-04", "x")


class Backup(Base):
    def write(self, name, text):
        with open(os.path.join(self.td.name, name), "w", encoding="utf-8") as f:
            f.write(text)

    def test_the_scan_stops_keys_and_lets_normal_text_pass(self):
        self.write("a.json", json.dumps({"text": "パスワードを変更するタスク"}, ensure_ascii=False))
        self.write("b.json", json.dumps({"note": "api_key: abcdef1234567890"}))
        self.write("c.json", "ghp_" + "a" * 36)
        self.assertEqual(backup_data.leaks(["a.json", "b.json", "c.json"]), ["b.json", "c.json"])

    def test_nothing_is_committed_when_a_key_is_found(self):
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull}
        backup_data.git("init", "-q", "-b", "main")
        self.write("c.json", "ghp_" + "a" * 36)
        backup_data.backup()
        head = subprocess.run(["git", "-C", self.td.name, "rev-parse", "HEAD"], capture_output=True, env=env)
        self.assertNotEqual(head.returncode, 0)

    def test_a_change_is_committed_without_a_remote(self):
        backup_data.git("init", "-q", "-b", "main")
        self.write("a.json", "{}")
        backup_data.backup()
        self.assertEqual(backup_data.git("rev-list", "--count", "HEAD").stdout.strip(), "1")
        self.assertEqual(backup_data.changed_files(), [])


if __name__ == "__main__":
    unittest.main()
