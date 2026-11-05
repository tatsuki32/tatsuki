#!/usr/bin/env python3
"""Tatsuki's Desk — what is coming: spending ahead from the calendar and booking mail, and one money tip a day.
Scheduled Claude tasks read the mail and the web and hand over short records. Mail text is never kept. USD amounts and
estimates are computed here from the person's own rows, never supplied by the task.

    python3 desk/wise_plan.py context            what the two tasks read first
    python3 desk/wise_plan.py upcoming-put <file>
    python3 desk/wise_plan.py tip-put <file>
"""
import datetime as dt
import json
import re
import statistics
import sys

import wise_analysis
import wise_sync

KINDS = {"flight": "航空券", "stay": "宿", "event": "予定", "payment": "支払い", "other": "その他"}
MAX_TEXT = 120
MIN_DAYS = 5  # fewer days spent in a currency than this says nothing about the next trip
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
CODE = re.compile(r"[A-Z]{3}")


def recent_rates(rows):
    """USD per unit of each currency, from the newest row paid in it."""
    out = {}
    for a in sorted((a for a in rows if a.get("usd") and a.get("value")), key=lambda a: a.get("date") or ""):
        out[a.get("currency")] = a["usd"] / a["value"]
    return {**out, "USD": 1}


def daily_estimate(rows, currency):
    """(a usual day, how many days it rests on) of the person's own spending priced in that currency, or (None, n)."""
    days = {}
    for a in rows:
        amount = wise_analysis.spent_amount(a)
        if amount and a.get("currency") == currency and a.get("date"):
            days[a["date"]] = days.get(a["date"], 0) + amount
    return (round(statistics.median(days.values()), 2), len(days)) if len(days) >= MIN_DAYS else (None, len(days))


def to_usd(money, rates):
    if not money or money.get("value") is None:
        return 0 if not money else None
    rate = rates.get(money.get("currency"))
    return round(money["value"] * rate, 2) if rate else None


def ahead(rows, today, runway=None, usual=None):
    """Everything still to come. What is already paid is shown but counts towards nothing, so it is never counted twice."""
    saved = wise_sync.read_json("upcoming.json", {})
    hidden = set(wise_sync.read_json("upcoming-hidden.json", []))
    rates = recent_rates(rows)
    out, due, estimate, unknown = [], 0, 0, 0
    for item in saved.get("items") or []:
        if item["id"] in hidden or (item.get("end") or item["date"]) < today:
            continue
        paid, owed = to_usd(item.get("paid"), rates), to_usd(item.get("due"), rates)
        day, based_on = daily_estimate(rows, item.get("currency")) if item.get("currency") else (None, 0)
        first, last = dt.date.fromisoformat(item["date"]), dt.date.fromisoformat(item.get("end") or item["date"])
        length = (last - first).days + 1
        guess = round(day * length, 2) if day is not None and item.get("end") else None
        low = round(guess * 0.7, 2) if guess is not None else None
        high = round(guess * 1.3, 2) if guess is not None else None
        out.append({**item, "kindName": KINDS.get(item.get("kind"), "その他"), "paidUsd": paid, "dueUsd": owed, "estimate": guess, "low": low, "high": high, "days": length, "basedOn": based_on})
        if owed is None:
            unknown += 1
        else:
            due += owed
        estimate += guess or 0
    out.sort(key=lambda i: i["date"])
    total = round(due + estimate, 2)
    after = None
    if runway and usual and usual.get("usd"):
        after = round(max(runway["balance"] - total, 0) / usual["usd"], 1)
    return {"items": out, "due": round(due, 2), "estimate": round(estimate, 2), "total": total, "unknown": unknown, "runwayAfter": after, "at": saved.get("at")}


def clean_money(m):
    if not isinstance(m, dict) or not isinstance(m.get("value"), (int, float)) or isinstance(m.get("value"), bool) or m["value"] < 0:
        return None
    return {"value": m["value"], "currency": m["currency"]} if CODE.fullmatch(str(m.get("currency"))) else None


def clean_item(raw):
    """A short record or None. Only these fields are kept, so a mail's text cannot come along."""
    if not isinstance(raw, dict) or not DATE.fullmatch(str(raw.get("date"))) or not str(raw.get("id") or "").strip() or not str(raw.get("title") or "").strip():
        return None
    end = raw.get("end")
    if end is not None and (not DATE.fullmatch(str(end)) or end < raw["date"]):
        return None
    url = raw.get("url")
    return {"id": str(raw["id"])[:80], "title": str(raw["title"])[:MAX_TEXT], "kind": raw.get("kind") if raw.get("kind") in KINDS else "other", "date": raw["date"], "end": end,
            "place": str(raw.get("place") or "")[:MAX_TEXT], "currency": raw["currency"] if CODE.fullmatch(str(raw.get("currency"))) else None, "paid": clean_money(raw.get("paid")),
            "due": clean_money(raw.get("due")), "url": url if isinstance(url, str) and url.startswith("https://") and len(url) < 300 else None, "source": "gmail" if raw.get("source") == "gmail" else "calendar"}


def put_upcoming(new, today):
    kept = [i for i in map(clean_item, new.get("items") or []) if i]
    wise_sync.write_json("upcoming.json", {"at": today, "items": kept})
    return {"saved": len(kept), "dropped": len(new.get("items") or []) - len(kept)}


def age_on(birth, today):
    b, t = dt.date.fromisoformat(birth), dt.date.fromisoformat(today)
    return t.year - b.year - ((t.month, t.day) < (b.month, b.day))


def tips():
    return wise_sync.read_json("tips.json", [])


def today_tip(today):
    """The newest tip, for as long as it is at most two days old: a stale one is left in the list, not on the front page."""
    saved = tips()
    if not saved:
        return None
    last = saved[-1]
    return last if (dt.date.fromisoformat(today) - dt.date.fromisoformat(last["date"])).days <= 2 else None


def put_tip(new, today):
    """Refuses a tip with no source, no year, a topic already used, or words that blame."""
    url, year = new.get("url"), new.get("year")
    title, body, topic, source = (str(new.get(k) or "").strip() for k in ("title", "body", "topic", "source"))
    if not (title and body and topic and source):
        return {"saved": False, "why": "題名・本文・話題・出典が必要です"}
    if not (isinstance(url, str) and url.startswith("https://")) or not isinstance(year, int):
        return {"saved": False, "why": "出典の URL と年が必要です"}
    if any(w in title + body for w in wise_analysis.BANNED_WORDS):
        return {"saved": False, "why": "使わない言葉があります"}
    saved = tips()
    if any(t["topic"] == topic or t["url"] == url for t in saved):
        return {"saved": False, "why": "同じ話がすでにあります"}
    wise_sync.write_json("tips.json", saved + [{"date": today, "topic": topic[:60], "title": title[:80], "body": body[:400], "source": source[:80], "url": url[:300], "year": year}])
    return {"saved": True}


def context(today):
    profile = wise_sync.read_json("profile.json", {})
    saved = wise_sync.read_json("upcoming.json", {})
    return {"today": today, "age": age_on(profile["birth"], today) if profile.get("birth") else None, "sex": profile.get("sex"), "usedTopics": [t["topic"] for t in tips()],
            "lastTips": tips()[-5:], "upcoming": saved.get("items") or [], "hidden": wise_sync.read_json("upcoming-hidden.json", [])}


if __name__ == "__main__":
    import wise_report
    args, now = sys.argv[1:], wise_report.today_hcmc().isoformat()
    if args[:1] == ["context"]:
        print(json.dumps(context(now), ensure_ascii=False, indent=1))
    elif args[:1] in (["upcoming-put"], ["tip-put"]) and len(args) == 2:
        with open(args[1], encoding="utf-8") as f:
            print(json.dumps((put_upcoming if args[0] == "upcoming-put" else put_tip)(json.load(f), now), ensure_ascii=False))
    else:
        print(__doc__)
        sys.exit(2)
