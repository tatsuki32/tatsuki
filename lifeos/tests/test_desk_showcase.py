"""見せる素材: the folder page is read as it is, a failure keeps the link, and only the configured file can be opened."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desk"))
import showcase as sc

ROOT_ID, SUB_ID, PHOTO_ID, VIDEO_ID = "rootfolder01", "subfolder001", "photo0000001", "video0000001"


def entry(fid, title, href, icon=""):
    return (
        f'<div class="flip-entry" id="entry-{fid}" tabindex="0"><a href="{href}" target="_blank">{icon}'
        f'<div class="flip-entry-title">{title}</div></a></div>'
    )


PARENT = "<title>Press &amp; PR</title><div class=\"flip-entries\">" + entry(
    SUB_ID, "Snaps", f"https://drive.google.com/drive/folders/{SUB_ID}"
) + entry(
    PHOTO_ID, "Face.jpg", f"https://drive.google.com/file/d/{PHOTO_ID}/view",
    '<img src="https://drive-thirdparty.googleusercontent.com/16/type/image/png">',
) + "</div>"
CHILD = "<title>Snaps</title><div class=\"flip-entries\">" + entry(
    VIDEO_ID, "Clip.mp4", f"https://drive.google.com/file/d/{VIDEO_ID}/view",
    '<img src="https://drive-thirdparty.googleusercontent.com/16/type/video/mp4">',
) + "</div>"
LOGIN = "<title>Sign in</title><form>password</form>"


def pages(table):
    def fetch(fid):
        if fid not in table:
            raise OSError("no page")
        return table[fid]
    return fetch


class Listing(unittest.TestCase):
    def test_reads_title_and_kinds(self):
        title, entries = sc.parse_listing(PARENT)
        self.assertEqual(title, "Press & PR")
        self.assertEqual([(e["id"], e["kind"]) for e in entries], [(SUB_ID, "folder"), (PHOTO_ID, "photo")])

    def test_a_login_page_is_not_an_empty_folder(self):
        self.assertIsNone(sc.parse_listing(LOGIN))

    def test_goes_into_folders_inside(self):
        got = sc.read_folders(ROOT_ID, pages({ROOT_ID: PARENT, SUB_ID: CHILD}))
        self.assertEqual([f["title"] for f in got], ["Press & PR", "Snaps"])
        self.assertEqual([i["kind"] for i in got[0]["items"]], ["photo"])
        self.assertEqual(got[1]["items"][0]["url"], f"https://drive.google.com/file/d/{VIDEO_ID}/view")
        self.assertIn(VIDEO_ID, got[1]["items"][0]["thumb"])

    def test_an_inside_folder_that_fails_keeps_its_name_and_link(self):
        got = sc.read_folders(ROOT_ID, pages({ROOT_ID: PARENT}))
        self.assertTrue(got[0]["ok"])
        self.assertEqual((got[1]["title"], got[1]["ok"]), ("Snaps", False))
        self.assertIn(SUB_ID, got[1]["url"])

    def test_a_folder_inside_itself_ends(self):
        loop = "<div class=\"flip-entries\">" + entry(ROOT_ID, "Again", f"https://drive.google.com/drive/folders/{ROOT_ID}") + "</div>"
        self.assertEqual(len(sc.read_folders(ROOT_ID, pages({ROOT_ID: loop}))), 1)


class Cache(unittest.TestCase):
    def setUp(self):
        sc._cache.update(key=None, at=0, ok=False, folders=None)

    def test_a_failure_keeps_the_good_list(self):
        good = sc.drive_view(ROOT_ID, now=1000, fetch=pages({ROOT_ID: PARENT, SUB_ID: CHILD}))
        self.assertTrue(good["ok"])
        later = sc.drive_view(ROOT_ID, now=1000 + sc.FRESH + 1, fetch=pages({}))
        self.assertEqual(later["folders"], good["folders"])

    def test_a_good_list_is_not_asked_for_again_soon(self):
        sc.drive_view(ROOT_ID, now=1000, fetch=pages({ROOT_ID: PARENT, SUB_ID: CHILD}))
        fetch = mock.Mock(side_effect=OSError)
        sc.drive_view(ROOT_ID, now=1001, fetch=fetch)
        fetch.assert_not_called()

    def test_never_read_says_so_and_keeps_the_link(self):
        got = sc.drive_view(ROOT_ID, now=1000, fetch=pages({}))
        self.assertFalse(got["ok"])
        self.assertIn(ROOT_ID, got["url"])


class Conf(unittest.TestCase):
    def setUp(self):
        sc._cache.update(key=None, at=0, ok=False, folders=None)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.file = os.path.join(self.dir.name, "page.html")
        with open(self.file, "w") as f:
            f.write("x")

    def conf(self, **values):
        path = os.path.join(self.dir.name, "showcase.json")
        with open(path, "w") as f:
            json.dump(values, f)
        return mock.patch.object(sc, "CONF", path)

    def test_nothing_set_shows_nothing(self):
        with self.conf():
            self.assertEqual(sc.view(), {"site": None, "local": None, "drive": None})

    def test_only_https_sites_and_a_real_folder_id(self):
        with self.conf(siteUrl="http://example.com", driveFolder="../x"):
            got = sc.view()
        self.assertIsNone(got["site"])
        self.assertIsNone(got["drive"])

    def test_the_page_gets_the_file_name_not_its_path(self):
        with self.conf(localFile=self.file):
            self.assertEqual(sc.view()["local"], {"name": "page.html", "exists": True})

    def test_opens_only_the_configured_file(self):
        with self.conf(localFile=self.file), mock.patch.object(sc.subprocess, "run") as run:
            self.assertTrue(sc.open_local())
        run.assert_called_once_with(["/usr/bin/open", self.file], check=True, timeout=10)

    def test_a_missing_file_is_not_opened(self):
        with self.conf(localFile=self.file + ".gone"), mock.patch.object(sc.subprocess, "run") as run:
            self.assertFalse(sc.open_local())
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
