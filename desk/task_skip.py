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
  python3 desk/task_skip.py genre-apply            # stdin [{"id","rename"?,"mergeInto"?,"retire"?,"emoji"?,"color"?,"desc"?} | {"add":{name,emoji,color,desc}}]
                                                   # only Claude's genres; every change goes to genrelog (the Genres page can undo it)
  python3 desk/task_skip.py type-review            # how much each type was used in the last 4 weeks, and open tasks with no type
  python3 desk/task_skip.py type-propose           # stdin [{"kind":"retire","name","why"} | {"kind":"add","name","group","why"}]: cards only

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
# one stable color per genre; the same list as TL_COLORS in desk/index.html (mid tones that read on the dark background, no gold, no red)
GENRE_COLORS = ("#7aa7ff", "#4cc2ad", "#86cf73", "#a98bf5", "#5bc8e8", "#c3d562", "#d48ad4", "#9aa6e0", "#8fd6b8", "#c0a6e8")
TYPE_UNUSED_MS = 28 * DAY_MS
TYPE_PROPOSAL_MAX = 5


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
    """[(group, name)] — the fixed ones, then the added ones (tasktypes). A type retired on a card is left out."""
    custom = custom if custom is not None else _load_col("tasktypes")
    retired = {text(x.get("name"), NAME_MAX).lower() for x in custom if x.get("retired") and not x.get("deleted")}
    out = [(g, n) for g, names in TYPE_GROUPS for n in names if n.lower() not in retired]
    seen = {n.lower() for _, n in out} | retired
    for x in custom:
        name = text(x.get("name"), NAME_MAX)
        if name and not x.get("deleted") and not x.get("retired") and name.lower() not in seen:
            out.append((x.get("group") if x.get("group") in dict(TYPE_GROUPS) else "Yours", name))
            seen.add(name.lower())
    return out


def live_genres(items=None):
    return [g for g in (items if items is not None else _load_col("genres")) if not g.get("deleted") and not g.get("mergedInto") and not g.get("retired")]


def pick_color(row_color, gid):
    return row_color if row_color in GENRE_COLORS else GENRE_COLORS[sum(map(ord, gid)) % len(GENRE_COLORS)]


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
                g = {"id": slug(gname), "name": gname, "emoji": text(row.get("genreEmoji"), 4) or "🏷️", "color": None,
                     "desc": text(row.get("genreDesc"), DESC_MAX), "order": len(live), "by": "claude",
                     "createdAt": now_ms, "updatedAt": now_ms, "deleted": False}
                while any(x["id"] == g["id"] for x in list(live.values()) + new_genres + list(genres)):
                    g["id"] += "-2"
                g["color"] = pick_color(row.get("genreColor"), g["id"])
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


def genre_apply(genres, rows, now_ms, open_count=None):
    """Only genres Claude made change here; the person's stay as they are. Returns (records to save, changes for genrelog).
    retire: only a genre with no open task. add: a new genre (up to 100)."""
    live = {g["id"]: g for g in live_genres(genres)}
    names = {g.get("name", "").lower(): gid for gid, g in live.items()}
    open_count = open_count or {}
    out, changes = [], []
    keep = ("name", "emoji", "color", "desc", "mergedInto", "retired")
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        if isinstance(row.get("add"), dict):
            a = row["add"]
            name = text(a.get("name"), NAME_MAX)
            if not name or name.lower() in names or len(live) >= GENRE_MAX:
                continue
            gid = slug(name)
            while gid in live or any(x["id"] == gid for x in genres):
                gid += "-2"
            g = {"id": gid, "name": name, "emoji": text(a.get("emoji"), 4) or "🏷️", "color": pick_color(a.get("color"), gid),
                 "desc": text(a.get("desc"), DESC_MAX), "order": len(live), "by": "claude", "createdAt": now_ms, "updatedAt": now_ms, "deleted": False}
            live[gid], names[name.lower()] = g, gid
            out.append(g)
            changes.append({"gid": gid, "kind": "add", "before": None, "after": {k: g.get(k) for k in keep}})
            continue
        g = live.get(row.get("id"))
        if not g or g.get("by") == "me":
            continue
        rec = dict(g)
        into = row.get("mergeInto")
        kind = "edit"
        if into and into in live and into != g["id"]:
            rec["mergedInto"], kind = into, "merge"
        elif row.get("retire") and not open_count.get(g["id"]):
            rec["retired"], kind = True, "retire"
        else:
            name = text(row.get("rename"), NAME_MAX)
            if name and name.lower() not in names:
                rec["name"] = name
            if text(row.get("emoji"), 4):
                rec["emoji"] = text(row.get("emoji"), 4)
            if row.get("color") in GENRE_COLORS:
                rec["color"] = row["color"]
            if text(row.get("desc"), DESC_MAX):
                rec["desc"] = text(row.get("desc"), DESC_MAX)
        if rec != g:
            rec["updatedAt"] = now_ms
            out.append(rec)
            changes.append({"gid": g["id"], "kind": kind, "before": {k: g.get(k) for k in keep}, "after": {k: rec.get(k) for k in keep}})
            if rec.get("mergedInto") or rec.get("retired"):
                live.pop(g["id"], None)
    return out, changes


def type_review(items, custom, now_ms):
    used = {}
    for t in items:
        if t.get("deleted") or not t.get("cat"):
            continue
        u = used.setdefault(t["cat"], {"open": 0, "recent": 0})
        u["open"] += t.get("status") in OPEN
        u["recent"] += max(t.get("createdAt") or 0, t.get("tagAt") or 0) >= now_ms - TYPE_UNUSED_MS
    mine = {text(x.get("name"), NAME_MAX).lower() for x in custom if x.get("by") != "claude" and not x.get("deleted")}
    return {"types": [{"group": g, "name": n, **used.get(n, {"open": 0, "recent": 0}), "yours": n.lower() in mine} for g, n in all_types(custom)],
            "noType": [{"id": t["id"], "title": t.get("title", "")} for t in items if eligible(t) and not t.get("cat")][:40],
            "groups": [g for g, _ in TYPE_GROUPS]}


def type_propose(items, custom, props, rows, now_ms):
    """Proposals only: the person decides on a card. A type the person added or edited is never proposed for retiring."""
    review = {x["name"].lower(): x for x in type_review(items, custom, now_ms)["types"]}
    open_now = [p for p in props if p.get("status") == "open" and not p.get("deleted")]
    recent = {(p.get("kind"), (p.get("name") or "").lower()) for p in props if not p.get("deleted") and now_ms - (p.get("createdAt") or 0) < 90 * DAY_MS}
    out = []
    for row in rows if isinstance(rows, list) else []:
        if len(open_now) + len(out) >= TYPE_PROPOSAL_MAX or not isinstance(row, dict):
            break
        kind, name, why = row.get("kind"), text(row.get("name"), NAME_MAX), text(row.get("why"), WHY_MAX)
        if not name or (kind, name.lower()) in recent:
            continue
        if kind == "retire":
            r = review.get(name.lower())
            if not r or r["yours"] or r["open"] or r["recent"]:
                continue
            rec = {"kind": "retire", "name": r["name"], "group": r["group"]}
        elif kind == "add":
            if name.lower() in review or row.get("group") not in dict(TYPE_GROUPS):
                continue
            rec = {"kind": "add", "name": name, "group": row["group"]}
        else:
            continue
        out.append({"id": f"tp-{now_ms}-{len(out)}", **rec, "why": why, "status": "open", "by": "claude", "createdAt": now_ms, "updatedAt": now_ms, "deleted": False})
        recent.add((kind, name.lower()))
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
        items = tasks._load()
        open_count = {}
        for t in items:
            if eligible(t) and t.get("genre"):
                open_count[t["genre"]] = open_count.get(t["genre"], 0) + 1
        recs, changes = genre_apply(_load_col("genres"), json.load(sys.stdin), now_ms, open_count)
        for g in recs:
            _put_col("genres", g)
        if changes:
            day = time.strftime("%Y-%m-%d")
            old = next((x for x in _load_col("genrelog") if x["id"] == day), {})
            _put_col("genrelog", {"id": day, "date": day, "changes": (old.get("changes") or []) + changes, "at": now_ms})
        print(f"Genres changed {len(recs)}")
    elif cmd == ["type-review"]:
        print(json.dumps(type_review(tasks._load(), _load_col("tasktypes"), now_ms), ensure_ascii=False, indent=1))
    elif cmd == ["type-propose"]:
        new = type_propose(tasks._load(), _load_col("tasktypes"), _load_col("typeprops"), json.load(sys.stdin), now_ms)
        for p in new:
            _put_col("typeprops", p)
        print(f"Type proposals {len(new)}")
    elif cmd == ["stats"]:
        print(json.dumps(stats(tasks._load(), _load_col("genres"), now_ms), indent=1))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
