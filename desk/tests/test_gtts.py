"""desk/gtts.py against a fake Google (desk/tests/fake_gtts.py). python3 -m unittest desk.tests.test_gtts"""
import os
import socket
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
with socket.socket() as _s:
    _s.bind(("127.0.0.1", 0))
    PORT = _s.getsockname()[1]
TMP = tempfile.mkdtemp()
os.environ["DESK_READER_DIR"] = TMP
os.environ["GTTS_URL"] = f"http://127.0.0.1:{PORT}"
os.environ["GTTS_KEY"] = "test-key"

import fake_gtts  # noqa: E402
import gtts  # noqa: E402
import reader  # noqa: E402

gtts.DIR = os.path.join(TMP, "tts")
gtts.CACHE, gtts.USAGE, gtts.VOICES = os.path.join(gtts.DIR, "cache"), os.path.join(gtts.DIR, "usage.json"), os.path.join(gtts.DIR, "voices.json")


class GttsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.log = fake_gtts.serve(PORT)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        del self.log[:]
        for p in (gtts.USAGE, gtts.VOICES):
            if os.path.exists(p):
                os.remove(p)
        os.environ["GTTS_KEY"] = "test-key"

    def test_voices_hd_first_and_short(self):
        v = gtts.voices("en")
        names = [x["name"] for x in v]
        self.assertEqual(names[:2], ["en-US-Chirp3-HD-Aoede", "en-US-Chirp3-HD-Charon"])  # favourites first
        self.assertNotIn("en-US-Neural2-A", names)  # HD exists, so no lesser voices
        self.assertIn("en-GB-Chirp3-HD-Kore", names)
        self.assertTrue(all(x["id"].startswith("google:") for x in v))
        self.assertEqual([x["name"] for x in gtts.voices("eo")], [])

    def test_key_goes_in_header_never_url(self):
        gtts.voices("ru")
        gtts.speak("Привет.", "ru-RU-Chirp3-HD-Leda")
        for method, path, k, extra in self.log:
            self.assertEqual(k, "test-key")
            self.assertNotIn("test-key", path + (extra if isinstance(extra, str) else ""))

    def test_one_sentence_sent_once_and_counted(self):
        a = gtts.speak("Every age has its afflictions.", "en-US-Chirp3-HD-Aoede", 1.0)
        b = gtts.speak("Every age has its afflictions.", "en-US-Chirp3-HD-Aoede", 1.0)
        self.assertEqual(a, b)
        self.assertTrue(a.startswith(b"ID3"))
        posts = [x for x in self.log if x[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][3]["input"]["text"], "Every age has its afflictions.")
        self.assertEqual(posts[0][3]["voice"]["languageCode"], "en-US")
        self.assertEqual(gtts.usage()["used"], len("Every age has its afflictions."))

    def test_voice_without_speed_retries_plain(self):
        gtts.speak("Hello there.", "en-US-Chirp3-HD-Zubenelgenubi", 1.2)
        posts = [x for x in self.log if x[0] == "POST"]
        self.assertEqual(len(posts), 2)
        self.assertNotIn("speakingRate", posts[1][3]["audioConfig"])

    def test_monthly_limit_stops_sending(self):
        gtts._write(gtts.USAGE, {"limit": 10, "months": {}})
        with self.assertRaises(gtts.Unavailable):
            gtts.speak("A sentence longer than ten characters.", "en-US-Chirp3-HD-Aoede")
        self.assertFalse([x for x in self.log if x[0] == "POST"])

    def test_no_key_no_voices(self):
        os.environ["GTTS_KEY"] = ""
        self.assertEqual(gtts.voices("en"), [])
        with self.assertRaises(gtts.Unavailable):
            gtts.speak("Hi there.", "en-US-Chirp3-HD-Aoede", 0.95)

    @unittest.skipUnless(sys.platform == "darwin", "say is macOS only")
    def test_reader_falls_back_to_the_mac(self):
        os.environ["GTTS_KEY"] = ""
        code, out = reader.speak({"text": "Fall back please.", "lang": "en", "voice": "google:en-US-Chirp3-HD-Aoede"})
        self.assertEqual(code, 200)
        self.assertFalse(out.startswith(b"ID3"))  # the Mac's m4a

    def test_reader_uses_google(self):
        code, out = reader.speak({"text": "Read by Google.", "lang": "en", "voice": "google:en-US-Chirp3-HD-Aoede"})
        self.assertEqual((code, out[:3]), (200, b"ID3"))


if __name__ == "__main__":
    unittest.main()
