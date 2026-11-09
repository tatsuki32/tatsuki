"""08 2-minute tidy: one place at a time, the place tidied longest ago first.

The list of places and their last dates live in <data>/tools/08-tidy/places.json:
    {"places": [{"room": "", "name": "Desk", "last": "2026-11-04", "seq": 1, "off": false}],
     "seq": 1, "current": {"room": "", "name": "Desk", "day": "2026-11-04"}}
"seq" only breaks ties between places tidied on the same day.

Replies never score anything: no before/after, no clutter rating, no counts, no days since.
"Removing" a place only sets off:true, so sending the add command brings it back with its date.
Commands work in English and Japanese: "tidy" 片付け, "done" 済, "tidy places" 片付け場所,
"tidy add Bedroom Nightstand" 片付け追加 寝室 枕元, "tidy remove Nightstand" 片付け外す 枕元.
Replies are in English. Names already in places.json stay as they are.
"""
import fcntl
import json
import os
import re
import tempfile
import unicodedata
from contextlib import contextmanager

from lifeos.lib import logs, paths

PRIORITY = 10
DEFAULT_PLACES = ("Desk", "Kitchen", "Bathroom")  # only for a new places.json
USAGE_ADD = 'Send it like "tidy add Desk" or "tidy add Bedroom Nightstand".'
USAGE_OFF = 'Send it like "tidy remove Desk".'

_ASK = ("片付け", "tidy", "tidy up")
_DONE = ("済", "済み", "done", "tidied")
_LIST = ("片付け場所", "tidy places", "tidy list")
_EDIT = re.compile(r"^(?:片付け(追加|外す)|tidy\s+(add|remove))(?:\s+(.*))?$", re.I)
_ADD = ("追加", "add")


class PlacesError(Exception):
    """places.json exists but cannot be read. Nothing was written."""


def _clean(text):
    text = unicodedata.normalize("NFKC", text or "").strip().rstrip("。．.!！、,")
    return text.replace("片づけ", "片付け").replace("かたづけ", "片付け")


def _cmd(text):
    """The text with English words lowercased and spaces folded, for comparing to a command."""
    return " ".join(text.split()).lower()


def match(text):
    t = _cmd(_clean(text))
    return t in _ASK or t in _LIST or t in _DONE or bool(_EDIT.match(t))


def _file():
    return paths.data_path("tools", "08-tidy", "places.json")


def _fresh():
    return {"places": [_place("", n) for n in DEFAULT_PLACES], "seq": 0, "current": None}


def _place(room, name):
    return {"room": room, "name": name, "last": "", "seq": 0, "off": False}


def _read():
    try:
        with open(_file(), encoding="utf-8") as f:
            v = json.load(f)
    except FileNotFoundError:
        return _fresh()
    except ValueError as e:
        raise PlacesError("cannot read places.json") from e
    if not isinstance(v, dict) or not isinstance(v.get("places"), list):
        raise PlacesError("places.json has the wrong shape")
    places = []
    for p in v["places"]:  # the file may have been edited by hand
        if isinstance(p, str):
            p = {"name": p}
        if isinstance(p, dict) and p.get("name"):
            places.append({**_place(p.get("room") or "", p["name"]), **p})
    return {"places": places, "seq": v.get("seq") or 0, "current": v.get("current")}


def _write(state):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(_file()), prefix=".places", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _file())


@contextmanager
def _state():
    """The state, under a lock, saved on leaving the block."""
    with open(_file() + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = _read()
        yield state
        _write(state)


def _label(p):
    return p["room"] + " " + p["name"] if p.get("room") else p["name"]


def _fold(s):
    return (s or "").casefold()


def _same(p, q):
    """"tidy add desk" finds "Desk": English names compare without case."""
    return _fold(p["room"]) == _fold(q.get("room")) and _fold(p["name"]) == _fold(q.get("name"))


def _active(state):
    return [p for p in state["places"] if not p["off"]]


def _in_order(state):
    """Active places, longest ago first. Never-tidied places come first, in list order."""
    return sorted(_active(state), key=lambda p: (p["last"] or "", p["seq"] or 0))


def _current(state):
    cur = state["current"]
    return next((p for p in _active(state) if cur and _same(p, cur)), None)


def _ask():
    with _state() as state:
        places = _in_order(state)
        if not places:
            return "There are no places yet. " + USAGE_ADD
        cur = _current(state)
        today = paths.today()
        if not (cur and state["current"].get("day") == today):
            cur = places[0]
            state["current"] = {"room": cur["room"], "name": cur["name"], "day": today}
        return "Two minutes today: " + _label(cur) + '\nSend "done" when you finish.'


def _done():
    with _state() as state:
        cur = _current(state)
        if not cur:
            state["current"] = None
            return 'No place is out right now. Send "tidy" to get one.'
        state["seq"] += 1
        cur["last"], cur["seq"] = paths.today(), state["seq"]
        state["current"] = None
        logs.append("tidy", {"place": _label(cur)})
        return _label(cur) + ' is recorded. Send "tidy" for another one.'


def _list():
    with _state() as state:
        on = [_label(p) for p in _in_order(state)]
        off = [_label(p) for p in state["places"] if p["off"]]
    lines = ["Next up, in order: " + (", ".join(on) if on else "none")]
    if off:
        lines.append("Removed: " + ", ".join(off))
    lines.append("Add: tidy add Bedroom Nightstand   Remove: tidy remove Nightstand")
    return "\n".join(lines)


def _split(arg):
    """"Desk" is a place on its own; "Bedroom Nightstand" (寝室 枕元) is a room and a place."""
    words = (arg or "").split()
    if len(words) < 2:
        return "", "".join(words)
    return words[0], " ".join(words[1:])


def _add(arg):
    room, name = _split(arg)
    if not name:
        return USAGE_ADD
    with _state() as state:
        for p in state["places"]:
            if _same(p, {"room": room, "name": name}):
                if not p["off"]:
                    return _label(p) + " is already on the list."
                p["off"] = False
                return "Put " + _label(p) + " back on the list."
        p = _place(room, name)
        state["places"].append(p)
        return "Added " + _label(p) + "."


def _remove(arg):
    room, name = _split(arg)
    if not name:
        return USAGE_OFF
    with _state() as state:
        found = [p for p in _active(state) if _fold(p["name"]) == _fold(name) and (not room or _fold(p["room"]) == _fold(room))]
        if not found:
            return name + ' is not on the list. Send "tidy places" to check.'
        if len(found) > 1:
            return ("There is more than one " + name + '. Add the room, like "tidy remove '
                    + found[0]["room"] + " " + name + '".')
        found[0]["off"] = True
        if _current(state) is None:
            state["current"] = None
        return "Removed " + _label(found[0]) + '. Send "tidy add ' + _label(found[0]) + '" to bring it back.'


def handle(text, ctx):
    t = _clean(text)
    cmd = _cmd(t)
    if cmd in _ASK:
        return _ask()
    if cmd in _DONE:
        return _done()
    if cmd in _LIST:
        return _list()
    ja, en, arg = _EDIT.match(t).groups()
    return _add(arg) if (ja or en.lower()) in _ADD else _remove(arg)
