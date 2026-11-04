"""08 2-minute tidy: one place at a time, the place tidied longest ago first.

The list of places and their last dates live in <data>/tools/08-tidy/places.json:
    {"places": [{"room": "", "name": "机", "last": "2026-11-04", "seq": 1, "off": false}],
     "seq": 1, "current": {"room": "", "name": "机", "day": "2026-11-04"}}
"seq" only breaks ties between places tidied on the same day.

Replies never score anything: no before/after, no clutter rating, no counts, no days since.
"Removing" a place only sets off:true, so sending the add command brings it back with its date.
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
DEFAULT_PLACES = ("机", "キッチン", "洗面所")
USAGE_ADD = "「片付け追加 机」か「片付け追加 寝室 枕元」のように送ってください。"
USAGE_OFF = "「片付け外す 机」のように送ってください。"

_ASK = "片付け"
_DONE = ("済", "済み")
_LIST = "片付け場所"
_EDIT = re.compile(r"^片付け(追加|外す)(?:\s+(.*))?$")


class PlacesError(Exception):
    """places.json exists but cannot be read. Nothing was written."""


def _clean(text):
    text = unicodedata.normalize("NFKC", text or "").strip().rstrip("。．.!！、,")
    return text.replace("片づけ", "片付け").replace("かたづけ", "片付け")


def match(text):
    t = _clean(text)
    return t == _ASK or t == _LIST or t in _DONE or bool(_EDIT.match(t))


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
        raise PlacesError("places.json を読めません") from e
    if not isinstance(v, dict) or not isinstance(v.get("places"), list):
        raise PlacesError("places.json の形が違います")
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
    return p["room"] + "の" + p["name"] if p.get("room") else p["name"]


def _same(p, q):
    return p["room"] == (q.get("room") or "") and p["name"] == q.get("name")


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
            return "場所がありません。" + USAGE_ADD
        cur = _current(state)
        today = paths.today()
        if not (cur and state["current"].get("day") == today):
            cur = places[0]
            state["current"] = {"room": cur["room"], "name": cur["name"], "day": today}
        return "今日の2分：" + _label(cur) + "\n終わったら「済」と送ってください。"


def _done():
    with _state() as state:
        cur = _current(state)
        if not cur:
            state["current"] = None
            return "いま出ている場所はありません。「片付け」と送ると1つ出ます。"
        state["seq"] += 1
        cur["last"], cur["seq"] = paths.today(), state["seq"]
        state["current"] = None
        logs.append("tidy", {"place": _label(cur)})
        return _label(cur) + "を記録しました。もう1つやるなら「片付け」と送ってください。"


def _list():
    with _state() as state:
        on = [_label(p) for p in _in_order(state)]
        off = [_label(p) for p in state["places"] if p["off"]]
    lines = ["次に出る順：" + ("、".join(on) if on else "なし")]
    if off:
        lines.append("外した場所：" + "、".join(off))
    lines.append("足す：片付け追加 寝室 枕元　外す：片付け外す 枕元")
    return "\n".join(lines)


def _split(arg):
    """"机" is a place on its own; "寝室 枕元" is a room and a place."""
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
                    return _label(p) + "はもう入っています。"
                p["off"] = False
                return _label(p) + "を戻しました。"
        p = _place(room, name)
        state["places"].append(p)
        return _label(p) + "を足しました。"


def _remove(arg):
    room, name = _split(arg)
    if not name:
        return USAGE_OFF
    with _state() as state:
        found = [p for p in _active(state) if p["name"] == name and (not room or p["room"] == room)]
        if not found:
            return name + "は一覧にありません。「片付け場所」で確かめられます。"
        if len(found) > 1:
            return name + "は複数あります。「片付け外す " + found[0]["room"] + " " + name + "」のように部屋も書いてください。"
        found[0]["off"] = True
        if _current(state) is None:
            state["current"] = None
        return _label(found[0]) + "を外しました。「片付け追加」で戻せます。"


def handle(text, ctx):
    t = _clean(text)
    if t == _ASK:
        return _ask()
    if t in _DONE:
        return _done()
    if t == _LIST:
        return _list()
    verb, arg = _EDIT.match(t).groups()
    return _add(arg) if verb == "追加" else _remove(arg)
