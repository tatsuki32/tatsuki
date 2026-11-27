"""Minutes insight: labels, the calendar event found by itself, the month's totals and the speaking review.
Nothing here reaches the transcriber, Claude or Google: MINUTES_FAKE answers for Claude, the rest is replaced in each test."""
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["MINUTES_DIR"] = tempfile.mkdtemp(prefix="desk-minutes-test-")
os.environ["MINUTES_FAKE"] = "1"

import minutes  # noqa: E402
import minutes_insight as mi  # noqa: E402

SID = "b" * 32
DAY = datetime(2026, 11, 20, 10, 2)  # the Mac's own time, like the Desk
AT = DAY.timestamp()


def iso(h, m=0, day=20):
    return datetime(2026, 11, day, h, m).astimezone().isoformat(timespec="seconds")


def event(eid, title, h1, h2, **marks):
    return {"id": eid + "_1", "uid": eid, "title": title, "start": iso(h1), "end": iso(h2), "allDay": False, "location": "",
            "meet": True, "mine": True, "guests": True, **marks}


def recording(title="Weekly sync", at=AT, length=3000, **more):
    now = time.time()
    rec = {"id": os.urandom(16).hex(), "title": title, "body": "## Summary\n- A plan was made\n\n## Transcript\nsecret words", "status": "draft",
           "place": "", "at": at, "source": {"kind": "transcribe", "sid": SID, "v": minutes.VERSION}, "event": None, "calendar": None,
           "created": now, "updated": now, "length": length, **more}
    minutes._write(rec)
    return rec


def note(title="Old notes", at=AT):
    return recording(title, at, 0, source={"kind": "transcribe", "sid": "c" * 32, "from": "notes"},
                     body="## Summary\nNot made yet. Press \"Make minutes\".\n\n## Original notes\nprivate notes")


def history(*events):
    mi._save("history.json", {"events": list(events), "from": datetime(2026, 10, 1).timestamp(), "fetchedAt": time.time()})


SEGS = [{"start": 0, "end": 30, "text": "なんか今日はちょっと確認したいんですが、なんか資料ありますか", "spk": "A"},
        {"start": 31, "end": 40, "text": "はい、あります。", "spk": "B"},
        {"start": 41, "end": 100, "text": "ちょっと見せてください。なんかいい感じですね？", "spk": "A"},
        {"start": 101, "end": 160, "text": "まあそうですね、なんか続けます。", "spk": "A"},
        {"start": 170, "end": 200, "text": "では次回までにやります。", "spk": "B"}]


class Insight(unittest.TestCase):
    def setUp(self):
        if "desk-minutes-test-" not in minutes.DIR:
            raise RuntimeError("minutes.DIR is not a test folder; run this test on its own")
        shutil.rmtree(minutes.DIR, ignore_errors=True)
        mi._busy.clear()

    # ----- labels -----

    def test_labels_are_given_once_and_the_persons_choice_stays(self):
        a, b = recording("Call with Acme"), recording("Family chat")
        self.assertEqual(mi.label_pass(limit=5), 2)
        self.assertEqual(mi.label_pass(limit=5), 0)  # nothing left to label
        lab = mi.labels()
        self.assertEqual([x["name"] for x in lab["genres"]], ["Test genre"])
        self.assertEqual([x["name"] for x in lab["clients"]], ["Acme"])
        self.assertEqual(minutes.get(a["id"])["client"], lab["clients"][0]["id"])
        self.assertEqual(minutes.get(b["id"])["client"], "")  # no outside party: No client
        got = mi.set_tag(b["id"], {"kind": "client", "name": "Globex"})
        self.assertEqual(got["tagBy"]["client"], "me")
        self.assertEqual(got["updated"], b["updated"])  # a label is not an edit of the text
        minutes.patch(b["id"], lambda cur: cur.pop("tagBy"))  # even if asked again, "me" would have been kept
        mi.set_tag(b["id"], {"kind": "client", "name": "globex "})  # the same label under another spelling
        self.assertEqual(len(mi.labels()["clients"]), 2)

    def test_a_note_without_a_summary_sends_only_its_title(self):
        r = note("Meeting with Acme")
        self.assertEqual(mi._summary_part(r), "")
        with mock.patch.object(mi, "_fake", return_value=False), mock.patch.object(mi, "_claude", return_value={"genre": "Sales", "client": ""}) as ask:
            mi.label_pass()
        self.assertNotIn("private notes", ask.call_args[0][0])
        self.assertIn("Meeting with Acme", ask.call_args[0][0])
        self.assertNotIn("secret words", mi._summary_part(recording()))  # the transcript under a summary is never sent either

    def test_a_hint_from_the_summary_needs_no_second_request(self):
        r = recording(tagHint={"genre": "AI adoption", "client": "Globex"})
        with mock.patch.object(mi, "_ask_labels") as ask:
            mi.label_pass()
        ask.assert_not_called()
        got = minutes.get(r["id"])
        self.assertNotIn("tagHint", got)
        names = {x["id"]: x["name"] for x in mi.labels()["genres"] + mi.labels()["clients"]}
        self.assertEqual((names[got["genre"]], names[got["client"]]), ("AI adoption", "Globex"))

    def test_renaming_to_an_existing_name_merges(self):
        a, b = recording("One"), recording("Two")
        mi.set_tag(a["id"], {"kind": "genre", "name": "AI"})
        mi.set_tag(b["id"], {"kind": "genre", "name": "AI adoption"})
        ai = minutes.get(a["id"])["genre"]
        lab = mi.put_label({"kind": "genre", "id": ai, "name": "ai adoption"})
        self.assertEqual([x["name"] for x in lab["genres"]], ["AI adoption"])
        rows = mi.decorate(minutes.view())["minutes"]
        self.assertEqual(len({r["genre"] for r in rows}), 1)  # both minutes show under the one label that is left
        self.assertEqual(mi.label_id("genre", "AI"), rows[0]["genre"])  # the old name leads to it too
        with self.assertRaises(minutes.MinutesError):
            mi.put_label({"kind": "genre", "id": ai, "name": "Again"})  # a merged label is gone

    def test_the_summary_prompt_carries_the_label_keys(self):
        mi.label_id("client", "Globex")
        p = minutes._prompt()
        self.assertIn('- "genre"', p)
        self.assertIn("Existing clients: Globex", p)
        self.assertTrue(p.endswith("\nTranscript:\n"))
        self.assertEqual(minutes.sanitize({"genre": "Sales", "client": "Acme", "summary": []})["client"], "Acme")

    # ----- the calendar event -----

    def test_one_event_at_the_same_time_is_linked_by_time(self):
        r = recording(length=50 * 60)  # 10:02, 50 minutes
        history(event("e1", "Something else entirely", 10, 11), event("e2", "Lunch", 12, 13))
        self.assertEqual(mi.match_pass(), 1)
        got = minutes.get(r["id"])
        self.assertEqual((got["event"]["uid"], got["event"]["how"], got["event"]["auto"]), ("e1", "time", True))
        self.assertIsNone(got["calendar"])  # nothing was written to Google
        self.assertEqual(got["updated"], r["updated"])
        self.assertEqual(mi.match_pass(), 0)

    def test_two_events_at_that_time_are_decided_by_content_or_suggested(self):
        r = recording("Weekly sync with the team", length=50 * 60)
        history(event("e1", "Focus time", 10, 11), event("e2", "Weekly sync", 10, 11))
        mi.match_pass()
        self.assertEqual((minutes.get(r["id"])["event"]["uid"], minutes.get(r["id"])["event"]["how"]), ("e2", "content"))
        shutil.rmtree(minutes.DIR)
        r = recording("Budget talk", length=50 * 60)
        history(event("e1", "Focus time", 10, 11), event("e2", "Weekly sync", 10, 11))
        mi.match_pass()
        got = minutes.get(r["id"])
        self.assertIsNone(got["event"])
        self.assertEqual({e["uid"] for e in got["suggest"]}, {"e1", "e2"})
        self.assertEqual(mi.match_pass(), 0)  # asked once for these candidates, not every minute

    def test_time_kept_for_oneself_does_not_hide_the_meeting(self):
        r = recording("Budget talk", length=50 * 60)
        history(event("e1", "Focus time", 10, 11, meet=False, guests=False), event("e2", "Call", 10, 11))
        mi.match_pass()
        self.assertEqual((minutes.get(r["id"])["event"]["uid"], minutes.get(r["id"])["event"]["how"]), ("e2", "time"))

    def test_a_late_start_needs_the_content(self):
        r = recording("Chat", at=datetime(2026, 11, 20, 10, 25).timestamp(), length=30 * 60)
        history(event("e1", "Weekly sync", 10, 11))
        mi.match_pass()
        self.assertIsNone(minutes.get(r["id"])["event"])
        self.assertEqual([e["uid"] for e in minutes.get(r["id"])["suggest"]], ["e1"])

    def test_notes_are_matched_within_their_day_by_content(self):
        r = note("Globex meeting notes", at=datetime(2026, 11, 20, 12).timestamp())
        history(event("e1", "Globex", 15, 16), event("e2", "globex", 15, 16, start=iso(15, day=21), end=iso(16, day=21)))
        mi.match_pass()
        self.assertEqual(minutes.get(r["id"])["event"]["uid"], "e1")  # the other one is on another day

    def test_not_this_meeting_is_remembered(self):
        r = recording(length=50 * 60)
        history(event("e1", "Call", 10, 11))
        mi.match_pass()
        got = mi.not_this(r["id"], {})
        self.assertIsNone(got["event"])
        self.assertEqual(got["eventNo"], ["e1_1"])
        mi.match_pass()
        self.assertIsNone(minutes.get(r["id"])["event"])
        self.assertNotIn("suggest", minutes.get(r["id"]))

    def test_an_unlink_by_the_person_is_remembered_and_their_link_is_kept(self):
        r = recording(length=50 * 60)
        history(event("e1", "Call", 10, 11), event("e2", "Other", 14, 15))
        mi.match_pass()
        minutes.put(r["id"], {"event": None})
        mi.match_pass()
        self.assertIsNone(minutes.get(r["id"])["event"])
        chosen = minutes.put(r["id"], {"event": mi._slim(event("e2", "Other", 14, 15))})
        self.assertNotIn("auto", chosen["event"])
        mi.match_pass()
        self.assertEqual(minutes.get(r["id"])["event"]["uid"], "e2")

    def test_when_claude_cannot_be_asked_it_is_tried_again_later(self):
        r = recording("Weekly sync", length=50 * 60)
        history(event("e1", "Focus time", 10, 11), event("e2", "Weekly sync", 10, 11))
        with mock.patch.object(mi, "_ask_event", return_value=None):
            mi.match_pass()
        self.assertTrue(minutes.get(r["id"])["match"]["failed"])
        self.assertEqual(mi.match_pass(), 0)  # not within the hour
        minutes.patch(r["id"], lambda cur: cur["match"].update(at=time.time() - 4000))
        mi.match_pass()
        self.assertEqual(minutes.get(r["id"])["event"]["uid"], "e2")

    def test_history_keeps_no_addresses(self):
        import gcal_ics
        ics_text = "\r\n".join(["BEGIN:VCALENDAR", "BEGIN:VEVENT", "UID:u1", "SUMMARY:Call", "DTSTART:20261120T030000Z", "DTEND:20261120T040000Z",
                              "ORGANIZER;CN=Me:mailto:me@example.com", "ATTENDEE;PARTSTAT=ACCEPTED:mailto:guest@example.com",
                              "LOCATION:https://meet.google.com/abc-defg-hij", "END:VEVENT", "END:VCALENDAR"])
        recording()
        n = mi.store_history(gcal_ics.parse(ics_text, owner="me@example.com"), now=datetime(2026, 11, 27).timestamp())
        self.assertEqual(n, 1)
        with open(mi._file("history.json"), encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn("example.com", raw.replace("u1", ""))
        e = mi.history()["events"][0]
        self.assertTrue(e["meet"] and e["mine"] and e["guests"])

    # ----- the month -----

    def test_the_month_counts_each_meeting_once(self):
        now = datetime(2026, 11, 27, 9).timestamp()
        a = recording("Linked", length=40 * 60)
        recording("Not linked", at=datetime(2026, 11, 21, 9).timestamp(), length=30 * 60)
        note("Notes only", at=datetime(2026, 11, 22, 12).timestamp())
        recording("October", at=datetime(2026, 10, 5, 9).timestamp(), length=600)
        history(event("e1", "Linked", 10, 11),
                event("e2", "Covered by the unlinked recording", 9, 10, start=iso(9, day=21), end=iso(10, day=21)),
                event("e3", "Not recorded", 15, 16),
                event("e4", "Alone", 16, 17, meet=False, guests=False),
                event("e5", "Not answered", 17, 18, mine=False),
                event("e6", "Still to come", 10, 11, start=iso(10, day=28), end=iso(11, day=28)),
                event("e7", "Did not happen", 13, 14))
        minutes.put(a["id"], {"event": mi._slim(event("e1", "Linked", 10, 11))})
        m = mi.month("2026-11", now=now)
        self.assertEqual(m["totals"]["recorded"], {"n": 3, "secs": 70 * 60, "untimed": 1})
        self.assertEqual(m["totals"]["notRecorded"], {"n": 2, "secs": 2 * 3600})
        self.assertEqual(m["totals"]["meetings"], 5)
        self.assertEqual(m["prev"]["meetings"], 1)
        mi.put_skip({"id": "e7_1"})
        m = mi.month("2026-11", now=now)
        self.assertEqual(m["totals"]["notRecorded"], {"n": 1, "secs": 3600})
        self.assertEqual([r["skipped"] for r in m["rows"] if r["id"] == "e7_1"], [True])
        mi.put_skip({"id": "e7_1", "on": False})
        self.assertEqual(mi.month("2026-11", now=now)["totals"]["meetings"], 5)
        self.assertTrue(m["calendar"]["covered"])
        self.assertFalse(mi.month("2026-08", now=now)["calendar"]["covered"])
        with self.assertRaises(minutes.MinutesError):
            mi.month("November")

    def test_the_month_groups_by_label(self):
        a, b = recording("A", length=1200), recording("B", length=600)
        mi.set_tag(a["id"], {"kind": "client", "name": "Acme"})
        m = mi.month("2026-11", now=datetime(2026, 11, 27).timestamp())
        self.assertEqual([(g["name"], g["n"], g["secs"]) for g in m["byClient"]], [("Acme", 1, 1200), ("", 1, 600)])
        self.assertEqual(b["id"] in {r["id"] for r in m["rows"]}, True)

    # ----- the review -----

    def test_numbers_are_counted_for_the_owners_voice(self):
        s = mi.speak_stats(SEGS, "A")
        self.assertEqual((s["mine"], s["total"]), (148, 187))
        self.assertEqual(s["share"], round(148 / 187, 3))
        self.assertEqual(s["longest"], 119)  # 41 to 160: two lines in a row
        self.assertEqual(s["questions"], 2)
        self.assertEqual(s["fillers"][0], {"w": "なんか", "n": 4})
        self.assertEqual(mi.speak_stats(SEGS, "B")["mine"], 39)
        plain = mi.speak_stats([{**x, "spk": None} for x in SEGS], "")
        self.assertEqual((plain["split"], plain["share"]), (False, None))

    def full(self, mode="inperson", segs=SEGS):
        return {"id": SID, "mode": mode, "speakers": {"A": "Speaker A", "B": "Speaker B"}, "segments": segs}

    def test_the_review_guesses_the_owner_and_changes_when_told(self):
        r = recording()
        with mock.patch.object(mi, "_session", return_value=self.full()):
            self.assertEqual(mi.review_pass(), 1)
            rv = minutes.get(r["id"])["review"]
            self.assertEqual((rv["me"], rv["meBy"]), ("A", "claude"))
            self.assertEqual(rv["advice"]["shorter"], [{"point": "Test shorter"}])  # 5 minutes saved in a 3-minute talk is dropped
            self.assertEqual(rv["advice"]["spoke"][0]["quote"], "なんか今日はちょ")
            self.assertNotIn("quote", rv["advice"]["spoke"][1])  # words the owner never said are not shown as a quote
            self.assertEqual(mi.review_pass(), 0)
            with mock.patch.object(mi, "_later") as later:
                got = mi.set_voice(r["id"], {"me": "B"})
            later.assert_called_once()
            self.assertEqual((got["review"]["me"], got["review"]["meBy"], got["review"]["stats"]["mine"]), ("B", "me", 39))
            self.assertIsNone(got["review"]["advice"])  # the old advice was about someone else's voice
            mi.review_pass()
            rv = minutes.get(r["id"])["review"]
            self.assertEqual((rv["me"], rv["meBy"]), ("B", "me"))
            self.assertEqual(rv["advice"]["next"], "Test next")
            with self.assertRaises(minutes.MinutesError):
                mi.set_voice(r["id"], {"me": "Z"})
        self.assertEqual(minutes.get(r["id"])["updated"], r["updated"])

    def test_an_online_meeting_uses_the_owners_own_track(self):
        r = recording()
        segs = [{**x, "spk": "me" if x["spk"] == "A" else "them"} for x in SEGS]
        with mock.patch.object(mi, "_session", return_value=self.full("meeting", segs)):
            mi.review_pass()
        self.assertEqual(minutes.get(r["id"])["review"]["meBy"], "track")

    def test_notes_and_missing_recorders_get_no_review(self):
        n, r = note(), recording()
        with mock.patch.object(mi, "_session", return_value=None):
            self.assertEqual(mi.review_pass(), 0)
        self.assertNotIn("review", minutes.get(r["id"]))
        with mock.patch.object(mi, "_session", return_value="gone"):
            mi.review_pass()
        self.assertTrue(minutes.get(r["id"])["review"]["empty"])
        self.assertNotIn("review", minutes.get(n["id"]))

    def test_without_claude_the_numbers_are_still_there(self):
        r = recording()
        with mock.patch.object(mi, "_session", return_value=self.full()), mock.patch.object(mi, "_ask_review", return_value=None):
            mi.review_pass()
        rv = minutes.get(r["id"])["review"]
        self.assertEqual((rv["me"], rv["meBy"], rv["advice"], rv["tries"]), ("A", "guess", None, 1))  # the one who spoke most

    def test_the_review_is_not_shared(self):
        import minutes_share
        r = recording()
        with mock.patch.object(mi, "_session", return_value=self.full()):
            mi.review_pass()
        self.assertNotIn("Test spoke", minutes_share.doc_body(minutes.get(r["id"])))

    def test_the_months_speaking_numbers_and_focus(self):
        a, b = recording("A"), recording("B")
        with mock.patch.object(mi, "_session", return_value=self.full()):
            mi.review_pass()
        now = datetime(2026, 11, 27).timestamp()
        self.assertEqual(mi.focus_pass(now=now), 1)
        self.assertEqual(mi.focus_pass(now=now), 0)
        m = mi.month("2026-11", now=now)
        self.assertEqual((m["speaking"]["n"], m["speaking"]["share"], m["focus"]), (2, round(148 / 187, 3), "Test next"))
        self.assertEqual(m["speaking"]["fillers"][0], {"w": "なんか", "n": 8})

    # ----- the page's requests -----

    def test_handle_routes(self):
        r = recording()
        self.assertEqual(mi.handle("POST", ["minutes", r["id"], "tag"], {"kind": "genre", "name": "Sales"})[0], 200)
        self.assertEqual(mi.handle("PUT", ["minutes", "skip"], {"id": "x_1"}), (200, {"ok": True}))
        self.assertIsNone(mi.handle("PUT", ["minutes", r["id"]], {"title": "x"}))  # minutes.put's own
        self.assertEqual(mi.handle_get("/api/minutes/insights", {"month": ["2026-11"]})[0], 200)
        self.assertEqual(mi.handle_get("/api/minutes/insights", {"month": ["x"]})[0], 400)
        view = mi.decorate(minutes.view())
        self.assertEqual(view["labels"]["genres"][0]["name"], "Sales")


if __name__ == "__main__":
    unittest.main()
