"""小さな道具: QR bits are right, nothing is sent unless asked, and a compressed PDF says the same thing."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desk"))
import utilities as ut


class QR(unittest.TestCase):
    def test_size_grows_with_the_text(self):
        self.assertEqual(len(ut.qr_matrix("a")), 21)  # version 1
        self.assertEqual(len(ut.qr_matrix("a" * 100)), 41)  # version 6
        self.assertEqual(len(ut.qr_matrix("a" * ut.QR_MAX_BYTES)), 177)  # version 40

    def test_finder_patterns(self):
        m = ut.qr_matrix("https://example.com/")
        n = len(m)
        row = [1, 1, 1, 1, 1, 1, 1]
        self.assertEqual(m[0][:7], row)
        self.assertEqual(m[0][n - 7:], row)
        self.assertEqual(m[n - 1][:7], row)
        self.assertEqual(m[n - 8][8], 1)  # the dark module

    def test_too_long_is_a_sentence(self):
        code, body = ut.qr("a" * (ut.QR_MAX_BYTES + 1))
        self.assertEqual(code, 413)
        self.assertIn("長すぎ", body["error"])

    def test_png_is_a_png_and_only_the_latest_stay(self):
        tokens = []
        for i in range(10):
            code, body = ut.qr(f"https://example.com/{i}")
            self.assertEqual(code, 200)
            tokens.append(body["file"])
        self.assertIsNone(ut.get_file(tokens[0]))
        self.assertTrue(ut.get_file(tokens[-1])["data"].startswith(b"\x89PNG"))

    @unittest.skipUnless(sys.platform == "darwin", "macOS only")
    def test_macos_can_read_it(self):
        """Apple's own reader, so a wrong table cannot pass by agreeing with itself."""
        with tempfile.TemporaryDirectory() as d:
            lines = []
            for i, n in enumerate((5, 40, 150, 400, 900)):
                text = ("https://example.com/日本語?" + "x" * n)
                with open(os.path.join(d, f"{i}.png"), "wb") as f:
                    f.write(ut._png(ut.qr_matrix(text), scale=6))
                lines.append(text)
            script = os.path.join(d, "read.swift")
            with open(script, "w") as f:
                f.write('import CoreImage\nimport Foundation\nlet det = CIDetector(ofType: CIDetectorTypeQRCode, context: nil, options: [CIDetectorAccuracy: CIDetectorAccuracyHigh])!\n'
                        'for p in CommandLine.arguments.dropFirst() { let i = CIImage(contentsOf: URL(fileURLWithPath: p))!; print((det.features(in: i).first as? CIQRCodeFeature)?.messageString ?? "NONE") }\n')
            env = dict(os.environ, CLANG_MODULE_CACHE_PATH=os.path.join(d, "mc"))
            out = subprocess.run(["swift", script] + [os.path.join(d, f"{i}.png") for i in range(5)], capture_output=True, text=True, env=env, timeout=240)
            if out.returncode:
                self.skipTest("swift is not available here")
            self.assertEqual(out.stdout.strip().split("\n"), lines)


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Shorten(unittest.TestCase):
    def test_sends_only_to_is_gd(self):
        seen = []

        def opener(req, timeout):
            seen.append(req.full_url)
            return Answer(json.dumps({"shorturl": "https://is.gd/abc"}).encode())

        self.assertEqual(ut.shorten("https://example.com/a b".replace(" ", "%20"), opener), (200, {"short": "https://is.gd/abc"}))
        self.assertTrue(seen[0].startswith("https://is.gd/create.php?"))

    def test_refused_before_anything_is_sent(self):
        def opener(*a, **k):
            raise AssertionError("must not be sent")

        for url in ("", "ftp://example.com", "javascript:alert(1)", "http://localhost:4747/", "http://192.168.1.2/", "http://mac.local/", "https://exa mple.com"):
            self.assertNotEqual(ut.shorten(url, opener)[0], 200, url)

    def test_service_errors_become_sentences(self):
        code, body = ut.shorten("https://example.com", lambda r, timeout: Answer(json.dumps({"errorcode": 3}).encode()))
        self.assertEqual(code, 502)
        self.assertIn("待って", body["error"])
        code, body = ut.shorten("https://example.com", mock.Mock(side_effect=urllib.error.URLError("down")))
        self.assertEqual(code, 502)
        code, body = ut.shorten("https://example.com", lambda r, timeout: Answer(json.dumps({"shorturl": "https://evil.example/x"}).encode()))
        self.assertEqual(code, 502)


class PDF(unittest.TestCase):
    def test_not_a_pdf(self):
        data = b"hello"
        self.assertEqual(ut.start_pdf(io.BytesIO(data), len(data), "a.pdf", "standard")[0], 415)

    def test_bad_level_and_cut_upload(self):
        self.assertEqual(ut.start_pdf(io.BytesIO(b"%PDF-"), 5, "a.pdf", "huge")[0], 400)
        self.assertEqual(ut.start_pdf(io.BytesIO(b"%PDF-"), 50, "a.pdf", "small")[0], 400)

    def test_names(self):
        self.assertEqual(ut.clean_name("../../報告書.PDF"), "報告書")
        self.assertEqual(ut.clean_name(""), "PDF")
        self.assertIn("filename*=UTF-8''", ut.disposition("報告書-圧縮.pdf"))
        self.assertNotIn("\n", ut.disposition("a\nb.pdf"))

    def test_old_jobs_are_removed(self):
        folder = tempfile.mkdtemp(prefix="desk-pdf-")
        ut._jobs["old"] = {"dir": folder, "name": "a", "before": 1, "at": 0}
        ut.purge(now=ut.KEEP_SECONDS + 1)
        self.assertNotIn("old", ut._jobs)
        self.assertFalse(os.path.exists(folder))


if __name__ == "__main__":
    unittest.main()
