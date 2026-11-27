#!/usr/bin/env python3
"""Tatsuki's Desk — what the Minutes view knows about a minute besides its text (desk/minutes.py).

  labels    a genre and a client for each minute. Claude names them, the person can change them.
  events    the calendar event a minute belongs to, found by its time or by its content. Nothing is written to Google.
  months    how many meetings and hours a month had, with and without minutes.
  reviews   how the owner spoke in a recording (numbers counted here) and Claude's advice on the meeting.

Files, all in desk/data/minutes/ (private; backup_data.py ignores the folder):
  labels.json    {"genres": [...], "clients": [...]}   id, name, by, mergedInto, deleted
  history.json   past calendar events: title, start, end, place, and three yes/no marks. No guests' addresses.
  insight.json   {"skip": [event ids that did not happen], "months": {"YYYY-MM": {"focus": ...}}}
A minute keeps: genre, client, tagBy, length, event.how, eventNo, suggest, match, review. None of them is an edit of its
text (minutes.patch), so the Google Doc and the mail to the owner are not made again.

What goes to Claude (claude-haiku-5-5, the key the summary uses): for labels the minute's own summary (a note that has
no summary yet sends its title only); for the event its title and summary with the titles of the candidate events; for
the review the transcript, once per recording.

  python3 minutes_insight.py pass       one pass of everything (labels, events, reviews)
  python3 minutes_insight.py history    read the calendar's past events now
  python3 minutes_insight.py month [YYYY-MM]
"""
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta

import minutes

KINDS = ("genre", "client")
LABEL_MAX = {"genre": 40, "client": 300}
NEAR = 600            # a recording that starts within 10 minutes of an event's start
HISTORY_EVERY = 3600  # the calendar's past is read once an hour
HISTORY_DAYS = 400    # and never further back than this
REVIEW_V = 1
TEXT_MAX = 4000       # characters of a minute sent for its labels or its event
ILOCK = threading.RLock()
_state = {"history": 0}
_busy = set()  # minutes whose review is being written right now


# ---------- files ----------

def _file(name):
    return os.path.join(minutes.DIR, name)


def _load(name, default):
    try:
        with open(_file(name), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return default


def _save(name, obj):
    os.makedirs(minutes.DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=minutes.DIR, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _file(name))


def _fake():
    return os.environ.get("MINUTES_FAKE") == "1"


def _claude(prompt, max_tokens=2000, timeout=180):
    """One JSON object from Claude, or None (no key, no network, no usable answer).
    max_tokens is never small, even for a one-word answer: the model thinks before it answers, and a tight cap is
    used up by the thinking, so no answer comes back (2026-11-27: half of the event questions failed at 100)."""
    key = minutes._anthropic_key()
    if not key:
        return None
    payload = {"model": minutes.MODEL, "max_tokens": max_tokens, "output_config": {"effort": "low"},
               "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(payload).encode("utf-8"),
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
        out = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        got = json.loads(out[out.find("{"): out.rfind("}") + 1])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return got if isinstance(got, dict) else None


def _recording(r):
    src = r.get("source") or {}
    return src.get("kind") == "transcribe" and src.get("from") != "notes"


def _pending(r):
    return (r.get("body") or "").startswith("## Summary\nNot made yet.")


CUT = re.compile(r"\n## (?:Transcript|元のメモ|Original notes)\n")


def _summary_part(r):
    """The minute without the transcript or the notes under it. A note with no summary yet gives nothing: its text is
    only sent to Claude when the person presses Make minutes."""
    if _pending(r):
        return ""
    return CUT.split(r.get("body") or "", 1)[0].strip()[:TEXT_MAX]


# ---------- labels: genre and client ----------

def _rows(d, kind):
    return [x for x in d.get(kind + "s", []) if isinstance(x, dict) and x.get("id")]


def _resolve(rows, lid):
    """The label an id stands for now: merged labels lead to the one they were merged into. "" when it is gone."""
    by, seen = {x["id"]: x for x in rows}, set()
    while lid in by and by[lid].get("mergedInto") and lid not in seen:
        seen.add(lid)
        lid = by[lid]["mergedInto"]
    return lid if lid in by and not by[lid].get("deleted") and not by[lid].get("mergedInto") else ""


def _live(rows):
    return [x for x in rows if not x.get("deleted") and not x.get("mergedInto")]


def labels():
    d = _load("labels.json", {})
    return {k + "s": sorted(({"id": x["id"], "name": x.get("name", ""), "by": x.get("by", "")} for x in _live(_rows(d, k))),
                            key=lambda x: x["name"].casefold()) for k in KINDS}


def _clean_name(name):
    return re.sub(r"\s+", " ", str(name or "")).strip()[:60]


def label_id(kind, name, by="claude"):
    """The id of the label with this name, made when there is none. "" for no name, or when the list is full."""
    name = _clean_name(name)
    if kind not in KINDS or not name:
        return ""
    with ILOCK:
        d = _load("labels.json", {})
        rows = _rows(d, kind)
        for x in rows:
            if x.get("name", "").casefold() == name.casefold() and not x.get("deleted"):
                return _resolve(rows, x["id"])
        if len(_live(rows)) >= LABEL_MAX[kind]:
            return ""
        new = {"id": uuid.uuid4().hex[:10], "name": name, "by": by, "at": time.time()}
        d[kind + "s"] = rows + [new]
        _save("labels.json", d)
        return new["id"]


def put_label(rec):
    """Rename a label, merge it into another, or remove it. A new name that another label already has merges the two."""
    rec = rec if isinstance(rec, dict) else {}
    kind, lid = rec.get("kind"), rec.get("id")
    if kind not in KINDS:
        raise minutes.MinutesError("Unknown kind of label.")
    with ILOCK:
        d = _load("labels.json", {})
        rows = _rows(d, kind)
        cur = next((x for x in rows if x["id"] == lid and not x.get("deleted") and not x.get("mergedInto")), None)
        if not cur:
            raise minutes.MinutesError("Not found")
        if rec.get("deleted"):
            cur["deleted"] = True
        else:
            name = _clean_name(rec.get("name"))
            if not name:
                raise minutes.MinutesError("A label needs a name.")
            same = next((x for x in _live(rows) if x["id"] != lid and x.get("name", "").casefold() == name.casefold()), None)
            if same:
                cur["mergedInto"] = same["id"]
            else:
                cur["name"] = name
                cur["by"] = "me"  # named by the person: Claude keeps using it under this name
        d[kind + "s"] = rows
        _save("labels.json", d)
    return labels()


def set_tag(mid, rec):
    """The person's own choice of genre or client for one minute. An empty name means none."""
    rec = rec if isinstance(rec, dict) else {}
    kind = rec.get("kind")
    if kind not in KINDS:
        raise minutes.MinutesError("Unknown kind of label.")
    name = _clean_name(rec.get("name"))
    lid = label_id(kind, name, by="me") if name else ""
    if name and not lid:
        raise minutes.MinutesError("There are too many labels. Merge some first.")

    def fn(cur):
        cur[kind] = lid
        cur["tagBy"] = {**(cur.get("tagBy") or {}), kind: "me"}
    return minutes.patch(mid, fn)


def prompt_keys():
    """Two more keys for the summary request of a new recording, with the labels there are now."""
    lab = labels()
    return LABEL_RULES + "\nExisting genres: " + _names(lab["genres"]) + "\nExisting clients: " + _names(lab["clients"]) + "\n"


def _names(rows):
    return "; ".join(x["name"] for x in rows[:120]) or "(none yet)"


LABEL_RULES = """- "genre": what kind of talk it was, as a short English label of 1 to 3 words (the form of "AI adoption", "Sales", "User interview"). Use one of the existing genres when it fits; make a new one only when none does
- "client": the company, organization or business counterpart on the other side, written as it is in the meeting. Use an existing client when it is the same one under another spelling. Empty when there is no outside party (a talk with friends or family, a memo to oneself) or when it is not clear; never guess one"""

LABEL_PROMPT = """You sort meeting minutes for one person's own dashboard. Give the meeting below a genre and a client.
""" + LABEL_RULES + """
Return only one JSON object: {"genre": "...", "client": "..."}.
"""


def _ask_labels(r):
    text = _summary_part(r)
    if _fake():
        return {"genre": "Test genre", "client": "Acme" if "acme" in (r.get("title", "") + text).lower() else ""}
    lab = labels()
    got = _claude(LABEL_PROMPT + "Existing genres: " + _names(lab["genres"]) + "\nExisting clients: " + _names(lab["clients"])
                  + "\n\nTitle: " + (r.get("title") or "") + ("\nMinutes:\n" + text if text else ""), timeout=90)
    return {k: _clean_name(got.get(k)) for k in KINDS} if got else None


def label_pass(limit=3):
    """Labels for minutes that have none yet. What the person chose is never changed. Returns how many were handled."""
    done = 0
    for r in sorted(minutes._all(), key=lambda x: x.get("at") or 0, reverse=True):
        if done >= limit:
            break
        by = r.get("tagBy") or {}
        if all(k in by for k in KINDS):
            continue
        if (r.get("tagTries") or 0) >= 3 and time.time() - (r.get("tagTriedAt") or 0) < 86400:
            continue  # three failures in a row: once more a day later
        if (r.get("source") or {}).get("kind") == "manual" and len(r.get("body") or "") < 200:
            continue  # a minute the person is still writing
        hint = r.get("tagHint") if isinstance(r.get("tagHint"), dict) else _ask_labels(r)
        done += 1
        if hint is None:
            minutes.patch(r["id"], lambda cur: cur.update(tagTries=(cur.get("tagTries") or 0) + 1, tagTriedAt=time.time()))
            continue
        ids = {k: label_id(k, hint.get(k)) for k in KINDS}

        def fn(cur, ids=ids):
            tb = dict(cur.get("tagBy") or {})
            for k in KINDS:
                if k not in tb:
                    cur[k], tb[k] = ids[k], "claude"
            cur["tagBy"] = tb
            cur.pop("tagHint", None)
        minutes.patch(r["id"], fn)
    return done


# ---------- the calendar's past ----------

def _ts(v):
    try:
        d = datetime.fromisoformat(str(v))
        return d.timestamp()
    except ValueError:
        return 0.0


def _history_from(now):
    """The first day of the month of the oldest minute, at most HISTORY_DAYS back."""
    ats = [float(r.get("at") or 0) for r in minutes._all() if r.get("at")]
    first = max(min(ats) if ats else now - 60 * 86400, now - HISTORY_DAYS * 86400)
    return datetime.fromtimestamp(first).replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


def store_history(evs, now=None):
    """Past events from parsed iCal (gcal_ics.parse): what the calendar shows and three marks, no guests' addresses."""
    import gcal_ics
    now = now or time.time()
    lo = _history_from(now)
    guests = {e.get("uid") for e in evs if e.get("people")}
    rows = []
    for r in gcal_ics.events(evs, lo, now + 86400):
        uid = r["id"][:r["id"].rfind("_")]
        rows.append({"id": r["id"], "uid": uid, "title": r["title"], "start": r["start"], "end": r["end"], "allDay": r["allDay"],
                     "location": r.get("location", ""), "meet": bool(r.get("meet")), "mine": bool(r.get("mine")), "guests": uid in guests})
    with ILOCK:
        _save("history.json", {"events": rows, "from": lo, "fetchedAt": now})
    return len(rows)


def refresh_history():
    """Reads the calendar's secret iCal address (the one the Calendar view uses). None when it cannot be read: the
    history read before stays."""
    import gcal_ics
    evs = []
    found = gcal_ics.urls()
    if not found:
        return None
    for u in found:
        try:
            with urllib.request.urlopen(u, timeout=60) as r:
                evs += gcal_ics.parse(r.read().decode("utf-8", "replace"), owner=gcal_ics.owner_of(u))
        except (OSError, ValueError):
            return None  # never the URL in a message: it is the secret
    return store_history(evs)


def history():
    return _load("history.json", {})


# ---------- the event a minute belongs to ----------

def _span(r):
    """(start, end) of a recording in epoch seconds. None for notes and hand-written minutes: they only have a day."""
    start, length = float(r.get("at") or 0), float(r.get("length") or 0)
    return (start, start + length) if _recording(r) and start and length >= 60 else None


def _day(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _meeting(e):
    return bool(e.get("meet") or e.get("guests"))


def candidates(r, events):
    """(exact, near): events that fit the recording's time exactly, and all that could be the meeting (best first)."""
    no = set(r.get("eventNo") or [])
    timed = [e for e in events if not e.get("allDay") and e.get("id") not in no]
    span = _span(r)
    if not span:
        day = _day(float(r.get("at") or 0))
        # only a day is known: time kept for oneself (a routine, a block of work) is not offered as the meeting
        return [], [e for e in timed if _day(_ts(e["start"])) == day and _meeting(e)][:8]
    a, b = span
    hits = []
    for e in timed:
        s, en = _ts(e["start"]), _ts(e["end"])
        ov = min(b, en) - max(a, s)
        if ov > 0:
            hits.append((ov, abs(s - a), e))
    hits.sort(key=lambda x: (not _meeting(x[2]), -x[0], x[1]))  # an event with other people before time kept for oneself
    exact = [e for ov, gap, e in hits if gap <= NEAR and ov >= 0.5 * (b - a)]
    with_people = [e for e in exact if _meeting(e)]
    if len(exact) > 1 and len(with_people) == 1:
        exact = with_people  # "Focus time" over the same hour is not the meeting
    return exact, [e for _, _, e in hits][:5]


EVENT_PROMPT = """The minutes of a meeting and the calendar events around that time are below. Say which event the minutes belong to.
Pick an event only when its title and the minutes are clearly about the same meeting (the same people, organization or topic).
A time that fits is not enough on its own. When none fits, or you are not sure, answer 0.
Return only one JSON object: {"pick": <the number of the event, or 0>}.
"""


def _ask_event(r, cands):
    """The index of the event the minute is about, -1 for none, None when Claude could not be asked."""
    text = _summary_part(r)
    if _fake():
        hay = ((r.get("title") or "") + " " + text).casefold()
        return next((i for i, e in enumerate(cands) if e["title"].casefold() in hay), -1)
    rows = "\n".join(f"{i + 1}. {e['title']} ({e['start'][:16].replace('T', ' ')} to {e['end'][11:16]})" for i, e in enumerate(cands))
    got = _claude(EVENT_PROMPT + "\nMinutes title: " + (r.get("title") or "") + ("\nMinutes:\n" + text if text else "")
                  + "\n\nEvents:\n" + rows, timeout=90)
    if got is None:
        return None
    try:
        n = int(got.get("pick") or 0)
    except (TypeError, ValueError):
        n = 0
    return n - 1 if 1 <= n <= len(cands) else -1


def _slim(e):
    return {"id": e["id"], "uid": e.get("uid") or e["id"][:e["id"].rfind("_")], "title": e["title"], "start": e["start"],
            "end": e.get("end", ""), "allDay": bool(e.get("allDay"))}


def match_pass(limit=5):
    """Links minutes without an event to the calendar event they belong to. Only inside the Desk: Google is not written.
    One fitting time is enough; otherwise the content decides, and when it cannot, the candidates are kept as suggestions."""
    events = history().get("events") or []
    if not events:
        return 0
    done, now = 0, time.time()
    for r in sorted(minutes._all(), key=lambda x: x.get("at") or 0, reverse=True):
        if r.get("event"):
            continue
        exact, near = candidates(r, events)
        pool = exact if len(exact) > 1 else near
        key = hashlib.sha256(json.dumps([[e["id"] for e in exact], [e["id"] for e in pool], r.get("title", ""),
                                         minutes._fp(_summary_part(r))]).encode()).hexdigest()[:16]
        old = r.get("match") or {}
        if old.get("key") == key and not (old.get("failed") and now - old.get("at", 0) > 3600):
            continue
        if done >= limit:
            break
        done += 1
        pick, how, failed = None, "", False
        if len(exact) == 1:
            pick, how = exact[0], "time"
        elif pool:
            i = _ask_event(r, pool)
            failed = i is None
            if i is not None and i >= 0:
                pick, how = pool[i], "content"

        def fn(cur, pick=pick, how=how, failed=failed, key=key, pool=pool):
            if cur.get("event"):
                return False  # linked in the meantime, by the person or by an auto-recording
            cur["match"] = {"key": key, "at": now, **({"failed": True} if failed else {})}
            if pick:
                # auto: nobody chose it, so minutes_mail still checks the event was accepted before it drafts anything
                cur["event"] = {**_slim(pick), "auto": True, "how": how}
                cur["calendar"] = None
                cur.pop("suggest", None)
            elif pool:
                cur["suggest"] = [_slim(e) for e in pool[:3]]
            else:
                cur.pop("suggest", None)
        minutes.patch(r["id"], fn)
    return done


def not_this(mid, rec):
    """Takes off an event found by itself, or drops a suggestion. The event is never offered for this minute again."""
    want = (rec if isinstance(rec, dict) else {}).get("id")

    def fn(cur):
        ev = cur.get("event") or {}
        eid = want or ev.get("id")
        if not eid:
            return False
        cur["eventNo"] = [x for x in (cur.get("eventNo") or []) if x != eid][-19:] + [eid]
        if ev.get("id") == eid:
            cur["event"], cur["calendar"] = None, None
        left = [e for e in (cur.get("suggest") or []) if e.get("id") != eid]
        if left:
            cur["suggest"] = left
        else:
            cur.pop("suggest", None)
    return minutes.patch(mid, fn)


# ---------- lengths ----------

def length_pass():
    """The length of recordings made before `length` was kept. One read of the recorder's list, only when one is missing."""
    todo = [r for r in minutes._all() if _recording(r) and "length" not in r]
    if not todo:
        return 0
    try:
        by = {s.get("id"): s for s in minutes._http_json(f"{minutes.TRANSCRIBE}/api/sessions")}
    except (OSError, ValueError):
        return 0
    for r in todo:
        s = by.get(r["source"].get("sid")) or {}
        minutes.patch(r["id"], lambda cur, s=s: cur.update(length=float(s.get("duration") or 0)))
    return len(todo)


# ---------- the review: how the owner spoke ----------

# What is counted as a filler. Each entry: the word shown, and the pattern that finds it in the owner's own lines.
FILLERS = [("なんか", r"なんか"), ("ちょっと", r"ちょっと"), ("まあ", r"ま[あぁ]"), ("えー", r"えー+|ええと|えっと"), ("あの", r"あのー+|あの、"),
           ("みたいな", r"みたいな"), ("っていう", r"っていう"), ("とか", r"とか"), ("やっぱ", r"やっぱ"), ("一応", r"一応|いちおう"),
           ("結構", r"結構|けっこう"), ("要は", r"要は"), ("逆に", r"逆に"), ("普通に", r"普通に"), ("基本的に", r"基本的に"),
           ("多分", r"多分|たぶん"), ("ぶっちゃけ", r"ぶっちゃけ"), ("感じ", r"感じ"),
           ("you know", r"\byou know\b"), ("I mean", r"\bi mean\b"), ("kind of", r"\bkind of\b"), ("sort of", r"\bsort of\b"),
           ("basically", r"\bbasically\b"), ("actually", r"\bactually\b"), ("literally", r"\bliterally\b"), ("um", r"\bu[hm]+\b")]
QUESTION = re.compile(r"[?？]|(?<!と)(?:ですか|ますか|でしょうか|かな)(?=[。、\s]|$)")


def speak_stats(segs, me):
    """Numbers counted from the lines of a recording: no AI. `me` is the owner's speaker key ("" when voices were not told apart)."""
    segs = sorted((s for s in segs if isinstance(s, dict) and (s.get("text") or "").strip()), key=lambda s: (s.get("start") or 0, s.get("end") or 0))
    split = any(s.get("spk") for s in segs)
    per, total = {}, 0.0
    for s in segs:
        d = max(0.0, float(s.get("end") or 0) - float(s.get("start") or 0))
        per[s.get("spk") or ""] = per.get(s.get("spk") or "", 0.0) + d
        total += d
    mine = [s for s in segs if s.get("spk") == me] if split else segs
    my_secs = sum(max(0.0, float(s.get("end") or 0) - float(s.get("start") or 0)) for s in mine)
    longest, run_start, last_end = 0.0, None, 0.0
    for s in segs:
        is_me = (s.get("spk") == me) if split else True
        st, en = float(s.get("start") or 0), float(s.get("end") or 0)
        if is_me and run_start is not None and st - last_end <= 3:
            last_end = max(last_end, en)
        elif is_me:
            run_start, last_end = st, en
        else:
            run_start = None
        if run_start is not None:
            longest = max(longest, last_end - run_start)
    text = "\n".join(s["text"].strip() for s in mine)
    low = text.lower()
    fill = sorted(([w, len(re.findall(p, low))] for w, p in FILLERS), key=lambda x: -x[1])
    fill = [{"w": w, "n": n} for w, n in fill if n >= 2][:6]
    n_fill = sum(len(re.findall(p, low)) for _, p in FILLERS)
    return {"split": split, "total": round(total), "mine": round(my_secs),
            "share": round(my_secs / total, 3) if split and total and me else None, "longest": round(longest),
            "questions": len(QUESTION.findall(text)), "fillers": fill, "fillerCount": n_fill,
            "fillerPerMin": round(n_fill / (my_secs / 60), 1) if my_secs >= 60 else None,
            "speakers": {k: round(v) for k, v in per.items() if k}}


REVIEW_PROMPT = """You coach one person on how they run meetings and how they speak in them. Below is the transcript of a meeting they
recorded, made by a speech engine (it mishears words and drops some fillers), with rough numbers counted from it.
{who}
Write in plain American English: kind, direct, never blaming. Quote the owner's own words exactly as they stand in the
transcript, in the original language. Say only what the transcript shows; when there is too little for a list, leave it empty.
At most 3 items in each list, one sentence each.

Return only one JSON object with these keys:
- "me": {me_rule}
- "shorter": a list of {{"point": "what could have been skipped, kept shorter, or settled in writing instead", "minutes": minutes it would save as a number, 0 when unclear}}
- "ran": a list of {{"point": "how the meeting was run: was its purpose said at the start, were decisions, owners and dates confirmed at the end, where did it drift"}}
- "spoke": a list of {{"point": "a habit in how the owner spoke that came up too often: a filler, hedging, a long stretch without a question, cutting in", "quote": "a short example from the owner's own lines"}}
- "good": one sentence on what the owner did well
- "next": one concrete thing to try in the next meeting

Numbers: {numbers}

Transcript:
"""


def _stamp(sec):
    sec = int(sec or 0)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _lines(segs, names, me):
    out, last = [], None
    for s in sorted(segs, key=lambda s: (s.get("start") or 0, s.get("end") or 0)):
        text = (s.get("text") or "").strip()
        if not text:
            continue
        k = s.get("spk") or ""
        if out and k == last:
            out[-1] += " " + text
            continue
        who = "YOU" if me and k == me else f"{names.get(k, k)} [{k}]" if k else ""
        out.append(f"[{_stamp(s.get('start'))}] " + (who + ": " if who else "") + text)
        last = k
    return "\n".join(out)[:minutes.TRANSCRIPT_MAX]


def _norm(v):
    return re.sub(r"\s+", "", str(v or ""))


def _clean_advice(got, my_text, total):
    """Claude's answer cut to what the page shows. A quote that is not in the owner's own lines is dropped."""
    def rows(v, extra=()):
        out = []
        for x in (v if isinstance(v, list) else [])[:3]:
            if isinstance(x, dict) and minutes._text(x.get("point"), 400):
                row = {"point": minutes._text(x.get("point"), 400)}
                if "quote" in extra and _norm(x.get("quote")) and _norm(x.get("quote")) in my_text:
                    row["quote"] = minutes._text(x.get("quote"), 200)
                if "minutes" in extra:
                    try:
                        m = float(x.get("minutes") or 0)
                    except (TypeError, ValueError):
                        m = 0
                    if 0 < m <= total / 60:
                        row["minutes"] = round(m)
                out.append(row)
        return out
    return {"shorter": rows(got.get("shorter"), ("minutes",)), "ran": rows(got.get("ran")), "spoke": rows(got.get("spoke"), ("quote",)),
            "good": minutes._text(got.get("good"), 400), "next": minutes._text(got.get("next"), 400)}


def _ask_review(segs, names, keys, me, stats_by):
    if _fake():
        mine = me or (keys[0] if keys else "")
        first = next((s["text"].strip()[:8] for s in segs if (s.get("spk") or "") == mine and (s.get("text") or "").strip()), "")
        return {"me": mine, "shorter": [{"point": "Test shorter", "minutes": 5}], "ran": [{"point": "Test ran"}],
                "spoke": [{"point": "Test spoke", "quote": first}, {"point": "Test made up", "quote": "words nobody said"}],
                "good": "Test good", "next": "Test next"}
    if me or not keys:
        who = 'The owner\'s lines are marked "YOU".' if me else "The voices were not told apart, so nothing says who spoke: leave \"spoke\" empty."
        me_rule = '""'
    else:
        who = ("The owner is one of these speakers: " + ", ".join(f"{names.get(k, k)} [{k}]" for k in keys) + ". They made the recording "
               "and usually set up or lead the talk. Decide which speaker is most likely the owner and write the review for that speaker.")
        me_rule = "the key in square brackets of the speaker you took as the owner"
    return _claude(REVIEW_PROMPT.format(who=who, me_rule=me_rule, numbers=json.dumps(stats_by, ensure_ascii=False)) + _lines(segs, names, me), 2500)


def _session(sid):
    """The recording with its lines. None when the recorder is not running, "gone" when the recording was erased."""
    try:
        return minutes._http_json(f"{minutes.TRANSCRIBE}/api/sessions/{sid}")
    except urllib.error.HTTPError as e:
        return "gone" if e.code == 404 else None
    except (OSError, ValueError):
        return None


def _keys(segs):
    return sorted({s.get("spk") for s in segs if s.get("spk")})


def build_review(mid, me=None, advice=True):
    """Makes the review of one recording and keeps it on the minute. me: the speaker the person said is theirs.
    advice=False counts the numbers only (the answer to a click; Claude's part follows on a later pass)."""
    r = minutes.get(mid)
    if not _recording(r):
        raise minutes.MinutesError("Only recordings have a review.")
    full = _session(r["source"].get("sid") or "")
    now = time.time()
    if full is None:
        raise minutes.MinutesError("Transcribe is not running. Open the Transcribe view once, then try again.")
    if full == "gone" or not (full.get("segments") or []):
        return minutes.patch(mid, lambda cur: cur.update(review={"v": REVIEW_V, "empty": True, "at": now}))
    segs, names = full["segments"], {k: str(v) for k, v in (full.get("speakers") or {}).items()}
    keys, prev = _keys(segs), r.get("review") or {}
    if me and me not in keys:
        raise minutes.MinutesError("That speaker is not in this recording.")
    if me:
        mine, by = me, "me"
    elif full.get("mode") == "meeting" and "me" in keys:
        mine, by = "me", "track"  # an online meeting: the owner's microphone is its own track
    elif prev.get("meBy") == "me" and prev.get("me") in keys:
        mine, by = prev["me"], "me"
    elif len(keys) <= 1:
        mine, by = (keys[0] if keys else ""), "only"
    else:
        mine, by = "", ""
    got = None
    if advice:
        got = _ask_review(segs, names, keys, mine, {k or "all": speak_stats(segs, k) for k in ([mine] if mine or not keys else keys)})
    if not mine and keys:
        if got and got.get("me") in keys:
            mine, by = got["me"], "claude"
        elif prev.get("me") in keys:
            mine, by = prev["me"], prev.get("meBy") or "guess"
        else:  # Claude could not be asked: the one who spoke most, until the person says otherwise
            mine, by = max(keys, key=lambda k: speak_stats(segs, k)["mine"]), "guess"
    stats = speak_stats(segs, mine)
    my_text = _norm("".join((s.get("text") or "") for s in segs if not keys or s.get("spk") == mine))
    done = _clean_advice(got, my_text, stats["total"]) if got else None
    review = {"v": REVIEW_V, "me": mine, "meBy": by, "stats": stats, "names": {k: names.get(k, k) for k in keys},
              "advice": done if advice else None, "at": now,
              "tries": 0 if done or not advice else (prev.get("tries") or 0) + 1, **({} if advice else {"redo": True})}
    return minutes.patch(mid, lambda cur: cur.update(review=review))


def _later(mid):
    def run():
        try:
            build_review(mid)
        except Exception as e:  # the next pass tries again; the message names no content
            print(f"[minutes] review failed: {type(e).__name__}", flush=True)
        finally:
            _busy.discard(mid)
    _busy.add(mid)
    threading.Thread(target=run, daemon=True).start()


def set_voice(mid, rec):
    """The person says which speaker they are: the numbers change at once, the advice is written again after."""
    me = str((rec if isinstance(rec, dict) else {}).get("me") or "")
    if not me:
        raise minutes.MinutesError("Choose a speaker.")
    out = build_review(mid, me=me, advice=False)
    _later(mid)
    return out


def redo_review(mid):
    r = minutes.get(mid)
    me = (r.get("review") or {}).get("me") if (r.get("review") or {}).get("meBy") == "me" else None
    out = build_review(mid, me=me, advice=False)
    _later(mid)
    return out


def _needs_review(r, now):
    rv = r.get("review") or {}
    if not rv or rv.get("v") != REVIEW_V or rv.get("redo"):
        return True
    return not rv.get("empty") and rv.get("advice") is None and (rv.get("tries") or 0) < 3 and now - (rv.get("at") or 0) > 3600


def review_pass(limit=3):
    done, now = 0, time.time()
    for r in sorted(minutes._all(), key=lambda x: x.get("at") or 0, reverse=True):
        if done >= limit:
            break
        if not _recording(r) or not _needs_review(r, now) or r["id"] in _busy:
            continue
        try:
            build_review(r["id"])
        except minutes.MinutesError:
            return done  # the recorder is not running: nothing to read from for the rest either
        done += 1
    return done


# ---------- the month ----------

def _month_bounds(month):
    try:
        lo = datetime.strptime(month + "-01", "%Y-%m-%d")
    except (TypeError, ValueError):
        raise minutes.MinutesError("The month must look like 2026-11.")
    hi = (lo + timedelta(days=32)).replace(day=1)
    return lo.timestamp(), hi.timestamp()


def _event_secs(e):
    return max(0.0, _ts(e.get("end")) - _ts(e.get("start"))) if e and not e.get("allDay") else 0.0


def _minute_secs(r):
    return float(r.get("length") or 0) if _recording(r) and float(r.get("length") or 0) > 0 else _event_secs(r.get("event"))


def _counted(lo, hi, now):
    """(minutes of the month, calendar meetings of the month that have no minute). A meeting is counted once."""
    rows = minutes._all()
    mine = [r for r in rows if lo <= float(r.get("at") or 0) < hi]
    linked = {(r.get("event") or {}).get("id") for r in rows if r.get("event")}
    spans = [s for s in (_span(r) for r in rows if not r.get("event")) if s]
    events = []
    for e in history().get("events") or []:
        s, en = _ts(e.get("start")), _ts(e.get("end"))
        if e.get("allDay") or not (lo <= s < hi) or en > now or e.get("id") in linked:
            continue
        if not e.get("mine") or not _meeting(e):
            continue  # not answered or declined, or time kept for oneself
        # a recording over the same time with no event of its own is this meeting: counted once, with the recording
        if any(min(b, en) - max(a, s) >= 0.5 * min(b - a, en - s) > 0 for a, b in spans):
            continue
        events.append(e)
    return mine, events


def _totals(mine, events, skip):
    kept = [e for e in events if e["id"] not in skip]
    rec_secs = sum(_minute_secs(r) for r in mine)
    cal_secs = sum(_event_secs(e) for e in kept)
    return {"meetings": len(mine) + len(kept), "secs": round(rec_secs + cal_secs),
            "recorded": {"n": len(mine), "secs": round(rec_secs), "untimed": sum(1 for r in mine if not _minute_secs(r))},
            "notRecorded": {"n": len(kept), "secs": round(cal_secs)}}


def _by_label(mine, kind, lab):
    names = {x["id"]: x["name"] for x in lab[kind + "s"]}
    groups = {}
    for r in mine:
        g = groups.setdefault(r.get(kind) if r.get(kind) in names else "", {"n": 0, "secs": 0.0})
        g["n"] += 1
        g["secs"] += _minute_secs(r)
    return sorted(({"id": k, "name": names.get(k, ""), "n": v["n"], "secs": round(v["secs"])} for k, v in groups.items()),
                  key=lambda x: (not x["id"], -x["secs"], -x["n"]))


def _speaking(mine):
    """The month's reviews put together: averages of the numbers and the fillers heard most."""
    revs = [r["review"] for r in mine if isinstance(r.get("review"), dict) and r["review"].get("stats")]
    if not revs:
        return None
    shares = [v["stats"]["share"] for v in revs if v["stats"].get("share") is not None]
    fill = {}
    for v in revs:
        for f in v["stats"].get("fillers") or []:
            fill[f["w"]] = fill.get(f["w"], 0) + f["n"]
    my_min = sum(v["stats"].get("mine") or 0 for v in revs) / 60
    return {"n": len(revs), "share": round(sum(shares) / len(shares), 3) if shares else None,
            "longest": round(sum(v["stats"].get("longest") or 0 for v in revs) / len(revs)),
            "questions": round(sum(v["stats"].get("questions") or 0 for v in revs) / len(revs), 1),
            "fillerPerMin": round(sum(v["stats"].get("fillerCount") or 0 for v in revs) / my_min, 1) if my_min >= 1 else None,
            "fillers": [{"w": w, "n": n} for w, n in sorted(fill.items(), key=lambda x: -x[1])[:6]]}


def month(month=None, now=None):
    """What the Insights tab shows for one month (the Mac's own time)."""
    now = now or time.time()
    month = month or datetime.fromtimestamp(now).strftime("%Y-%m")
    lo, hi = _month_bounds(month)
    plo, _ = _month_bounds(datetime.fromtimestamp(lo - 86400).strftime("%Y-%m"))
    ins, hist, lab = _load("insight.json", {}), history(), labels()
    skip = set(ins.get("skip") or [])
    mine, events = _counted(lo, hi, now)
    _decorate_rows(mine)
    pmine, pevents = _counted(plo, lo, now)
    rows = [{"kind": "minute", "id": r["id"], "title": r.get("title", ""), "at": r.get("at"), "secs": round(_minute_secs(r)),
             "recording": _recording(r), "genre": r.get("genre", ""), "client": r.get("client", "")} for r in mine]
    rows += [{"kind": "event", "id": e["id"], "title": e["title"], "at": _ts(e["start"]), "secs": round(_event_secs(e)),
              "skipped": e["id"] in skip} for e in events]
    rows.sort(key=lambda x: x["at"] or 0, reverse=True)
    covered = bool(hist.get("events") is not None and hist.get("from") is not None and hist["from"] <= lo)
    return {"month": month, "totals": _totals(mine, events, skip), "prev": _totals(pmine, pevents, skip),
            "byGenre": _by_label(mine, "genre", lab), "byClient": _by_label(mine, "client", lab), "rows": rows,
            "speaking": _speaking(mine), "focus": ((ins.get("months") or {}).get(month) or {}).get("focus", ""),
            "calendar": {"covered": covered, "from": hist.get("from"), "fetchedAt": hist.get("fetchedAt")}}


def put_skip(rec):
    """A calendar event that did not happen: out of the month's count (or back in)."""
    rec = rec if isinstance(rec, dict) else {}
    eid = minutes._text(rec.get("id"), 300)
    if not eid:
        raise minutes.MinutesError("Which event?")
    with ILOCK:
        ins = _load("insight.json", {})
        skip = [x for x in (ins.get("skip") or []) if x != eid]
        ins["skip"] = (skip + [eid] if rec.get("on", True) else skip)[-500:]
        _save("insight.json", ins)
    return {"ok": True}


FOCUS_PROMPT = """Below are notes from reviews of one person's meetings this month: habits in how they spoke, and what each review
suggested they try next. Choose the one thing that would help most if they worked on it for the rest of the month.
Write in plain American English, kind and direct, one or two sentences, with the habit named concretely.
Return only one JSON object: {"focus": "..."}.

"""


def focus_pass(now=None):
    """One thing to work on this month, from the month's reviews. Asked again only when the reviews changed."""
    now = now or time.time()
    m = datetime.fromtimestamp(now).strftime("%Y-%m")
    lo, hi = _month_bounds(m)
    revs = [(r["id"], r["review"]) for r in minutes._all() if lo <= float(r.get("at") or 0) < hi
            and isinstance(r.get("review"), dict) and r["review"].get("advice")]
    if not revs:
        return 0
    key = hashlib.sha256(json.dumps(sorted([i, v.get("at")] for i, v in revs)).encode()).hexdigest()[:16]
    with ILOCK:
        cur = (_load("insight.json", {}).get("months") or {}).get(m) or {}
    if cur.get("key") == key:
        return 0
    if len(revs) == 1 or _fake():
        focus = revs[0][1]["advice"].get("next") or ""
    else:
        notes = "\n".join("- " + "; ".join([x["point"] for x in v["advice"].get("spoke") or []] + [v["advice"].get("next") or ""])
                          for _, v in revs)[:6000]
        got = _claude(FOCUS_PROMPT + notes, timeout=90)
        if got is None:
            return 0
        focus = minutes._text(got.get("focus"), 500)
    with ILOCK:
        ins = _load("insight.json", {})
        months = {k: v for k, v in sorted((ins.get("months") or {}).items())[-23:]}
        months[m] = {"focus": focus, "key": key, "at": now}
        ins["months"] = months
        _save("insight.json", ins)
    return 1


# ---------- what the page reads and sends ----------

def _decorate_rows(rows):
    d = _load("labels.json", {})
    for r in rows:
        for k in KINDS:
            r[k] = _resolve(_rows(d, k), r.get(k) or "")
    return rows


def decorate(view):
    """minutes.view() with the labels there are and each minute's labels as they stand now (merged ones followed)."""
    _decorate_rows(view.get("minutes") or [])
    return {**view, "labels": labels()}


def events_near(day):
    """Past calendar events on a day and the days next to it, for the picker of a minute made long ago."""
    try:
        mid = datetime.strptime(str(day), "%Y-%m-%d")
    except ValueError:
        raise minutes.MinutesError("The day must look like 2026-11-27.")
    days = {(mid + timedelta(days=d)).strftime("%Y-%m-%d") for d in (-1, 0, 1)}
    rows = [_slim(e) for e in history().get("events") or [] if not e.get("allDay") and _day(_ts(e["start"])) in days]
    return {"events": sorted(rows, key=lambda e: e["start"])[:60]}


def handle_get(path, query):
    """The reads of this module, or None when the path is not one of them (a minute's own id, for one)."""
    try:
        if path == "/api/minutes/insights":
            return 200, month((query.get("month") or [None])[0])
        if path == "/api/minutes/events":
            return 200, events_near((query.get("day") or [""])[0])
    except minutes.MinutesError as e:
        return 400, {"error": str(e)}
    return None


def handle(method, p, rec):
    """The writes of this module, or None when the path is not one of them. p: [minutes, ...] from the request path."""
    if p == ["minutes", "labels"] and method == "PUT":
        return 200, put_label(rec)
    if p == ["minutes", "skip"] and method == "PUT":
        return 200, put_skip(rec)
    if len(p) == 3 and method == "POST":
        if p[2] == "tag":
            return 200, set_tag(p[1], rec)
        if p[2] == "notthis":
            return 200, not_this(p[1], rec)
        if p[2] == "voice":
            return 200, set_voice(p[1], rec)
        if p[2] == "review":
            return 200, redo_review(p[1])
    return None


def once():
    return {"lengths": length_pass(), "labels": label_pass(), "events": match_pass(), "reviews": review_pass(), "focus": focus_pass()}


def watch():
    """Run by the Desk server on 4747: a pass every minute, the calendar's past once an hour. Errors are printed without
    any text of a minute, an event or a key."""
    while True:
        try:
            if time.time() - _state["history"] > HISTORY_EVERY:
                _state["history"] = time.time()
                refresh_history()
            once()
        except Exception as e:
            print(f"[minutes] insight pass failed: {type(e).__name__}", flush=True)
        time.sleep(60)


if __name__ == "__main__":
    if sys.argv[1:] == ["pass"]:
        print(json.dumps(once()))
    elif sys.argv[1:] == ["history"]:
        n = refresh_history()
        print("could not read the calendar" if n is None else f"{n} past event(s)")
    elif sys.argv[1:2] == ["month"]:
        print(json.dumps({k: v for k, v in month(sys.argv[2] if len(sys.argv) > 2 else None).items() if k != "rows"}, ensure_ascii=False, indent=1))
    else:
        print(__doc__)
