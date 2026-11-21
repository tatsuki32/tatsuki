"""Minutes: made from ready recordings, linked to calendar events, and the link written into Google Calendar.
Nothing here reaches the transcriber, Claude or Google: the network calls are replaced in each test."""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["MINUTES_DIR"] = tempfile.mkdtemp(prefix="desk-minutes-test-")
os.environ["MINUTES_FAKE"] = "1"  # the summary comes from a fixed test answer, not Claude

import minutes  # noqa: E402

SID = "a" * 32
CREATED = 1795000000.0


class Minutes(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(minutes.DIR, ignore_errors=True)

    def fake_sessions(self, status="done"):
        return [{"id": SID, "title": "Meeting 2026-11-21 10:00", "status": status, "created": CREATED}]

    def test_sync_makes_one_minute_for_a_ready_recording(self):
        with mock.patch.object(minutes, "_http_json", return_value=self.fake_sessions()), \
                mock.patch.object(minutes, "_http_text", return_value="Me: hello\nThem: a plan\n"):
            self.assertEqual(minutes.sync(), 1)
            self.assertEqual(minutes.sync(), 0)  # the second pass finds it and makes nothing
        rows = minutes.view()["minutes"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], {"kind": "transcribe", "sid": SID})
        self.assertEqual(rows[0]["status"], "draft")
        self.assertIn("## Summary", rows[0]["body"])
        self.assertIn("- [ ] Check the test result", rows[0]["body"])

    def test_recording_still_running_is_not_made_yet(self):
        with mock.patch.object(minutes, "_http_json", return_value=self.fake_sessions("transcribing")):
            self.assertEqual(minutes.sync(), 0)
        self.assertEqual(minutes.view()["minutes"], [])

    def test_a_deleted_minute_is_not_made_again(self):
        with mock.patch.object(minutes, "_http_json", return_value=self.fake_sessions()), \
                mock.patch.object(minutes, "_http_text", return_value="Me: hi\n"):
            minutes.sync()
            mid = minutes.view()["minutes"][0]["id"]
            minutes.remove(mid)
            self.assertEqual(minutes.sync(), 0)
        self.assertEqual(minutes.view()["minutes"], [])

    def notes_server(self, state):
        """A fake transcriber with one text note; state['minutes'] can change between passes."""
        def fake(url):
            if url.endswith("/api/sessions"):
                return [{"id": SID, "kind": "text", "status": "done", "title": "Planning", "has_minutes": bool(state.get("minutes"))}]
            return {"id": SID, "kind": "text", "title": "Planning", "met": "2026-06-23", "created": CREATED,
                    "text": "We agreed on the plan.", "minutes": state.get("minutes")}
        return fake

    def test_notes_become_a_minute_without_claude_and_pick_up_minutes_later(self):
        state = {}
        with mock.patch.object(minutes, "_http_json", side_effect=self.notes_server(state)), \
                mock.patch.object(minutes, "summarize", side_effect=AssertionError("no Claude for notes")):
            self.assertEqual(minutes.sync(), 1)
            rec = minutes.view()["minutes"][0]
            self.assertEqual(rec["source"]["from"], "notes")
            self.assertIn("Not made yet", rec["body"])
            self.assertIn("## Original notes\nWe agreed on the plan.", rec["body"])
            self.assertEqual(minutes.datetime.fromtimestamp(rec["at"]).strftime("%Y-%m-%d"), "2026-06-23")
            # minutes made in Transcribe arrive on the next pass
            state["minutes"] = {"summary": ["The plan"], "decisions": ["Go"], "next": [], "at": 1795000100,
                                "actions": [{"what": "Send the quote", "who": "Aki", "due": "2026-12-01"}]}
            self.assertEqual(minutes.sync(), 0)
            body = minutes.get(rec["id"])["body"]
            self.assertIn("- [ ] Send the quote — Aki (due 2026-12-01)", body)
            self.assertNotIn("Not made yet", body)
            # an edit here is kept, even when the minutes change again
            minutes.put(rec["id"], {"body": "My own text"})
            state["minutes"] = {**state["minutes"], "edited": 1795000200}
            minutes.sync()
            self.assertEqual(minutes.get(rec["id"])["body"], "My own text")
            # pressing Make minutes here lets the next minutes replace the edit
            cur = minutes._read(rec["id"])
            cur["source"]["force"] = True
            minutes._write(cur)
            state["minutes"] = {**state["minutes"], "edited": 1795000300}
            minutes.sync()
            self.assertIn("Send the quote", minutes.get(rec["id"])["body"])
            self.assertNotIn("force", minutes.get(rec["id"])["source"])

    def test_make_from_notes_asks_transcribe(self):
        with mock.patch.object(minutes, "_http_json", side_effect=self.notes_server({})):
            minutes.sync()
        rec = minutes.view()["minutes"][0]
        with mock.patch.object(minutes.urllib.request, "urlopen") as op:
            op.return_value.__enter__.return_value = io.BytesIO(b"{}")
            self.assertEqual(minutes.make_from_notes(rec["id"]), {"ok": True})
        self.assertTrue(minutes.get(rec["id"])["source"]["force"])
        with mock.patch.object(minutes.urllib.request, "urlopen") as op:
            op.return_value.__enter__.return_value = io.BytesIO(b"{}")
            minutes.make_from_notes(rec["id"])
            req = op.call_args[0][0]
            self.assertTrue(req.full_url.endswith(f"/api/sessions/{SID}/minutes"))
            self.assertEqual(req.get_method(), "POST")
        plain = minutes.create({"title": "Typed"})
        with self.assertRaises(minutes.MinutesError):
            minutes.make_from_notes(plain["id"])

    def test_no_transcriber_is_not_an_error(self):
        with mock.patch.object(minutes, "_http_json", side_effect=OSError("down")):
            self.assertEqual(minutes.sync(), 0)

    def test_put_checks_the_status_and_keeps_the_link_only_for_the_event_id(self):
        m = minutes.create({"title": "Plan"})
        with self.assertRaises(minutes.MinutesError):
            minutes.put(m["id"], {"status": "published"})
        ev = {"id": "e1_1", "uid": "e1", "title": "Call", "start": "2026-11-21T10:00:00+07:00", "end": "", "allDay": False}
        out = minutes.put(m["id"], {"event": ev, "status": "ready"})
        self.assertEqual(out["event"]["uid"], "e1")
        self.assertEqual(out["status"], "ready")

    def test_writing_the_link_keeps_the_old_description_and_replaces_the_old_link(self):
        m = minutes.create({"title": "Plan"})
        ev = {"id": "e1_1", "uid": "e1", "title": "Call", "start": "2026-11-21T10:00:00+07:00", "end": "", "allDay": False}
        minutes.put(m["id"], {"event": ev})
        calls = []

        def fake_api(method, url, body=None):
            calls.append((method, url, body))
            if method == "GET":
                return {"items": [{"id": "g1", "description": "Bring the slides\nMinutes (Desk): http://old",
                                   "start": {"dateTime": "2026-11-21T10:00:00+07:00"}}]}
            return {}

        with mock.patch.object(minutes, "_api", side_effect=fake_api):
            minutes.write_calendar(m["id"])
            minutes.write_calendar(m["id"])
        patch = [c for c in calls if c[0] == "PATCH"][-1]
        desc = patch[2]["description"]
        self.assertIn("Bring the slides", desc)
        self.assertEqual(desc.count("Minutes (Desk): "), 1)
        self.assertIn(f"#minutes/{m['id']}", desc)
        self.assertNotIn("http://old", desc)
        self.assertIn("/events/g1", patch[1])
        self.assertTrue(minutes.get(m["id"])["calendar"]["ok"])

    def test_no_event_means_nothing_is_written(self):
        m = minutes.create({"title": "Plan"})
        with mock.patch.object(minutes, "_api") as api:
            with self.assertRaises(minutes.MinutesError):
                minutes.write_calendar(m["id"])
        api.assert_not_called()

    def test_summary_text_is_cut_to_the_limits(self):
        out = minutes.sanitize({"title": "t" * 500, "summary": ["x"] * 40, "todos": [{"text": ""}, {"text": "Ship"}],
                                "topics": [{"heading": "H", "points": ["p"]}] * 9})
        self.assertLessEqual(len(out["title"]), 200)
        self.assertEqual(len(out["summary"]), 12)
        self.assertEqual([t["text"] for t in out["todos"]], ["Ship"])
        self.assertEqual(len(out["topics"]), 5)


if __name__ == "__main__":
    unittest.main()
