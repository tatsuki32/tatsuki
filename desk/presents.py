#!/usr/bin/env python3
"""プレゼント: good news that came in. Nothing here is a task, and nothing here sends anything.

  python3 desk/presents.py wise            # adds Wise deposits whose kind is income
  python3 desk/presents.py whatsapp        # prints new one-to-one messages for Claude to judge, once each
  python3 desk/presents.py removed         # what was taken off the list, so the next judgement can learn from it
  python3 desk/presents.py add FILE.json   # [{"kind", "title", "from", "date", "source", "ref", "url"}]

A row is known by its source and ref, so one taken off never comes back. Mail and chat text is not kept: only a one-line title.
"""
import fcntl
import hashlib
import json
import os
import sys
import tempfile
import urllib.parse
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "desk"))
from lifeos.lib import paths, tasks  # noqa: E402

KINDS = ("award", "hired", "payment", "invoice", "praise", "self")
SOURCES = ("gmail", "whatsapp", "wise", "self")
TITLE_MAX, FROM_MAX = 120, 80
FIRST_WINDOW = 48 * 3600  # the first look at WhatsApp goes back this far


def _file():
    return os.path.join(paths.desk_data_dir(), "presents.json")


def _read_file():
    try:
        with open(_file(), encoding="utf-8") as f:
            v = json.load(f)
    except FileNotFoundError:
        return []
    return [x for x in v if isinstance(x, dict) and x.get("id")]


def load():
    """Every row, also the ones taken off."""
    if tasks._server_up():
        try:
            return tasks._call("GET", "/api/presents")
        except (OSError, ValueError):
            pass
    return _read_file()


def _write_file(rec):
    os.makedirs(paths.desk_data_dir(), exist_ok=True)
    with open(_file() + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        items = [x for x in _read_file() if x["id"] != rec["id"]] + [rec]
        fd, tmp = tempfile.mkstemp(dir=paths.desk_data_dir(), prefix=".presents", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _file())


def _put(rec):
    if tasks._server_up():
        try:
            tasks._call("PUT", "/api/presents/" + urllib.parse.quote(rec["id"], safe=""), {k: v for k, v in rec.items() if k != "id"})
            return
        except (OSError, ValueError):
            pass  # the server went away, or has not restarted with this collection yet
    _write_file(rec)


def row_id(source, ref):
    return source + "-" + hashlib.sha1(str(ref).encode()).hexdigest()[:16]


def _line(value, limit):
    return " ".join(str(value or "").split())[:limit]


def _date(value):
    value = str(value or "")[:10]
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return paths.today()
    return value


def add(rows):
    """Adds what is new and returns how many. A row with a kind or source outside the lists is left out."""
    known = {p["id"] for p in load()}
    count = 0
    for r in rows:
        title, ref = _line(r.get("title"), TITLE_MAX), r.get("ref")
        if r.get("kind") not in KINDS or r.get("source") not in SOURCES or not title or not ref:
            continue
        pid = row_id(r["source"], ref)
        if pid in known:
            continue
        url = str(r.get("url") or "")
        ms = paths.now_ms()
        _put({"id": pid, "kind": r["kind"], "title": title, "from": _line(r.get("from"), FROM_MAX), "date": _date(r.get("date")),
              "source": r["source"], "ref": str(ref), "url": url if url.startswith("https://") else "",
              "createdAt": ms, "updatedAt": ms, "deleted": False})
        known.add(pid)
        count += 1
    return count


def removed():
    return [{"kind": p.get("kind"), "title": p.get("title"), "from": p.get("from")} for p in load() if p.get("deleted")]


def add_wise():
    """Deposits the person or Claude has called income. Moves between one's own accounts, repayments and refunds stay out."""
    import wise_analysis
    import wise_sync
    chosen, guess = wise_sync.read_json("flows.json", {}), wise_sync.read_json("guess.json", {})
    rows = []
    for a in wise_sync.read_json("activities.json", {}).values():
        a = {**a, "flow": wise_sync.flow(a)}
        if a["flow"] == "in" and wise_analysis.kind_of(a, chosen, guess)[0] == "income":
            rows.append({"kind": "payment", "title": (a.get("amountText") or "").lstrip("+ ") + " の入金", "from": a.get("title"),
                         "date": wise_sync.local_date(a.get("createdOn")), "source": "wise", "ref": a.get("id")})
    return add(rows)


def _seen_file():
    return os.path.join(paths.desk_data_dir(), "whatsapp", "presents-seen.json")


def whatsapp(now=None):
    """Their one-to-one text messages since the last look. The text is only printed, for Claude to judge, and not kept."""
    import time
    import whatsapp_sync as wa
    now = now or int(time.time())
    try:
        with open(_seen_file(), encoding="utf-8") as f:
            since = int(json.load(f).get("at") or 0)
    except FileNotFoundError:
        since = now - FIRST_WINDOW
    db = wa.connect()
    try:
        out = []
        for c in wa.chats_with_news(db, since):
            for m in wa.recent(db, c["session"]):
                if not m["me"] and m["kind"] == "text" and since < m["at"] <= now and m["text"].strip():
                    out.append({"ref": m["id"], "from": wa.clean_name(c["name"]), "text": m["text"], "at": m["at"]})
    finally:
        db.close()
    os.makedirs(os.path.dirname(_seen_file()), exist_ok=True)
    with open(_seen_file(), "w", encoding="utf-8") as f:
        json.dump({"at": now}, f)
    return sorted(out, key=lambda m: m["at"])


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["wise"]:
        print(json.dumps({"added": add_wise()}))
    elif args == ["whatsapp"]:
        print(json.dumps(whatsapp(), ensure_ascii=False, indent=1))
    elif args == ["removed"]:
        print(json.dumps(removed(), ensure_ascii=False, indent=1))
    elif args[:1] == ["add"] and len(args) == 2:
        with open(args[1], encoding="utf-8") as f:
            print(json.dumps({"added": add(json.load(f))}))
    else:
        print(__doc__)
        sys.exit(2)
