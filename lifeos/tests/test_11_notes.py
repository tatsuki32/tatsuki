import contextlib
import io
import json
import os
import re

from lifeos.lib import md, paths
from lifeos.lib.tools import load_tool
from lifeos.tests.sandbox import SandboxCase

memos = load_tool("11-notes", "memos")
links = load_tool("11-notes", "links")
summary = load_tool("11-notes", "summary")
core = load_tool("11-notes", "core")
cli = load_tool("11-notes", "cli")
desk_notes = load_tool("11-notes", "desk_notes")


def write(rel, text):
    p = paths.data_path(rel)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def memo_file(name, text, at="2026-11-03T09:00:00+07:00", kind=""):
    write("notes/" + name, "---\nat: {}\nkind: {}\n---\n{}\n".format(at, kind, text))


def links_in(body):
    """Same as linksIn in desk/index.html."""
    found = [x.strip() for x in re.findall(r"\[([^\[\]\n]+)\]", body)]
    return [x for x in found if not x.startswith("https://")]


def norm_title(t):
    return links.norm(t)


def page_for(title):
    t = norm_title(title)
    return next((n for n in desk_notes.load() if not n.get("deleted") and norm_title(n.get("title")) == t), None)


def backlinks(note):
    """Same as backlinks in desk/index.html, for notes."""
    t = norm_title(note["title"])
    return [n for n in desk_notes.load() if not n.get("deleted") and n["id"] != note["id"]
            and any(norm_title(x) == t for x in links_in(n["body"]))]


class Base(SandboxCase):
    def setUp(self):
        super().setUp()
        memos.set_image_reader(None)
        links.set_cosense_loader(None)
        self.addCleanup(memos.set_image_reader, None)
        self.addCleanup(links.set_cosense_loader, None)

    def seed(self):
        memo_file("a.md", "英語の単語帳アプリを作るアイデア #語学", kind="アイデア")
        memo_file("b.md", "単語帳アプリの復習間隔を調べた #語学", at="2026-11-03T10:00:00+07:00")
        memo_file("c.md", "洗濯機の修理を頼む", at="2026-11-04T10:00:00+07:00", kind="タスク")


class TestReading(Base):
    def test_the_file_04_writes(self):
        """The exact shape of _file_part in lifeos/04-voice/core.py."""
        md.save("notes/20261104-120000-ab12-0-diary.md",
                "---\nkind: diary\nat: 2026-11-04T12:00:00+07:00\nfrom: 音声\n---\n\n今日は疲れた")
        md.save("notes/20261104-120000-ab12-1-idea.md",
                "---\nkind: idea\nat: 2026-11-04T12:00:00+07:00\nfrom: 文字\n---\n\n単語帳を作る")
        got = {m["kind"]: m for m in memos.read_all()}
        self.assertEqual(sorted(got), ["アイデア", "日記"])
        self.assertEqual(got["日記"]["text"], "今日は疲れた")
        self.assertEqual(got["日記"]["day"], "2026-11-04")
        core.run()
        self.assertEqual(next(v for v in core.load_index().values() if v["kind"] == "日記")["tags"], ["日記"])

    def test_markdown_with_and_without_front_matter(self):
        memo_file("a.md", "本文", kind="日記")
        write("notes/2026-10-01-plain.md", "見出しなし\n")
        got = {m["src"]: m for m in memos.read_all()}
        self.assertEqual(got["a.md"]["kind"], "日記")
        self.assertEqual(got["a.md"]["day"], "2026-11-03")
        self.assertEqual(got["2026-10-01-plain.md"]["day"], "2026-10-01")
        self.assertEqual(got["2026-10-01-plain.md"]["text"], "見出しなし")

    def test_jsonl_keeps_going_past_a_broken_line(self):
        write("notes/voice.jsonl", json.dumps({"text": "一つ目", "category": "アイデア", "at": "2026-11-01T08:00:00+07:00"},
                                              ensure_ascii=False) + "\nこわれた行\n" + json.dumps({"transcript": "二つ目"}, ensure_ascii=False) + "\n")
        got = memos.read_all()
        self.assertEqual(sorted(m["text"] for m in got), ["一つ目", "二つ目"])
        self.assertEqual(next(m for m in got if m["text"] == "一つ目")["kind"], "アイデア")

    def test_time_in_ms_and_utc_are_ho_chi_minh_days(self):
        write("notes/x.jsonl", json.dumps({"text": "ミリ秒", "createdAt": 1793750400000}) + "\n"
              + json.dumps({"text": "協定", "at": "2026-11-03T20:00:00Z"}) + "\n")
        days = {m["text"]: m["day"] for m in memos.read_all()}
        self.assertEqual(days["協定"], "2026-11-04")
        self.assertRegex(days["ミリ秒"], r"^\d{4}-\d{2}-\d{2}$")

    def test_photo_without_reader_is_kept_as_unread(self):
        write("notes/board.jpg", "x")
        m = memos.read_all()[0]
        self.assertTrue(m["unread"])
        self.assertEqual(m["kind"], "写真")
        self.assertFalse(memos.reader_connected())

    def test_text_beside_a_photo_is_its_text_not_a_second_memo(self):
        write("notes/手書き/page.png", "x")
        write("notes/手書き/page.txt", "買うものを書いた紙\n")
        got = memos.read_all()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "手書き")
        self.assertEqual(got[0]["text"], "買うものを書いた紙")
        self.assertFalse(got[0]["unread"])

    def test_connected_reader_runs_once_per_image(self):
        write("notes/board.jpg", "x")
        calls = []
        memos.set_image_reader(lambda p: calls.append(p) or "黒板の文字")
        self.assertEqual(memos.read_all()[0]["text"], "黒板の文字")
        memos.read_all()
        self.assertEqual(len(calls), 1)

    def test_failing_reader_does_not_stop_the_pass(self):
        write("notes/board.jpg", "x")
        memos.set_image_reader(lambda p: 1 / 0)
        self.assertTrue(memos.read_all()[0]["unread"])

    def test_sources_are_never_changed(self):
        self.seed()
        p = paths.data_path("notes", "a.md")
        with open(p, encoding="utf-8") as f:
            before = f.read()
        core.run()
        with open(p, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)

    def test_add_refuses_empty(self):
        with self.assertRaises(ValueError):
            memos.add("  ")


class TestTags(Base):
    def memo(self, text, kind="", tags=()):
        return {"text": text, "kind": kind, "tags": list(tags)}

    def test_sources_of_tags(self):
        got = links.tags_for(self.memo("#語学 の [単語帳] を睡眠の前に", kind="アイデア", tags=["手元"]), ["単語帳", "前に"], {"健康": ["睡眠"]})
        self.assertEqual(got, ["アイデア", "手元", "語学", "単語帳", "前に", "健康"])

    def test_heading_and_url_are_not_tags(self):
        self.assertEqual(links.tags_for(self.memo("# 見出し\n[https://example.com 外]"), [], {}), [])

    def test_latin_note_title_matches_whole_words_only(self):
        self.assertEqual(links.tags_for(self.memo("MAIL を確認"), ["AI"], {}), [])
        self.assertEqual(links.tags_for(self.memo("AI を試す"), ["AI"], {}), ["AI"])

    def test_own_dictionary_is_added(self):
        write("tools/11-notes/tags.json", json.dumps({"庭": ["剪定"]}, ensure_ascii=False))
        self.assertEqual(links.tags_for(self.memo("剪定をした")), ["庭"])

    def test_tag_count_is_limited(self):
        got = links.tags_for(self.memo(" ".join("#t{}".format(i) for i in range(20))), [], {})
        self.assertEqual(len(got), links.MAX_TAGS)


class TestRelated(Base):
    def related(self):
        core.run(desk=False)
        ix = core.load_index()
        return {v["src"]: {ix[r]["src"] for r in v["related"]} for v in ix.values()}

    def test_shared_words_link_both_ways_and_others_stay_apart(self):
        self.seed()
        r = self.related()
        self.assertEqual(r["a.md"], {"b.md"})
        self.assertEqual(r["b.md"], {"a.md"})
        self.assertEqual(r["c.md"], set())

    def test_shared_tag_alone_links(self):
        memo_file("a.md", "朝の散歩 #庭")
        memo_file("b.md", "夕方の水やり #庭")
        self.assertEqual(self.related()["a.md"], {"b.md"})

    def test_same_kind_alone_does_not_link(self):
        memo_file("a.md", "洗濯機を直す", kind="タスク")
        memo_file("b.md", "切手を貼る", kind="タスク")
        self.assertEqual(self.related()["a.md"], set())

    def test_common_words_alone_do_not_link(self):
        memo_file("a.md", "今日は洗濯機を直す")
        memo_file("b.md", "今日は切手を貼る")
        self.assertEqual(self.related()["a.md"], set())

    def test_many_memos_stay_within_reason(self):
        for i in range(30):
            memo_file("m{}.md".format(i), "共通計画 番号{} #同じ".format(i))
        r = self.related()
        self.assertTrue(all(r.values()))
        self.assertLess(max(len(v) for v in r.values()), 30)


class TestCompletionCondition(Base):
    """From one memo, a related memo within 2 clicks, following Desk's own link rules."""

    def test_related_memo_is_one_click_away(self):
        self.seed()
        core.run()
        ix = core.load_index()
        a = next(v for v in ix.values() if v["src"] == "a.md")
        b = next(v for v in ix.values() if v["src"] == "b.md")
        page = page_for(a["title"])
        self.assertIn(b["title"], links_in(page["body"]))
        self.assertEqual(page_for(b["title"])["id"], core.PREFIX + next(k for k, v in ix.items() if v["src"] == "b.md"))

    def test_tag_page_reaches_every_memo_with_that_tag_in_two_clicks(self):
        self.seed()
        core.run()
        ix = core.load_index()
        a = next(v for v in ix.values() if v["src"] == "a.md")
        b = next(v for v in ix.values() if v["src"] == "b.md")
        self.assertIn("語学", links_in(page_for(a["title"])["body"]))  # click 1
        tag = page_for("語学")
        self.assertTrue(tag and tag["body"])
        self.assertIn(b["title"], [n["title"] for n in backlinks(tag)])  # click 2

    def test_every_link_in_every_memo_page_lands_on_an_existing_page(self):
        self.seed()
        core.run()
        for n in desk_notes.load():
            for word in links_in(n["body"]):
                self.assertIsNotNone(page_for(word), word)

    def test_titles_with_brackets_and_duplicates_still_link(self):
        memo_file("a.md", "[計画] 単語帳アプリ #語学")
        memo_file("b.md", "[計画] 単語帳アプリ #語学")
        core.run()
        ix = core.load_index()
        titles = [v["title"] for v in ix.values()]
        self.assertEqual(len({norm_title(t) for t in titles}), 2)
        for t in titles:
            self.assertNotRegex(t, r"[\[\]]")
            other = next(x for x in titles if x != t)
            self.assertIn(other, links_in(page_for(t)["body"]))


class TestDeskPages(Base):
    def test_record_has_only_what_desk_keeps_and_no_auto_link(self):
        self.seed()
        core.run()
        keep = {"id", "title", "body", "createdAt", "updatedAt", "deleted", "noAuto"}
        for n in desk_notes.load():
            self.assertEqual(set(n), keep)
            self.assertIs(n["noAuto"], True)
            self.assertLessEqual(len(n["title"]), 80)

    def test_second_run_writes_nothing(self):
        self.seed()
        core.run()
        before = desk_notes.load()
        r = core.run()
        self.assertEqual(r["written"], 0)
        self.assertEqual(desk_notes.load(), before)

    def _edit(self, note, **fields):
        desk_notes.put_many([{**note, **fields, "updatedAt": note["updatedAt"] + 1}])

    def test_edit_in_desk_is_kept(self):
        self.seed()
        core.run()
        a = next(v for v in core.load_index().values() if v["src"] == "a.md")
        self._edit(page_for(a["title"]), body="自分で書き直した")
        memo_file("d.md", "単語帳アプリの画面案 #語学", at="2026-11-04T11:00:00+07:00")
        r = core.run()
        self.assertEqual(r["kept"], 1)
        self.assertEqual(page_for(a["title"])["body"], "自分で書き直した")

    def test_rename_in_desk_is_followed_by_links(self):
        self.seed()
        core.run()
        ix = core.load_index()
        a = next(v for v in ix.values() if v["src"] == "a.md")
        b = next(v for v in ix.values() if v["src"] == "b.md")
        self._edit(page_for(a["title"]), title="単語帳の案")
        core.run()
        self.assertIn("単語帳の案", links_in(page_for(b["title"])["body"]))
        self.assertEqual(next(v for v in core.load_index().values() if v["src"] == "a.md")["title"], "単語帳の案")

    def test_deleted_in_desk_stays_deleted_and_links_to_it_go(self):
        self.seed()
        core.run()
        ix = core.load_index()
        a = next(v for v in ix.values() if v["src"] == "a.md")
        b = next(v for v in ix.values() if v["src"] == "b.md")
        self._edit(page_for(a["title"]), deleted=True)
        r = core.run()
        self.assertEqual(r["hidden"], 1)
        self.assertIsNone(page_for(a["title"]))
        self.assertNotIn(a["title"], links_in(page_for(b["title"])["body"]))

    def test_own_note_with_the_tag_name_is_used_and_untouched(self):
        desk_notes.put_many([{"id": "n1", "title": "語学", "body": "自分のノート", "createdAt": 1, "updatedAt": 1, "deleted": False}])
        self.seed()
        core.run()
        self.assertEqual(page_for("語学")["id"], "n1")
        self.assertEqual(page_for("語学")["body"], "自分のノート")

    def test_own_note_titles_become_tags_but_generated_ones_do_not(self):
        desk_notes.put_many([{"id": "n1", "title": "洗濯機", "body": "x", "createdAt": 1, "updatedAt": 1, "deleted": False},
                             {"id": "n2", "title": "切手", "body": "x", "createdAt": 1, "updatedAt": 1, "deleted": False, "noAuto": True}])
        memo_file("a.md", "洗濯機に切手を貼る")
        core.run()
        self.assertEqual(next(iter(core.load_index().values()))["tags"], ["洗濯機"])

    def test_broken_notes_file_stops_before_writing(self):
        self.seed()
        os.makedirs(paths.desk_data_dir(), exist_ok=True)
        with open(os.path.join(paths.desk_data_dir(), "notes.json"), "w") as f:
            f.write("こわれている")
        with self.assertRaises(desk_notes.NoteStoreError):
            core.run()
        with open(os.path.join(paths.desk_data_dir(), "notes.json")) as f:
            self.assertEqual(f.read(), "こわれている")

    def test_no_desk_leaves_desk_alone(self):
        self.seed()
        core.run(desk=False)
        self.assertEqual(desk_notes.load(), [])
        self.assertTrue(md.read("briefs/2026-11-03-memo.md"))

    def test_sandbox_never_calls_the_server(self):
        self.assertIsNone(paths.desk_api())


class TestSummaries(Base):
    def test_day_week_and_year_files(self):
        self.seed()
        core.run()
        day = md.read("briefs/2026-11-03-memo.md")
        self.assertIn("2件", day)
        self.assertIn("語学", day)
        self.assertNotIn("洗濯機", day)
        week = md.read("briefs/2026-W45-memo.md")
        self.assertIn("2026-11-02 から 2026-11-08", week)
        self.assertIn("2026-11-04：1件", week)
        year = md.read("briefs/2026-memo.md")
        self.assertIn("2026-11：3件", year)

    def test_morning_brief_file_of_01_is_not_touched(self):
        md.save("briefs/2026-11-03.md", "01の朝のブリーフィング")
        self.seed()
        core.run()
        self.assertEqual(md.read("briefs/2026-11-03.md").strip(), "01の朝のブリーフィング")

    def test_day_without_memos_has_no_file(self):
        self.seed()
        core.run()
        self.assertIsNone(md.read("briefs/2026-11-05-memo.md"))

    def test_day_list_is_limited(self):
        for i in range(summary.MAX_DAY + 3):
            memo_file("m{:02d}.md".format(i), "メモ番号{}".format(i))
        core.run()
        self.assertIn("ほか3件", md.read("briefs/2026-11-03-memo.md"))

    def test_new_tags_in_a_week(self):
        memo_file("a.md", "#古い 前の週", at="2026-10-20T09:00:00+07:00")
        memo_file("b.md", "#古い #新しい この週", at="2026-11-03T09:00:00+07:00")
        core.run()
        week = md.read("briefs/2026-W45-memo.md")
        part = week.split("# この週に初めて出たタグ")[1]
        self.assertIn("新しい", part)
        self.assertNotIn("古い", part.split("#")[0])

    def test_summaries_are_in_desk_with_links_to_memos(self):
        self.seed()
        core.run()
        page = page_for("📅 メモの要約 2026-11-03")
        ix = core.load_index()
        for v in ix.values():
            if v["day"] == "2026-11-03":
                self.assertIn(v["title"], links_in(page["body"]))
        self.assertIsNotNone(page_for("📅 週の振り返り 2026-W45"))
        self.assertIsNotNone(page_for("📅 年のまとめ 2026"))

    def test_unread_photo_is_counted_in_the_summary(self):
        write("notes/2026-11-03-board.jpg", "x")
        r = core.run()
        self.assertEqual(r["unread"], 1)
        self.assertIn("読めていない写真：1枚", md.read("briefs/2026-11-03-memo.md"))

    def test_no_scolding_words(self):
        self.seed()
        core.run()
        for rel in ("briefs/2026-11-03-memo.md", "briefs/2026-W45-memo.md", "briefs/2026-memo.md"):
            for word in ("できなかった", "サボ", "未達", "不足", "減りました"):
                self.assertNotIn(word, md.read(rel))


class TestCosense(Base):
    PAGES = [{"title": "単語帳", "updated": 1793750400, "lines": ["単語帳", "#語学 の話"]},
             {"title": "洗濯機", "updated": 1600000000, "lines": ["洗濯機", {"text": "家の話"}]}]

    def links_of(self, src="a.md"):
        core.run()
        return next(v for v in core.load_index().values() if v["src"] == src)["cosense"]

    def setUp(self):
        super().setUp()
        memo_file("a.md", "単語帳と洗濯機の話")
        self.loaded = []
        links.set_cosense_loader(lambda: self.loaded.append(1) or self.PAGES)

    def test_no_scope_uses_nothing_and_loads_nothing(self):
        self.assertEqual(self.links_of(), [])
        self.assertEqual(self.loaded, [])

    def test_scope_all(self):
        core.save_settings(cosense={"scope": {"all": True}, "project": "p"})
        self.assertEqual(sorted(t for t, _ in self.links_of()), ["単語帳", "洗濯機"])

    def test_scope_by_tag_prefix_and_date(self):
        for scope, want in (({"tags": ["語学"]}, ["単語帳"]), ({"prefixes": ["洗"]}, ["洗濯機"]), ({"since": "2026-01-01"}, ["単語帳"])):
            core.save_settings(cosense={"scope": scope, "project": "p"})
            self.assertEqual([t for t, _ in self.links_of()], want, scope)

    def test_page_shows_a_link_out_and_no_page_body(self):
        core.save_settings(cosense={"scope": {"all": True}, "project": "p q"})
        got = self.links_of()
        self.assertTrue(all(u.startswith("https://scrapbox.io/p%20q/") for _, u in got))
        body = page_for(next(iter(core.load_index().values()))["title"])["body"]
        self.assertIn("# Cosense", body)
        self.assertNotIn("家の話", body)
        self.assertEqual([x for x in links_in(body) if x in ("単語帳", "洗濯機")], [])

    def test_export_file_is_the_default_source(self):
        links.set_cosense_loader(None)
        write("tools/11-notes/cosense-export.json", json.dumps({"pages": self.PAGES}, ensure_ascii=False))
        core.save_settings(cosense={"scope": {"prefixes": ["単語"]}, "project": "p"})
        self.assertEqual([t for t, _ in self.links_of()], ["単語帳"])


class TestStopping(Base):
    def out(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.main(list(argv))
        return buf.getvalue()

    def test_stop_and_start(self):
        self.seed()
        self.out("stop")
        self.assertIn("止めてあります", self.out("run"))
        self.assertEqual(desk_notes.load(), [])
        self.out("start")
        self.assertIn("メモ 3 件", self.out("run"))

    def test_unpublish_needs_yes(self):
        self.seed()
        self.out("run")
        self.assertIn("何も変えていません", self.out("unpublish"))
        self.assertTrue(all(not n["deleted"] for n in desk_notes.load()))

    def test_unpublish_marks_deleted_keeps_edits_and_memos_and_can_return(self):
        self.seed()
        self.out("run")
        a = next(v for v in core.load_index().values() if v["src"] == "a.md")
        page = page_for(a["title"])
        desk_notes.put_many([{**page, "body": "自分で直した"}])
        self.out("unpublish", "--yes")
        left = [n for n in desk_notes.load() if not n["deleted"]]
        self.assertEqual([n["body"] for n in left], ["自分で直した"])
        self.assertEqual(len(memos.read_all()), 3)
        self.assertEqual(core.run()["written"], 0)
        self.assertEqual(len([n for n in desk_notes.load() if not n["deleted"]]), 1)
        self.out("start")
        self.out("run")
        self.assertIsNotNone(page_for("語学"))

    def test_show_status_summary_and_add(self):
        self.seed()
        self.out("run")
        self.assertIn("語学", self.out("show", "単語帳アプリを"))
        self.assertIn("未決定", self.out("status"))
        self.assertIn("未接続", self.out("status"))
        self.out("add", "新しいメモ", "--kind", "日記")
        self.assertEqual(len(memos.read_all()), 4)

    def test_scope_command_validates(self):
        self.out("cosense-scope", "tag:語学", "since:2026-01-01", "--project", "p")
        self.assertEqual(core.settings()["cosense"]["scope"], {"tags": ["語学"], "since": "2026-01-01"})
        with self.assertRaises(SystemExit):
            self.out("cosense-scope", "なにか", "--project", "p")
