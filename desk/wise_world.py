#!/usr/bin/env python3
"""Tatsuki's Desk — other countries for comparison: public statistics only. No statement line ever leaves this Mac.

    python3 desk/wise_world.py    fetch the World Bank's International Comparison Program data
"""
import http.client
import json
import sys
import time
import urllib.request

import wise_analysis
import wise_sync

API = "https://api.worldbank.org/v2"
SOURCE = {"name": "世界銀行 International Comparison Program", "year": 2021, "url": "https://www.worldbank.org/en/programs/icp"}
TOTAL = "9020000"
# Spending is grouped by use, with the statistic's own definition beside it on the page
USES = {"food": ("1101000", "食料品"), "outside": ("1111000", "外食・宿泊"), "transport": ("1107000", "交通"), "communication": ("1108000", "通信"), "housing": ("9060000", "住居・光熱"),
        "goods": ("1103000;1105000", "衣類・家具・日用品"), "health": ("9080000", "医療"), "recreation": ("9110000", "娯楽・文化"), "education": ("9120000", "教育"), "other": ("1102000;9140000", "酒・たばこ・その他")}
MINE = {"食料品": "food", "コンビニ": "food", "外食・カフェ": "outside", "旅行・宿泊": "outside", "交通": "transport", "通信": "communication", "サブスク・ツール": "communication",
        "買い物": "goods", "日用品・薬": "goods", "医療": "health", "娯楽・文化": "recreation", "学び": "education", "手続き・手数料": "other"}
STALE_DAYS = 30
MONTHS = 12
TRIES = 4


def get_json(path):
    """The connection sometimes breaks mid-answer, and that is worth another try."""
    for attempt in range(TRIES):
        try:
            with urllib.request.urlopen(urllib.request.Request(API + path, headers={"User-Agent": "tatsuki-desk"}), timeout=60) as r:
                return json.load(r)
        except (http.client.HTTPException, OSError, ValueError):
            if attempt == TRIES - 1:
                raise OSError("no answer") from None
            time.sleep(2 * (attempt + 1))


def fetch_series(get, series, classification):
    """{country: value} for one or more series, added together. A country missing any part stays missing."""
    out, parts = {}, series.split(";")
    for part in parts:
        data = get(f"/sources/90/country/all/series/{part}/classification/{classification}/time/YR2021/data?format=json&per_page=1000")
        for row in data["source"]["data"]:
            if row["value"] is None:
                continue
            code = next(v["id"] for v in row["variable"] if v["concept"] == "Country")
            out.setdefault(code, []).append(row["value"])
    return {code: sum(values) for code, values in out.items() if len(values) == len(parts)}


def fetch(get=get_json):
    """The whole table: only places that are countries, each with its own two-letter code for naming it in Japanese."""
    places = get("/country?format=json&per_page=400")[1]
    real = {p["id"]: p["iso2Code"] for p in places if p["region"]["value"] != "Aggregates"}
    totals = fetch_series(get, TOTAL, "PCAP.CD")
    ppp = fetch_series(get, TOTAL, "PCAP.PP")
    uses = {key: fetch_series(get, series, "PCAP.CD") for key, (series, _) in USES.items()}
    countries = {}
    for code, iso2 in real.items():
        if code in totals:
            countries[code] = {"iso2": iso2, "month": round(totals[code] / MONTHS, 2), "monthPpp": round(ppp[code] / MONTHS, 2) if code in ppp else None,
                               "uses": {key: round(values[code] / totals[code] * 100, 1) for key, values in uses.items() if code in values}}
    return countries


def update(get=get_json):
    """Keeps the old table when the network says no."""
    try:
        countries = fetch(get)
    except (OSError, ValueError, LookupError, TypeError):
        return False
    if not countries:
        return False
    wise_sync.write_json("world.json", {"at": int(time.time() * 1000), "countries": countries})
    return True


def update_if_stale():
    saved = wise_sync.read_json("world.json", {})
    if time.time() * 1000 - saved.get("at", 0) > STALE_DAYS * 86400000:
        update()


def mine(rows, today):
    """The person's own last twelve finished months, as a month's average and as shares by the same uses."""
    current = today[:7]
    months = {wise_sync.month_add(current, -n) for n in range(1, MONTHS + 1)}
    here = [a for a in rows if a.get("kind") and a.get("usd") is not None and (a.get("date") or "")[:7] in months]
    total = wise_analysis.spent_total(here)
    uses, unknown = {}, 0
    for a in here:
        amount = wise_analysis.spent_amount(a)
        key = MINE.get(a["category"])
        if key:
            uses[key] = uses.get(key, 0) + amount
        else:
            unknown += amount
    share = lambda v: round(v / total * 100, 1) if total > 0 else 0
    return {"month": round(total / MONTHS, 2), "uses": {k: share(v) for k, v in uses.items()}, "unknownShare": share(unknown)}


def view(rows, today):
    saved = wise_sync.read_json("world.json", {})
    return {"at": saved.get("at"), "countries": saved.get("countries") or {}, "source": SOURCE, "useNames": {k: name for k, (_, name) in USES.items()}, "me": mine(rows, today)}


if __name__ == "__main__":
    ok = update()
    print("ok" if ok else "stopped: 統計を取れませんでした", len(wise_sync.read_json("world.json", {}).get("countries") or {}), "countries", flush=True)
    sys.exit(0 if ok else 1)
