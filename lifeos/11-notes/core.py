"""One pass: read memos, tag, link, write the summaries, show them in Desk's notes. Safe to run again.

Desk's notes page is the viewer: a memo becomes a note whose [links] reach its related memos,
so nothing here draws a screen. Sources in notes/ are never changed.
"""
import hashlib
import json
import os
import re
import tempfile

from lifeos.lib import md, paths
from lifeos.lib.tools import load_tool

memos_mod = load_tool("11-notes", "memos")
links = load_tool("11-notes", "links")
summary = load_tool("11-notes", "summary")
desk_notes = load_tool("11-notes", "desk_notes")

PREFIX = "ln-"  # every note this tool writes has an id starting with this
TAG_PREFIX = "ln-tag-"
TITLE_HEAD = 30
TAG_PAGE_BODY = "メモ自動連携が付けたタグです。\nこのタグのメモは、下の「このノートにリンクしているもの」に並びます。"


def _tool_path(name):
    return paths.data_path("tools", "11-notes", name)


def _read_json(name, default):
    try:
        with open(_tool_path(name), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return default


def _write_json(name, value):
    p = _tool_path(name)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".tool", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def settings():
    return {"enabled": True, "desk": True, "cosense": {}, **_read_json("settings.json", {})}


def save_settings(**fields):
    _write_json("settings.json", {**settings(), **fields})


def load_index():
    return _read_json("index.json", {})


def _sha(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def _title_for(m, used):
    """Unique in Desk's sense of the word, and safe inside [ ]."""
    head = m["title"] or next((l for l in m["text"].split("\n") if l.strip()), "") or m.get("file") or "メモ"
    head = re.sub(r"[#＃]\S*", "", head)
    head = re.sub(r"[\[\]]", "", re.sub(r"\s+", " ", head)).strip()[:TITLE_HEAD] or "メモ"
    mark = {"写真": "📷", "手書き": "✍️"}.get(m["kind"], "📝")
    base = "{} {} {}".format(mark, m["day"], head)
    title, i = base, 2
    while links.norm(title) in used:
        title, i = "{} ({})".format(base, i), i + 1
    used.add(links.norm(title))
    return title


def _build(desk):
    """All memos with tags, related ids and Cosense links."""
    conf = settings()
    all_memos = memos_mod.read_all()
    vocabulary = [n["title"] for n in desk if not n.get("deleted") and not n.get("noAuto")
                  and not str(n["id"]).startswith(PREFIX) and len(links.norm(n.get("title"))) >= 2]
    dictionary = links.tag_dictionary()
    previous = load_index()
    used = {links.norm(v.get("title")) for v in previous.values()} | {links.norm(n.get("title")) for n in desk}
    titles = links.cosense_pages(conf)
    project = (conf.get("cosense") or {}).get("project")
    for m in all_memos:
        m["all_tags"] = links.tags_for(m, vocabulary, dictionary)
        m["cosense"] = links.cosense_links(m, titles, project)
        m["display"] = (previous.get(m["id"]) or {}).get("title") or _title_for(m, used)
    related = links.relate(all_memos)
    for m in all_memos:
        m["related"] = related.get(m["id"], [])
    return all_memos


def _plain(m):
    return m["display"]


def _linked(m):
    return "[" + m["display"] + "]"


def day_title(day):
    return "📅 メモの要約 " + day


def _summaries(all_memos, link):
    """[(file label, Desk title, text)]: every day, week and year that has memos."""
    days, weeks, years = {}, {}, {}
    for m in all_memos:
        days.setdefault(m["day"], []).append(m)
        weeks.setdefault(summary.week_of(m["day"]), []).append(m)
        years.setdefault(m["day"][:4], []).append(m)
    out = [(k, day_title(k), summary.daily(k, v, link)) for k, v in days.items()]
    for k, v in weeks.items():
        earlier = [m for m in all_memos if summary.week_of(m["day"]) < k]
        out.append((k, "📅 週の振り返り " + k, summary.weekly(k, v, earlier, link)))
    out += [(k, "📅 年のまとめ " + k, summary.yearly(k, v, link)) for k, v in years.items()]
    return out


def write_briefs(all_memos):
    """briefs/<label>-memo.md. 01 saves briefs/YYYY-MM-DD.md whole, so these are separate files."""
    written = 0
    for label, _, text in _summaries(all_memos, _plain):
        rel = "briefs/{}-memo.md".format(label)
        if md.read(rel) != text:
            md.save(rel, text)
            written += 1
    return written


def _memo_body(m, alive, tag_title):
    parts = [m["text"]] if m["text"] else []
    if m["unread"]:
        parts.append("写真の文字はまだ読めていません（ファイル：{}）".format(m.get("file", "")))
    if m["all_tags"]:
        parts.append("# タグ\n" + " ".join("[" + tag_title(t) + "]" for t in m["all_tags"]))
    rel = [alive[r] for r in m["related"] if r in alive]
    if rel:
        parts.append("# 関連メモ\n" + "\n".join("- " + _linked(x) for x in rel))
    if m["cosense"]:
        parts.append("# Cosense\n" + "\n".join("- [{} {}]".format(url, t) for t, url in m["cosense"]))
    parts.append("[" + day_title(m["day"]) + "]")
    return "\n\n".join(parts)


def _pages(all_memos, desk, state):
    """{note id: (title, body)} for everything that should be in Desk, and the memos he deleted there."""
    by_id = {n["id"]: n for n in desk}
    taken = {links.norm(n.get("title")): n for n in desk if not n.get("deleted")}

    def deleted_by_him(note_id):
        return bool(by_id.get(note_id, {}).get("deleted")) and note_id in state

    for m in all_memos:
        n = by_id.get(PREFIX + m["id"])
        if n and not n.get("deleted") and n.get("title"):
            m["display"] = n["title"]  # renamed in Desk
    alive = {m["id"]: m for m in all_memos if not deleted_by_him(PREFIX + m["id"])}

    def tag_title(t):
        return taken[links.norm(t)]["title"] if links.norm(t) in taken else t

    pages = {}
    for _, title, text in _summaries(list(alive.values()), _linked):
        pages["ln-sum-" + _sha(title)] = (title, text.rstrip("\n"))
    for m in alive.values():
        pages[PREFIX + m["id"]] = (m["display"], _memo_body(m, alive, tag_title))
    for t in {t for m in alive.values() for t in m["all_tags"]}:
        have = taken.get(links.norm(t))
        if not have or str(have["id"]).startswith(TAG_PREFIX):  # his own note of that name is the tag's page
            pages[TAG_PREFIX + _sha(links.norm(t))] = (t, TAG_PAGE_BODY)
    return {k: v for k, v in pages.items() if not deleted_by_him(k)}, len(all_memos) - len(alive)


def _digest(title, body):
    return _sha(str(title) + "\n" + str(body))


def publish(all_memos, desk):
    """Writes the pages that changed. His own edits in Desk win: a page changed since this tool last
    wrote it is left alone, and a page he deleted stays deleted. Pages are noAuto, so a memo's title
    does not become a link all over Desk."""
    state = _read_json("published.json", {})
    pages, hidden = _pages(all_memos, desk, state)
    by_id = {n["id"]: n for n in desk}
    now = paths.now_ms()
    records, kept = [], 0
    for note_id, (title, body) in sorted(pages.items()):
        cur = by_id.get(note_id)
        live = cur if cur and not cur.get("deleted") else None
        if live and note_id in state and _digest(live.get("title"), live.get("body")) != state[note_id]:
            kept += 1
            continue
        state[note_id] = _digest(title, body)
        if live and live.get("title") == title and live.get("body") == body:
            continue
        records.append({"id": note_id, "title": title, "body": body, "createdAt": (cur or {}).get("createdAt") or now,
                        "updatedAt": now, "deleted": False, "noAuto": True})
    desk_notes.put_many(records)
    _write_json("published.json", state)
    return {"written": len(records), "kept": kept, "hidden": hidden}


def unpublish():
    """Marks this tool's pages in Desk deleted (deleted:true, like Desk's own delete). Pages he changed stay.
    The caller must have his approval. Turns publishing off, so the next run does not bring them back."""
    state = _read_json("published.json", {})
    now = paths.now_ms()
    records, kept = [], 0
    for n in desk_notes.load():
        if n["id"] not in state or n.get("deleted"):
            continue
        if _digest(n.get("title"), n.get("body")) != state[n["id"]]:
            kept += 1
            continue
        records.append({**n, "deleted": True, "updatedAt": now})
        state.pop(n["id"])
    desk_notes.put_many(records)
    _write_json("published.json", state)
    save_settings(desk=False)
    return {"removed": len(records), "kept": kept}


def count_published():
    return len(_read_json("published.json", {}))


def run(desk=None):
    """The whole pass. desk=False skips Desk's notes for this run."""
    conf = settings()
    if not conf["enabled"]:
        return {"stopped": True}
    use_desk = conf["desk"] if desk is None else desk
    notes = desk_notes.load()
    all_memos = _build(notes)
    shown = publish(all_memos, notes) if use_desk else {"written": 0, "kept": 0, "hidden": 0}
    briefs = write_briefs(all_memos)
    _write_json("index.json", {m["id"]: {
        "title": m["display"], "src": m["src"], "day": m["day"], "kind": m["kind"], "tags": m["all_tags"],
        "related": m["related"], "cosense": m["cosense"], "unread": m["unread"]} for m in all_memos})
    return {"stopped": False, "memos": len(all_memos), "linked": sum(1 for m in all_memos if m["related"]),
            "unread": sum(1 for m in all_memos if m["unread"]), "briefs": briefs,
            "desk": use_desk, **shown}


def find(word):
    """Index entries whose id or title contains word, newest first."""
    index = load_index()
    w = links.norm(word)
    hits = [(k, v) for k, v in index.items() if w == k or w in links.norm(v.get("title"))]
    return sorted(hits, key=lambda x: x[1].get("day", ""), reverse=True), index
