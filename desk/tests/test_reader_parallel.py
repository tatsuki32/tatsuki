"""python3 -m unittest desk.tests.test_reader_parallel (from the repo root). A fake Anthropic answers; nothing leaves the Mac."""
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
TMP = tempfile.mkdtemp()
os.environ["DESK_READER_DIR"] = TMP
import reader  # noqa: E402
import reader_parallel as rp  # noqa: E402

P0 = "Despite widespread fear, we are fine. He gave up smoking."
P1 = "The clocks were striking thirteen."
ANSWER = {"paras": [
    {"id": 0, "sents": [{"src": "Despite widespread fear, we are fine.", "tr": "広く恐れられているにもかかわらず、私たちは大丈夫だ。"},
                        {"src": "He gave up smoking.", "tr": "彼はたばこをやめた。"}],
     "pairs": [{"i": 0, "src": "widespread", "tr": "広く", "idiom": False},
               {"i": 0, "src": "fear", "tr": "恐れ", "idiom": False},
               {"i": 1, "src": "gave up", "tr": "やめた", "idiom": True},
               {"i": 1, "src": "invented", "tr": "うそ", "idiom": False}]},
    {"id": 1, "sents": [{"src": "The clocks were striking thirteen.", "tr": "時計が13時を打っていた。"}],
     "pairs": [{"i": 0, "src": "clocks", "tr": "時計", "idiom": False}]}]}


class Fake:
    def __init__(self, answer=ANSWER, fail=False):
        self.answer, self.fail, self.calls = answer, fail, []

    def __call__(self, req, timeout=0):
        body = json.loads(req.data)
        self.calls.append(body)
        if self.fail:
            raise OSError("down")
        return io.BytesIO(json.dumps({"model": "claude-haiku-5-5", "usage": {"input_tokens": 1000, "output_tokens": 4000},
                                      "content": [{"type": "text", "text": json.dumps(self.answer)}]}).encode())


def book():
    code, b = reader.add_text(P0 + "\n\n" + P1, title="Test", lang="en")
    return b["id"]


class AlignTest(unittest.TestCase):
    def test_pairs_point_at_real_words(self):
        v = rp.align(P0, ANSWER["paras"][0], "ja")
        self.assertEqual(v["t"], "広く恐れられているにもかかわらず、私たちは大丈夫だ。彼はたばこをやめた。")
        got = [(P0[a:b], v["t"][c:d], k) for a, b, c, d, k in v["p"]]
        self.assertIn(("widespread", "広く", 0), got)
        self.assertIn(("gave up", "やめた", 1), got)
        self.assertNotIn("invented", [g[0] for g in got])  # not in the text: dropped
        self.assertEqual([P0[a:b] for a, b, c, d in v["s"]], ["Despite widespread fear, we are fine.", "He gave up smoking."])

    def test_english_target_joins_with_spaces(self):
        v = rp.align("一。二。", {"sents": [{"src": "一。", "tr": "One."}, {"src": "二。", "tr": "Two."}], "pairs": []}, "en")
        self.assertEqual(v["t"], "One. Two.")

    def test_target(self):
        self.assertEqual(rp.target("en"), "ja")
        self.assertEqual(rp.target("ja"), "en")


class ChapterTest(unittest.TestCase):
    def test_nothing_is_sent_before_asking(self):
        bid = book()
        code, st = rp.state(bid, 0)
        self.assertEqual((code, st["state"], st["total"]), (200, "none", 2))
        self.assertIsNone(rp.read(bid, 0))

    def test_translate_once_then_from_the_file(self):
        bid = book()
        fake = Fake()
        code, st = rp.start(bid, 0, key="k", opener=fake, wait=True)
        self.assertEqual((code, st["state"]), (200, "done"))
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("Japanese", fake.calls[0]["system"])
        self.assertEqual(st["paras"][1]["t"], "時計が13時を打っていた。")
        code, st = rp.start(bid, 0, key="k", opener=fake, wait=True)  # nothing missing: no call
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(st["usage"], {"in": 1000, "out": 4000, "cacheRead": 0, "cacheWrite": 0, "calls": 1, "usd": 0.0021})
        rp.start(bid, 0, again=True, key="k", opener=fake, wait=True)
        self.assertEqual(len(fake.calls), 2)
        code, st = rp.state(bid, 0)  # Translate again is paid too: it adds up, it does not start over
        self.assertEqual((st["usage"]["calls"], st["usage"]["out"], st["bookUsage"]["usd"]), (2, 8000, 0.0042))

    def test_failure_keeps_try_again(self):
        bid = book()
        code, st = rp.start(bid, 0, key="k", opener=Fake(fail=True), wait=True)
        self.assertEqual(st["state"], "error")
        self.assertTrue(all(p.get("err") for p in st["paras"]))
        code, st = rp.start(bid, 0, key="k", opener=Fake(), wait=True)
        self.assertEqual(st["state"], "done")

    def test_bad_ids(self):
        self.assertEqual(rp.state("../x", 0)[0], 404)
        self.assertEqual(rp.state(book(), 9)[0], 404)


class ChunkTest(unittest.TestCase):
    def test_chunks(self):
        paras = ["x" * 1000] * 7
        self.assertEqual(rp.chunks(paras, range(7)), [[0, 1, 2], [3, 4, 5], [6]])


if __name__ == "__main__":
    unittest.main()
