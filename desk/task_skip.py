#!/usr/bin/env python3
"""Deciding what not to do, and labeling tasks with a type and a genre.

  python3 desk/task_skip.py pending [--limit 40]   # tasks that need "If I don't do it", a type or a genre (JSON)
  python3 desk/task_skip.py types                  # the fixed types (about 45, in 8 groups) and any you added
  python3 desk/task_skip.py genres                 # the genres so far (up to 100)
  python3 desk/task_skip.py apply                  # reads a JSON list from stdin and writes it
  python3 desk/task_skip.py stats                  # counts only, no titles
  python3 desk/task_skip.py merge-pending          # open tasks by genre, for finding ones that belong together (weekly)
  python3 desk/task_skip.py merge-add              # stdin [{"title","note","ids":[...]}]: proposals; nothing is merged
  python3 desk/task_skip.py genre-review           # every genre with its counts and who made it (weekly)
  python3 desk/task_skip.py genre-apply            # stdin [{"id","rename"?,"mergeInto"?,"emoji"?,"desc"?}]; only Claude's genres

The nightly scheduled task (desk-task-skip) writes here. Rows look like
  {"id": "...", "ifNot": "Nothing breaks. ...", "minor": true, "why": "...", "cat": "Research", "genre": "Georgia company"}

What it never does:
- touch what the person wrote: `skip.by == "me"` keeps ifNot/minor/why, `tagBy == "me"` keeps type and genre;
- mark as minor a task with a due date (past or future), or a Paperwork / health task (money, procedures, health);
- change the title, note, status, due date or anything else on the task;
- make more than 100 genres.
Replies (email, WhatsApp, LINE, nudges) are skipped: they are always the type Reply / Follow up and live in their own window.
"""
import json
import os
import re
import sys
import time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "desk"))
from lifeos.lib import paths, tasks  # noqa: E402
from replywin import is_reply  # noqa: E402

DAY_MS = 86400000
RECHECK_MS = 30 * DAY_MS  # "Keep" on the card: asked again after 30 days
OPEN = ("notyet", "doing", "paused")
GENRE_MAX = 100
IFNOT_MAX, WHY_MAX, NAME_MAX, DESC_MAX = 240, 160, 40, 160
DEFAULT_LIMIT = 40

TYPE_GROUPS = [
    ("Talk", ["Reply", "Follow up", "Call", "Meeting", "Ask someone", "Negotiate"]),
    ("Make", ["Write a document", "Write a post", "Design", "Shoot video", "Edit video", "Build software", "Fix a bug", "Translate"]),
    ("Think", ["Decide", "Plan", "Research", "Compare options", "Brainstorm", "Reflect"]),
    ("Paperwork", ["Application form", "Taxes and accounting", "Banking", "Contract and legal", "Set up an account",
                   "Cancel a subscription", "Pay a bill", "Send an invoice"]),
    ("Out and about", ["Buy online", "Shop in person", "Visit an office", "Book travel", "Pack and move", "Post and pick up"]),
    ("Learn", ["Read", "Watch to learn", "Language study", "Practice a skill"]),
    ("Body and home", ["Exercise", "Health and appointments", "Grooming", "Chores", "Cook"]),
    ("Hold", ["Waiting for someone", "Someday"]),
]
NEVER_MINOR_TYPES = set(dict(TYPE_GROUPS)["Paperwork"]) | {"Health and appointments"}


# ---------- small store helpers: through the Desk server when it runs, else the file ----------

def _load_col(col):
    if tasks._server_up():
        try:
            return tasks._call("GET", "/api/" + col)
        except (OSError, ValueError):
            pass
    try:
        with open(os.path.join(paths.desk_data_dir(), col + ".json"), encoding="utf-8") as f:
            v = json.load(f)
    except (OSError, ValueError):
        return []
    return [x for x in v if isinstance(x, dict) and x.get("id")] if isinstance(v, list) else []


def _put_col(col, rec):
    if tasks._server_up():
        try:
            tasks._call("PUT", f"/api/{col}/" + urllib.parse.quote(rec["id"], safe=""), {k: v for k, v in rec.items() if k != "id"})
            return
        except (OSError, ValueError):
            pass
    path = os.path.join(paths.desk_data_dir(), col + ".json")
    items = [x for x in _load_col(col) if x["id"] != rec["id"]] + [rec]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------- types and genres ----------

def all_types(custom=None):
    """[(group, name)] — the fixed ones, then the ones the person added (tasktypes)."""
    out = [(g, n) for g, names in TYPE_GROUPS for n in names]
    seen = {n.lower() for _, n in out}
    for x in custom if custom is not None else _load_col("tasktypes"):
        name = text(x.get("name"), NAME_MAX)
        if name and not x.get("deleted") and name.lower() not in seen:
            out.append((x.get("group") if x.get("group") in dict(TYPE_GROUPS) else "Hold", name))
            seen.add(name.lower())
    return out


def live_genres(items=None):
    return [g for g in (items if items is not None else _load_col("genres")) if not g.get("deleted") and not g.get("mergedInto")]


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return "g-" + (s[:40] or str(int(time.time())))


def text(value, limit):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


# ---------- choosing and checking ----------

def eligible(t):
    return (not t.get("deleted") and t.get("status") in OPEN and not is_reply(t) and t.get("by") != "claude"
            and not t.get("mergedInto"))


def needs(t, now_ms):
    s = t.get("skip") or {}
    if not s.get("ifNot") and s.get("by") != "me":
        return True
    if t.get("tagBy") != "me" and (not t.get("cat") or not t.get("genre")):
        return True
    kept = s.get("kept")
    return s.get("by") != "me" and isinstance(kept, (int, float)) and now_ms - kept >= RECHECK_MS and (s.get("at") or 0) < kept + RECHECK_MS


def pending(items, now_ms, limit=DEFAULT_LIMIT):
    picked = sorted((t for t in items if eligible(t) and needs(t, now_ms)), key=lambda t: -(t.get("createdAt") or 0))[:limit]
    return [{"id": t["id"], "title": t.get("title", ""), "clearTitle": (t.get("clear") or {}).get("title") or "",
             "note": (t.get("note") or "")[:300], "due": t.get("due"), "status": t.get("status"), "source": t.get("source"),
             "has": {"ifNot": bool((t.get("skip") or {}).get("ifNot")), "cat": t.get("cat") or "", "genre": t.get("genre") or "",
                     "mine": (t.get("skip") or {}).get("by") == "me", "tagsMine": t.get("tagBy") == "me"}} for t in picked]


def can_be_minor(t, cat):
    return t.get("due") is None and cat not in NEVER_MINOR_TYPES


def apply_rows(items, rows, now_ms, genres, types):
    """Returns (task records to save, genre records to save). Pure."""
    by_id = {t["id"]: t for t in items if eligible(t)}
    names = {n.lower(): n for _, n in types}
    live = {g["name"].lower(): g for g in live_genres(genres) if g.get("name")}
    new_genres, out = [], []
    for row in rows if isinstance(rows, list) else []:
        t = by_id.get(row.get("id")) if isinstance(row, dict) else None
        if not t:
            continue
        rec = dict(t)
        changed = False
        if t.get("tagBy") != "me":
            cat = names.get(text(row.get("cat"), NAME_MAX).lower())
            gname = text(row.get("genre"), NAME_MAX)
            g = live.get(gname.lower()) if gname else None
            if gname and not g and len(live) < GENRE_MAX:
                g = {"id": slug(gname), "name": gname, "emoji": text(row.get("genreEmoji"), 4) or "🏷️",
                     "desc": text(row.get("genreDesc"), DESC_MAX), "order": len(live), "by": "claude",
                     "createdAt": now_ms, "updatedAt": now_ms, "deleted": False}
                while any(x["id"] == g["id"] for x in list(live.values()) + new_genres + list(genres)):
                    g["id"] += "-2"
                live[gname.lower()] = g
                new_genres.append(g)
            if cat:
                rec["cat"] = cat
            if g:
                rec["genre"] = g["id"]
            if cat or g:
                rec["tagBy"], rec["tagAt"] = "claude", now_ms
                changed = True
        old = t.get("skip") or {}
        if old.get("by") != "me":
            ifnot = text(row.get("ifNot"), IFNOT_MAX)
            if ifnot:
                minor = bool(row.get("minor")) and can_be_minor(t, rec.get("cat"))
                rec["skip"] = {**{k: old[k] for k in ("kept",) if k in old}, "ifNot": ifnot, "minor": minor,
                               "why": text(row.get("why"), WHY_MAX) if minor else "", "by": "claude", "at": now_ms}
                changed = True
        if changed:
            out.append(rec)  # updatedAt stays: this is not something the person did
    return out, new_genres


def stats(items, genres, now_ms):
    el = [t for t in items if eligible(t)]
    return {"open": len(el), "pending": sum(needs(t, now_ms) for t in el),
            "withIfNot": sum(bool((t.get("skip") or {}).get("ifNot")) for t in el),
            "minor": sum(bool((t.get("skip") or {}).get("minor")) for t in el),
            "typed": sum(bool(t.get("cat")) for t in el), "genred": sum(bool(t.get("genre")) for t in el),
            "genres": len(live_genres(genres)),
            "dropped": sum(t.get("status") == "dropped" and not t.get("deleted") for t in items)}


# ---------- weekly: proposals to merge similar tasks, and tidying the genres ----------
# A proposal is only a card. The person decides on it in the Desk; merging there makes one new task and archives
# the originals with mergedInto (nothing is deleted, and "Undo" brings them back).
PROPOSAL_MAX = 10  # new proposals a week
MERGE_MIN, MERGE_MAX = 2, 30


def prop_key(ids):
    return "|".join(sorted(ids))


def merge_pending(items, props):
    taken = {i for p in props if not p.get("deleted") and p.get("status") in ("open", "kept") for i in p.get("ids") or []}
    groups = {}
    for t in items:
        if eligible(t) and t.get("genre") and t["id"] not in taken:
            groups.setdefault(t["genre"], []).append({"id": t["id"], "title": t.get("title", ""), "clearTitle": (t.get("clear") or {}).get("title") or "",
                                                       "cat": t.get("cat") or "", "status": t.get("status")})
    return {g: xs for g, xs in groups.items() if len(xs) >= MERGE_MIN}


def merge_add(items, props, rows, now_ms):
    by_id = {t["id"]: t for t in items if eligible(t)}
    seen = {prop_key(p.get("ids") or []) for p in props}
    taken = {i for p in props if not p.get("deleted") and p.get("status") in ("open", "kept") for i in p.get("ids") or []}
    out = []
    for row in rows if isinstance(rows, list) else []:
        if len(out) >= PROPOSAL_MAX or not isinstance(row, dict):
            break
        ids = [i for i in dict.fromkeys(row.get("ids") or []) if i in by_id and i not in taken]
        genres = {by_id[i].get("genre") for i in ids}
        title = text(row.get("title"), 80)
        if not (MERGE_MIN <= len(ids) <= MERGE_MAX) or len(genres) != 1 or None in genres or not title or prop_key(ids) in seen:
            continue
        rec = {"id": "mp-" + str(now_ms) + "-" + str(len(out)), "title": title, "note": text(row.get("note"), 300), "ids": ids,
               "genre": genres.pop(), "status": "open", "by": "claude", "createdAt": now_ms, "updatedAt": now_ms, "deleted": False}
        out.append(rec)
        seen.add(prop_key(ids))
        taken.update(ids)
    return out


def genre_review(items, genres):
    count = {}
    for t in items:
        if not t.get("deleted") and t.get("genre"):
            c = count.setdefault(t["genre"], {"open": 0, "all": 0})
            c["all"] += 1
            c["open"] += t.get("status") in OPEN
    return [{"id": g["id"], "name": g.get("name"), "by": g.get("by") or "claude", "desc": g.get("desc") or "", **count.get(g["id"], {"open": 0, "all": 0})}
            for g in live_genres(genres)]


def genre_apply(genres, rows, now_ms):
    """Only genres Claude made can be renamed or merged here; the person's stay as they are."""
    live = {g["id"]: g for g in live_genres(genres)}
    names = {g.get("name", "").lower(): gid for gid, g in live.items()}
    out = []
    for row in rows if isinstance(rows, list) else []:
        g = live.get(row.get("id")) if isinstance(row, dict) else None
        if not g or g.get("by") == "me":
            continue
        rec = dict(g)
        into = row.get("mergeInto")
        if into and into in live and into != g["id"]:
            rec["mergedInto"] = into
        else:
            name = text(row.get("rename"), NAME_MAX)
            if name and name.lower() not in names:
                rec["name"] = name
            if text(row.get("emoji"), 4):
                rec["emoji"] = text(row.get("emoji"), 4)
            if text(row.get("desc"), DESC_MAX):
                rec["desc"] = text(row.get("desc"), DESC_MAX)
        if rec != g:
            rec["updatedAt"] = now_ms
            out.append(rec)
            if rec.get("mergedInto"):
                live.pop(g["id"], None)
    return out


def main(argv):
    now_ms = int(time.time() * 1000)
    cmd = argv[:1]
    if cmd == ["pending"]:
        limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else DEFAULT_LIMIT
        print(json.dumps(pending(tasks._load(), now_ms, limit), ensure_ascii=False, indent=1))
    elif cmd == ["types"]:
        out = {}
        for g, n in all_types():
            out.setdefault(g, []).append(n)
        print(json.dumps(out, ensure_ascii=False, indent=1))
    elif cmd == ["genres"]:
        print(json.dumps([{k: g.get(k) for k in ("id", "name", "emoji", "desc", "by")} for g in live_genres()], ensure_ascii=False, indent=1))
    elif cmd == ["apply"]:
        rows = json.load(sys.stdin)
        if not isinstance(rows, list):
            sys.exit("list expected")
        recs, new = apply_rows(tasks._load(), rows, now_ms, _load_col("genres"), all_types())
        for g in new:
            _put_col("genres", g)
        for r in recs:
            tasks._put(r)
        print(f"Labeled {len(recs)}" + (f" / skipped {len(rows) - len(recs)}" if len(rows) != len(recs) else "")
              + (f" / new genres {len(new)}" if new else ""))
    elif cmd == ["merge-pending"]:
        print(json.dumps(merge_pending(tasks._load(), _load_col("mergeprops")), ensure_ascii=False, indent=1))
    elif cmd == ["merge-add"]:
        new = merge_add(tasks._load(), _load_col("mergeprops"), json.load(sys.stdin), now_ms)
        for p in new:
            _put_col("mergeprops", p)
        print(f"Proposals {len(new)}")
    elif cmd == ["genre-review"]:
        print(json.dumps(genre_review(tasks._load(), _load_col("genres")), ensure_ascii=False, indent=1))
    elif cmd == ["genre-apply"]:
        recs = genre_apply(_load_col("genres"), json.load(sys.stdin), now_ms)
        for g in recs:
            _put_col("genres", g)
        print(f"Genres changed {len(recs)}")
    elif cmd == ["stats"]:
        print(json.dumps(stats(tasks._load(), _load_col("genres"), now_ms), indent=1))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
