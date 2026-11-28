"""Speak into a text box: folder names, the silence rule (no microphone, no whisper)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import voice  # noqa: E402


class Voice(unittest.TestCase):
    def test_only_hex_ids(self):
        for bad in ("../x", "a" * 31, "g" * 32, None):
            with self.assertRaises(voice.VoiceError):
                voice._folder(bad)
        self.assertTrue(voice._folder("a" * 32).endswith(os.path.join("sessions", "a" * 32)))
        parts = voice._folder("a" * 32).split(os.sep)
        self.assertTrue(parts[-3].startswith("transcribe"))  # the mic app refuses other folders

    def test_unknown_recording(self):
        code, body = voice.handle("GET", ["b" * 32], {})
        self.assertEqual(code, 400)
        self.assertEqual(voice.handle("POST", ["x"], {})[0], 404)

    def test_short_audio_is_nothing(self):
        self.assertEqual(voice.transcribe("/nonexistent.pcm"), "")


if __name__ == "__main__":
    unittest.main()
