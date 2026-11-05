#!/usr/bin/env python3
"""Tatsuki's Desk — the numbers behind the お金 tabs: what each movement was, how a period compares, and what stands out.
Every number here is computed from the saved rows. Claude writes words about them and never supplies a figure
(advice with a number that is not on this page is refused).

    python3 desk/wise_analysis.py facts                what /money-review reads
    python3 desk/wise_analysis.py unknown              titles with no proposal yet
    python3 desk/wise_analysis.py guess-put <file>     add proposals; the person's own choices still win
    python3 desk/wise_analysis.py advice-put <file>    save up to 3 pieces of advice
"""
import calendar
import datetime as dt
import json
import re
import statistics
import sys

import wise_sync

KINDS = {"spend": "支出", "income": "収益", "self": "自分の移動", "advance": "立替", "refund": "返金", "unknown": "未確認"}
UNSORTED = "未分類"
GROUPS = {"食料品": "飲食費", "外食・カフェ": "飲食費", "コンビニ": "飲食費", "交通": "交通費", "旅行・宿泊": "旅行・宿泊", "買い物": "買い物・日用品",
          "日用品・薬": "買い物・日用品", "学び": "学び・手続き", "手続き・手数料": "学び・手続き", "現金": "現金"}
GROUP_ORDER = ("飲食費", "交通費", "旅行・宿泊", "買い物・日用品", "学び・手続き", "現金", "その他")
# For seeing what money was for, in the words a business uses. Not a tax judgement: whether anything is an expense is not decided here.
ACCOUNTS = {"交通": ("旅費交通費", "Travel"), "旅行・宿泊": ("旅費交通費", "Travel"), "通信": ("通信費", "Utilities"), "サブスク・ツール": ("支払手数料", "Other expenses"),
            "買い物": ("消耗品費", "Supplies"), "日用品・薬": ("消耗品費", "Supplies"), "学び": ("研修費・新聞図書費", "Other expenses"), "手続き・手数料": ("租税公課・支払手数料", "Taxes and licenses"),
            "カード": ("支払手数料", "Other expenses")}
LIVING = ("生活費", "Personal")
UNKNOWN_ACCOUNT = ("使途不明", "Uncategorized")
BANNED_WORDS = ("赤字", "使いすぎ", "使い過ぎ", "無駄", "浪費", "おすすめの銘柄", "買うべき", "売るべき")
FREE_NUMBER = 36  # months, days and counts may appear in words without being a computed amount
MAX_ADVICE = 3
HISTORY = 12
Z_LIMIT = 3.5
MIN_OUTLIER_MONTHS = 6
MAX_DAY_BARS = 92
FX_MIN_SHARE, FX_MIN_USD = 0.01, 5


def kind_of(a, chosen, guess):
    """What a movement was: the person's choice, then Claude's proposal, then what its type says. (kind, proposal or None)"""
    direction = a.get("flow")
    if not direction:
        return None, None
    key = direction + ":" + (a.get("title") or "").lower()
    if key in chosen:
        return chosen[key], None
    proposal = (guess.get("flows") or {}).get(key)
    if proposal:
        return proposal["kind"], proposal
    if direction == "out":
        return ("unknown" if a.get("type") == "TRANSFER" else "spend"), None
    return {"CARD_PAYMENT": "refund", "BALANCE_DEPOSIT": "self"}.get(a.get("type"), "unknown"), None


def enrich(items, category_of):
    """Adds kind, group and the proposal's reason to each row, in place."""
    chosen = wise_sync.read_json("flows.json", {})
    guess = wise_sync.read_json("guess.json", {})
    overrides = wise_sync.read_json("categories.json", {})
    direct = set(wise_sync.read_json("direct.json", []))
    for a in items:
        title = (a.get("title") or "").lower()
        a["kind"], proposal = kind_of(a, chosen, guess)
        a["kindSure"] = proposal["sure"] if proposal else True
        a["kindWhy"] = proposal["why"] if proposal else ""
        a["direct"] = (a.get("flow") or "") + ":" + title in direct
        a["category"] = category_of(a, overrides)
        a["group"] = GROUPS.get(a["category"], "その他")
        found = (guess.get("categories") or {}).get(title)
        a["use"] = found["use"] if found and title not in overrides else ""
        a["useSure"] = bool(found and found["sure"]) or title in overrides or not found
    return items


def spent_amount(a):
    """What this row adds to spending: a refund takes it back, a move or a repayment is not spending."""
    if a.get("usd") is None:
        return 0
    return {"spend": a["usd"], "refund": -a["usd"]}.get(a.get("kind"), 0)


def spent_total(items):
    return round(sum(spent_amount(a) for a in items), 2)


def kind_totals(items):
    out = {k: {"usd": 0, "count": 0} for k in KINDS}
    for a in items:
        if a.get("kind") and a.get("usd") is not None:
            t = out[a["kind"]]
            t["usd"] = round(t["usd"] + a["usd"], 2)
            t["count"] += 1
    return out


def accounts(items):
    """Spending under the names a business would use, Japanese and American, to switch between."""
    totals = {}
    for a in items:
        amount = spent_amount(a)
        if not amount:
            continue
        if a["category"] in ACCOUNTS:
            jp, us = ACCOUNTS[a["category"]]
        elif a["category"] in (UNSORTED, "現金", "送金"):
            jp, us = UNKNOWN_ACCOUNT
        else:
            jp, us = LIVING
        for side, name in (("jp", jp), ("us", us)):
            t = totals.setdefault(side, {})
            t[name] = round(t.get(name, 0) + amount, 2)
    return {side: sorted(({"name": n, "usd": v} for n, v in t.items()), key=lambda r: -r["usd"]) for side, t in totals.items()}


def month_days(month):
    return calendar.monthrange(int(month[:4]), int(month[5:7]))[1]


def within_day(a, day):
    return int(a["date"][8:10]) <= day


def by_group_month(rows):
    out = {}
    for a in rows:
        amount = spent_amount(a)
        if amount:
            m = out.setdefault(a["date"][:7], {})
            m[a["group"]] = round(m.get(a["group"], 0) + amount, 2)
    return out


def months_series(rows, first, last):
    """One entry for every month from first to last, so a month with nothing still counts as a month."""
    out, month = [], first
    while month <= last:
        here = [a for a in rows if a["date"][:7] == month]
        income = round(sum(a["usd"] for a in here if a["kind"] == "income"), 2)
        spent = spent_total(here)
        out.append({"month": month, "income": income, "spent": spent, "net": round(income - spent, 2)})
        month = wise_sync.month_add(month, 1)
    return out


def days_series(rows, first, last):
    totals = {}
    for a in rows:
        amount = spent_amount(a)
        if amount and first <= a["date"] <= last:
            totals[a["date"]] = round(totals.get(a["date"], 0) + amount, 2)
    out, day = [], dt.date.fromisoformat(first)
    while day.isoformat() <= last:
        out.append({"date": day.isoformat(), "spent": totals.get(day.isoformat(), 0)})
        day += dt.timedelta(days=1)
    return out


def in_window(a, window):
    return window[0] <= a["date"] <= window[1]


def variances(rows, this, windows, field="category"):
    """`this` against the median of the same stretch in earlier windows, largest gap first."""
    names = {a[field] for a in rows if spent_amount(a)}
    out = []
    for name in names:
        now = round(sum(spent_amount(a) for a in this if a[field] == name), 2)
        past = [sum(spent_amount(a) for a in rows if a[field] == name and in_window(a, w)) for w in windows]
        usual = round(statistics.median(past), 2) if past else 0
        out.append({"name": name, "usd": now, "usual": usual, "gap": round(now - usual, 2)})
    return sorted(out, key=lambda v: -abs(v["gap"]))


def outliers(rows, this, windows):
    """Categories far from their own usual, by modified z-score. Fewer than six earlier stretches say nothing."""
    if len(windows) < MIN_OUTLIER_MONTHS:
        return []
    out = []
    for v in variances(rows, this, windows):
        past = [sum(spent_amount(a) for a in rows if a["category"] == v["name"] and in_window(a, w)) for w in windows]
        mid = statistics.median(past)
        mad = statistics.median(abs(x - mid) for x in past)
        if mad and 0.6745 * (v["usd"] - mid) / mad > Z_LIMIT:
            out.append(v)
    return sorted(out, key=lambda v: -v["gap"])[:3]


def fx_effect(this, before):
    """How much of the change came from the rate and how much from what was bought, per currency of the price (Bennet)."""
    now, then = {}, {}
    for side, part in ((now, this), (then, before)):
        for a in part:
            if a["kind"] not in ("spend", "refund") or a.get("value") is None:
                continue
            sign = spent_amount(a) / a["usd"] if a["usd"] else 0
            t = side.setdefault(a["currency"], [0, 0])
            t[0] += sign * a["value"]
            t[1] += spent_amount(a)
    use = rate = 0
    for currency in set(now) | set(then):
        l1, u1 = now.get(currency, (0, 0))
        l0, u0 = then.get(currency, (0, 0))
        if l0 > 0 and l1 > 0:
            r0, r1 = u0 / l0, u1 / l1
            use += (l1 - l0) * (r0 + r1) / 2
            rate += (r1 - r0) * (l0 + l1) / 2
        else:
            use += u1 - u0
    total = sum(t[1] for t in now.values())
    return {"use": round(use, 2), "rate": round(rate, 2), "show": bool(abs(rate) >= FX_MIN_USD and total and abs(rate) / abs(total) >= FX_MIN_SHARE)}


def by_currency(items):
    totals = {}
    for a in items:
        amount = spent_amount(a)
        if amount and a.get("value") is not None:
            t = totals.setdefault(a["currency"], {"currency": a["currency"], "value": 0, "usd": 0, "count": 0})
            t["value"] = round(t["value"] + (a["value"] if amount > 0 else -a["value"]), 2)
            t["usd"] = round(t["usd"] + amount, 2)
            t["count"] += 1
    for t in totals.values():
        t["rate"] = round(t["usd"] / t["value"], 6) if t["value"] else None
    return sorted(totals.values(), key=lambda t: -t["usd"])


def monthly_charges(rows, today):
    """Charges that come about once a month at a steady price. Unlike the list for subscriptions this keeps ones that
    stopped, tolerates a one-off price jump, and takes the person's own marks."""
    marks = wise_sync.read_json("recurring.json", {})
    groups = {}
    for a in rows:
        if a["kind"] == "spend":
            groups.setdefault((a.get("title") or "").lower() + "|" + str(a.get("currency")), []).append(a)
    out = []
    for key, xs in groups.items():
        mark = marks.get(key) or {}
        if mark.get("mark") is False:
            continue
        xs.sort(key=lambda a: a["date"])
        months = sorted({a["date"][:7] for a in xs})
        span = (int(months[-1][:4]) - int(months[0][:4])) * 12 + int(months[-1][5:7]) - int(months[0][5:7]) + 1
        mid = statistics.median(a["value"] for a in xs)
        steady = sum(abs(a["value"] - mid) <= mid * 0.25 for a in xs) >= len(xs) * 0.8
        found = len(months) >= 3 and any(wise_sync.month_add(m, 1) in months for m in months) and len(xs) <= span * 1.5 and steady
        if not found and not mark.get("mark"):
            continue
        usd = round(statistics.median(a["usd"] for a in xs), 2)
        last = xs[-1]["date"]
        out.append({"key": key, "title": xs[-1]["title"], "usd": usd, "yearly": round(usd * 12, 2), "amountText": xs[-1]["amountText"], "months": len(months), "last": last,
                    "active": (dt.date.fromisoformat(today) - dt.date.fromisoformat(last)).days <= 62, "reviewed": mark.get("reviewed"), "choice": mark.get("choice")})
    return sorted(out, key=lambda r: (not r["active"], -r["usd"]))


def next_payments(charges, today):
    """Expected dates are a guess from the last charge, and say so on the page."""
    today_d, out = dt.date.fromisoformat(today), []
    for c in charges:
        if not c["active"]:
            continue
        last = dt.date.fromisoformat(c["last"])
        month = wise_sync.month_add(c["last"][:7], 1)
        expected = dt.date(int(month[:4]), int(month[5:7]), min(last.day, month_days(month)))
        if 0 <= (expected - today_d).days <= 7:
            out.append({"title": c["title"], "usd": c["usd"], "date": expected.isoformat()})
    return sorted(out, key=lambda p: p["date"])[:3]


def week_action(charges, gaps, today):
    """One thing to look at this week. A charge kept on purpose is not asked about again for six months."""
    limit = (dt.date.fromisoformat(today) - dt.timedelta(days=183)).isoformat()
    for c in charges:
        if c["active"] and (not c["reviewed"] or c["reviewed"] < limit):
            return {"kind": "charge", "key": c["key"], "fact": f"毎月の請求で一番大きいのは {c['title']} で、月 {c['usd']:,.2f} USD（年 {c['yearly']:,.2f} USD）です。", "ask": "今も使っていますか"}
    up = next((v for v in gaps if v["gap"] > 0 and v["name"] != UNSORTED), None)
    if up:
        return {"kind": "gap", "name": up["name"], "fact": f"{up['name']} は今月 {up['usd']:,.2f} USD で、いつもの同じ時期より {up['gap']:,.2f} USD 多いです。", "ask": "明細を1回だけ見ますか"}
    return None


def balance_now():
    snaps = wise_sync.read_json("balances.json", {})
    if not snaps:
        return None
    rows = snaps[max(snaps)]
    known = [r["usd"] for r in rows if r.get("usd") is not None]
    return {"date": max(snaps), "usd": round(sum(known), 2), "complete": len(known) == len(rows)}


def month_windows(series_months, cut):
    return [(f"{m}-01", f"{m}-{min(cut, month_days(m)):02d}") for m in series_months]


def same_length_windows(first, last, data_start, n=HISTORY):
    """Earlier stretches of the same length, as far back as there is data."""
    start, end = dt.date.fromisoformat(first), dt.date.fromisoformat(last)
    length, out = (end - start).days + 1, []
    for i in range(1, n + 1):
        s0 = start - dt.timedelta(days=length * i)
        if s0.isoformat() < data_start:
            break
        out.append((s0.isoformat(), (s0 + dt.timedelta(days=length - 1)).isoformat()))
    return out


def analyze(rows, today, month=None, start=None, end=None, before=None, label=None):
    """rows: every enriched row. With `month`, the month looked at (the current one only up to today). With start and end,
    any stretch, compared with the stretch before it and with earlier stretches of the same length."""
    current = today[:7]
    counted = [a for a in rows if a.get("kind") and a.get("usd") is not None and a.get("date")]
    first_month = min([a["date"][:7] for a in counted] or [current])
    data_start = min([a["date"] for a in counted] or [today])
    series = months_series(counted, first_month, current)
    done = [m for m in series if m["month"] < current]
    spends = [m["spent"] for m in done[-HISTORY:]]
    usual = {"usd": round(statistics.median(spends), 2), "low": min(spends), "high": max(spends), "months": len(spends)} if spends else None
    balance = balance_now()
    runway = None
    if balance and usual and usual["usd"] > 0:
        runway = {"long": round(balance["usd"] / usual["usd"], 1), "short": round(balance["usd"] / usual["high"], 1) if usual["high"] > 0 else None,
                  "balance": balance["usd"], "complete": balance["complete"], "date": balance["date"]}
    this_month_cut = int(today[8:10])
    current_windows = month_windows([m["month"] for m in series if m["month"] < current][-HISTORY:], this_month_cut)
    current_rows = [a for a in counted if a["date"][:7] == current and int(a["date"][8:10]) <= this_month_cut]
    current_gaps = variances(counted, current_rows, current_windows)
    is_month = start is None
    if is_month:
        month = month or current
        cut = this_month_cut if month == current else month_days(month)
        previous = wise_sync.month_add(month, -1)
        prior_cut = min(cut, month_days(previous))
        start, end = f"{month}-01", f"{month}-{cut:02d}"
        window_before = (f"{previous}-01", f"{previous}-{prior_cut:02d}")
        windows = month_windows([m["month"] for m in series if m["month"] < month][-HISTORY:], cut)
        scope = ("今月" + f"1日〜{cut}日") if month == current else f"{int(month[:4])}年{int(month[5:7])}月"
    else:
        month, cut, prior_cut = None, None, None
        window_before = tuple(d.isoformat() if hasattr(d, "isoformat") else d for d in before) if before else None
        windows = same_length_windows(start, end, data_start)
        scope = label or f"{start} 〜 {end}"
    this = [a for a in counted if in_window(a, (start, end))]
    prior = [a for a in counted if window_before and in_window(a, window_before)]
    gaps = variances(counted, this, windows)
    so_far = spent_total(this)
    same_time = [sum(spent_amount(a) for a in counted if in_window(a, w)) for w in windows]
    categories = []
    for v in gaps:
        prev = spent_total(a for a in prior if a["category"] == v["name"])
        categories.append({**v, "last": prev, "change": round(v["usd"] - prev, 2), "share": round(v["usd"] / so_far * 100, 1) if so_far > 0 else 0})
    focus = []
    group_gaps = variances(counted, this, windows, "group")
    for name in ("交通費", "飲食費"):
        v = next((g for g in group_gaps if g["name"] == name), {"name": name, "usd": 0, "usual": 0, "gap": 0})
        focus.append({**v, "last": spent_total(a for a in prior if a["group"] == name), "share": round(v["usd"] / so_far * 100, 1) if so_far > 0 else 0})
    moved = [a for a in counted if a["date"] and not a.get("direct")]  # paid straight from a bank: the balance never moved
    money_in, money_out = round(sum(a["usd"] for a in moved if a["flow"] == "in"), 2), round(sum(a["usd"] for a in moved if a["flow"] == "out"), 2)
    bridge = {"in": money_in, "out": money_out, "balance": balance["usd"], "gap": round(balance["usd"] - (money_in - money_out), 2)} if balance else None
    today_d = dt.date.fromisoformat(today)
    day_of = lambda n: (today_d - dt.timedelta(days=n)).isoformat()
    on_day = lambda d: spent_total(a for a in counted if a["date"] == d)
    nets = [m["net"] for m in done[-3:]]
    charges = monthly_charges(counted, today)
    income = round(sum(a["usd"] for a in this if a["kind"] == "income"), 2)
    span_months = max(1, (int(end[:4]) - int(start[:4])) * 12 + int(end[5:7]) - int(start[5:7]) + 1)
    shown = series[-HISTORY - 1:] if is_month or span_months < 2 else [m for m in series if start[:7] <= m["month"] <= end[:7]]
    last_day = min(end, today)
    day_start = max(start, (dt.date.fromisoformat(last_day) - dt.timedelta(days=MAX_DAY_BARS - 1)).isoformat())
    cur_totals = {}
    for a in counted:
        amount = spent_amount(a)
        if amount and a.get("currency"):
            cur_totals[a["currency"]] = cur_totals.get(a["currency"], 0) + amount
    largest_move = next((v for v in gaps if v["gap"] and v["name"] != UNSORTED), None)
    return {
        "today": today, "month": month, "cut": cut, "isCurrent": month == current, "isMonth": is_month, "scope": scope, "start": start, "end": end,
        "before": window_before, "windows": len(windows), "months": shown, "monthsAll": len(done),
        "usual": usual, "runway": runway, "soFar": so_far, "sameTime": round(statistics.median(same_time), 2) if same_time else None, "lastSame": spent_total(prior), "lastCut": prior_cut,
        "income": income, "netNow": round(income - so_far, 2), "netUsual": round(statistics.median(nets), 2) if nets else None, "perMonth": round(so_far / span_months, 2), "spanMonths": span_months,
        "yesterday": {"date": day_of(1), "usd": on_day(day_of(1)), "before": on_day(day_of(2)), "lastWeek": on_day(day_of(8))},
        "days": days_series(counted, day_start, last_day), "daysCut": day_start > start, "groups": by_group_month(counted), "groupOrder": GROUP_ORDER,
        "categories": categories, "focus": focus, "bridge": bridge, "outliers": outliers(counted, this, windows), "fx": fx_effect(this, prior), "moved": largest_move,
        "currencyOrder": sorted(cur_totals, key=lambda c: -cur_totals[c]),
        "charges": charges, "next": next_payments(charges, today), "action": week_action(charges, current_gaps, today),
    }


def seen_since(rows, seen):
    """What arrived since the person last opened the page. No number of days away: that would read as a reproach."""
    if not seen:
        return None
    new = [a for a in rows if a.get("kind") and (a.get("date") or "") >= seen]
    return {"since": seen, "count": len(new), "spent": spent_total(new)}


def numbers_in(value):
    """Every number on the page, as text forms Claude might write."""
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, (int, float)):
        return {round(float(value), 2), round(float(value), 1), float(round(value))}
    if isinstance(value, dict):
        return set().union(*(numbers_in(v) for v in value.values())) if value else set()
    if isinstance(value, (list, tuple)):
        return set().union(*(numbers_in(v) for v in value)) if value else set()
    return set()


def check_advice(item, allowed):
    """None if it may be shown, or the reason it may not."""
    text = " ".join(str(item.get(k) or "") for k in ("fact", "why", "next"))
    for word in BANNED_WORDS:
        if word in text:
            return f"使わない言葉：{word}"
    for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        n = float(raw.replace(",", ""))
        if n > FREE_NUMBER and round(n, 2) not in allowed:
            return f"計算にない数字：{raw}"
    return None if str(item.get("fact") or "").strip() and str(item.get("next") or "").strip() else "事実と次の1つが必要です"


def facts():
    """What /money-review reads. The row list stays out: shop names only as the page itself lists them."""
    import wise_report
    out = {}
    for name, period in (("month", "month"), ("threeMonths", "3m"), ("sixMonths", "6m"), ("all", "all")):
        data = wise_report.view(period=period)
        z = data["analysis"]
        out[name] = {"scope": z["scope"], "start": z["start"], "end": z["end"], "spent": z["soFar"], "income": z["income"], "net": z["netNow"], "perMonth": z["perMonth"], "sameLengthUsual": z["sameTime"], "before": z["lastSame"],
                     "categories": z["categories"], "focus": z["focus"], "outliers": z["outliers"], "fx": z["fx"], "kinds": data["kinds"], "accounts": data["accounts"], "currencies": data["currencies"], "largest": data["largest"]}
    now = wise_report.view(period="month")
    z = now["analysis"]
    return {"periods": out, "usual": z["usual"], "runway": z["runway"], "bridge": z["bridge"], "charges": z["charges"], "action": z["action"], "balances": now["balances"], "budget": now["budget"], "saving": now["saving"]}


def unknown_titles():
    import wise_report
    rows = wise_report.view(period="all")["items"]
    guess = wise_sync.read_json("guess.json", {})
    own, known = wise_sync.read_json("flows.json", {}), guess.get("flows") or {}
    cats, out = guess.get("categories") or {}, {}
    for a in rows:
        if not a.get("flow"):
            continue
        title = (a.get("title") or "").lower()
        if a["kind"] == "unknown" or (a["category"] == UNSORTED and title not in cats and a["flow"] == "out"):
            key = a["flow"] + ":" + title
            if key not in own and key not in known:
                t = out.setdefault(key, {"title": a["title"], "flow": a["flow"], "type": a["type"], "count": 0, "usd": 0, "currency": a["currency"]})
                t["count"] += 1
                t["usd"] = round(t["usd"] + (a["usd"] or 0), 2)
    return sorted(out.values(), key=lambda t: -t["usd"])


def put_guess(new):
    """Adds proposals to what is saved. A category outside the list or a kind outside the list is dropped, and reported."""
    import wise_report
    saved = wise_sync.read_json("guess.json", {"categories": {}, "flows": {}})
    dropped = []
    for key, g in (new.get("categories") or {}).items():
        if g.get("category") in wise_report.CATEGORIES and isinstance(g.get("use"), str):
            saved.setdefault("categories", {})[key.lower()] = {"category": g["category"], "use": g["use"][:80], "sure": bool(g.get("sure"))}
        else:
            dropped.append(key)
    for key, g in (new.get("flows") or {}).items():
        if g.get("kind") in KINDS and key.split(":")[0] in ("in", "out"):
            saved.setdefault("flows", {})[key.lower()] = {"kind": g["kind"], "sure": bool(g.get("sure")), "why": str(g.get("why") or "")[:80]}
        else:
            dropped.append(key)
    saved["at"] = wise_sync.summary()["state"].get("lastTry") or saved.get("at")
    wise_sync.write_json("guess.json", saved)
    return {"saved": len(new.get("categories") or {}) + len(new.get("flows") or {}) - len(dropped), "dropped": dropped}


def put_advice(new):
    import wise_report
    allowed = numbers_in(facts())
    kept, refused = [], []
    for item in (new.get("items") or [])[:MAX_ADVICE]:
        why = check_advice(item, allowed)
        if why:
            refused.append({"fact": item.get("fact"), "why": why})
        else:
            kept.append({k: str(item.get(k) or "").strip() for k in ("fact", "why", "next")})
    if kept:
        wise_sync.write_json("advice.json", {"at": wise_report.today_hcmc().isoformat(), "items": kept})
    return {"saved": len(kept), "refused": refused}


if __name__ == "__main__":
    args = sys.argv[1:]
    command = args[0] if args else ""
    if command == "facts":
        print(json.dumps(facts(), ensure_ascii=False, indent=1))
    elif command == "unknown":
        print(json.dumps(unknown_titles(), ensure_ascii=False, indent=1))
    elif command in ("guess-put", "advice-put") and len(args) == 2:
        with open(args[1], encoding="utf-8") as f:
            print(json.dumps((put_guess if command == "guess-put" else put_advice)(json.load(f)), ensure_ascii=False, indent=1))
    else:
        print(__doc__)
        sys.exit(2)
