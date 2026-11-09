"""Files placed on a note: only real images and PDFs, stored once under a name made from their content."""
import os
import tempfile
import unittest

from lifeos.tests import desk_import  # noqa: F401
import server

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
PDF = b"%PDF-1.7\n" + b"0" * 32


class Uploads(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.was, server.UPLOADS = server.UPLOADS, self.dir.name
        self.addCleanup(lambda: setattr(server, "UPLOADS", self.was))

    def test_a_real_image_and_pdf_are_kept(self):
        for data, ext in ((PNG, ".png"), (PDF, ".pdf")):
            url = server.save_upload(data)
            self.assertTrue(url.startswith("/uploads/") and url.endswith(ext))
            self.assertTrue(server.UPLOAD_NAME.match(url.rsplit("/", 1)[1]))

    def test_the_same_file_is_stored_once(self):
        self.assertEqual(server.save_upload(PNG), server.save_upload(PNG))
        self.assertEqual(len(os.listdir(self.dir.name)), 1)

    def test_svg_and_html_are_refused_whatever_they_are_called(self):
        for data in (b"<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>", b"<html></html>", b""):
            with self.assertRaises(ValueError):
                server.save_upload(data)
        self.assertEqual(os.listdir(self.dir.name), [])

    def test_a_path_cannot_be_named(self):
        for name in ("../notes.json", "a.png", "0" * 32 + ".svg", "0" * 32 + ".png/x"):
            self.assertIsNone(server.UPLOAD_NAME.match(name))


if __name__ == "__main__":
    unittest.main()
