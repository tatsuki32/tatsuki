"""Customer memos: one record per person or company, in <data>/tools/13-brain/contacts.json.

Automatic updates only touch history and last_contact. note and next_action are the owner's
words and are never overwritten. Only short summaries are kept: a message body is dropped.
Text from outside is data, and it is stored as single-line, shortened text.
Addresses nobody registered do not become customers: they wait in unmatched.json.
"""
import hashlib
import os
import re
import unicodedata
from datetime import datetime, timezone

from lifeos.lib import paths
from lifeos.lib.tools import load_tool

store = load_tool("13-brain", "store")

FILE = "contacts.json"
UNMATCHED = "unmatched.json"
CHANNELS = ("gmail", "whatsapp", "other")
HISTORY_MAX = 500
SUBJECT_MAX = 120
SUMMARY_MAX = 300
_COMPANY = re.compile(r"株式会社|有限会社|合同会社|\(株\)|\(有\)|\(同\)|^カ\)|\(カ\)?$|\(カ\)|co\.?,?ltd\.?|inc\.?$|llc\.?$|corp\.?$")


def name_key(name):
    """A name for comparing: width, case, spaces and company markers do not count."""
    s = unicodedata.normalize("NFKC", str(name or "")).casefold()
    s = re.sub(r"\s+", "", s)
    return _COMPANY.sub("", s)


def address_key(addr):
    """Lower-case mail address, @domain, or the digits of a phone number."""
    s = unicodedata.normalize("NFKC", str(addr or "")).strip().lower()
    m = re.search(r"<([^>]+)>", s)
    if m:
        s = m.group(1).strip()
    if "@" in s:
        return s
    return re.sub(r"\D", "", s)


def _short(text, limit):
    return " ".join(str(text or "").split())[:limit]


def _time(value):
    """ISO time in Ho Chi Minh time. A time without a zone is taken as local. None if unreadable."""
    if not value:
        return paths.now().isoformat(timespec="seconds")
    try:
        t = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=paths.HCMC)
    return t.astimezone(paths.HCMC).isoformat(timespec="seconds")


def _load():
    v = store.read_json(FILE, [])
    if not isinstance(v, list):
        raise store.StoreError(FILE + " の形が違います")
    return v


def all_contacts():
    return [c for c in _load() if not c.get("deleted")]


def get(contact_id):
    return next((c for c in all_contacts() if c["id"] == contact_id), None)


def add(name, addresses=(), aliases=(), org="", note="", next_action=""):
    name = _short(name, SUBJECT_MAX)
    if not name:
        raise ValueError("名前が空です")
    keys = [k for k in (address_key(a) for a in addresses) if k]
    ms = paths.now_ms()
    rec = {"id": "c" + format(ms, "x") + os.urandom(2).hex(), "name": name, "aliases": [_short(a, SUBJECT_MAX) for a in aliases if a],
           "addresses": keys, "org": _short(org, SUBJECT_MAX), "note": note, "next_action": next_action,
           "last_contact": None, "history": [], "createdAt": ms, "updatedAt": ms, "deleted": False}
    with store.locked("contacts"):
        items = _load()
        used = {a: c["name"] for c in items if not c.get("deleted") for a in c.get("addresses", [])}
        for k in keys:
            if k in used:
                raise ValueError("この宛先は登録済みです: " + used[k])
        items.append(rec)
        store.write_json(FILE, items)
    return rec


def update(contact_id, **fields):
    """Changes the owner's own fields."""
    allowed = ("name", "aliases", "addresses", "org", "note", "next_action", "deleted")
    bad = [k for k in fields if k not in allowed]
    if bad:
        raise ValueError("変えられない項目です: " + ", ".join(bad))
    if "addresses" in fields:
        fields["addresses"] = [k for k in (address_key(a) for a in fields["addresses"]) if k]
    with store.locked("contacts"):
        items = _load()
        for c in items:
            if c["id"] == contact_id:
                c.update(fields, updatedAt=paths.now_ms())
                store.write_json(FILE, items)
                return c
    raise KeyError(contact_id)


def _find(items, key):
    """Exact address first, then a registered @domain."""
    live = [c for c in items if not c.get("deleted")]
    for c in live:
        if key in c.get("addresses", []):
            return c
    if "@" in key:
        domain = "@" + key.split("@", 1)[1]
        for c in live:
            if domain in c.get("addresses", []):
                return c
    return None


def _entry(item, when):
    channel = item.get("channel") if item.get("channel") in CHANNELS else "other"
    direction = "out" if item.get("direction") == "out" else "in"
    subject = _short(item.get("subject"), SUBJECT_MAX)
    summary = _short(item.get("summary"), SUMMARY_MAX)
    ref = _short(item.get("ref"), SUBJECT_MAX)
    if not ref:
        seed = "|".join((channel, when, address_key(item.get("address")), subject))
        ref = "h" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return {"at": when, "channel": channel, "direction": direction, "subject": subject, "summary": summary, "ref": ref}


def ingest(items):
    """Adds interactions to the matching memos and returns counts.

    An interaction is {channel, direction, address, at, subject, summary, ref}. Other keys,
    such as a message body, are ignored. Running it twice on the same items changes nothing.
    """
    store.require_running()
    counts = {"added": 0, "duplicate": 0, "unmatched": 0, "out_of_scope": 0, "bad": 0}
    with store.locked("contacts"):
        contacts = _load()
        unmatched = store.read_json(UNMATCHED, {})
        changed = False
        for item in items:
            key = address_key(item.get("address")) if isinstance(item, dict) else ""
            when = _time(item.get("at")) if key else None
            if not key or not when:
                counts["bad"] += 1
                continue
            c = _find(contacts, key)
            if not c:
                u = unmatched.setdefault(key, {"count": 0, "last": None})
                u["count"] += 1
                u["last"] = max(u["last"] or when, when)
                counts["unmatched"] += 1
                continue
            if not store.in_scope(c.get("org")):
                counts["out_of_scope"] += 1
                continue
            entry = _entry(item, when)
            if any(h["ref"] == entry["ref"] and h["channel"] == entry["channel"] for h in c["history"]):
                counts["duplicate"] += 1
                continue
            c["history"] = sorted(c["history"] + [entry], key=_stamp)[-HISTORY_MAX:]
            c["last_contact"] = c["history"][-1]["at"]
            c["updatedAt"] = paths.now_ms()
            counts["added"] += 1
            changed = True
        if changed:
            store.write_json(FILE, contacts)
        if counts["unmatched"]:
            store.write_json(UNMATCHED, unmatched)
    return counts


def _stamp(entry):
    return datetime.fromisoformat(entry["at"]).astimezone(timezone.utc)


def unmatched():
    """Addresses that are not registered, the most frequent first."""
    v = store.read_json(UNMATCHED, {})
    return sorted(({"address": k, **u} for k, u in v.items()), key=lambda x: -x["count"])


def same_party():
    """A function saying whether two names are the same customer, using the memos' other names."""
    groups = {}
    for c in all_contacts():
        names = {name_key(n) for n in [c["name"], *c.get("aliases", [])]} - {""}
        for n in names:
            groups.setdefault(n, set()).update(names)

    def same(a, b):
        ka, kb = name_key(a), name_key(b)
        return bool(ka) and bool(kb) and (ka == kb or kb in groups.get(ka, ()))
    return same


def render(c, recent=10):
    """The memo as Markdown."""
    lines = ["# " + c["name"], ""]
    if c.get("org"):
        lines.append("- 取引元：" + c["org"])
    if c.get("aliases"):
        lines.append("- 別名：" + "、".join(c["aliases"]))
    if c.get("addresses"):
        lines.append("- 宛先：" + "、".join(c["addresses"]))
    lines.append("- 最後のやりとり：" + (c.get("last_contact") or "まだありません"))
    lines += ["", "## メモ", c.get("note") or "（まだありません）", "", "## 次にすること", c.get("next_action") or "（まだありません）", "", "## やりとり"]
    arrow = {"in": "受信", "out": "送信"}
    for h in reversed(c.get("history", [])[-recent:]):
        text = "：".join(x for x in (h["subject"], h["summary"]) if x)
        lines.append("- {} {} {} {}".format(h["at"][:16].replace("T", " "), h["channel"], arrow[h["direction"]], text).rstrip())
    if not c.get("history"):
        lines.append("（まだありません）")
    return "\n".join(lines) + "\n"
