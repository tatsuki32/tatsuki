#!/usr/bin/env python3
"""Tatsuki's Desk — Wise activity on this Mac (launchd, once a day). Reads only: every request is a GET.

The statements stay in desk/data/wise/, which backup_data.py never sends anywhere. The token lives in the macOS
keychain, and neither the token nor a statement line goes to the log.

  python3 wise_sync.py                  one pass
  python3 wise_sync.py summary [YYYY-MM]   the month's list and totals as JSON (what Claude reads)
  python3 wise_sync.py status           the last pass

Put the token in the keychain (macOS asks for it, so it stays out of the shell history):
  security add-generic-password -U -a wise -s tatsuki-desk-wise -w
"""
import datetime as dt
import html
import http.client
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import backup_data

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
API = "https://api.wise.com"
KEYCHAIN = ("tatsuki-desk-wise", "wise")  # service, account
HCMC = dt.timedelta(hours=7)
OVERLAP_DAYS = 14  # a payment can still change state this long after it was made
MAX_PAGES = 500
TRIES = 4
AMOUNT = re.compile(r"([+-]?)\s*(\d[\d,]*(?:\.\d+)?)\s*([A-Z]{3})\b")
TAG = re.compile(r"<[^>]+>")


class Stop(Exception):
    """The pass cannot go on. The message is shown on the Desk's page, so it holds no statement line."""


def wise_dir():
    return os.path.join(DATA, "wise")


def read_json(name, default):
    try:
        with open(os.path.join(wise_dir(), name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(name, value):
    os.makedirs(wise_dir(), mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=wise_dir(), prefix="." + name, suffix=".tmp")  # mkstemp: only this user can read it
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(wise_dir(), name))


def token():
    v = os.environ.get("WISE_TOKEN")
    if v:
        return v
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN[0], "-a", KEYCHAIN[1], "-w"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def http_get(path, tok):
    """One GET with retries: the connection sometimes breaks mid-answer, and that is worth another try. A refusal is not."""
    req = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + tok, "User-Agent": "tatsuki-desk"})
    for attempt in range(TRIES):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise Stop("Wise にトークンを断られました。トークンの期限と権限を確かめてください") from None
            if e.code < 500 and e.code != 429:
                raise Stop(f"Wise からエラーが返りました（{e.code}）") from None
        except (http.client.HTTPException, OSError, ValueError):
            pass
        if attempt < TRIES - 1:
            time.sleep(2 * (attempt + 1))
    raise Stop("Wise につながりません。次の回にもう一度試します")


def parse_iso(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def local_date(s):
    """The Ho Chi Minh date of a Wise timestamp, or '' when it cannot be read."""
    try:
        d = parse_iso(s)
    except ValueError:
        return ""
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return (d.astimezone(dt.timezone.utc) + HCMC).date().isoformat()


def parse_amount(raw):
    """'<positive>+ 1,234.50 USD</positive>' -> (1234.5, 'USD', True). (None, None, False) when it cannot be read."""
    raw = raw or ""
    m = AMOUNT.search(TAG.sub("", html.unescape(raw)))
    if not m:
        return None, None, False
    return float(m.group(2).replace(",", "")), m.group(3), m.group(1) == "+" or "<positive>" in raw


def plain(raw):
    return " ".join(TAG.sub("", html.unescape(raw or "")).split())


def clean(a):
    """Keeps what the Desk and Claude use, in a form they need not parse again."""
    value, currency, incoming = parse_amount(a.get("primaryAmount"))
    return {"id": a.get("id"), "type": a.get("type"), "status": a.get("status"), "title": plain(a.get("title")),
            "description": plain(a.get("description")), "amountText": plain(a.get("primaryAmount")),
            "secondaryText": plain(a.get("secondaryAmount")), "value": value, "currency": currency,
            "incoming": incoming, "createdOn": a.get("createdOn"), "updatedOn": a.get("updatedOn")}


def fetch(get, since=None):
    """Everything newer than `since` (ISO time), or everything. Pages come newest first."""
    out = []
    for profile in get("/v2/profiles"):
        cursor = None
        for _ in range(MAX_PAGES):
            page = get(f"/v1/profiles/{profile['id']}/activities?size=100" + (f"&nextCursor={cursor}" if cursor else ""))
            acts = page.get("activities") or []
            out += acts
            cursor = page.get("cursor")
            if not cursor or not acts or (since and all(str(a.get("createdOn")) < since for a in acts)):
                break
        else:
            raise Stop("明細のページが多すぎます。取り込みを止めました")
    return out


def recorded_usd(a):
    """The USD amount Wise itself recorded, or None. What the balance paid wins over the shop's price."""
    second, second_currency, _ = parse_amount(a.get("secondaryText"))
    if second_currency == "USD":
        return second
    return a.get("value") if a.get("currency") == "USD" else None


def fetch_rates(get, store):
    """Wise's own rate at the moment of each movement with no USD on record, kept per row so each is asked for once.
    Returns how many are still missing. Those stay out of the USD total and are shown apart, never guessed."""
    rates = read_json("rates.json", {})
    missing = failed_in_a_row = asked = 0
    for a in store.values():
        if not flow(a) or recorded_usd(a) is not None or a["id"] in rates:
            continue
        if failed_in_a_row >= 5:  # a refused token or no network: stop asking, the next pass tries again
            missing += 1
            continue
        when = parse_iso(a["createdOn"]).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")
        try:
            found = get("/v1/rates?" + urllib.parse.urlencode({"source": settled(a)[1], "target": "USD", "time": when}))
            rates[a["id"]] = float(found[0]["rate"])
            failed_in_a_row = 0
        except (Stop, LookupError, TypeError, ValueError):
            missing += 1
            failed_in_a_row += 1
        asked += 1
        if asked % 50 == 0:
            write_json("rates.json", rates)
    write_json("rates.json", rates)
    return missing


def snapshot_balances(get, today=None):
    """Today's balances with Wise's current USD rate, one entry a day. Part of the record, so a failure never stops a pass."""
    today = today or (dt.datetime.now(dt.timezone.utc) + HCMC).date().isoformat()
    snaps = read_json("balances.json", {})
    rows = []
    try:
        for profile in get("/v2/profiles"):
            for b in get(f"/v4/profiles/{profile['id']}/balances?types=STANDARD"):
                value, currency = float(b["amount"]["value"]), b["amount"]["currency"]
                if currency == "USD":
                    usd = value
                else:
                    try:
                        usd = value * float(get("/v1/rates?" + urllib.parse.urlencode({"source": currency, "target": "USD"}))[0]["rate"])
                    except (Stop, LookupError, TypeError, ValueError):
                        usd = None
                rows.append({"currency": currency, "value": value, "usd": None if usd is None else round(usd, 2)})
    except (Stop, LookupError, TypeError, ValueError):
        return
    snaps[today] = rows
    write_json("balances.json", snaps)


def run(get=None):
    """One pass. Returns the state it wrote; a failure keeps the statements as they were."""
    state = read_json("state.json", {})
    state["lastTry"] = int(time.time() * 1000)
    try:
        backup_data.ensure_ignore()  # first, so a statement never lands in a folder that gets sent
        if get is None:
            tok = token()
            if not tok:
                raise Stop("トークンがまだ設定されていません")
            get = lambda path: http_get(path, tok)
        last = state.get("lastSuccess")
        since = None
        if last:
            start = dt.datetime.fromtimestamp(last / 1000, dt.timezone.utc) - dt.timedelta(days=OVERLAP_DAYS)
            since = start.strftime("%Y-%m-%dT%H:%M:%S")
        store = read_json("activities.json", {})
        before = len(store)
        for a in fetch(get, since):
            if a.get("id"):
                store[a["id"]] = clean(a)
        write_json("activities.json", store)
        missing = fetch_rates(get, store)
        snapshot_balances(get)
    except Stop as e:
        state.update(ok=False, error=str(e))
        write_json("state.json", state)
        return state
    state.update(ok=True, error="", lastSuccess=state["lastTry"], count=len(store), added=len(store) - before, ratesMissing=missing)
    write_json("state.json", state)
    return state


def counted(a):
    """A card payment that went through. A card check, a cancelled payment, a transfer or a deposit is not spending."""
    return a.get("type") == "CARD_PAYMENT" and a.get("status") == "COMPLETED" and a.get("value") is not None


def to_usd(a, rates):
    """(USD to the cent, 'record' or 'rate'), or (None, None) with no rate yet. Rounding each row means a total always
    equals the sum of the rows shown and exported."""
    usd = recorded_usd(a)
    if usd is not None:
        return round(usd, 2), "record"
    rate, value = rates.get(a.get("id")), settled(a)[0]
    return (round(value * rate, 2), "rate") if rate and value is not None else (None, None)


def settled(a):
    """The amount the totals use: (value, currency). A yen amount wins, whichever of the two holds it, since yen is the
    one total that must add up. Otherwise the second amount, what the balance paid, fees included; a payment split over
    several balances names no amounts, so the first one stands. Nothing is ever converted with a rate."""
    second, second_currency, _ = parse_amount(a.get("secondaryText"))
    if second_currency == "JPY":
        return second, "JPY"
    if a.get("currency") == "JPY" or second is None:
        return a.get("value"), a.get("currency")
    return second, second_currency


def flow(a):
    """'in', 'out' or None. Moving money between one's own balances changes nothing, and a cancelled row never happened."""
    if a.get("status") != "COMPLETED" or a.get("value") is None or a.get("type") in ("INTERBALANCE", "CARD_CHECK"):
        return None
    return "in" if a.get("incoming") else "out"


def month_add(month, n):
    y, m = int(month[:4]), int(month[5:7]) - 1 + n
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"


def recurring(items, today):
    """Monthly-charge candidates: the same title and currency in consecutive months, about once a month, at a steady
    price, and charged lately. A shop visited ten times a month is left out."""
    groups = {}
    for a in items:
        if counted(a) and not a["incoming"] and a["date"]:
            groups.setdefault((a["title"].lower(), a["currency"]), []).append(a)
    out = []
    for (key, currency), xs in groups.items():
        xs.sort(key=lambda a: a["date"])
        months = sorted({a["date"][:7] for a in xs})
        if not any(month_add(m, 1) in months for m in months):
            continue
        span = (int(months[-1][:4]) - int(months[0][:4])) * 12 + int(months[-1][5:7]) - int(months[0][5:7]) + 1
        mid = statistics.median(a["value"] for a in xs)
        if len(xs) > span * 1.5 or any(abs(a["value"] - mid) > mid * 0.25 for a in xs):
            continue
        if (dt.date.fromisoformat(today) - dt.date.fromisoformat(xs[-1]["date"])).days > 62:
            continue
        out.append({"key": key + "|" + currency, "title": xs[-1]["title"], "amount": xs[-1]["value"], "currency": currency,
                    "amountText": xs[-1]["amountText"], "months": len(months), "last": xs[-1]["date"]})
    return sorted(out, key=lambda r: r["last"], reverse=True)


def summary(month=None, today=None, start=None, end=None):
    """The Desk's page and Claude read this. Every completed movement counts, in USD: Wise's own USD amount when it has
    one, otherwise converted at Wise's rate at that moment. "paid" keeps the currencies really paid in."""
    today = today or (dt.datetime.now(dt.timezone.utc) + HCMC).date().isoformat()
    month = month or today[:7]
    within = (lambda d: start <= d <= end) if start else (lambda d: d[:7] == month)
    rates = read_json("rates.json", {})
    items = [{**a, "date": local_date(a.get("createdOn"))} for a in read_json("activities.json", {}).values()]
    usd = {"in": 0, "out": 0, "net": 0, "count": 0, "recorded": 0, "converted": 0, "types": {}}
    paid, unconverted, shown = {}, {}, []
    for a in items:
        if not a["date"] or not within(a["date"]):
            continue
        value, currency = settled(a)
        amount, source = to_usd(a, rates)
        direction = flow(a)
        shown.append({**a, "flow": direction, "settledValue": value, "settledCurrency": currency, "usd": amount, "usdSource": source})
        if not direction:
            continue
        p = paid.setdefault(currency, {"in": 0, "out": 0, "count": 0})
        p[direction] = round(p[direction] + value, 2)
        p["count"] += 1
        if amount is None:
            unconverted[currency] = unconverted.get(currency, 0) + 1
            continue
        usd[direction] = round(usd[direction] + amount, 2)
        usd["net"] = round(usd["in"] - usd["out"], 2)
        usd["count"] += 1
        usd["recorded" if source == "record" else "converted"] += 1
        key = direction + ":" + str(a.get("type"))
        usd["types"][key] = round(usd["types"].get(key, 0) + amount, 2)
    shown.sort(key=lambda a: str(a.get("createdOn")), reverse=True)
    months = sorted({a["date"][:7] for a in items if a["date"]} | {today[:7]}, reverse=True)
    return {"month": month, "months": months, "usd": usd, "paid": paid, "unconverted": unconverted, "items": shown,
            "recurring": recurring(items, today), "unread": sum(1 for a in items if a.get("value") is None),
            "state": read_json("state.json", {})}


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["summary"]:
        print(json.dumps(summary(args[1] if len(args) > 1 else None), ensure_ascii=False, indent=1))
    elif args[:1] == ["status"]:
        print(json.dumps(read_json("state.json", {}), ensure_ascii=False, indent=1))
    else:
        result = run()
        import wise_world  # here, not at the top: it needs this module loaded
        wise_world.update_if_stale()
        import presents
        presents.add_wise()  # new deposits also show under プレゼント
        print(time.strftime("%F %T ") + (f"ok {result['count']} rows, {result['added']} new" if result["ok"] else "stopped: " + result["error"]), flush=True)
        sys.exit(0 if result["ok"] else 1)
