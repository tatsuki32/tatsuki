#!/usr/bin/env python3
"""Tatsuki's Desk — what the person decides on the お金 page: budgets, trip plans, saving goals, and the daily lesson.
Everything here is written by the person or by the lesson task. Nothing touches Wise. Trip plans are separate from
the bank rows on purpose: they are typed in, not matched.

    python3 desk/wise_goals.py lesson-context
    python3 desk/wise_goals.py lesson-put <file>
"""
import calendar
import datetime as dt
import json
import statistics
import sys

import wise_analysis
import wise_plan
import wise_sync

FIELDS = ("ライフプランニング", "リスク管理", "金融資産運用", "タックスプランニング", "不動産", "相続・事業承継")
REPORT = "金融機関のレポート"
REPORT_EVERY = 5  # one lesson in this many reads a published report instead of the next exam field
HISTORY = 12


def month_days(month):
    return calendar.monthrange(int(month[:4]), int(month[5:7]))[1]


def number(v):
    """A non-negative number, None when empty. ValueError on anything else."""
    if v in (None, ""):
        return None
    n = float(v)
    if n < 0 or n != n or n in (float("inf"),):
        raise ValueError("bad number")
    return round(n, 2)


def budget_for(saved, month):
    return (saved.get("months") or {}).get(month) or saved.get("default") or {}


def spent_in(rows, month):
    here = [a for a in rows if a.get("kind") and a.get("usd") is not None and (a.get("date") or "")[:7] == month]
    by_cat = {}
    for a in here:
        amount = wise_analysis.spent_amount(a)
        if amount:
            by_cat[a["category"]] = round(by_cat.get(a["category"], 0) + amount, 2)
    return wise_analysis.spent_total(here), by_cat


def budget(rows, today, month=None):
    """This month's room, and how earlier months went against the same budget. Nothing is shown until a budget exists."""
    saved = wise_sync.read_json("budgets.json", {})
    month = month or today[:7]
    current = budget_for(saved, month)
    spent, by_cat = spent_in(rows, month)
    days_left = max(month_days(month) - int(today[8:10]) + 1, 1) if month == today[:7] else 0
    total = current.get("total")
    now = {"month": month, "total": total, "spent": spent, "left": round(total - spent, 2) if total is not None else None,
           "perDay": round(max(total - spent, 0) / days_left, 2) if total is not None and days_left else None, "daysLeft": days_left,
           "cats": sorted(({"name": n, "budget": b, "spent": by_cat.get(n, 0), "left": round(b - by_cat.get(n, 0), 2)} for n, b in (current.get("cats") or {}).items()), key=lambda c: c["name"])}
    history = []
    previous = today[:7]
    spends = []
    for _ in range(HISTORY):
        previous = wise_sync.month_add(previous, -1)
        s, _ = spent_in(rows, previous)
        spends.append(s)
        planned = budget_for(saved, previous).get("total")
        history.append({"month": previous, "spent": s, "budget": planned, "over": round(s - planned, 2) if planned is not None else None})
    return {"now": now, "history": history, "average": round(sum(spends) / len(spends), 2) if spends else 0, "median": round(statistics.median(spends), 2) if spends else 0,
            "default": saved.get("default") or {}, "months": saved.get("months") or {}}


def put_budget(rec, categories):
    """{month?: 'YYYY-MM', total, cats: {name: amount}}. Without a month it is the budget for every month with none of its own."""
    total, cats = number(rec.get("total")), {}
    for name, value in (rec.get("cats") or {}).items():
        if name not in categories:
            raise ValueError("bad category")
        amount = number(value)
        if amount is not None:
            cats[name] = amount
    saved = wise_sync.read_json("budgets.json", {})
    entry = {"total": total, "cats": cats}
    if rec.get("month"):
        saved.setdefault("months", {})[str(rec["month"])] = entry
    else:
        saved["default"] = entry
    wise_sync.write_json("budgets.json", saved)


def clean_trip(rec, trip_id):
    """A trip plan as typed. Dates and the currency are checked; its items are plain lines."""
    name = str(rec.get("name") or "").strip()[:80]
    start, end = str(rec.get("start") or ""), str(rec.get("end") or "")
    dt.date.fromisoformat(start)
    dt.date.fromisoformat(end)
    if not name or end < start:
        raise ValueError("bad trip")
    currency = str(rec.get("currency") or "USD").upper()
    if not wise_plan.CODE.fullmatch(currency):
        raise ValueError("bad currency")
    items = []
    for it in (rec.get("items") or [])[:40]:
        label = str(it.get("name") or "").strip()[:60]
        if label:
            items.append({"name": label, "plan": number(it.get("plan")), "actual": number(it.get("actual"))})
    return {"id": trip_id, "name": name, "country": str(rec.get("country") or "").strip()[:60], "start": start, "end": end, "currency": currency, "budget": number(rec.get("budget")),
            "items": items, "note": str(rec.get("note") or "").strip()[:300], "ahead": bool(rec.get("ahead")), "deleted": bool(rec.get("deleted"))}


def put_trip(trip_id, rec):
    saved = wise_sync.read_json("trips.json", {})
    saved[trip_id] = clean_trip(rec, trip_id)
    wise_sync.write_json("trips.json", saved)


def trips(rows):
    """Newest first, with totals in the plan's own currency and in USD when a rate is known. Deleted ones stay on disk."""
    rates = wise_plan.recent_rates(rows)
    out = []
    for t in wise_sync.read_json("trips.json", {}).values():
        if t.get("deleted"):
            continue
        rate = rates.get(t["currency"])
        plan = round(sum(i["plan"] or 0 for i in t["items"]), 2)
        actual = round(sum(i["actual"] or 0 for i in t["items"]), 2)
        days = (dt.date.fromisoformat(t["end"]) - dt.date.fromisoformat(t["start"])).days + 1
        limit = t["budget"] if t["budget"] is not None else plan
        out.append({**t, "planTotal": plan, "actualTotal": actual, "days": days, "left": round(limit - actual, 2), "perDay": round(limit / days, 2), "rate": rate,
                    "budgetUsd": round(limit * rate, 2) if rate else None, "actualUsd": round(actual * rate, 2) if rate else None})
    return sorted(out, key=lambda t: t["start"], reverse=True)


def months_until(today, day):
    t, d = dt.date.fromisoformat(today), dt.date.fromisoformat(day)
    return max((d.year - t.year) * 12 + d.month - t.month, 1)


def add_months(today, n):
    month = wise_sync.month_add(today[:7], n)
    return f"{month}-01"


def saving(rows, today):
    """Pace comes from the last three finished months; a goal shows what it needs and when the pace or target reaches it."""
    saved = wise_sync.read_json("goals.json", {})
    series, month = [], today[:7]
    for _ in range(3):
        month = wise_sync.month_add(month, -1)
        spent, _unused = spent_in(rows, month)
        income = sum(a["usd"] for a in rows if a.get("kind") == "income" and a.get("usd") is not None and (a.get("date") or "")[:7] == month)
        series.append(round(income - spent, 2))
    pace = round(statistics.median(series), 2)
    target = saved.get("monthly")
    balance = wise_analysis.balance_now()
    start = balance["usd"] if balance else 0
    forecast = [{"month": wise_sync.month_add(today[:7], n), "pace": round(start + pace * n, 2), "target": round(start + target * n, 2) if target is not None else None} for n in range(0, 13)]
    goals = []
    for g in saved.get("items") or []:
        left = max(g["amount"] - (g.get("saved") or 0), 0)
        needed = round(left / months_until(today, g["due"]), 2) if g.get("due") else None
        reach = lambda speed: add_months(today, int(left // speed) + (1 if left % speed else 0)) if speed and speed > 0 else None
        goals.append({**g, "left": round(left, 2), "needed": needed, "byPace": reach(pace), "byTarget": reach(target)})
    return {"monthly": target, "pace": pace, "paceMonths": series, "start": start, "hasBalance": bool(balance), "forecast": forecast, "goals": goals}


def put_goals(rec):
    """Replaces what the saving page holds. An id must come with each goal, so an edit does not make a new one."""
    items = []
    for g in (rec.get("items") or [])[:30]:
        name, amount = str(g.get("name") or "").strip()[:60], number(g.get("amount"))
        if not name or amount is None:
            raise ValueError("bad goal")
        due = g.get("due") or None
        if due:
            dt.date.fromisoformat(str(due))
        items.append({"id": str(g.get("id") or name)[:80], "name": name, "amount": amount, "saved": number(g.get("saved")) or 0, "due": due})
    wise_sync.write_json("goals.json", {"monthly": number(rec.get("monthly")), "items": items})


def put_sims(rec):
    """The numbers typed into the simulators, kept as typed. Rates have no default and are not filled in here."""
    invest = rec.get("invest") or {}
    life = rec.get("life") or {}
    clean = {"invest": {k: number(invest.get(k)) for k in ("start", "monthly", "years", "fee", "rate1", "rate2", "rate3")},
             "life": {"start": number(life.get("start")), "monthly": number(life.get("monthly")), "rate": number(life.get("rate")), "toAge": number(life.get("toAge")),
                      "events": [{"age": number(e.get("age")), "name": str(e.get("name") or "").strip()[:60], "cost": number(e.get("cost"))} for e in (life.get("events") or [])[:40] if str(e.get("name") or "").strip()]}}
    wise_sync.write_json("sims.json", clean)


def age_info(today):
    profile = wise_sync.read_json("profile.json", {})
    return {"age": wise_plan.age_on(profile["birth"], today)} if profile.get("birth") else {"age": None}


def lessons():
    return wise_sync.read_json("lessons.json", [])


def next_field(saved):
    """Reports come every fifth lesson; otherwise the exam fields go round in order."""
    count = len(saved)
    if count and (count + 1) % REPORT_EVERY == 0:
        return REPORT
    done = sum(1 for s in saved if s["field"] != REPORT)
    return FIELDS[done % len(FIELDS)]


def put_lesson(new, today):
    """One lesson. Refused with no source, no year, or a quiz whose answer is not one of its choices."""
    field, title, body, source, topic = (str(new.get(k) or "").strip() for k in ("field", "title", "body", "source", "topic"))
    url, year, quiz = new.get("url"), new.get("year"), new.get("quiz") or {}
    if field not in FIELDS + (REPORT,):
        return {"saved": False, "why": "分野が違います"}
    if not (title and body and source and topic):
        return {"saved": False, "why": "題名・本文・話題・出典が必要です"}
    if not (isinstance(url, str) and url.startswith("https://")) or not isinstance(year, int):
        return {"saved": False, "why": "出典の URL と年が必要です"}
    choices = quiz.get("choices") if isinstance(quiz.get("choices"), list) else []
    answer = quiz.get("answer")
    if not str(quiz.get("q") or "").strip() or not 2 <= len(choices) <= 4 or not isinstance(answer, int) or isinstance(answer, bool) or not 0 <= answer < len(choices):
        return {"saved": False, "why": "クイズの問い、2〜4個の選択肢、正解が必要です"}
    if any(w in title + body for w in wise_analysis.BANNED_WORDS):
        return {"saved": False, "why": "使わない言葉があります"}
    saved = lessons()
    if any(s["topic"] == topic for s in saved):
        return {"saved": False, "why": "同じ話題がすでにあります"}
    lesson = {"id": f"{today}-{len(saved) + 1}", "date": today, "field": field, "topic": topic[:60], "title": title[:80], "body": body[:1200], "source": source[:80], "url": url[:300], "year": year,
              "quiz": {"q": str(quiz["q"])[:200], "choices": [str(c)[:120] for c in choices], "answer": answer, "why": str(quiz.get("why") or "")[:300]}}
    wise_sync.write_json("lessons.json", saved + [lesson])
    return {"saved": True}


def answer_lesson(lesson_id, choice):
    found = next((s for s in lessons() if s["id"] == lesson_id), None)
    if not found or not isinstance(choice, int) or isinstance(choice, bool) or not 0 <= choice < len(found["quiz"]["choices"]):
        raise ValueError("bad answer")
    answers = wise_sync.read_json("lesson-answers.json", {})
    answers.setdefault(lesson_id, {"choice": choice, "correct": choice == found["quiz"]["answer"]})  # the first answer is the one that counts
    wise_sync.write_json("lesson-answers.json", answers)


def learn(today):
    """The newest lesson first, each with its answer if given, and how far each field has come."""
    saved, answers = lessons(), wise_sync.read_json("lesson-answers.json", {})
    progress = {f: {"read": 0, "answered": 0, "correct": 0} for f in FIELDS + (REPORT,)}
    for s in saved:
        p = progress[s["field"]]
        p["read"] += 1
        given = answers.get(s["id"])
        if given:
            p["answered"] += 1
            p["correct"] += 1 if given["correct"] else 0
    return {"lessons": [{**s, "given": answers.get(s["id"])} for s in reversed(saved[-60:])], "progress": progress, "fields": FIELDS + (REPORT,)}


def lesson_context():
    saved = lessons()
    return {"nextField": next_field(saved), "usedTopics": [s["topic"] for s in saved], "last": saved[-3:]}


if __name__ == "__main__":
    import wise_report
    args = sys.argv[1:]
    if args[:1] == ["lesson-context"]:
        print(json.dumps(lesson_context(), ensure_ascii=False, indent=1))
    elif args[:1] == ["lesson-put"] and len(args) == 2:
        with open(args[1], encoding="utf-8") as f:
            print(json.dumps(put_lesson(json.load(f), wise_report.today_hcmc().isoformat()), ensure_ascii=False))
    else:
        print(__doc__)
        sys.exit(2)
