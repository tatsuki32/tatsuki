"""Reflections: what ran late or did not happen gets a reflection to write, card by card, and the person may publish it.

The Desk finds (every 10 minutes, port 4747 only):
  late      a task still open the day after its due date
  latedone  a task done a day or more after its due date
  moved     a due date pushed later (three or more in one project on one day make one reflection)
  reply     a reply task still open after `replyHours` (72), or done after that long
  promise   a habit or routine goal marked "Promise" that was missed (habits without the mark are left alone)
  money     over the week or month spending limit, or a month that spent more than came in
Only what happens from `since` on (the day this started) is found by itself; earlier late tasks can be picked by hand.

Records are the `reflections` collection (desk/data/reflections.json). Settings in desk/data/reflect/settings.json.
"Write it up" sends the person's spoken text and card answers to Claude, which writes the reflection in the language
spoken. "Publish" sends only the final text to Rhizome Box (PUT /_/api/desk/reflection, key in the keychain item
tatsuki-desk-rhizomebox-reflect = the Worker's REFLECT_KEY). Nothing leaves the Mac unless the person presses.
"""
import datetime as dt
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("REFLECT_DIR") or os.path.join(ROOT, "data", "reflect")
KEYCHAIN = "tatsuki-desk-rhizomebox-reflect"
MODEL = "claude-sonnet-5-5"
API = "https://api.anthropic.com/v1/messages"
DAY = 86400000
CAUSES = {"estimate": "Estimate too small", "late": "Started late", "unclear": "First step unclear", "energy": "Low energy or health",
          "interrupt": "Interrupted", "priority": "Priority changed", "forgot": "Forgot", "other": "Other"}
FEELS = {"frustrated": "Frustrated", "relieved": "Relieved", "tired": "Tired", "nothing": "Nothing much"}
PLEDGE = {"ja": "今後起こさないように誓います。", "en": "I pledge not to let this happen again."}
# words that blame a person; a draft holding one is written again once
BLAME = re.compile(r"怠け|だらしな|最低|ダメ人間|情けな|lazy|useless|pathetic|failure as|my fault entirely", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"\+?\d[\d -]{8,}\d")
_lock = threading.Lock()


def now_ms():
    return int(time.time() * 1000)


def fake():
    return os.environ.get("REFLECT_FAKE") == "1"


def _read(name, default):
    try:
        with open(os.path.join(DIR, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(name, data):
    os.makedirs(DIR, exist_ok=True)
    p = os.path.join(DIR, name)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(p + ".tmp", p)


def settings():
    s = _read("settings.json", {})
    changed = False
    if not s.get("since"):
        s["since"], changed = now_ms(), True
    for k, v in (("replyHours", 72), ("sign", "Tatsuki")):
        if k not in s:
            s[k], changed = v, True
    if changed:
        _write("settings.json", s)
    return s


def put_settings(rec):
    s = settings()
    if "sign" in rec:
        s["sign"] = str(rec.get("sign") or "").strip()[:60]
    if "replyHours" in rec:
        try:
            s["replyHours"] = max(12, min(24 * 14, int(rec["replyHours"])))
        except (TypeError, ValueError):
            return 400, {"error": "replyHours must be a number of hours"}
    _write("settings.json", s)
    return 200, s


def day_of(ms):
    """The Mac's local date of a time in ms (the person's own clock)."""
    return dt.datetime.fromtimestamp(ms / 1000).date()


def _ok_task(t):
    return isinstance(t, dict) and not t.get("deleted") and t.get("by") != "claude"


def _reply(t):
    import replywin
    return replywin.reply_kind(t)


def _days(a, b):
    return (day_of(b) - day_of(a)).days


def _base(kind, ref, title, facts, at):
    return {"kind": kind, "ref": ref, "title": title, "facts": facts, "status": "open", "createdAt": at, "updatedAt": at}


def find(tasks, habits=(), goals=(), routinelog=(), money=None, s=None, now=None):
    """What should have a reflection, as {id: record}. Pure: the caller keeps what already exists."""
    s = s or settings()
    now = now or now_ms()
    since, today = s["since"], day_of(now)
    out = {}
    hours = s.get("replyHours", 72)
    moves = {}
    for t in tasks:
        if not _ok_task(t):
            continue
        st, due, title = t.get("status"), t.get("due"), str(t.get("title") or "")
        if _reply(t):
            made = t.get("createdAt") or 0
            if made < since:
                continue
            end = t.get("doneAt") if st == "done" else now
            if st in ("notyet", "doing", "done") and end and end - made > hours * 3600000:
                out["reply-" + t["id"]] = _base("reply", t["id"], title, {"from": made, "to": end if st == "done" else None,
                                                "hours": round((end - made) / 3600000)}, now)
            continue
        if isinstance(due, (int, float)):
            if st in ("notyet", "doing") and today > day_of(due):
                out["late-" + t["id"]] = _base("late", t["id"], title, {"due": due, "days": (today - day_of(due)).days}, now)
            elif st == "done" and t.get("doneAt") and t["doneAt"] >= since and day_of(t["doneAt"]) > day_of(due):
                out["latedone-" + t["id"]] = _base("latedone", t["id"], title, {"due": due, "done": t["doneAt"], "days": _days(due, t["doneAt"])}, now)
        for m in t.get("dueMoves") or []:
            if (m.get("at") or 0) >= since and (m.get("to") or 0) > (m.get("from") or 0):
                key = (t.get("projectId") or "", day_of(m["at"]).isoformat())
                moves.setdefault(key, []).append((t, m))
    for (proj, d), lst in moves.items():
        if proj and len(lst) >= 3:
            days = max(_days(m["from"], m["to"]) for _, m in lst)
            out[f"moved-{proj}-{d}"] = _base("moved", proj, f"{len(lst)} due dates moved later", {
                "count": len(lst), "days": days, "day": d, "tasks": [t["id"] for t, _ in lst][:40], "titles": [t.get("title") for t, _ in lst][:8]}, now)
        else:
            for t, m in lst:
                out[f"moved-{t['id']}-{m['at']}"] = _base("moved", t["id"], str(t.get("title") or ""), {
                    "from": m["from"], "to": m["to"], "days": _days(m["from"], m["to"]), "day": d}, now)
    # promises: only habits and goals the person marked
    for h in habits:
        if not isinstance(h, dict) or h.get("deleted") or not h.get("promise"):
            continue
        start = max(day_of(h.get("promiseAt") or since), day_of(since))
        done = set(h.get("done") or [])
        d = start
        while d < today:
            if d.isoformat() not in done:
                out[f"promise-{h['id']}-{d}"] = _base("promise", h["id"], str(h.get("name") or ""), {"day": d.isoformat(), "period": "day"}, now)
            d += dt.timedelta(days=1)
    counts = {}
    for r in routinelog:
        if isinstance(r, dict) and isinstance(r.get("goals"), dict) and re.match(r"^\d{4}-\d\d-\d\d$", str(r.get("id"))):
            counts[r["id"]] = r["goals"]
    for g in goals:
        if not isinstance(g, dict) or g.get("deleted") or g.get("kind") != "goal" or not g.get("promise"):
            continue
        start, target, per = max(day_of(g.get("promiseAt") or since), day_of(since)), int(g.get("target") or 1), g.get("period") or "day"
        d = start if per == "day" else start - dt.timedelta(days=start.weekday()) if per == "week" else start.replace(day=1)
        while True:
            if per == "day":
                end = d
            elif per == "week":
                end = d + dt.timedelta(days=6)
            else:
                end = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
            if end >= today:
                break
            n, x = 0, d
            while x <= end:
                n += int((counts.get(x.isoformat()) or {}).get(g["id"]) or 0)
                x += dt.timedelta(days=1)
            if n < target and end >= start:
                out[f"promise-{g['id']}-{d}"] = _base("promise", g["id"], str(g.get("title") or ""), {
                    "day": d.isoformat(), "end": end.isoformat(), "period": per, "count": n, "target": target}, now)
            d = end + dt.timedelta(days=1)
    for m in money or []:
        out[m["id"]] = _base("money", m["ref"], m["title"], m["facts"], now)
    return out


def money_misses(s=None, today=None):
    """Over this week's or month's limit, or last month spent more than came in. Reads the お金 files; never raises."""
    s = s or settings()
    out = []
    try:
        import wise_report
        import wise_sync
        today = today or wise_report.today_hcmc()
        limits = wise_sync.read_json("limits.json", {})
        for key, p in wise_report.pace(limits, today).items():
            if p["spent"] > p["limit"]:
                ref = (today - dt.timedelta(days=today.weekday())).isoformat() if key == "week" else today.isoformat()[:7]
                out.append({"id": f"money-{key}-{ref}", "ref": ref, "title": f"Spending over the {key}ly limit",
                            "facts": {"what": "limit", "period": key, "ref": ref, "limit": p["limit"], "spent": p["spent"]}})
        last = (today.replace(day=1) - dt.timedelta(days=1))
        if last >= day_of(s["since"]).replace(day=1):
            usd = wise_sync.summary(month=last.isoformat()[:7], today=today.isoformat())["usd"]
            if usd.get("count") and usd["net"] < 0:
                out.append({"id": "money-net-" + last.isoformat()[:7], "ref": last.isoformat()[:7], "title": "More went out than came in",
                            "facts": {"what": "net", "month": last.isoformat()[:7], "in": usd["in"], "out": usd["out"], "net": usd["net"]}})
    except Exception:  # the お金 files may be missing or half-written: try again next round
        return out
    return out


def scan(load, save, lock, now=None, money=None):
    """Add the reflections found to the collection. One that exists (open, written, or "Not a miss") is never made again."""
    s = settings()
    with lock:
        tasks, habits, routines, rlog = load("tasks"), load("habits"), load("routines"), load("routinelog")
    found = find(tasks, habits, routines, rlog, money_misses(s) if money is None else money, s, now)
    if not found:
        return 0
    with lock:
        items = load("reflections")
        have = {x["id"] for x in items}
        # a late task that is then done late is the same miss: one reflection
        new = [{**r, "id": i} for i, r in found.items() if i not in have and not (i.startswith("latedone-") and "late-" + i[9:] in have)]
        if new:
            items.extend(new)
            save("reflections", items)
    return len(new)


def earlier(tasks, items, s=None):
    """Tasks finished late before this started: the person may pick one to write about."""
    s = s or settings()
    have = {x.get("ref") for x in items}
    out = []
    for t in tasks:
        if _ok_task(t) and not _reply(t) and t.get("status") == "done" and isinstance(t.get("due"), (int, float)) and t.get("doneAt") \
                and t["doneAt"] < s["since"] and day_of(t["doneAt"]) > day_of(t["due"]) and t["id"] not in have:
            out.append({"ref": t["id"], "title": t.get("title"), "due": t["due"], "done": t["doneAt"], "days": _days(t["due"], t["doneAt"])})
    return sorted(out, key=lambda x: -x["done"])[:30]


def last_try(items, before_id=None):
    """The newest written reflection with an if-then plan, for the "Did you do it?" card."""
    done = [x for x in items if x.get("status") in ("done", "published") and (x.get("answers") or {}).get("ifThen") and x["id"] != before_id]
    return max(done, key=lambda x: x.get("doneAt") or 0) if done else None


# ---------- Claude writes it up ----------

def is_ja(text):
    return len(re.findall(r"[぀-ヿ一-鿿]", text or "")) >= max(3, len(text or "") * 0.15)


def facts_line(r):
    f, k = r.get("facts") or {}, r.get("kind")
    d = lambda ms: day_of(ms).isoformat() if ms else "?"
    if k == "late":
        return f"Due {d(f.get('due'))}, still open {f.get('days')} days later."
    if k == "latedone":
        return f"Due {d(f.get('due'))}, done {d(f.get('done'))} ({f.get('days')} days late)."
    if k == "moved":
        if f.get("count"):
            return f"{f['count']} due dates in one project moved later on {f.get('day')} (up to {f.get('days')} days)."
        return f"Due date moved from {d(f.get('from'))} to {d(f.get('to'))} ({f.get('days')} days later)."
    if k == "reply":
        return f"A reply took {f.get('hours')} hours" + ("" if f.get("to") else " and is still open") + "."
    if k == "promise":
        if f.get("period") == "day" and f.get("target") is None:
            return f"Not done on {f.get('day')}."
        return f"{f.get('count')} of {f.get('target')} from {f.get('day')} to {f.get('end')}."
    if k == "money":
        if f.get("what") == "net":
            return f"{f.get('month')}: in ${f.get('in')}, out ${f.get('out')} (net ${f.get('net')})."
        return f"Spent ${f.get('spent')} against a {f.get('period')} limit of ${f.get('limit')}."
    return ""


SYSTEM = """You turn a person's own answers about something that ran late or did not happen into a short written reflection.
Rules:
- Write in the language the person used in `spoken` and their answers (Japanese stays Japanese). If there is nothing to tell, English.
- Blame-free, like a good postmortem: describe what happened and the conditions, never call the person lazy or bad. Plain and honest.
- Use only what the person said and the facts given. Never invent events, numbers, names or dates.
- Never write another person's real name, an email subject, an email address or a phone number: say "a contact", "a message".
- Never write money amounts unless the person wrote them in their own words.
- title: 3-8 words naming the thing, no brackets, no date.
- sections: exactly four, in this order. Headings in the language of the text: English What happened / Causes / What I learned / What I will try next;
  Japanese 何があったか / 原因 / 学んだこと / 次に試すこと.
  Each section 1-4 short sentences. The last section must contain the if-then plan and its date if given.
- Do not add a pledge, signature or date at the end; the Desk adds them."""
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["title", "sections"], "properties": {
    "title": {"type": "string"},
    "sections": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["heading", "text"],
                 "properties": {"heading": {"type": "string"}, "text": {"type": "string"}}}}}}


class ReflectError(Exception):
    pass


def _ask(user):
    import lifelog
    key = lifelog.api_key()
    if not key:
        raise ReflectError("No Anthropic API key on this Mac. Write it yourself in the box.")
    body = {"model": MODEL, "max_tokens": 2000, "system": SYSTEM,
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            "messages": [{"role": "user", "content": user}]}
    req = urllib.request.Request(API, data=json.dumps(body).encode(), headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json", "user-agent": "tatsuki-desk"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        raise ReflectError(f"Claude is not available ({e.code}). Try again or write it yourself.") from None
    except (OSError, ValueError):
        raise ReflectError("Couldn't reach Claude. Try again or write it yourself.") from None
    out = next((b.get("text") for b in res.get("content", []) if b.get("type") == "text"), None)
    try:
        return json.loads(out)
    except (TypeError, ValueError):
        raise ReflectError("Claude's answer couldn't be read. Try again.") from None


def _user_text(r, prev):
    a = r.get("answers") or {}
    lines = [f"Kind: {r.get('kind')}", f"Item: {r.get('title')}", f"Facts: {facts_line(r)}"]
    if prev:
        lines.append(f"Last time's plan: {(prev.get('answers') or {}).get('ifThen')} -> did it: {a.get('kept') or 'not answered'}")
    lines += [f"Feeling: {FEELS.get(a.get('feel'), '')}",
              "Causes: " + ", ".join(CAUSES.get(c, c) for c in a.get("causes") or []),
              f"What was going on: {a.get('deeper') or ''}", f"What still went well: {a.get('keep') or ''}",
              f"If-then plan: {a.get('ifThen') or ''}", f"Try on: {a.get('tryDate') or ''}",
              f"spoken (the person's own words, dictated):\n{str(r.get('spoken') or '')[:6000]}"]
    return "\n".join(lines)


def clean_title(t):
    t = re.sub(r"[\[\]\n]", " ", str(t or "")).strip()
    return re.sub(r"\s+", " ", t)[:80] or "Untitled"


def write_up(r, items, s=None):
    """Claude's draft: {title, text} where text is the page body (lines joined) the person can still change."""
    s = s or settings()
    prev = last_try(items, r["id"])
    spoken = " ".join([str(r.get("spoken") or ""), *[str(v) for v in (r.get("answers") or {}).values() if isinstance(v, str)]])
    lang = "ja" if is_ja(spoken) else "en"
    if fake():
        d = {"title": r.get("title") or "Something late", "sections": [
            {"heading": "What happened", "text": facts_line(r)}, {"heading": "Causes", "text": ", ".join(CAUSES.get(c, c) for c in (r.get("answers") or {}).get("causes") or [])},
            {"heading": "What I learned", "text": (r.get("answers") or {}).get("deeper") or ""}, {"heading": "What I will try next", "text": (r.get("answers") or {}).get("ifThen") or ""}]}
    else:
        d = _ask(_user_text(r, prev))
        if any(BLAME.search(str(x.get("text"))) for x in d.get("sections") or []):
            d = _ask(_user_text(r, prev) + "\n\nThe last draft blamed the person. Describe conditions, not character.")
    title = clean_title(d.get("title"))
    body = []
    for sec in d.get("sections") or []:
        body += [f"[* {clean_title(sec.get('heading'))}]", *[x.strip() for x in str(sec.get("text") or "").splitlines() if x.strip()], ""]
    return {"title": title, "text": "\n".join(body).strip(), "lang": lang}


# ---------- publish ----------

def banned_words():
    words = []
    p = os.path.join(ROOT, "..", "tools", "banned-words.local.txt")
    try:
        with open(p, encoding="utf-8") as f:
            words = [w.strip() for w in f if w.strip() and not w.startswith("#")]
    except OSError:
        pass
    return words


def check_public(text):
    """Why this text must not go public, or ''. The banned words file holds real names; the reason never repeats the word."""
    low = text.lower()
    for w in banned_words():
        if w.lower() in low:
            return "It holds a word from your private list (tools/banned-words.local.txt). Take it out first."
    if EMAIL.search(text):
        return "It holds an email address. Take it out first."
    if PHONE.search(re.sub(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}:\d\d", " ", text)):  # dates and times are not phone numbers
        return "It holds what looks like a phone number. Take it out first."
    return ""


def page_lines(r, s=None):
    s = s or settings()
    day = r.get("day") or day_of(r.get("doneAt") or now_ms()).isoformat()
    title = f"Reflection: {clean_title(r.get('pageTitle') or r.get('title'))} ({day})"
    body = [x.rstrip() for x in str(r.get("final") or "").splitlines()]
    while body and not body[-1]:
        body.pop()
    lang = "ja" if is_ja(r.get("final") or "") else "en"
    return title, [title, "", *body, "", PLEDGE[lang], f"{s.get('sign')} — {day}" if s.get("sign") else day]


def key():
    try:
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN, "-w"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def site():
    if os.environ.get("REFLECT_SITE"):
        return os.environ["REFLECT_SITE"].rstrip("/")
    import rhizomebox_backup
    return rhizomebox_backup.site()


def _put(body, k, opener=None):
    req = urllib.request.Request(site() + "/_/api/desk/reflection", data=json.dumps(body).encode(), method="PUT",
                                 headers={"Authorization": "Bearer " + k, "X-RB": "1", "Content-Type": "application/json",
                                          "User-Agent": "tatsuki-desk-reflect"})
    with (opener or urllib.request.urlopen)(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def index_lines(items, s=None):
    """The index page below its mark: newest first, one link each, only what is public."""
    rows = sorted([x for x in items if x.get("rb", {}).get("pub")], key=lambda x: -(x["rb"].get("at") or 0))
    return [f" [{x['rb']['title']}]" for x in rows]


def publish(r, items, pub=True, k=None, opener=None):
    """Send one reflection to Rhizome Box (pub=False makes it private again). Returns the updated record or raises."""
    s = settings()
    if not str(r.get("final") or "").strip():
        raise ReflectError("Write the reflection first.")
    title, lines = page_lines(r, s)
    why = check_public("\n".join(lines)) if pub else ""
    if why:
        raise ReflectError(why)
    k = k or os.environ.get("REFLECT_KEY") or key()
    if not k:
        raise ReflectError("No Rhizome Box key yet. Run bash desk/reflect_setup.sh once.")
    old = r.get("rb") or {}
    rb = {"title": title, "pub": bool(pub), "at": now_ms() if pub else old.get("at") or now_ms()}
    others = [x for x in items if x["id"] != r["id"]]
    idx = index_lines(others + ([{**r, "rb": rb}] if pub else []))
    try:
        if old.get("title") and old["title"] != title:
            _put({"title": old["title"], "lines": [old["title"], "", "(moved)"], "pub": False, "index": idx}, k, opener)
        res = _put({"title": title, "lines": lines, "pub": bool(pub), "index": idx}, k, opener)
    except urllib.error.HTTPError as e:
        raise ReflectError(f"Rhizome Box said no ({e.code}).") from None
    except (OSError, ValueError):
        raise ReflectError("Couldn't reach Rhizome Box. Try again.") from None
    page = res.get("page") or {}
    rb["id"] = page.get("id", old.get("id"))
    rb["url"] = site() + "/" + urllib.parse.quote(title, safe="!'()*-._~").replace("%20", "_")  # the same as pageUrl in rhizomebox/src/app.js
    return {**r, "rb": rb, "status": "published" if pub else "done", "updatedAt": now_ms()}


# ---------- the server's door ----------

def handle(path, rec, load, upsert, lock):
    """POST /api/reflect/<op>. op: draft {id} · publish {id, pub} · start {ref} (an earlier late task) · try {id}"""
    op = path.rsplit("/", 1)[-1]
    with lock:
        items = load("reflections")
    r = next((x for x in items if x["id"] == str(rec.get("id") or "")), None)
    try:
        if op == "start":
            with lock:
                tasks = load("tasks")
            t = next((x for x in tasks if x.get("id") == rec.get("ref")), None)
            if not t or not t.get("due") or not t.get("doneAt"):
                return 404, {"error": "No such task"}
            i = "latedone-" + t["id"]
            if any(x["id"] == i for x in items):
                return 200, next(x for x in items if x["id"] == i)
            return 200, upsert("reflections", i, _base("latedone", t["id"], t.get("title"), {"due": t["due"], "done": t["doneAt"], "days": _days(t["due"], t["doneAt"])}, now_ms()), False)
        if not r:
            return 404, {"error": "No such reflection"}
        if op == "draft":
            d = write_up(r, items)
            return 200, upsert("reflections", r["id"], {**r, "draft": d["text"], "pageTitle": d["title"], "lang": d["lang"], "status": r.get("status") if r.get("status") == "published" else "draft", "updatedAt": now_ms()}, False)
        if op == "publish":
            out = publish(r, items, pub=rec.get("pub", True) is not False)
            return 200, upsert("reflections", r["id"], out, False)
        if op == "try":
            a = r.get("answers") or {}
            if not a.get("ifThen"):
                return 400, {"error": "Write the if-then plan first."}
            day = a.get("tryDate") or (day_of(now_ms()) + dt.timedelta(days=1)).isoformat()
            due = int(dt.datetime.fromisoformat(day + "T12:00:00").timestamp() * 1000)
            tid = r.get("tryTaskId") or "rf" + str(now_ms())
            upsert("tasks", tid, {"title": "Try: " + a["ifThen"][:200], "status": "notyet", "due": due, "source": "reflect",
                                  "sourceRef": r["id"], "createdAt": now_ms(), "updatedAt": now_ms()}, False)
            return 200, upsert("reflections", r["id"], {**r, "tryTaskId": tid, "updatedAt": now_ms()}, False)
    except ReflectError as e:
        return 400, {"error": str(e)}
    return 404, {"error": "not found"}


def state(load, lock):
    """GET /api/reflect: the settings, earlier late tasks to pick from, and last time's plan."""
    s = settings()
    with lock:
        items, tasks = load("reflections"), load("tasks")
    lt = last_try(items)
    return {"settings": s, "earlier": earlier(tasks, items, s), "causes": CAUSES, "feels": FEELS,
            "lastTry": {"id": lt["id"], "ifThen": lt["answers"]["ifThen"], "title": lt.get("title")} if lt else None,
            "hasKey": bool(os.environ.get("REFLECT_KEY") or (not fake() and key()))}


def watch(load, save, lock, every=600):
    time.sleep(20)
    while True:
        try:
            scan(load, save, lock)
        except Exception as e:  # never stop the server's thread over one bad record
            print("reflect:", e, flush=True)
        time.sleep(every)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ROOT)
    import server
    if sys.argv[1:2] == ["scan"]:
        print(scan(server.load, server.save, server.LOCK), "new")
    else:
        print(json.dumps(settings(), indent=1))
