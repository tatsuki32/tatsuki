#!/usr/bin/env python3
"""Tatsuki's Desk — which screens, cards and buttons of Desk get used, and the weekly re-arrangement that follows.

Counting: the page posts one summary each time a screen is left (POST /api/usage). Only daily totals are kept, in
desk/data/usage/days.json: per screen the opens, seconds in front and where the visit came from; per button and per
Home card the presses and the visits that had a press. No text that was on screen, no titles, no ids of tasks or
notes, no time of day. Button names are code names the page builds from element ids and attribute names.

Arranging: once a week (the first time Desk is opened on or after Monday, Ho Chi Minh time) plan() moves at most
three screens and three Home cards. Screens opened on many days go to 「よく使う」 at the top of the menu, screens not
opened for 14 days go to 「あまり使っていない」 at the bottom, Home cards that get pressed go up. Nothing is ever
removed: the layout is only two lists of ids and an order, and the page shows every screen and card whatever they
say. A pinned item, one that moved in the last two weeks, and one newer than 14 days stay where they are.
Fixed arithmetic only; no model is asked.

A server on another port than 4747 (a test run) neither counts nor arranges, unless DESK_USAGE_DIR points it at
its own folder.

  python3 usage.py view [7|14|30|all]   what the 使い方 screen shows
  python3 usage.py layout               the current arrangement
"""
import copy
import json
import os
import re
import sys
import tempfile
import threading
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("DESK_USAGE_DIR") or os.path.join(ROOT, "data", "usage")
HCM = timezone(timedelta(hours=7))
VIEW_ID = re.compile(r"^_?[a-z][a-z0-9]{0,23}$")
KEY = re.compile(r"^[A-Za-z0-9_#.:>\-]{1,48}$")
VIAS = ("nav", "home", "link", "key", "search", "other")
PERIODS = {"7": 7, "14": 14, "30": 30, "all": None}
MAX_SEC = 4 * 3600   # one summary never adds more than this
MAX_KEYS = 60
MAX_PRESS = 500
SPAN = 14            # days looked at, and days before anything moves
FAV_MAX = 5
FAV_MIN_DAYS = 4     # opened on this many of the 14 days to count as often used
HOME_MIN = 10        # Home opens needed before a click rate means anything
OFTEN = 0.2          # a card pressed on one Home visit in five is pressed often
MOVES = 3            # screens moved per week, and cards moved per week
HOLD = 14            # days a moved item stays put
HISTORY = 26

_lock = threading.Lock()


def enabled(port):
    return bool(os.environ.get("DESK_USAGE_DIR")) or port == 4747


def today_hcm():
    return datetime.now(HCM).date()


def monday(d):
    return d - timedelta(days=d.weekday())


def _read(name, default):
    try:
        with open(os.path.join(DIR, name), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else default
    except (OSError, ValueError):
        return default


def _write(name, value):
    os.makedirs(DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DIR, prefix=".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(DIR, name))


def read_days():
    return _read("days.json", {})


def read_layout():
    return normal(_read("layout.json", {}))


def normal(lay):
    """A layout with every field present and of the right shape; a broken file reads as the first arrangement."""
    lay = lay if isinstance(lay, dict) else {}

    def ids(v):
        out = []
        for x in v if isinstance(v, list) else []:
            if isinstance(x, str) and VIEW_ID.match(x) and x not in out:
                out.append(x)
        return out

    def dates(v):
        return {k: d for k, d in (v if isinstance(v, dict) else {}).items()
                if isinstance(k, str) and VIEW_ID.match(k) and isinstance(d, str) and re.match(r"^\d{4}-\d\d-\d\d$", d)}

    def pair(v, fn):
        v = v if isinstance(v, dict) else {}
        return {"nav": fn(v.get("nav")), "home": fn(v.get("home"))}

    nav = lay.get("nav") if isinstance(lay.get("nav"), dict) else {}
    fav = [i for i in ids(nav.get("fav")) if i != "home"][:FAV_MAX]
    return {
        "auto": lay.get("auto") is not False,
        "week": lay.get("week") if isinstance(lay.get("week"), str) else None,
        "nav": {"fav": fav, "rare": [i for i in ids(nav.get("rare")) if i != "home" and i not in fav]},
        "home": ids(lay.get("home")),
        "pins": pair(lay.get("pins"), ids),
        "moved": pair(lay.get("moved"), dates),
        "known": pair(lay.get("known"), dates),
        "notice": lay.get("notice") if isinstance(lay.get("notice"), dict) else None,
        "history": [h for h in lay.get("history") or [] if isinstance(h, dict)][-HISTORY:] if isinstance(lay.get("history"), list) else [],
    }


# ---------- counting ----------

def _count(v, top):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 < v <= top else 0


def record(rec, today=None):
    """Adds one summary of a visit to today's totals. Returns False when it is not one."""
    view = rec.get("view")
    if not isinstance(view, str) or not VIEW_ID.match(view) or view.startswith("_"):
        return False
    sec = rec.get("sec")
    sec = min(int(sec), MAX_SEC) if isinstance(sec, (int, float)) and not isinstance(sec, bool) and sec > 0 else 0
    via = rec.get("via") if rec.get("via") in VIAS else "other"
    hits = rec.get("hits") if isinstance(rec.get("hits"), dict) else {}
    cards = rec.get("cards") if isinstance(rec.get("cards"), dict) else {}
    first = set(x for x in rec.get("new") or [] if isinstance(x, str)) if isinstance(rec.get("new"), list) else set()
    first_cards = set(x for x in rec.get("newCards") or [] if isinstance(x, str)) if isinstance(rec.get("newCards"), list) else set()
    with _lock:
        days = read_days()
        day = days.setdefault((today or today_hcm()).isoformat(), {})
        views = day.setdefault("views", {})
        v = views.setdefault(view, {"n": 0, "sec": 0, "via": {}})
        if not rec.get("cont"):  # the rest of a visit already counted (the window was hidden for a while)
            v["n"] += 1
            v["via"][via] = v["via"].get(via, 0) + 1
        v["sec"] += sec
        for name, n in list(hits.items())[:MAX_KEYS]:
            where, _, key = name.partition("|") if isinstance(name, str) else ("", "", "")
            n = _count(n, MAX_PRESS)
            if not n or not VIEW_ID.match(where) or not KEY.match(key):
                continue
            h = day.setdefault("hits", {}).setdefault(where + "|" + key, {"n": 0, "v": 0})
            h["n"] += n
            h["v"] += 1 if name in first else 0
        for cid, n in list(cards.items())[:MAX_KEYS]:
            n = _count(n, MAX_PRESS)
            if not n or not isinstance(cid, str) or not VIEW_ID.match(cid) or view != "home":
                continue
            c = day.setdefault("cards", {}).setdefault(cid, {"n": 0, "v": 0})
            c["n"] += n
            c["v"] += 1 if cid in first_cards else 0
        _write("days.json", days)
    return True


def first_run(first):
    """The first Monday with 14 days of counting behind it."""
    d = first + timedelta(days=SPAN)
    return d + timedelta(days=(7 - d.weekday()) % 7)


def view(period="14", today=None):
    """What the 使い方 screen shows for the last 7, 14 or 30 days, or everything."""
    today = today or today_hcm()
    span = PERIODS.get(str(period), SPAN)
    days = read_days()
    known = sorted(d for d in days if re.match(r"^\d{4}-\d\d-\d\d$", d))
    start = (today - timedelta(days=span - 1)).isoformat() if span else (known[0] if known else today.isoformat())
    views, cards, hits, last = {}, {}, {}, {}
    counted = 0
    for ds in known:
        day = days[ds]
        inside = start <= ds <= today.isoformat()
        counted += 1 if inside and day.get("views") else 0
        for vid, v in (day.get("views") or {}).items():
            if v.get("n", 0) > 0:
                last["v|" + vid] = ds
            if not inside:
                continue
            a = views.setdefault(vid, {"n": 0, "days": 0, "sec": 0, "via": {}})
            a["n"] += v.get("n", 0)
            a["days"] += 1 if v.get("n", 0) > 0 else 0
            a["sec"] += v.get("sec", 0)
            for k, n in (v.get("via") or {}).items():
                a["via"][k] = a["via"].get(k, 0) + n
        for kind, into in (("cards", cards), ("hits", hits)):
            for name, h in (day.get(kind) or {}).items():
                last[kind[0] + "|" + name] = ds
                if inside:
                    a = into.setdefault(name, {"n": 0, "v": 0})
                    a["n"] += h.get("n", 0)
                    a["v"] += h.get("v", 0)
    for vid, a in views.items():
        a["last"] = last.get("v|" + vid)
    for cid, a in cards.items():
        a["last"] = last.get("c|" + cid)
    edge = (today - timedelta(days=SPAN - 1)).isoformat()
    rows = [{"view": n.partition("|")[0], "key": n.partition("|")[2], **a, "last": last.get("h|" + n)} for n, a in hits.items()]
    stale = [{"view": n[2:].partition("|")[0], "key": n[2:].partition("|")[2], "last": ds}
             for n, ds in last.items() if n.startswith("h|") and ds < edge]
    first = date.fromisoformat(known[0]) if known else None
    run = first_run(first) if first else None
    ready = bool(run and monday(today) >= run)
    lay = read_layout()
    return {
        "period": str(period) if str(period) in PERIODS else str(SPAN),
        "from": start, "to": today.isoformat(), "first": first.isoformat() if first else None, "counted": counted,
        "views": views, "cards": cards, "lastOpen": {n[2:]: ds for n, ds in last.items() if n.startswith("v|")},
        "homeN": views.get("home", {}).get("n", 0), "allN": sum(a["n"] for a in views.values()),
        "hits": sorted(rows, key=lambda r: (-r["n"], r["view"], r["key"])),
        "stale": sorted(stale, key=lambda r: r["last"], reverse=True)[:50],
        "ready": ready,
        "nextRun": ((monday(today) + timedelta(days=7)) if ready else run).isoformat() if run else None,
        "seen": {kind: sorted(lay["known"][kind]) for kind in ("nav", "home")},
    }


# ---------- arranging ----------

def merge_order(saved, default):
    """The saved order of the Home cards with cards the page gained put next to their usual neighbour."""
    out = [c for c in saved if c in default]
    for k, c in enumerate(default):
        if c in out:
            continue
        before = [x for x in default[:k] if x in out]
        out.insert(out.index(before[-1]) + 1 if before else 0, c)
    return out


def window(days, today):
    """Per screen the days it was opened and the seconds, Home opens, and per card the visits with a press: last 14 full days."""
    opened, card_v, home_n = {}, {}, 0
    for k in range(1, SPAN + 1):
        day = days.get((today - timedelta(days=k)).isoformat()) or {}
        for vid, v in (day.get("views") or {}).items():
            if v.get("n", 0) > 0:
                o = opened.setdefault(vid, [0, 0])
                o[0] += 1
                o[1] += v.get("sec", 0)
        home_n += (day.get("views") or {}).get("home", {}).get("n", 0)
        for cid, c in (day.get("cards") or {}).items():
            card_v[cid] = card_v.get(cid, 0) + c.get("v", 0)
    return opened, home_n, card_v


def sound(lay, ids, cards):
    """Nothing lost and nothing twice: the lists hold only screens that exist, and the Home order is the cards, once each."""
    fav, rare = lay["nav"]["fav"], lay["nav"]["rare"]
    both = fav + rare
    return (len(set(both)) == len(both) and all(i in ids and i != "home" for i in both) and len(fav) <= FAV_MAX
            and sorted(lay["home"]) == sorted(cards) and len(set(lay["home"])) == len(cards))


def plan(days, layout, views, cards, today):
    """This week's arrangement. Returns (layout, moves); moves is None when it is not time or nothing may run."""
    lay = normal(copy.deepcopy(layout))
    week = monday(today)
    if not lay["auto"] or lay["week"] == week.isoformat():
        return lay, None
    known = sorted(d for d in days if re.match(r"^\d{4}-\d\d-\d\d$", d))
    if not known or week < first_run(date.fromisoformat(known[0])):
        return lay, None
    ids = [v["id"] for v in views]
    opened, home_n, card_v = window(days, today)
    before = {"nav": copy.deepcopy(lay["nav"]), "home": list(lay["home"]), "moved": copy.deepcopy(lay["moved"])}

    def free(kind, i):
        if i in lay["pins"][kind]:
            return False
        seen = lay["known"][kind].get(i)
        if not seen or (week - date.fromisoformat(seen)).days < SPAN:
            return False
        was = lay["moved"][kind].get(i)
        return not was or (week - date.fromisoformat(was)).days >= HOLD

    def score(i):
        return tuple(opened.get(i, (0, 0)))

    moves, done, left = [], set(), MOVES

    def move(i, to):
        nonlocal left
        moves.append({"kind": "nav", "id": i, "to": to})
        done.add(i)
        left -= 1

    fav = [i for i in lay["nav"]["fav"] if i in ids]
    rare = [i for i in lay["nav"]["rare"] if i in ids and i not in fav]
    often = sorted((i for i in ids if i != "home" and score(i)[0] >= FAV_MIN_DAYS),
                   key=lambda i: (-score(i)[0], -score(i)[1], ids.index(i)))[:FAV_MAX]
    for i in list(rare):  # opened again: back out of あまり使っていない
        if left and score(i)[0] >= 1 and free("nav", i):
            rare.remove(i)
            if i in often and len(fav) < FAV_MAX:
                fav.append(i)
                move(i, "fav")
            else:
                move(i, "group")
    for i in often:  # into よく使う, making room when it is full
        if i in fav or i in rare or i in done or not left or not free("nav", i):
            continue
        if len(fav) >= FAV_MAX:
            out = [j for j in fav if j not in often and j not in done and free("nav", j)]
            if not out or left < 2:
                continue
            j = min(out, key=score)
            fav.remove(j)
            move(j, "group")
        fav.append(i)
        move(i, "fav")
    for i in list(fav):  # hardly opened any more: back to its own group
        if left and i not in done and score(i)[0] <= 1 and free("nav", i):
            fav.remove(i)
            move(i, "group")
    for i in ids:  # not opened for 14 days
        if left and i != "home" and i not in fav and i not in rare and i not in done and score(i)[0] == 0 and free("nav", i):
            rare.append(i)
            move(i, "rare")
    rare.sort(key=ids.index)

    order = merge_order(lay["home"], cards)
    if home_n >= HOME_MIN:
        def tier(c):
            v = card_v.get(c, 0)
            return 0 if v / home_n >= OFTEN else 1 if v else 2
        slots = [k for k, c in enumerate(order) if free("home", c)]
        cur = [order[k] for k in slots]
        want = sorted(cur, key=tier)
        moved = 0
        for to, c in enumerate(want):  # from the top: bring up the card that belongs here; the ones passed slide down one place
            if moved == MOVES:
                break
            if cur[to] != c:
                cur.remove(c)
                cur.insert(to, c)
                moves.append({"kind": "home", "id": c, "dir": "up"})
                moved += 1
        for k, c in zip(slots, cur):
            order[k] = c

    lay["nav"] = {"fav": fav, "rare": rare}
    lay["home"] = order
    if not sound(lay, ids, cards):
        return normal(copy.deepcopy(layout)), None
    if order == cards:
        lay["home"] = []  # the first order is kept as "no order", so the page can tell nothing was moved
    lay["week"] = week.isoformat()
    for m in moves:
        lay["moved"][m["kind"]][m["id"]] = week.isoformat()
    if moves:
        lay["history"] = (lay["history"] + [{"week": week.isoformat(), "moves": moves, "before": before}])[-HISTORY:]
        lay["notice"] = {"week": week.isoformat(), "moves": moves}
    return lay, moves


def public(lay, on=True):
    """What the page needs: the arrangement, the pins, this week's note, and the history without the saved copies."""
    hist = [{"week": h.get("week"), "moves": h.get("moves") or [], "undone": bool(h.get("undone")), "reset": bool(h.get("reset"))}
            for h in lay["history"]]
    return {"on": on, "auto": lay["auto"], "week": lay["week"], "nav": lay["nav"], "home": lay["home"], "pins": lay["pins"],
            "notice": lay["notice"], "history": hist[::-1][:12],
            "canUndo": any(not h.get("undone") for h in lay["history"][-1:]),
            "changed": bool(lay["nav"]["fav"] or lay["nav"]["rare"] or lay["home"])}


def _inventory(rec):
    views = [{"id": v["id"], "g": v.get("g")} for v in rec.get("views") or []
             if isinstance(v, dict) and isinstance(v.get("id"), str) and VIEW_ID.match(v["id"]) and not v["id"].startswith("_")]
    cards = [c for c in rec.get("cards") or [] if isinstance(c, str) and VIEW_ID.match(c)]
    return views, list(dict.fromkeys(cards))


def change(rec, on=True, today=None):
    """PUT /api/layout. op: sync (the page's screens and cards; runs the weekly plan when due), pin, auto, undo, reset, seen."""
    today = today or today_hcm()
    op = rec.get("op")
    with _lock:
        lay = read_layout()
        if not on:
            return public(lay, False)
        was = json.dumps(lay, sort_keys=True)
        if op == "sync":
            views, cards = _inventory(rec)
            if views and cards:
                for kind, items in (("nav", [v["id"] for v in views]), ("home", cards)):
                    for i in items:
                        lay["known"][kind].setdefault(i, today.isoformat())
                lay, _ = plan(read_days(), lay, views, cards, today)
        elif op == "pin" and rec.get("kind") in ("nav", "home") and isinstance(rec.get("id"), str) and VIEW_ID.match(rec["id"]):
            pins = lay["pins"][rec["kind"]]
            if rec.get("on") and rec["id"] not in pins:
                pins.append(rec["id"])
            if not rec.get("on") and rec["id"] in pins:
                pins.remove(rec["id"])
        elif op == "auto":
            lay["auto"] = bool(rec.get("on"))
        elif op == "seen":
            lay["notice"] = None
        elif op == "undo" and lay["history"] and not lay["history"][-1].get("undone"):
            last = lay["history"][-1]
            b = normal({"nav": last.get("before", {}).get("nav"), "home": last.get("before", {}).get("home"),
                        "moved": last.get("before", {}).get("moved")})
            lay["nav"], lay["home"], lay["moved"] = b["nav"], b["home"], b["moved"]
            last["undone"] = True
            lay["notice"] = None
        elif op == "reset":
            before = {"nav": copy.deepcopy(lay["nav"]), "home": list(lay["home"]), "moved": copy.deepcopy(lay["moved"])}
            if lay["nav"]["fav"] or lay["nav"]["rare"] or lay["home"]:
                lay["history"] = (lay["history"] + [{"week": monday(today).isoformat(), "moves": [], "reset": True, "before": before}])[-HISTORY:]
            lay["nav"], lay["home"], lay["moved"] = {"fav": [], "rare": []}, [], {"nav": {}, "home": {}}
            lay["notice"] = None
        if json.dumps(lay, sort_keys=True) != was:
            _write("layout.json", lay)
        return public(lay, True)


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "view"
    out = public(read_layout()) if what == "layout" else view(sys.argv[2] if len(sys.argv) > 2 else "14")
    print(json.dumps(out, ensure_ascii=False, indent=1))
