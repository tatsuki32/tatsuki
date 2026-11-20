#!/usr/bin/env python3
"""Tatsuki's Desk — Forms on tany.im (the replacement for Google Forms).

A form is made on the Desk screen #forms and kept on this Mac (desk/data/forms/forms/<id>.json, the source).
Publish sends it to the tany.im Worker (desk/data/tany/site/forms.js, D1 table forms); people answer at
https://tany.im/f/<slug>. The Worker keeps the answers (90 days) and this module pulls them every 5 minutes
(server.py runs watch() on 4747 only) into desk/data/forms/responses/<id>.jsonl, which stays on this Mac for good.
desk/data/forms/ holds other people's answers, so backup_data.py leaves it out of the private copy too.

Nothing here publishes on its own: publish/close happen when the person presses the button. The passphrase for the
Worker is in the keychain as tatsuki-desk-forms (the Worker has it as FORMS_KEY).

"Summarize with Claude" sends the free-text answers (never the questions marked pii) to the Claude API once, when
pressed, and keeps the result in summaries/<id>.json with the date.

  python3 desk/forms.py pull       one pull now
  python3 desk/forms.py import <google forms url>   prints the draft a Google Form would become
"""
import csv
import io
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lifelog  # noqa: E402  (the Claude API key and endpoint)
import notify  # noqa: E402  (show_mac: a Mac notification with the form's name and a count)
from lifeos.lib import tasks  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("DESK_FORMS_DIR") or os.path.join(ROOT, "data", "forms")
FORMS = os.path.join(DIR, "forms")
RESPONSES = os.path.join(DIR, "responses")
SUMMARIES = os.path.join(DIR, "summaries")
STATE = os.path.join(DIR, "state.json")
KEYCHAIN = "tatsuki-desk-forms"
WORKER = os.environ.get("DESK_FORMS_WORKER") or "https://tany.im"
BASE = "https://tany.im"
MODEL = "claude-sonnet-5-5"
EVERY = 300
ID = re.compile(r"^[0-9a-f]{12}$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")
TYPES = ("short", "long", "choice", "multi", "dropdown", "number", "date", "email", "phone", "scale", "section")
LANGS = ("ja", "en", "vi")
ACCESS = ("public", "link", "me")
DESTS = ("keep", "task", "note")
HCM = timezone(timedelta(hours=7))
_lock = threading.Lock()
# server.py hands over its note store so a "me" form can write to the day's note: {"load": load, "upsert": upsert}
STORE = {}


# ---------- storage ----------

def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _path(fid):
    return os.path.join(FORMS, fid + ".json")


def get(fid):
    if not ID.match(fid or ""):
        return None
    d = _read(_path(fid), None)
    return d if d and not d.get("deleted") else None


def _all():
    out = []
    if os.path.isdir(FORMS):
        for name in os.listdir(FORMS):
            if name.endswith(".json") and ID.match(name[:-5]):
                d = _read(os.path.join(FORMS, name), None)
                if d and not d.get("deleted"):
                    out.append(d)
    return out


def _rows(fid):
    """Every answer kept for the form, oldest first."""
    out = []
    try:
        with open(os.path.join(RESPONSES, fid + ".jsonl"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
    except OSError:
        pass
    return out


def _append(fid, rows):
    os.makedirs(RESPONSES, mode=0o700, exist_ok=True)
    with open(os.path.join(RESPONSES, fid + ".jsonl"), "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ---------- forms ----------

def _lang_text(v, langs):
    """{lang: text} with only the form's languages, each a string."""
    if isinstance(v, str):
        v = {langs[0]: v}
    if not isinstance(v, dict):
        return {}
    return {l: re.sub(r"\r", "", str(v.get(l) or ""))[:2000] for l in langs if v.get(l)}


def _lang_list(v, langs):
    if isinstance(v, list):
        v = {langs[0]: v}
    if not isinstance(v, dict):
        return {}
    out = {}
    for l in langs:
        xs = v.get(l)
        if isinstance(xs, str):
            xs = xs.split("\n")
        if isinstance(xs, list):
            xs = [str(x).strip()[:200] for x in xs if str(x).strip()][:100]
            if xs:
                out[l] = xs
    return out


def _question(q, langs):
    if not isinstance(q, dict) or q.get("type") not in TYPES:
        return None
    qid = str(q.get("id") or "")
    if not re.match(r"^[a-z0-9]{4,12}$", qid):
        qid = "q" + secrets.token_hex(3)
    out = {"id": qid, "type": q["type"], "label": _lang_text(q.get("label"), langs), "desc": _lang_text(q.get("desc"), langs),
           "required": bool(q.get("required")) and q["type"] != "section", "pii": bool(q.get("pii")) and q["type"] != "section"}
    if q["type"] in ("choice", "multi", "dropdown"):
        out["options"] = _lang_list(q.get("options"), langs)
    if q["type"] == "scale":
        lo = max(0, min(10, _int(q.get("min"), 1)))
        out["min"], out["max"] = lo, max(lo + 1, min(10, _int(q.get("max"), 5)))
        out["minLabel"], out["maxLabel"] = _lang_text(q.get("minLabel"), langs), _lang_text(q.get("maxLabel"), langs)
    if q["type"] in ("email", "phone"):
        out["pii"] = True  # an address or a number always names a person
    return out


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _ms(v):
    """A timestamp from the page: milliseconds, or an ISO string from a datetime-local input (the Mac's time zone)."""
    if v in (None, "", 0):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    try:
        return int(datetime.fromisoformat(str(v)).astimezone().timestamp() * 1000)
    except ValueError:
        return None


def summary(d):
    rows = _rows(d["id"])
    return {k: d.get(k) for k in ("id", "slug", "title", "status", "access", "langs", "notify", "updatedAt", "createdAt", "publishedAt", "pushError")} | {
        "count": len(rows), "lastAt": rows[-1]["at"] if rows else None, "url": url_of(d)}


def url_of(d):
    u = f"{BASE}/f/{d['slug']}"
    if d.get("access") == "link" and d.get("key"):
        u += "?k=" + d["key"]
    return u


def view():
    items = sorted(_all(), key=lambda d: -(d.get("updatedAt") or 0))
    return {"forms": [summary(d) for d in items], "connected": bool(key()), "claude": bool(lifelog.api_key()),
            "state": _read(STATE, {}), "base": BASE}


def new(rec=None):
    rec = rec or {}
    fid = secrets.token_hex(6)
    now = int(time.time() * 1000)
    d = {"id": fid, "slug": "f-" + fid[:6], "title": {"ja": "無題のフォーム"}, "desc": {}, "langs": ["ja"], "access": "public", "key": "",
         "questions": [], "opensAt": None, "closesAt": None, "limit": 0, "once": False, "thanks": {}, "closedText": {},
         "confirmMail": False, "notify": True, "dest": "keep", "purpose": {}, "status": "draft", "createdAt": now, "updatedAt": now}
    with _lock:
        _write(_path(fid), d)
    if rec:
        code, body = save(fid, rec)
        if code == 200:
            return 200, body
    return 200, get(fid)


FIELDS = ("title", "desc", "slug", "langs", "access", "key", "questions", "opensAt", "closesAt", "limit", "once", "thanks", "closedText",
          "confirmMail", "notify", "dest", "purpose")


def save(fid, rec):
    with _lock:
        d = get(fid)
        if not d:
            return 404, {"error": "not found"}
        langs = [l for l in (rec.get("langs") if isinstance(rec.get("langs"), list) else d["langs"]) if l in LANGS] or ["ja"]
        d["langs"] = langs
        for k in ("title", "desc", "thanks", "closedText", "purpose"):
            if k in rec:
                d[k] = _lang_text(rec[k], langs)
        if "slug" in rec:
            slug = re.sub(r"[^a-z0-9-]", "-", str(rec["slug"] or "").strip().lower()).strip("-")[:60]
            if not SLUG.match(slug):
                return 400, {"error": "The address needs 2 to 60 letters, numbers or hyphens"}
            if any(x["slug"] == slug and x["id"] != fid for x in _all()):
                return 400, {"error": "Another form already uses that address"}
            d["slug"] = slug
        if "access" in rec:
            d["access"] = rec["access"] if rec["access"] in ACCESS else "public"
        if "key" in rec and d["access"] == "me":
            d["key"] = re.sub(r"\s+", " ", str(rec["key"] or "")).strip()[:80]
        if d["access"] == "link" and (rec.get("newKey") or not re.match(r"^[0-9a-f]{24,}$", d.get("key") or "")):
            d["key"] = secrets.token_hex(16)
        if d["access"] == "public":
            d["key"] = ""
        if "questions" in rec and isinstance(rec["questions"], list):
            qs = [q for q in (_question(x, langs) for x in rec["questions"][:100]) if q]
            seen = set()
            for q in qs:  # ids must be unique: the answers are keyed by them
                while q["id"] in seen:
                    q["id"] = "q" + secrets.token_hex(3)
                seen.add(q["id"])
            d["questions"] = qs
        for k in ("opensAt", "closesAt"):
            if k in rec:
                d[k] = _ms(rec[k])
        if "limit" in rec:
            d["limit"] = max(0, min(100000, _int(rec["limit"], 0)))
        for k in ("once", "confirmMail", "notify"):
            if k in rec:
                d[k] = bool(rec[k])
        if "dest" in rec:
            d["dest"] = rec["dest"] if rec["dest"] in DESTS else "keep"
        d["updatedAt"] = int(time.time() * 1000)
        _write(_path(fid), d)
    if d["status"] in ("open", "closed"):
        _push(d)  # a published form follows its edits; a failure shows on the screen and the next save tries again
    return 200, get(fid)


def _check(d):
    """What stops a publish. Empty = fine."""
    qs = [q for q in d["questions"] if q["type"] != "section"]
    if not qs:
        return "Add at least one question first"
    for q in qs:
        if not q["label"].get(d["langs"][0]):
            return "Every question needs a label in the form's first language"
        if q["type"] in ("choice", "multi", "dropdown") and not q.get("options", {}).get(d["langs"][0]):
            return "A choice question needs its options, one per line"
    if any(q.get("pii") for q in qs) and not d["purpose"].get(d["langs"][0]):
        return "This form asks for personal details, so Settings needs one line on what they are used for"
    if d["access"] == "me" and not d.get("key"):
        return "A form for yourself needs a passphrase in Settings"
    return ""


def publish(fid):
    d = get(fid)
    if not d:
        return 404, {"error": "not found"}
    why = _check(d)
    if why:
        return 400, {"error": why}
    if not key():
        return 400, {"error": "Forms aren't connected yet (keychain tatsuki-desk-forms and the Worker's FORMS_KEY)"}
    with _lock:
        d = get(fid)
        d["status"] = "open"
        d["publishedAt"] = d.get("publishedAt") or int(time.time() * 1000)
        d["updatedAt"] = int(time.time() * 1000)
        _write(_path(fid), d)
    err = _push(d)
    if err:
        with _lock:
            d = get(fid)
            d["status"] = "draft"
            _write(_path(fid), d)
        return 502, {"error": err}
    return 200, get(fid)


def close(fid):
    d = get(fid)
    if not d:
        return 404, {"error": "not found"}
    with _lock:
        d["status"] = "closed"
        d["updatedAt"] = int(time.time() * 1000)
        _write(_path(fid), d)
    err = _push(d)
    return (502, {"error": err}) if err else (200, get(fid))


def remove(fid):
    d = get(fid)
    if not d:
        return 404, {"error": "not found"}
    with _lock:
        d["deleted"] = True
        d["status"] = "draft"  # the Worker shows a draft as not found
        d["updatedAt"] = int(time.time() * 1000)
        _write(_path(fid), d)
    if d.get("publishedAt") and key():
        _push(d)
    return 200, {"ok": True}


def _push(d):
    """The form as the Worker needs it. Returns '' or the reason it failed (kept on the form as pushError)."""
    body = {k: v for k, v in d.items() if k not in ("deleted", "pushError")}
    try:
        worker("PUT", f"/_forms/{d['id']}", body)
        err = ""
    except RuntimeError as e:
        err = str(e)
    with _lock:
        cur = _read(_path(d["id"]), None)
        if cur is not None and (cur.get("pushError") or "") != err:
            cur["pushError"] = err
            _write(_path(d["id"]), cur)
    return err


# ---------- the Worker ----------

def key():
    v = os.environ.get("DESK_FORMS_KEY")
    if v is not None:
        return v
    try:
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN, "-w"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def worker(method, path, body=None, timeout=60):
    k = key()
    if not k:
        raise RuntimeError("Forms aren't connected yet (keychain: tatsuki-desk-forms)")
    req = urllib.request.Request(WORKER + path, method=method, data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + k, "Content-Type": "application/json", "User-Agent": "TatsukiDesk"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error")
        except (ValueError, AttributeError):
            msg = ""
        raise RuntimeError(msg or f"tany.im answered {e.code}") from None
    except (OSError, ValueError):
        raise RuntimeError("Could not reach tany.im. Check the connection and try again") from None


def pull():
    """New answers from the Worker, appended to this Mac's files. Returns what came in, by form."""
    if not key():
        return {"skipped": "not connected"}
    st = _read(STATE, {})
    last = int(st.get("last") or 0)
    got = {}
    forms = {d["id"]: d for d in _all()}
    while True:
        v = worker("GET", f"/_forms/export?since={last}&limit=500", timeout=60)
        by = {}
        for r in v.get("rows") or []:
            if not isinstance(r, dict) or not ID.match(str(r.get("form") or "")) or r["form"] not in forms:
                continue
            by.setdefault(r["form"], []).append({"id": r.get("id"), "at": r.get("at"), "lang": r.get("lang"), "answers": r.get("answers") or {}, "country": r.get("country") or ""})
        for fid, rows in by.items():
            _append(fid, rows)
            got[fid] = got.get(fid, 0) + len(rows)
        last = int(v.get("last") or last)
        if not v.get("more"):
            break
    st.update(last=last, pulledAt=int(time.time() * 1000), counts=v.get("counts") or {})
    _write(STATE, st)
    for fid, n in got.items():
        d = forms[fid]
        rows = _rows(fid)[-n:]
        if d.get("access") == "me" and d.get("dest") in ("task", "note"):
            for r in rows:
                try:
                    _deliver(d, r)
                except Exception as e:  # one answer must not stop the rest
                    print("forms deliver:", type(e).__name__, e, file=sys.stderr, flush=True)
        if d.get("notify", True):
            title = d["title"].get(d["langs"][0]) or next(iter(d["title"].values()), "Form")
            notify.show_mac({"title": title, "body": f"{n} new answer{'s' if n != 1 else ''}", "tier": ""}, sound=False)
    return {"new": got, "last": last}


def _lines(d, r):
    """label: value, one per answered question, in the form's first language."""
    l0 = d["langs"][0]
    out = []
    for q in d["questions"]:
        v = r["answers"].get(q["id"])
        if v in (None, "", []):
            continue
        out.append(f"{q['label'].get(l0) or q['id']}: {', '.join(v) if isinstance(v, list) else v}")
    return out


def _deliver(d, r):
    """A "me" form's answer goes where the form says: a Desk task, or a line in the day's note."""
    lines = _lines(d, r)
    l0 = d["langs"][0]
    title = d["title"].get(l0) or "Form"
    if d["dest"] == "task":
        first = next((q for q in d["questions"] if q["type"] in ("short", "long") and r["answers"].get(q["id"])), None)
        head = (r["answers"][first["id"]].split("\n")[0][:120] if first else title)
        tasks.add(head, note="\n".join([title] + lines), source="form", source_ref=f"{d['id']}:{r.get('id')}")
    elif d["dest"] == "note" and STORE.get("load") and STORE.get("upsert"):
        ms = int(r.get("at") or time.time() * 1000)
        when = datetime.fromtimestamp(ms / 1000, HCM)
        day = when.strftime("%Y-%m-%d")
        notes = STORE["load"]("notes")
        n = next((x for x in notes if not x.get("deleted") and (x.get("id") == "day-" + day or (x.get("noAuto") and x.get("title") == day))), None)
        body = (n or {}).get("body") or ""
        body = body.rstrip() + ("\n" if body.strip() else "") + when.strftime("%H:%M") + " " + title + " / " + " / ".join(lines) + "\n"
        rec = {"title": day, "body": body, "noAuto": True, "createdAt": (n or {}).get("createdAt") or ms, "updatedAt": int(time.time() * 1000), "deleted": False}
        STORE["upsert"]("notes", (n or {}).get("id") or "day-" + day, rec, False)


def watch():
    while True:
        try:
            pull()
        except Exception as e:  # keep the loop alive; the next round tries again
            print("forms:", type(e).__name__, e, file=sys.stderr, flush=True)
        time.sleep(EVERY)


# ---------- answers on the screen ----------

def responses(fid):
    d = get(fid)
    if not d:
        return 404, {"error": "not found"}
    rows = _rows(fid)
    rows.reverse()
    return 200, {"rows": rows[:2000], "count": len(rows), "summary": _summary(d, rows), "claude": _read(os.path.join(SUMMARIES, fid + ".json"), None)}


def _summary(d, rows):
    out = []
    l0 = d["langs"][0]
    for q in d["questions"]:
        if q["type"] == "section":
            continue
        vals = [r["answers"].get(q["id"]) for r in rows]
        vals = [v for v in vals if v not in (None, "", [])]
        item = {"id": q["id"], "type": q["type"], "label": q["label"].get(l0) or q["id"], "n": len(vals), "pii": q.get("pii", False)}
        if q["type"] in ("choice", "dropdown", "multi", "scale"):
            if q["type"] == "scale":
                opts = [str(i) for i in range(q.get("min", 1), q.get("max", 5) + 1)]
            else:
                opts = []
                for l in d["langs"]:
                    for o in q.get("options", {}).get(l, []):
                        if o not in opts:
                            opts.append(o)
            counts = {o: 0 for o in opts}
            for v in vals:
                for x in (v if isinstance(v, list) else [v]):
                    counts[x] = counts.get(x, 0) + 1
            item["counts"] = [{"option": o, "n": n} for o, n in counts.items()]
            if q["type"] == "scale" and vals:
                nums = [float(v) for v in vals if re.match(r"^\d+(\.\d+)?$", str(v))]
                if nums:
                    item["avg"] = round(sum(nums) / len(nums), 2)
        elif q["type"] == "number":
            nums = []
            for v in vals:
                try:
                    nums.append(float(v))
                except ValueError:
                    pass
            if nums:
                item.update(avg=round(sum(nums) / len(nums), 2), min=min(nums), max=max(nums))
        else:
            item["values"] = [str(v) for v in vals[:200]]
        out.append(item)
    return out


def to_csv(fid):
    d = get(fid)
    if not d:
        return 404, {"error": "not found"}
    l0 = d["langs"][0]
    qs = [q for q in d["questions"] if q["type"] != "section"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["At", "Language", "Country"] + [q["label"].get(l0) or q["id"] for q in qs])
    for r in _rows(fid):
        when = datetime.fromtimestamp(int(r.get("at") or 0) / 1000, HCM).strftime("%Y-%m-%d %H:%M")
        w.writerow([when, r.get("lang") or "", r.get("country") or ""] + [", ".join(v) if isinstance(v, list) else str(v) for v in (r["answers"].get(q["id"], "") for q in qs)])
    name = re.sub(r"[\\/:*?\"<>|]+", " ", d["title"].get(l0) or "form").strip() or "form"
    return 200, {"name": f"{name} {datetime.now(HCM).strftime('%Y-%m-%d')}", "text": buf.getvalue()}


# ---------- Claude ----------

SUMMARY_SCHEMA = {"type": "object", "properties": {"points": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5}}, "required": ["points"], "additionalProperties": False}
SUMMARY_SYSTEM = ("You read the free-text answers to a form and give the person who made the form 3 to 5 short points on what people said: "
                  "the common themes, what stood out, and anything several people asked for. Plain words, each point one sentence, in the language most of the answers use. "
                  "Never name or describe an individual respondent; speak about the group. Do not add advice or praise.")
TRANSLATE_SYSTEM = ("You translate the words of a web form (title, description, question labels, options, thank-you text, the line on how personal details are used) "
                    "into the requested languages. Keep the meaning and the register, keep options short, and return every requested key. Return JSON only.")


def _claude(system, user, schema, max_tokens=4000):
    k = lifelog.api_key()
    if not k:
        return None, "No Anthropic API key is registered on this Mac"
    body = {"model": MODEL, "max_tokens": max_tokens, "system": system,
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
            "messages": [{"role": "user", "content": user}]}
    req = urllib.request.Request(lifelog.API, data=json.dumps(body, ensure_ascii=False).encode(),
                                 headers={"x-api-key": k, "anthropic-version": lifelog.API_VERSION, "content-type": "application/json", "user-agent": "tatsuki-desk"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        return None, f"Anthropic returned an error ({e.code}). Try again in a moment"
    except (OSError, ValueError):
        return None, "Could not reach Anthropic. Check the connection and try again"
    text = next((b.get("text") for b in res.get("content", []) if b.get("type") == "text"), None)
    try:
        return json.loads(text), ""
    except (TypeError, ValueError):
        return None, "The answer came back unreadable. Try again"


def summarize(fid):
    d = get(fid)
    if not d:
        return 404, {"error": "not found"}
    rows = _rows(fid)
    l0 = d["langs"][0]
    parts, n = [], 0
    for q in d["questions"]:
        if q["type"] not in ("short", "long") or q.get("pii"):
            continue
        vals = [str(r["answers"].get(q["id"]) or "").strip() for r in rows]
        vals = [v for v in vals if len(v) > 1]
        if not vals:
            continue
        n += len(vals)
        parts.append(f"## {q['label'].get(l0) or q['id']}\n" + "\n".join("- " + v.replace("\n", " ")[:600] for v in vals[-300:]))
    if not parts:
        return 400, {"error": "No free-text answers to summarize yet (questions marked as personal details are left out)"}
    text = "\n\n".join(parts)[:80000]
    out, err = _claude(SUMMARY_SYSTEM, f"Form: {d['title'].get(l0) or ''}\nAnswers: {n}\n\n{text}", SUMMARY_SCHEMA)
    if err:
        return (400 if err.startswith("No Anthropic") else 502), {"error": err}
    rec = {"at": int(time.time() * 1000), "n": n, "points": [str(p)[:500] for p in out.get("points", [])][:5]}
    _write(os.path.join(SUMMARIES, fid + ".json"), rec)
    return 200, rec


def translate(fid):
    """Fills the words missing in the form's other languages. A suggestion saved into the draft; the person edits before publishing."""
    d = get(fid)
    if not d:
        return 404, {"error": "not found"}
    langs = d["langs"]
    if len(langs) < 2:
        return 400, {"error": "Add a second language in Settings first"}
    src = langs[0]
    todo = {}

    def need(path, v):
        if isinstance(v, dict) and v.get(src):
            missing = [l for l in langs[1:] if not v.get(l)]
            if missing:
                todo[path] = {"text": v[src], "to": missing}
    need("title", d["title"])
    need("desc", d["desc"])
    need("thanks", d["thanks"])
    need("purpose", d["purpose"])
    need("closedText", d["closedText"])
    for q in d["questions"]:
        need(f"q.{q['id']}.label", q["label"])
        need(f"q.{q['id']}.desc", q.get("desc") or {})
        if q["type"] == "scale":
            need(f"q.{q['id']}.minLabel", q.get("minLabel") or {})
            need(f"q.{q['id']}.maxLabel", q.get("maxLabel") or {})
        opts = q.get("options") or {}
        if opts.get(src):
            missing = [l for l in langs[1:] if not opts.get(l)]
            if missing:
                todo[f"q.{q['id']}.options"] = {"text": opts[src], "to": missing}
    if not todo:
        return 200, get(fid)
    schema = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
        "key": {"type": "string"}, "lang": {"type": "string"}, "text": {"type": "string"}, "list": {"type": "array", "items": {"type": "string"}}},
        "required": ["key", "lang"], "additionalProperties": False}}}, "required": ["items"], "additionalProperties": False}
    user = ("For each key, translate `text` (or every entry of `list`, keeping the order and count) into each language in `to`. "
            "Answer with items {key, lang, text} or {key, lang, list}.\n\n" + json.dumps(todo, ensure_ascii=False))
    out, err = _claude(TRANSLATE_SYSTEM, user, schema, max_tokens=16000)
    if err:
        return (400 if err.startswith("No Anthropic") else 502), {"error": err}
    with _lock:
        d = get(fid)
        byq = {q["id"]: q for q in d["questions"]}
        for it in out.get("items", []):
            k, l = str(it.get("key") or ""), str(it.get("lang") or "")
            if l not in langs[1:] or k not in todo:
                continue
            parts = k.split(".")
            if k.endswith(".options"):
                xs = [str(x).strip()[:200] for x in (it.get("list") or []) if str(x).strip()]
                if len(xs) == len(todo[k]["text"]):
                    byq[parts[1]]["options"][l] = xs
                continue
            text = re.sub(r"\r", "", str(it.get("text") or ""))[:2000]
            if not text:
                continue
            if parts[0] == "q" and parts[1] in byq:
                byq[parts[1]].setdefault(parts[2], {})[l] = text
            elif parts[0] in ("title", "desc", "thanks", "purpose", "closedText"):
                d[parts[0]][l] = text
        d["updatedAt"] = int(time.time() * 1000)
        _write(_path(fid), d)
    if d["status"] in ("open", "closed"):
        _push(d)
    return 200, get(fid)


# ---------- Google Forms ----------

GTYPES = {0: "short", 1: "long", 2: "choice", 3: "dropdown", 4: "multi", 5: "scale", 6: "section", 8: "section", 9: "date", 18: "scale"}
GSKIP = {7: "a grid", 10: "a time", 11: "an image", 12: "a video", 13: "a file upload"}


def import_google(url):
    """Reads a public Google Form's page once (no sign-in) and returns (status, draft form). Answers are never read."""
    url = str(url or "").strip()
    host = re.match(r"^https://(docs\.google\.com|forms\.gle|forms\.google\.com)/", url)
    if not host:
        return 400, {"error": "Paste the link of a Google Form (docs.google.com/forms/… or forms.gle/…)"}
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) TatsukiDesk", "Accept-Language": "ja,en"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            if not r.geturl().startswith("https://docs.google.com/forms/"):
                return 400, {"error": "That link did not lead to a Google Form"}
            page = r.read(3_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return 502, {"error": f"Google answered {e.code}. Is the form public?"}
    except (OSError, ValueError):
        return 502, {"error": "Could not reach Google. Check the connection and try again"}
    m = re.search(r"FB_PUBLIC_LOAD_DATA_\s*=\s*(\[.*?\]);\s*</script>", page, re.S)
    if not m:
        return 400, {"error": "Could not read the questions on that page. Google may have changed the page, or the form needs a sign-in"}
    try:
        data = json.loads(m.group(1))
        meta = data[1]
        items = meta[1] or []
        title = (meta[8] if len(meta) > 8 and isinstance(meta[8], str) else None) or (data[3] if len(data) > 3 and isinstance(data[3], str) else "") or "Imported form"
        desc = meta[0] if isinstance(meta[0], str) else ""
    except (IndexError, TypeError, ValueError):
        return 400, {"error": "The page's data was not in the shape expected. Google may have changed it"}
    lang = "ja" if re.search(r"[぀-ヿ一-鿿]", title + desc + " ".join(str(i[1] or "") for i in items if isinstance(i, list) and len(i) > 1)) else "en"
    qs, skipped = [], []
    for it in items:
        if not isinstance(it, list) or len(it) < 4:
            continue
        label = str(it[1] or "").strip()
        qdesc = str(it[2] or "").strip() if len(it) > 2 and isinstance(it[2], str) else ""
        gtype = it[3]
        if gtype in GSKIP:
            skipped.append(f"{label or 'Untitled'} ({GSKIP[gtype]})")
            continue
        t = GTYPES.get(gtype)
        if t is None:
            skipped.append(f"{label or 'Untitled'} (type {gtype})")
            continue
        q = {"id": "q" + secrets.token_hex(3), "type": t, "label": {lang: label or ("Section" if t == "section" else "Question")}, "desc": {lang: qdesc} if qdesc else {},
             "required": False, "pii": False}
        entry = it[4][0] if len(it) > 4 and isinstance(it[4], list) and it[4] and isinstance(it[4][0], list) else None
        if entry:
            q["required"] = bool(entry[2]) if len(entry) > 2 else False
            opts = [str(o[0]) for o in (entry[1] or []) if isinstance(o, list) and o and o[0] not in (None, "")] if len(entry) > 1 else []
            if t in ("choice", "multi", "dropdown"):
                q["options"] = {lang: opts or ["Option 1"]}
                if any(isinstance(o, list) and len(o) > 4 and o[4] for o in (entry[1] or [])):
                    skipped.append(f"{label}: the “Other” option (type it as an option if you need it)")
            if t == "scale":
                nums = [int(o) for o in opts if re.match(r"^\d+$", o)]
                q["min"], q["max"] = (min(nums), max(nums)) if nums else (1, 5)
                labels = entry[3] if len(entry) > 3 and isinstance(entry[3], list) else []
                q["minLabel"] = {lang: str(labels[0])} if labels and labels[0] else {}
                q["maxLabel"] = {lang: str(labels[1])} if len(labels) > 1 and labels[1] else {}
        if t in ("short",) and re.search(r"mail|メール", label, re.I):
            q["type"], q["pii"] = "email", True
        elif t == "short" and re.search(r"phone|tel|電話", label, re.I):
            q["type"], q["pii"] = "phone", True
        elif t == "short" and re.search(r"\bname\b|名前|氏名|お名前", label, re.I):
            q["pii"] = True
        qs.append(q)
    code, d = new({"title": {lang: title[:200]}, "desc": {lang: desc[:2000]}, "langs": [lang], "questions": qs})
    if code != 200:
        return code, d
    return 200, {"form": d, "skipped": skipped, "source": url}


# ---------- requests from server.py ----------

def handle(method, parts, rec):
    """parts: the path after /api/forms/. Returns (status, body)."""
    if method == "GET" and not parts:
        return 200, view()
    if method == "POST" and not parts:
        return new(rec)
    if method == "POST" and parts == ["import"]:
        return import_google(rec.get("url"))
    if method == "POST" and parts == ["pull"]:
        try:
            return 200, pull()
        except RuntimeError as e:
            return 502, {"error": str(e)}
    if parts:
        fid = parts[0]
        if method == "GET" and len(parts) == 1:
            d = get(fid)
            return (200, d) if d else (404, {"error": "not found"})
        if method == "PUT" and len(parts) == 1:
            return save(fid, rec)
        if method == "GET" and parts[1:] == ["responses"]:
            return responses(fid)
        if method == "GET" and parts[1:] == ["csv"]:
            return to_csv(fid)
        if method == "POST" and parts[1:] == ["publish"]:
            return publish(fid)
        if method == "POST" and parts[1:] == ["close"]:
            return close(fid)
        if method == "POST" and parts[1:] == ["remove"]:
            return remove(fid)
        if method == "POST" and parts[1:] == ["summarize"]:
            return summarize(fid)
        if method == "POST" and parts[1:] == ["translate"]:
            return translate(fid)
    return 404, {"error": "not found"}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "pull":
        print(json.dumps(pull(), ensure_ascii=False, indent=1))
    elif len(sys.argv) > 2 and sys.argv[1] == "import":
        print(json.dumps(import_google(sys.argv[2])[1], ensure_ascii=False, indent=1))
    else:
        print(json.dumps(view(), ensure_ascii=False, indent=1))
