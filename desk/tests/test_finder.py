"""python3 -m unittest desk.tests.test_finder (from the repo root). No network: the opener is fake."""
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("LIFELOG_DATA", "/nonexistent")  # never touch the keychain
import finder  # noqa: E402

PLACES = [{"id": "v:mailclean", "kind": "screen", "name": "Inbox Cleanup", "desc": "unsubscribe"},
          {"id": "v:money", "kind": "screen", "name": "Money", "desc": "Wise"}]


def fake(answer, sent):
    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def opener(req, timeout):
        sent.append(json.loads(req.data))
        return R(json.dumps({"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(answer)}]}).encode())
    return opener


class FinderTest(unittest.TestCase):
    def test_keeps_only_known_ids(self):
        sent = []
        out = finder.ask("メールの配信停止", PLACES, key="k", opener=fake(
            {"reply": "Here.", "picks": [{"id": "v:mailclean", "why": "unsubscribe"}, {"id": "v:made-up", "why": "x"}]}, sent))
        self.assertEqual([p["id"] for p in out["picks"]], ["v:mailclean"])
        self.assertIn("Inbox Cleanup", json.dumps(sent[0], ensure_ascii=False))
        self.assertEqual(sent[0]["model"], "claude-haiku-5-5")

    def test_history_goes_first(self):
        sent = []
        finder.ask("not that one", PLACES, [{"q": "money", "a": "Found | Money"}], key="k",
                   opener=fake({"reply": "", "picks": []}, sent))
        self.assertEqual([m["role"] for m in sent[0]["messages"]], ["user", "assistant", "user"])

    def test_no_key_is_unavailable(self):
        self.assertEqual(finder.handle({"q": "x", "places": PLACES})["ok"], False)

    def test_empty_question(self):
        with self.assertRaises(ValueError):
            finder.ask(" ", PLACES, key="k")


if __name__ == "__main__":
    unittest.main()
