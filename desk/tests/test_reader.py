"""python3 -m unittest desk.tests.test_reader (from the repo root). Books go to a temp folder; no `say`, no helper build."""
import io
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
TMP = tempfile.mkdtemp()
os.environ["DESK_READER_DIR"] = TMP
import reader  # noqa: E402

OPF = """<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/" version="3.0">
<metadata><dc:title>Little Book</dc:title><dc:creator>A. Writer</dc:creator><dc:language>en</dc:language></metadata>
<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="c1" href="text/c1.xhtml" media-type="application/xhtml+xml"/><item id="c2" href="text/c2.xhtml" media-type="application/xhtml+xml"/></manifest>
<spine><itemref idref="nav"/><itemref idref="c1"/><itemref idref="c2"/></spine></package>"""
C1 = "<html><head><title>x</title><style>p{}</style></head><body><h1>Chapter One</h1><p>It was a bright cold day in April. The clocks were striking thirteen.</p><p>Second paragraph here, with <i>emphasis</i> inside it and enough words to count as text for the reader to show.</p></body></html>"
C2 = "<html><body><h2>Chapter Two</h2><p>" + "More words in the second chapter. " * 8 + "</p></body></html>"


def make_epub():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", OPF)
        z.writestr("OEBPS/nav.xhtml", "<html><body><nav><ol><li>Chapter One</li></ol></nav></body></html>")
        z.writestr("OEBPS/text/c1.xhtml", C1)
        z.writestr("OEBPS/text/c2.xhtml", C2)
    return buf.getvalue()


class EpubTest(unittest.TestCase):
    def test_chapters_from_the_spine(self):
        title, author, lang, chapters = reader.parse_epub(make_epub())
        self.assertEqual((title, author, lang), ("Little Book", "A. Writer", "en"))
        self.assertEqual([c["title"] for c in chapters], ["Chapter One", "Chapter Two"])
        self.assertEqual(len(chapters[0]["paras"]), 2)
        self.assertIn("emphasis inside it", chapters[0]["paras"][1])
        self.assertNotIn("p{}", " ".join(chapters[0]["paras"]))

    def test_add_file_saves_and_lists(self):
        code, m = reader.add_file(make_epub(), "little.epub")
        self.assertEqual(code, 200)
        self.assertEqual(m["title"], "Little Book")
        self.assertEqual(len(m["chapters"]), 2)
        self.assertEqual(reader.list_books()[0]["id"], m["id"])
        code, full = reader.get(m["id"])
        self.assertEqual(len(full["chapters_full"][1]["paras"]), 1)
        code, m2 = reader.set_pos(m["id"], {"ch": 1, "para": 0})
        self.assertEqual(m2["pos"], {"ch": 1, "para": 0})
        self.assertGreater(m2["progress"], 0.5)
        reader.remove(m["id"])
        self.assertEqual(reader.list_books(), [])

    def test_entity_declarations_are_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("META-INF/container.xml", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><container><rootfiles><rootfile full-path="c.opf"/></rootfiles></container>')
            z.writestr("c.opf", OPF)
        with self.assertRaises(ValueError):
            reader.parse_epub(buf.getvalue())


class TextTest(unittest.TestCase):
    def test_pasted_text_splits_into_chapters_and_paragraphs(self):
        text = "CHAPTER 1\n\nFirst paragraph.\nStill the first.\n\nSecond paragraph.\n\nChapter 2\n\nThird paragraph."
        chs = reader.chapters_from_text(text)
        self.assertEqual([c["title"] for c in chs], ["CHAPTER 1", "Chapter 2"])
        self.assertEqual(chs[0]["paras"], ["First paragraph. Still the first.", "Second paragraph."])

    def test_lines_without_blank_lines_are_paragraphs(self):
        text = "\n".join(f"Line number {i} of a pasted article with words." for i in range(10))
        self.assertEqual(len(reader.chapters_from_text(text)[0]["paras"]), 10)

    def test_language_guess(self):
        self.assertEqual(reader.detect_lang("The clocks were striking thirteen and the wind was cold."), "en")
        self.assertEqual(reader.detect_lang("これは日本語の文章です。時計が十三時を打った。"), "ja")
        self.assertEqual(reader.detect_lang("Это было ясное холодное апрельское утро, и часы пробили тринадцать."), "ru")
        self.assertEqual(reader.detect_lang("Era un día frío y claro de abril, y los relojes daban las trece."), "es")

    def test_add_text_and_edit(self):
        code, m = reader.add_text("Hello world. This is a small text about nothing in particular.", "", "", "")
        self.assertEqual(code, 200)
        self.assertEqual(m["lang"], "en")
        code, m2 = reader.edit(m["id"], {"title": "Hello", "lang": "fr"})
        self.assertEqual((m2["title"], m2["lang"]), ("Hello", "fr"))
        code, _ = reader.add_text("   ", "", "", "")
        self.assertEqual(code, 400)

    def test_pdf_lines_become_paragraphs(self):
        text = "Chapter 1\nThe first line of a paragraph\nthat continues here.\nNext one starts here and ends.\n12\n\fAnother page begins\nand ends here."
        chs = reader.chapters_from_pdf(text)
        self.assertEqual(chs[0]["title"], "Chapter 1")
        self.assertEqual(chs[0]["paras"][0], "The first line of a paragraph that continues here.")
        self.assertEqual(chs[0]["paras"][1], "Next one starts here and ends.")
        self.assertNotIn("12", chs[0]["paras"])
        self.assertEqual(chs[0]["paras"][2], "Another page begins and ends here.")


class VoiceTest(unittest.TestCase):
    def test_voice_parsing_and_filter(self):
        reader._voices = [{"name": "Samantha", "lang": "en_US"}, {"name": "Kyoko", "lang": "ja_JP"}]
        self.assertEqual([v["name"] for v in reader.voices_for("ja")], ["Kyoko"])
        self.assertEqual(reader.speak({"text": ""})[0], 400)


if __name__ == "__main__":
    unittest.main()
