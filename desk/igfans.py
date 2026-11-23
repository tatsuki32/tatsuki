#!/usr/bin/env python3
"""Tatsuki's Desk — Followers (#followers): who follows an Instagram account, who the real fans are, what one lookup costs.

Three sources, all official and all read only:
  1. Instagram's own export (followers and following), dropped on the page. The page unpacks it (igfans_web/core.js); the
     ZIP never reaches this server. Works for any account and without any API.
  2. Meta's API through go.tany.im (golinks/src/insights.js) for accounts connected in Post Studio: who commented, wrote or
     tagged (never the text), audience counts.
  3. Meta's Business Discovery: the public numbers of one professional account, by ID. Personal accounts are not shown by Meta.
Nothing here follows, unfollows, likes, posts or sends, and no page of Instagram is read by a machine.

Every API call is written to usage.jsonl (kind, time, worked or not, the percentages Meta returns) so one account's cost can be
measured. desk/data/igfans/ is other people's data: mode 700, out of the GitHub copy (backup_data.py IGNORE).

  python3 igfans.py status | sync <account> | enrich <account> <number>
"""
import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(ROOT))

import golinks  # noqa: E402
import utilities  # noqa: E402

DATA = os.environ.get("IGFANS_DIR") or os.path.join(ROOT, "data", "igfans")
FAKE = os.environ.get("IGFANS_FAKE") == "1"
CORE = os.path.join(ROOT, "igfans_web", "core.js")
NAME = re.compile(r"[a-z0-9._]{1,30}")
DAY = 86400000
KEEP = 365 * DAY          # reactions older than a year are dropped
FRESH = 30 * DAY          # a profile is not asked for again for 30 days
STOP_AT = 70              # percent of Meta's limit at which profile checks pause
SYNC_EVERY = DAY
SYNC_CALLS = 300          # one daily read never uses more calls than this; the rest continues next time
PACE = 2.0                # seconds between profile checks
MAX_PEOPLE = 500000
WEIGHTS = {"comment": 3, "dm": 2, "tag": 4}
RATE_CODES = {4, 17, 32, 613, 80002}
PERMISSION_CODES = {10, 190, 200, 1001}
_lock = threading.RLock()
_busy = {}                # account -> "sync" | "enrich"
_wake = threading.Event()


def now_ms():
    return int(time.time() * 1000)


def clean(v):
    s = str(v or "").strip().lstrip("@").lower()
    # "..", "." and "__proto__" fit the letters but are never an account folder or a key
    return s if NAME.fullmatch(s) and s.strip(".") and s != "__proto__" else ""


# --- files ------------------------------------------------------------------------------------------------------

def _dir(account=None):
    os.makedirs(DATA, mode=0o700, exist_ok=True)
    try:
        os.chmod(DATA, 0o700)
    except OSError:
        pass
    if not account:
        return DATA
    return os.path.join(DATA, "accounts", account)


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = "%s.%s.tmp" % (path, secrets.token_hex(4))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def _f(account, name):
    return os.path.join(_dir(account), name)


def settings():
    s = _read(os.path.join(_dir(), "settings.json"), {})
    w = s.get("weights") if isinstance(s.get("weights"), dict) else {}
    return {"weights": {k: w.get(k, v) for k, v in WEIGHTS.items()}, "salt": s.get("salt") or "", "forgotten": s.get("forgotten") or [],
            "current": s.get("current") or ""}


def _salted(s):
    if not s["salt"]:
        s["salt"] = secrets.token_hex(16)
    return s


def _mark(salt, name):
    return hashlib.sha256((salt + "|" + name).encode()).hexdigest()


def _hidden():
    """A test for people the owner asked to forget: only a salted hash of each is kept."""
    s = settings()
    gone = set(s["forgotten"])
    return (lambda n: _mark(s["salt"], n) in gone) if gone else (lambda n: False)


# --- accounts ---------------------------------------------------------------------------------------------------

_conn = {"at": 0, "value": {}}


def connected():
    """{handle: Instagram id} of the accounts connected in Post Studio. The last answer is used when go.tany.im cannot be reached."""
    if FAKE:
        return {"demo.page": "111"}
    if os.environ.get("IGFANS_DIR"):  # a test server never talks to the real go.tany.im
        return {}
    if time.time() - _conn["at"] < 300:
        return _conn["value"]
    path = os.path.join(_dir(), "connected.json")
    try:
        v = {clean(a["handle"]): str(a["account"]) for a in golinks.accounts() if a.get("provider") == "instagram" and clean(a.get("handle"))}
        _write(path, v)
    except (RuntimeError, KeyError, TypeError):
        v = _read(path, {})
    _conn.update(at=time.time(), value=v)
    return v


def accounts():
    have = set()
    try:
        have = {clean(n) for n in os.listdir(os.path.join(_dir(), "accounts"))} - {""}
    except OSError:
        pass
    return sorted(have | set(connected()))


def current(wanted=""):
    """The account on screen: the one asked for, the last one used, the language page, or the first."""
    all_ = accounts()
    for a in (clean(wanted), clean(settings()["current"])):
        if a in all_:
            return a
    return next((x for x in all_ if "lang" in x), all_[0] if all_ else "")


# --- calls to Meta, each one measured ------------------------------------------------------------------------------

class ApiError(Exception):
    def __init__(self, message, code=0, meter=None):
        super().__init__(message)
        self.code = code
        self.meter = meter or {}


def _transport(ig_id, what, params):
    if FAKE:
        return _fake(what, params)
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v})
    return golinks._call("GET", "/api/ig/%s/%s%s" % (ig_id, what, "?" + q if q else ""), timeout=90)


def ask(account, what, **params):
    ig_id = connected().get(account)
    if not ig_id:
        raise ApiError("This account is not connected in Post Studio", 1001)
    t = time.time()
    try:
        r = _transport(ig_id, what, params)
    except RuntimeError as e:
        _log(account, what, False, 0, {}, t, str(e))
        raise ApiError(str(e), 0)
    meter = r.get("meter") if isinstance(r.get("meter"), dict) else {}
    _log(account, what, bool(r.get("ok")), int(r.get("code") or 0), meter, t, "" if r.get("ok") else _no_id(str(r.get("error") or ""), params))
    if not r.get("ok"):
        raise ApiError(_no_id(str(r.get("error") or "Meta did not answer"), params)[:300], int(r.get("code") or 0), meter)
    return r


def _no_id(text, params):
    """The measurements hold no names: an error that repeats the ID asked about loses it."""
    u = params.get("u")
    return re.sub(r"(?i)(?<![a-z0-9._])%s(?![a-z0-9_])" % re.escape(u), "this ID", text) if u else text


def _log(account, what, ok, code, meter, started, error):
    rec = {"at": now_ms(), "account": account, "kind": what, "ok": ok, "code": code, "calls": int(meter.get("calls") or 0),
           "app": meter.get("app"), "buc": meter.get("buc"), "wait": meter.get("wait") or 0, "ms": int((time.time() - started) * 1000)}
    if error:
        rec["error"] = error[:200]
    with _lock:
        path = os.path.join(_dir(), "usage.jsonl")
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _usage_rows(since):
    out = []
    try:
        with open(os.path.join(_dir(), "usage.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("at", 0) >= since:
                    out.append(r)
    except OSError:
        pass
    return out


def usage(account, followers):
    """What this account cost: calls by kind, calls per 1,000 followers, and how close to Meta's limit it came."""
    now = now_ms()
    rows = _usage_rows(now - 30 * DAY)
    mine = [r for r in rows if r.get("account") == account]
    kinds = {}
    for r in mine:
        k = kinds.setdefault(r["kind"], {"kind": r["kind"], "day": 0, "month": 0, "failed": 0, "ms": 0, "n": 0})
        k["month"] += r["calls"]
        k["day"] += r["calls"] if r["at"] >= now - DAY else 0
        k["failed"] += 0 if r["ok"] else 1
        k["ms"] += r["ms"]
        k["n"] += 1
    for k in kinds.values():
        k["ms"] = round(k["ms"] / max(1, k["n"]))
    # one account's normal day: the busiest of the days on which the daily read ran, without one-off profile checks
    days = {}
    for r in mine:
        if r["kind"] != "discover":
            d = time.strftime("%Y-%m-%d", time.localtime(r["at"] / 1000))
            days[d] = days.get(d, 0) + r["calls"]
    daily = [days[d] for d in sorted(days)]
    last = next((r for r in reversed(rows) if r.get("app") is not None or r.get("buc") is not None), None)
    hour = sum(r["calls"] for r in rows if r["at"] >= now - 3600000)
    peak_app = max([r["app"] for r in rows if r.get("app") is not None] or [None], key=lambda v: -1 if v is None else v)
    peak_buc = max([r["buc"] for r in rows if r.get("buc") is not None] or [None], key=lambda v: -1 if v is None else v)
    app = last.get("app") if last else None
    per_hour = round(hour / app * 100) if app and hour >= 20 else None  # only a guess, and only from real numbers
    return {"kinds": sorted(kinds.values(), key=lambda k: -k["month"]), "day": sum(k["day"] for k in kinds.values()), "month": sum(k["month"] for k in kinds.values()),
            "first": daily[0] if daily else 0, "normal": daily[-1] if len(daily) > 1 else None,
            "followers": followers, "perThousand": round((daily[-1] if daily else 0) / followers * 1000, 1) if followers else None,
            "app": app, "buc": last.get("buc") if last else None, "peakApp": peak_app, "peakBuc": peak_buc, "hour": hour, "limitGuess": per_hour,
            "stopAt": STOP_AT, "lastAt": last["at"] if last else 0,
            "errors": [{"at": r["at"], "kind": r["kind"], "code": r["code"], "error": r.get("error", "")} for r in reversed(mine) if not r["ok"]][:5]}


# --- imports --------------------------------------------------------------------------------------------------------

def imports(account):
    d = os.path.join(_dir(account), "imports")
    try:
        names = sorted(os.listdir(d))
    except OSError:
        names = []
    out = [_read(os.path.join(d, n), None) for n in names if n.endswith(".json")]
    return sorted([x for x in out if x], key=lambda x: x["at"])


def _people(v):
    if not isinstance(v, dict) or len(v) > MAX_PEOPLE:
        raise ValueError("list")
    out = {}
    for k, t in v.items():
        n = clean(k)
        if not n or n != k or isinstance(t, bool) or not isinstance(t, (int, float)):
            raise ValueError("list")
        out[n] = int(t)
    return out


def add_import(rec):
    account = clean(rec.get("account"))
    if not account:
        return 400, {"error": "Choose which account this export belongs to"}
    try:
        followers, following = _people(rec.get("followers")), _people(rec.get("following") or {})
    except ValueError:
        return 400, {"error": "The list could not be read"}
    if not followers:
        return 400, {"error": "The followers list is empty"}
    mark = str(rec.get("mark") or "")
    if not re.fullmatch(r"[0-9a-f]{16}", mark):
        return 400, {"error": "The list could not be read"}
    with _lock:
        have = imports(account)
        same = next((x for x in have if x.get("mark") == mark), None)
        if same:
            return 200, {"same": True, "id": same["id"], "account": account}
        hidden = _hidden()
        at = now_ms()
        if have and at <= have[-1]["at"]:
            at = have[-1]["at"] + 1
        x = {"id": "%013d" % at, "at": at, "mark": mark, "hasFollowing": rec.get("hasFollowing") is not False,
             "followers": {k: v for k, v in followers.items() if not hidden(k)}, "following": {k: v for k, v in following.items() if not hidden(k)},
             "partial": str(rec.get("partial") or "")[:300], "warnings": [str(w)[:200] for w in (rec.get("warnings") or [])[:5]]}
        _write(os.path.join(_dir(account), "imports", x["id"] + ".json"), x)
        use(account)
    return 200, {"same": False, "id": x["id"], "account": account}


def change_import(account, import_id, what):
    path = os.path.join(_dir(account), "imports", import_id + ".json")
    with _lock:
        x = _read(path, None)
        if not x:
            return 404, {"error": "not found"}
        if what == "remove":
            os.remove(path)
        elif what == "trust":  # the owner says the export did cover all time
            x["partial"] = ""
            _write(path, x)
        else:
            return 404, {"error": "not found"}
    return 200, {"ok": True}


def use(account):
    with _lock:  # the same file holds the erasure list
        path = os.path.join(_dir(), "settings.json")
        s = _read(path, {})
        s["current"] = account
        _write(path, s)


# --- reactions ------------------------------------------------------------------------------------------------------

def events(account):
    e = _read(_f(account, "events.json"), {})
    return {"comments": e.get("comments") or {}, "dms": e.get("dms") or {}, "tags": e.get("tags") or {}, "media": e.get("media") or {}}


def flat(account):
    e, floor, out = events(account), now_ms() - KEEP, []
    for rows, kind in ((e["comments"], "comment"), (e["dms"], "dm"), (e["tags"], "tag")):
        for group in rows.values():
            out += [{"u": x[0], "k": kind, "at": x[1]} for x in (group.values() if isinstance(group, dict) else group) if x[1] >= floor]
    return out


def sync(account):
    """One read of comments, tags, messages and audience. Stops by itself before Meta's limit and continues next time."""
    if not _take(account, "sync"):
        return {"error": "Already reading"}
    try:
        return _sync(account)
    finally:
        _busy.pop(account, None)


def _sync(account):
    state = _read(_f(account, "state.json"), {})
    left, problems, hidden = [SYNC_CALLS], {}, _hidden()
    e = events(account)
    floor = now_ms() - KEEP

    def call(what, **params):
        if left[0] <= 0:
            raise ApiError("more", -1)
        r = ask(account, what, **params)
        left[0] -= max(1, int(r["meter"].get("calls") or 1))
        if max(r["meter"].get("app") or 0, r["meter"].get("buc") or 0) >= STOP_AT:
            left[0] = 0
        return r

    def keep(x):
        n = clean(x.get("u"))
        return (n, int(x["at"])) if n and n != account and x.get("at", 0) >= floor and not hidden(n) else None

    def pages(what, limit, **params):
        """Every page, or an exception: a half-read list never replaces a whole one."""
        cursor, out = "", []
        for _ in range(limit):
            r = call(what, after=cursor, **params)
            out += r["items"]
            cursor = r.get("after") or ""
            if not cursor:
                return out, True
        return out, False

    def part(name, work):
        try:
            work()
        except ApiError as err:
            if err.code == -1:
                problems[name] = "Stopped before Meta's limit; the rest is read next time"
            else:
                problems[name] = str(err)
                if err.code in RATE_CODES:
                    left[0] = 0

    def profile():
        r = call("profile")
        state["profile"] = {k: r.get(k) for k in ("name", "followers", "following", "posts")}
        state["profile"]["at"] = now_ms()

    def comments():
        cursor, media = "", []
        while True:  # newest first; posts older than a year are not read
            r = call("media", after=cursor)
            media += r["items"]
            cursor = r.get("after") or ""
            if not cursor or (r["items"] and r["items"][-1]["at"] < floor):
                break
        for m in media:
            if m["at"] < floor:
                continue
            old = e["media"].get(m["id"]) or {}
            if not m.get("comments"):
                e["comments"].pop(m["id"], None)
            elif old.get("comments") != m["comments"]:
                rows, done = pages("comments", 60, media=m["id"])
                e["comments"][m["id"]] = [k for k in map(keep, rows) if k]
                if not done:  # an unusually long thread: keep what came and ask again next time
                    m = {**m, "comments": None}
            e["media"][m["id"]] = {k: m.get(k) for k in ("at", "link", "type", "likes", "comments")}
        live = {m["id"] for m in media if m["at"] >= floor}
        for old_id in [k for k in e["media"] if k not in live]:
            e["media"].pop(old_id, None)
            e["comments"].pop(old_id, None)

    def tags():
        rows, _ = pages("tags", 20)
        e["tags"] = {"all": [k for k in map(keep, rows) if k]}

    def dms():
        cursor, first = "", not state.get("dmsAt")
        since = floor if first else state["dmsAt"] - 2 * DAY
        started = now_ms()
        for _ in range(40):
            r = call("dms", after=cursor)
            for c in r["items"]:
                box = e["dms"].setdefault(c["id"], {})
                for x in c["items"]:
                    k = keep(x)
                    if k:
                        box[x["id"]] = k
            cursor = r.get("after") or ""
            if not cursor or (r["items"] and r["items"][-1]["updated"] < since):
                state["dmsAt"] = started
                break
        for cid in list(e["dms"]):
            e["dms"][cid] = {i: x for i, x in e["dms"][cid].items() if x[1] >= floor}
            if not e["dms"][cid]:
                del e["dms"][cid]

    def audience():
        if now_ms() - (state.get("audience") or {}).get("at", 0) < 7 * DAY:
            return
        state["audience"] = {"at": now_ms(), **{by: call("audience", by=by)["items"] for by in ("country", "city", "age", "gender")}}

    for name, work in (("profile", profile), ("comments", comments), ("tags", tags), ("dms", dms), ("audience", audience)):
        part(name, work)
    state.update(syncedAt=now_ms(), problems=problems)
    with _lock:
        gone = _hidden()  # someone may have been forgotten while this was reading
        for rows in (e["comments"], e["tags"]):
            for k in rows:
                rows[k] = [x for x in rows[k] if not gone(x[0])]
        for k in e["dms"]:
            e["dms"][k] = {i: x for i, x in e["dms"][k].items() if not gone(x[0])}
        _write(_f(account, "events.json"), e)
        fresh = _read(_f(account, "state.json"), {})
        fresh.update({k: state[k] for k in ("profile", "audience", "dmsAt", "syncedAt", "problems") if k in state})
        _write(_f(account, "state.json"), fresh)
    return {"ok": not problems, "problems": problems}


def _take(account, what):
    with _lock:
        if _busy.get(account):
            return False
        _busy[account] = what
        return True


# --- profiles -------------------------------------------------------------------------------------------------------

def profiles(account):
    return _read(_f(account, "people.json"), {})


def _profile(r):
    if not r.get("found"):
        return {"found": False, "at": now_ms()}
    return {"found": True, "at": now_ms(), "name": str(r.get("name") or "")[:150], "bio": str(r.get("bio") or "")[:600], "web": str(r.get("web") or "")[:300],
            "followers": r.get("followers"), "following": r.get("following"), "posts": r.get("posts")}


def waiting(account):
    """IDs still to check, fans first and then the newest followers."""
    last = next((x for x in reversed(imports(account)) if not x.get("partial")), None)
    followers = last["followers"] if last else {}
    points, weights = {}, settings()["weights"]
    for x in flat(account):
        points[x["u"]] = points.get(x["u"], 0) + weights[x["k"]]
    have, floor, hidden = profiles(account), now_ms() - FRESH, _hidden()
    names = [n for n in set(followers) | set(points) if n != account and not hidden(n) and (have.get(n) or {}).get("at", 0) < floor]
    return sorted(names, key=lambda n: (-points.get(n, 0), -followers.get(n, 0), n))


def enrich(account, count):
    """Check up to count profiles. Pauses (and says until when) near Meta's limit; a check that failed is tried again later."""
    if not _take(account, "enrich"):
        return {"error": "Already reading"}
    try:
        done, found, pause, problem = 0, 0, 0, ""
        for name in waiting(account)[:count]:
            with _lock:
                if not (_read(_f(account, "state.json"), {}).get("enrich") or {}).get("left"):
                    break  # stopped from the page
                if _hidden()(name):
                    continue  # erased since this run started: not asked about, not written
            try:
                r = ask(account, "discover", u=name)
            except ApiError as err:
                problem = str(err)
                pause = max(60, int(err.meter.get("wait") or 0)) * 60000 if err.code in RATE_CODES else 0
                if pause or err.code in PERMISSION_CODES or err.code == 0:
                    pause = pause or 15 * 60000
                    break
                with _lock:  # an odd answer about one ID does not hold up the rest
                    if not _hidden()(name):
                        p = profiles(account)
                        p[name] = {"found": False, "at": now_ms(), "error": problem[:120]}
                        _write(_f(account, "people.json"), p)
                _left(account, 1)
                done += 1
                continue
            with _lock:
                if not _hidden()(name):
                    p = profiles(account)
                    p[name] = _profile(r)
                    _write(_f(account, "people.json"), p)
            done += 1
            found += 1 if r.get("found") else 0
            _left(account, 1)
            if max(r["meter"].get("app") or 0, r["meter"].get("buc") or 0) >= STOP_AT:
                pause, problem = 20 * 60000, "Paused at %d%% of Meta's limit" % max(r["meter"].get("app") or 0, r["meter"].get("buc") or 0)
                break
            time.sleep(0 if FAKE else PACE)
        with _lock:
            st = _read(_f(account, "state.json"), {})
            e = st.get("enrich") or {}
            e.update(pauseUntil=now_ms() + pause if pause else 0, problem=problem, at=now_ms())
            if not waiting(account):
                e["left"] = 0
            st["enrich"] = e
            _write(_f(account, "state.json"), st)
        return {"done": done, "found": found, "problem": problem}
    finally:
        _busy.pop(account, None)


def _left(account, n):
    with _lock:
        st = _read(_f(account, "state.json"), {})
        e = st.get("enrich") or {}
        e["left"] = max(0, int(e.get("left") or 0) - n)
        e["done"] = int(e.get("done") or 0) + n
        st["enrich"] = e
        _write(_f(account, "state.json"), st)


def set_enrich(account, count):
    """count profiles from now (0 stops). The work is done by watch(), so the page can be closed."""
    with _lock:
        st = _read(_f(account, "state.json"), {})
        e = st.get("enrich") or {}
        e.update(left=count, pauseUntil=0 if count else e.get("pauseUntil", 0), problem="", at=now_ms())
        if count:
            e["done"] = 0
        st["enrich"] = e
        _write(_f(account, "state.json"), st)
    _wake.set()


def discover(account, name):
    """One ID, looked up now. The account on screen asks; Meta needs some connected professional account to ask with."""
    n = clean(name)
    if not n:
        return 400, {"error": "An ID has letters, numbers, dots and underscores (up to 30)"}
    conn = connected()
    via = account if account in conn else next(iter(sorted(conn)), "")
    if not via:
        return 409, {"error": "Connect an Instagram account in Post Studio first"}
    try:
        r = ask(via, "discover", u=n, media="1")
    except ApiError as err:
        return 502, {"error": _plain(err)}
    if account and r.get("found") is not None:
        with _lock:
            p = {} if _hidden()(n) else profiles(account)
            if n in p:
                p[n] = _profile(r)
                _write(_f(account, "people.json"), p)
    return 200, {"u": n, **{k: r.get(k) for k in ("found", "name", "bio", "web", "followers", "following", "posts", "media")}}


def _plain(err):
    if err.code in RATE_CODES:
        return "Meta's limit is reached. Please try again in an hour"
    if err.code in PERMISSION_CODES:
        return "Meta needs one more permission for this. Open Post Studio settings and connect Instagram again (%s)" % str(err)[:160]
    return str(err)


# --- forgetting one person --------------------------------------------------------------------------------------------

def forget(name):
    n = clean(name)
    if not n:
        return 400, {"error": "ID"}
    with _lock:
        path = os.path.join(_dir(), "settings.json")
        raw = _read(path, {})
        s = _salted(settings())
        gone = sorted(set(s["forgotten"]) | {_mark(s["salt"], n)})
        raw.update(salt=s["salt"], forgotten=gone)
        _write(path, raw)
        try:
            names = [a for a in os.listdir(os.path.join(_dir(), "accounts")) if clean(a) == a]
        except OSError:
            names = []
        for a in names:
            for x in imports(a):
                if n in x["followers"] or n in x["following"]:
                    x["followers"].pop(n, None)
                    x["following"].pop(n, None)
                    _write(os.path.join(_dir(a), "imports", x["id"] + ".json"), x)
            e = events(a)
            for rows in (e["comments"], e["tags"]):
                for k in rows:
                    rows[k] = [v for v in rows[k] if v[0] != n]
            for k in e["dms"]:
                e["dms"][k] = {i: v for i, v in e["dms"][k].items() if v[0] != n}
            _write(_f(a, "events.json"), e)
            p = profiles(a)
            if p.pop(n, None):
                _write(_f(a, "people.json"), p)
    return 200, {"ok": True}


# --- for the page -------------------------------------------------------------------------------------------------------

def view(wanted=""):
    a = current(wanted)
    s = settings()
    conn = connected()
    base = {"account": a, "accounts": [{"handle": h, "connected": h in conn} for h in accounts()], "weights": s["weights"], "forgotten": len(s["forgotten"])}
    if not a:
        return {**base, "empty": True}
    st = _read(_f(a, "state.json"), {})
    imp = imports(a)
    good = [x for x in imp if not x.get("partial")]
    n = len(good[-1]["followers"]) if good else (st.get("profile") or {}).get("followers")
    return {**base, "connected": a in conn, "imports": imp, "events": flat(a), "profiles": profiles(a), "profile": st.get("profile"), "audience": st.get("audience"),
            "syncedAt": st.get("syncedAt") or 0, "problems": {k: v for k, v in (st.get("problems") or {}).items()}, "busy": _busy.get(a) or "",
            "enrich": {**(st.get("enrich") or {}), "waiting": len(waiting(a))}, "usage": usage(a, n)}


def status(wanted=""):
    """The small part that changes while something runs."""
    a = current(wanted)
    st = _read(_f(a, "state.json"), {}) if a else {}
    return {"account": a, "busy": _busy.get(a) or "", "syncedAt": st.get("syncedAt") or 0, "enrich": st.get("enrich") or {}}


def put_settings(rec):
    w = rec.get("weights")
    if not isinstance(w, dict):
        return 400, {"error": "weights"}
    out = {}
    for k in WEIGHTS:
        v = w.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 100:
            return 400, {"error": "A weight is a number from 0 to 100"}
        out[k] = v
    with _lock:
        path = os.path.join(_dir(), "settings.json")
        s = _read(path, {})
        s["weights"] = out
        _write(path, s)
    return 200, {"weights": out}


def csv_file(rec):
    """The page made the CSV; the default browser saves it (the Desk window cannot). No size limit like other exports: every follower is in it."""
    text = rec.get("text")
    if not isinstance(text, str) or len(text) > 200 * 1024 * 1024:
        return 400, {"error": "csv"}
    name = re.sub(r"[^A-Za-z0-9._-]", "_", str(rec.get("name") or "followers"))[:80]
    return 200, {"file": utilities._token({"data": text.encode("utf-8"), "name": name + ".csv", "ctype": "text/csv; charset=utf-8"})}


def handle(method, step, rec):
    """Writes under /api/igfans/ (the server has checked Host, Origin and JSON)."""
    account = clean(rec.get("account"))
    if method == "PUT" and step == ["settings"]:
        return put_settings(rec)
    if method != "POST":
        return 404, {"error": "not found"}
    if step == ["import"]:
        return add_import(rec)
    if step == ["csv"]:
        return csv_file(rec)
    if step == ["forget"]:
        return forget(rec.get("u"))
    if step == ["discover"]:
        return discover(account, rec.get("u"))
    if not account or account not in accounts():
        return 404, {"error": "That account is not here"}
    if step == ["use"]:
        use(account)
        return 200, {"ok": True}
    if len(step) == 3 and step[0] == "imports" and re.fullmatch(r"\d{13}", step[1]):
        return change_import(account, step[1], step[2])
    if step == ["sync"]:
        if account not in connected():
            return 409, {"error": "Connect this account in Post Studio first"}
        if _busy.get(account):
            return 409, {"error": "Already reading"}
        threading.Thread(target=sync, args=(account,), daemon=True).start()
        return 200, {"ok": True}
    if step == ["enrich"]:
        n = rec.get("count")
        if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= MAX_PEOPLE:
            return 400, {"error": "count"}
        if n and account not in connected():
            return 409, {"error": "Connect this account in Post Studio first"}
        set_enrich(account, n)
        if os.environ.get("IGFANS_DIR"):  # test servers have no watch()
            threading.Thread(target=enrich, args=(account, n), daemon=True).start()
        return 200, {"ok": True}
    return 404, {"error": "not found"}


def watch():
    """Once a day: read the accounts that have an import. Profile checks run only after the owner asked for them."""
    while True:
        try:
            for a in sorted(connected()):
                if not os.path.isdir(os.path.join(DATA, "accounts", a)):
                    continue
                st = _read(_f(a, "state.json"), {})
                # the first read is started from the page; after that it repeats by itself
                if st.get("syncedAt") and now_ms() - st["syncedAt"] > SYNC_EVERY:
                    sync(a)
                e = st.get("enrich") or {}
                if e.get("left") and e.get("pauseUntil", 0) <= now_ms():
                    enrich(a, min(int(e["left"]), 200))
        except Exception as err:  # noqa: BLE001 - the loop must go on
            print("igfans:", type(err).__name__, err, file=sys.stderr)
        _wake.wait(60)
        _wake.clear()


# --- pretend answers for the test server -----------------------------------------------------------------------------------

def _fake(what, p):
    m = {"calls": 1, "app": 12, "buc": 3, "wait": 0}
    now = now_ms()
    if what == "profile":
        return {"ok": True, "meter": m, "name": "Demo", "followers": 1200, "following": 300, "posts": 2}
    if what == "media":
        return {"ok": True, "meter": m, "after": "", "items": [{"id": "1", "at": now - DAY, "link": "", "type": "IMAGE", "likes": 5, "comments": 3}, {"id": "2", "at": now - 400 * DAY, "link": "", "type": "IMAGE", "likes": 1, "comments": 9}]}
    if what == "comments":
        return {"ok": True, "meter": m, "after": "", "items": [{"id": "c%d" % i, "u": "user%d" % (i % 4), "at": now - i * DAY} for i in range(12)]}
    if what == "tags":
        return {"ok": True, "meter": m, "after": "", "items": [{"id": "t1", "u": "user2", "at": now - 3 * DAY}, {"id": "t2", "u": "not.a.follower", "at": now - DAY}]}
    if what == "dms":
        return {"ok": True, "meter": m, "after": "", "items": [{"id": "d1", "updated": now, "items": [{"id": "m1", "u": "user1", "at": now - DAY}]}]}
    if what == "audience":
        return {"ok": True, "meter": m, "items": {"country": [{"k": "RU", "n": 600}, {"k": "KZ", "n": 200}, {"k": "JP", "n": 90}], "city": [{"k": "Moscow, Moscow", "n": 200}], "age": [{"k": "18-24", "n": 400}, {"k": "25-34", "n": 500}], "gender": [{"k": "F", "n": 700}, {"k": "M", "n": 400}]}[p["by"]]}
    if what == "discover":
        u = p["u"]
        if u.endswith(("1", "3", "5", "7", "9")):
            return {"ok": True, "meter": m, "found": False}
        return {"ok": True, "meter": m, "found": True, "u": u, "name": "Name of " + u, "bio": "Language teacher", "web": "https://example.com", "followers": 100 + len(u) * 37, "following": 80, "posts": 12,
                "media": [{"at": now - DAY, "type": "IMAGE", "likes": 20, "comments": 2, "link": ""}] if p.get("media") else []}
    return {"ok": False, "error": "?", "code": 1000, "meter": {"calls": 0}}


if __name__ == "__main__":
    cmd = sys.argv[1:] or ["status"]
    if cmd[0] == "sync" and len(cmd) == 2:
        print(json.dumps(sync(clean(cmd[1])), ensure_ascii=False))
    elif cmd[0] == "enrich" and len(cmd) == 3:
        set_enrich(clean(cmd[1]), int(cmd[2]))
        print(json.dumps(enrich(clean(cmd[1]), int(cmd[2])), ensure_ascii=False))
    else:
        print(json.dumps({"accounts": accounts(), "connected": sorted(connected())}, ensure_ascii=False))
