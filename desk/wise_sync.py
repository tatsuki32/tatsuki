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
    except Stop as e:
        state.update(ok=False, error=str(e))
        write_json("state.json", state)
        return state
    state.update(ok=True, error="", lastSuccess=state["lastTry"], count=len(store), added=len(store) - before)
    write_json("state.json", state)
    return state


def counted(a):
    """A card payment that went through. A card check, a cancelled payment, a transfer or a deposit is not spending."""
    return a.get("type") == "CARD_PAYMENT" and a.get("status") == "COMPLETED" and a.get("value") is not None


def settled(a):
    """What left or reached the balance: (value, currency). The second amount is what the balance paid, fees included,
    when the first is the shop's price. A payment split over several balances names no amounts, so the first one stands."""
    value, currency, _ = parse_amount(a.get("secondaryText"))
    return (value, currency) if value is not None else (a.get("value"), a.get("currency"))


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


def summary(month=None, today=None):
    """The Desk's page and Claude read this. Every completed movement counts. Totals are per currency: without rates
    they cannot be added together."""
    today = today or (dt.datetime.now(dt.timezone.utc) + HCMC).date().isoformat()
    month = month or today[:7]
    items = [{**a, "date": local_date(a.get("createdOn"))} for a in read_json("activities.json", {}).values()]
    totals = {}
    shown = []
    for a in items:
        if a["date"][:7] != month:
            continue
        value, currency = settled(a)
        a = {**a, "flow": flow(a), "settledValue": value, "settledCurrency": currency}
        shown.append(a)
        if a["flow"]:
            t = totals.setdefault(currency, {"in": 0, "out": 0, "net": 0, "count": 0, "types": {}})
            t[a["flow"]] = round(t[a["flow"]] + value, 2)
            t["net"] = round(t["in"] - t["out"], 2)
            t["count"] += 1
            key = a["flow"] + ":" + str(a.get("type"))
            t["types"][key] = round(t["types"].get(key, 0) + value, 2)
    shown.sort(key=lambda a: str(a.get("createdOn")), reverse=True)
    months = sorted({a["date"][:7] for a in items if a["date"]} | {today[:7]}, reverse=True)
    return {"month": month, "months": months, "totals": totals, "items": shown, "recurring": recurring(items, today),
            "unread": sum(1 for a in items if a.get("value") is None), "state": read_json("state.json", {})}


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["summary"]:
        print(json.dumps(summary(args[1] if len(args) > 1 else None), ensure_ascii=False, indent=1))
    elif args[:1] == ["status"]:
        print(json.dumps(read_json("state.json", {}), ensure_ascii=False, indent=1))
    else:
        result = run()
        print(time.strftime("%F %T ") + (f"ok {result['count']} rows, {result['added']} new" if result["ok"] else "stopped: " + result["error"]), flush=True)
        sys.exit(0 if result["ok"] else 1)
