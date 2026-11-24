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
        # never the real folder: another test may have imported minutes before MINUTES_DIR was set
        if "desk-minutes-test-" not in minutes.DIR:
            raise RuntimeError("minutes.DIR is not a test folder; run this test on its own")
        shutil.rmtree(minutes.DIR, ignore_errors=True)

    def fake_sessions(self, status="done"):
        return [{"id": SID, "title": "Meeting 2026-11-21 10:00", "status": status, "created": CREATED}]

    def test_an_auto_recording_is_linked_to_its_event(self):
        ev = {"id": "abc@example.com_1795000000", "title": "Call", "start": "2026-11-21T10:00:00+09:00", "end": "2026-11-21T11:00:00+09:00"}
        for auto, want in ((True, "abc@example.com"), (False, None)):
            shutil.rmtree(minutes.DIR, ignore_errors=True)
            with mock.patch.object(minutes, "_http_json", return_value=[{**self.fake_sessions()[0], "auto": auto, "event": ev}]), \
                    mock.patch.object(minutes, "_http_text", return_value="Me: hello\n"):
                minutes.sync()
            got = minutes.view()["minutes"][0]["event"]
            self.assertEqual((got or {}).get("uid"), want)
            if auto:
                self.assertTrue(got["auto"])  # nobody chose it: minutes_mail still checks that it was accepted
                saved = minutes.put(minutes.view()["minutes"][0]["id"], {"event": got})
                self.assertNotIn("auto", saved["event"])  # chosen by the person now

    def test_sync_makes_one_minute_for_a_ready_recording(self):
        with mock.patch.object(minutes, "_http_json", return_value=self.fake_sessions()), \
                mock.patch.object(minutes, "_http_text", return_value="Me: hello\nThem: a plan\n"):
            self.assertEqual(minutes.sync(), 1)
            self.assertEqual(minutes.sync(), 0)  # the second pass finds it and makes nothing
        rows = minutes.view()["minutes"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], {"kind": "transcribe", "sid": SID, "v": minutes.VERSION})
        self.assertEqual(rows[0]["status"], "draft")
        self.assertIn("## Summary", rows[0]["body"])
        self.assertIn("| Check the test result | — | — |", rows[0]["body"])

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
            self.assertIn("| Send the quote | Aki | 2026-12-01 |", body)
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

    def test_an_empty_entry_made_before_notes_were_known_is_upgraded_once(self):
        now = 1795000000.0
        old = {"id": "b" * 32, "title": "Planning", "body": "## Summary\nNot made yet. Claude writes the summary when it can read the transcript.\n\n## Transcript\n",
               "status": "draft", "place": "", "at": now, "source": {"kind": "transcribe", "sid": SID}, "event": None,
               "calendar": None, "created": now, "updated": now}
        minutes._write(old)
        with mock.patch.object(minutes, "_http_json", side_effect=self.notes_server({})):
            self.assertEqual(minutes.sync(), 0)  # upgraded, not a second entry
        rows = minutes.view()["minutes"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"]["from"], "notes")
        self.assertIn("We agreed on the plan.", rows[0]["body"])

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

    def test_title_from_the_content_and_minutes_in_the_meetings_language(self):
        ja = minutes.sanitize({"title": "動画スクールの無料体験", "participants": ["田中"], "summary": ["8週間の体験の説明"],
                               "decisions": ["11月23日に開始"], "todos": [{"text": "資料を送る", "owner": "Me", "due": "2026-11-22"}], "topics": []})
        self.assertEqual(minutes._pick_title("Recording 2026-11-21 13:03", ja), "動画スクールの無料体験")
        self.assertEqual(minutes._pick_title("Meeting 2026-11-21 9:05", ja), "動画スクールの無料体験")
        self.assertEqual(minutes._pick_title("Weekly sync", ja), "Weekly sync")  # a name the owner gave stays
        info = minutes._meeting_info({"created": minutes.datetime(2026, 11, 21, 13, 3).timestamp(), "duration": 1353}, ja)
        self.assertEqual(info, "## 会議の情報\n- 日時：2026-11-21 13:03–13:25（23分）\n- 参加者：田中")
        body = minutes.build_body(ja, "", info)
        self.assertTrue(body.startswith("## 会議の情報"))
        self.assertIn("## 要点\n- 8週間の体験の説明", body)
        self.assertIn("## 決まったこと", body)
        self.assertIn("| 資料を送る | Me | 2026-11-22 |", body)
        self.assertIn("## 決まらなかったこと・確認すること", body)
        en = minutes.sanitize({"title": "Plan", "summary": ["We planned"], "decisions": [], "todos": [], "topics": []})
        self.assertIn("- Who: not clear from the recording", minutes._meeting_info({"created": 1795000000, "duration": 60}, en))
        self.assertTrue(minutes.build_body(en, "").startswith("## Summary"))

    def test_an_old_english_minute_is_made_again_once(self):
        now = 1795000000.0
        old = {"id": "c" * 32, "title": "Recording 2026-11-21 13:03", "body": "## Summary\n- old", "status": "draft", "place": "",
               "at": now, "source": {"kind": "transcribe", "sid": SID}, "event": None, "calendar": None, "created": now, "updated": now}
        minutes._write(old)
        sess = [{"id": SID, "title": "Recording 2026-11-21 13:03", "status": "done", "created": CREATED, "duration": 600}]
        with mock.patch.object(minutes, "_http_json", return_value=sess), \
                mock.patch.object(minutes, "_http_text", return_value="A: hi\n"), \
                mock.patch.object(minutes, "summarize", return_value=minutes.sanitize(
                    {"title": "New plan", "summary": ["x"], "decisions": [], "todos": [], "topics": []})):
            self.assertEqual(minutes.sync(), 1)
            self.assertEqual(minutes.sync(), 0)  # once only
        rec = minutes.get("c" * 32)
        self.assertEqual(rec["title"], "New plan")
        self.assertTrue(rec["body"].startswith("## Meeting"))
        self.assertEqual(rec["source"]["v"], minutes.VERSION)

    def test_a_failing_old_minute_is_tried_three_times_at_most(self):
        now = 1795000000.0
        minutes._write({"id": "d" * 32, "title": "Recording 2026-11-17 10:02", "body": "## Summary\nNot made yet.", "status": "draft",
                        "place": "", "at": now, "source": {"kind": "transcribe", "sid": SID}, "event": None, "calendar": None,
                        "created": now, "updated": now})
        sess = [{"id": SID, "title": "Recording 2026-11-17 10:02", "status": "done", "created": CREATED}]
        calls = []
        with mock.patch.object(minutes, "_http_json", return_value=sess), \
                mock.patch.object(minutes, "_http_text", return_value="A: hi\n"), \
                mock.patch.object(minutes, "summarize", side_effect=lambda *a: calls.append(1)):
            for _ in range(5):
                minutes.sync()
        self.assertEqual(len(calls), 3)

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
