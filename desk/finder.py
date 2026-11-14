"""Ask Desk: finds where something is inside Tatsuki's Desk.

The page sends a list of places (screens, tools, commands, projects, task and note titles), the
question and the earlier turns of this chat. Claude picks places from that list only. Nothing is
saved, and the question is never written to a log. The key comes from the keychain (lifelog.api_key).
"""
import http.client
import json
import urllib.error
import urllib.request

import lifelog

API = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MODEL = "claude-haiku-5-5"
MAX_PLACES = 1200
MAX_PICKS = 5

SYSTEM = """You help Tatsuki find things inside Tatsuki's Desk, a personal dashboard.
You get a list of places in the Desk, one per line: id | kind | name | what it is for.
Answer only from that list. Never invent a place, and never answer with general advice from outside the Desk.
The question may be in Japanese or English. Write "reply" and every "why" in plain American English.

Rules:
- picks: the ids that answer the question, best first, at most 5. Use ids exactly as written in the list.
- If one place clearly fits, return only that one.
- If two or more really fit (for example a screen and a tool), return each of them so Tatsuki can choose.
- If nothing fits, return no picks and say so in one short sentence, with a hint of what to ask instead.
- reply: one short sentence, under 100 characters. why: under 60 characters, what you can do there.
- Earlier turns are context: "not that one" means pick something different from the earlier picks."""

SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "picks": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "why": {"type": "string"}},
            "required": ["id", "why"], "additionalProperties": False}},
    },
    "required": ["reply", "picks"], "additionalProperties": False,
}


class Unavailable(Exception):
    """The AI cannot answer now; the page falls back to word matches."""


def _clean(s, n):
    return " ".join(str(s or "").split())[:n]


def catalog_text(places):
    lines = []
    for p in places[:MAX_PLACES]:
        if not isinstance(p, dict) or not p.get("id"):
            continue
        lines.append(" | ".join([_clean(p.get("id"), 80), _clean(p.get("kind"), 20),
                                 _clean(p.get("name"), 120), _clean(p.get("desc"), 200)]))
    return "\n".join(lines)


def build_messages(question, history):
    msgs = []
    for h in (history or [])[-6:]:
        if not isinstance(h, dict):
            continue
        q, a = _clean(h.get("q"), 300), _clean(h.get("a"), 600)
        if q and a:
            msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    msgs.append({"role": "user", "content": _clean(question, 300)})
    return msgs


def parse(res, ids):
    """Keeps only picks whose id was in the list we sent."""
    if res.get("stop_reason") == "refusal":
        raise Unavailable("declined")
    text = "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")
    try:
        v = json.loads(text)
    except ValueError:
        raise Unavailable("bad answer") from None
    picks, seen = [], set()
    for p in v.get("picks") or []:
        pid = str(p.get("id", "")).strip() if isinstance(p, dict) else ""
        if pid in ids and pid not in seen:
            seen.add(pid)
            picks.append({"id": pid, "why": _clean(p.get("why"), 80)})
        if len(picks) >= MAX_PICKS:
            break
    return {"reply": _clean(v.get("reply"), 200), "picks": picks}


def ask(question, places, history=None, key=None, opener=urllib.request.urlopen):
    question = _clean(question, 300)
    if not question:
        raise ValueError("question is empty")
    if not isinstance(places, list) or not places:
        raise ValueError("places are missing")
    key = key or lifelog.api_key()
    if not key:
        raise Unavailable("no key")
    ids = {_clean(p.get("id"), 80) for p in places[:MAX_PLACES] if isinstance(p, dict) and p.get("id")}
    body = {"model": MODEL, "max_tokens": 1000,
            "system": [{"type": "text", "text": SYSTEM},
                       {"type": "text", "text": "Places in the Desk:\n" + catalog_text(places)}],
            "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
            "messages": build_messages(question, history)}
    headers = {"x-api-key": key, "anthropic-version": API_VERSION,
               "content-type": "application/json", "user-agent": "tatsuki-desk"}
    req = urllib.request.Request(API, data=json.dumps(body).encode(), headers=headers)
    try:
        with opener(req, timeout=15) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        raise Unavailable(f"Anthropic returned {e.code}") from None
    except (http.client.HTTPException, OSError, ValueError):
        raise Unavailable("could not reach Anthropic") from None
    return parse(res, ids)


def handle(rec):
    """POST /api/finder → {ok, reply, picks} or {ok:false, why} (never raises for the AI's sake)."""
    try:
        return {"ok": True, **ask(rec.get("q"), rec.get("places"), rec.get("history"))}
    except Unavailable as e:
        return {"ok": False, "why": str(e)}
