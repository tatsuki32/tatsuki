#!/usr/bin/env python3
"""Tatsuki's Desk — other countries for comparison: public statistics only. No statement line ever leaves this Mac.

    python3 desk/wise_world.py    fetch the World Bank's International Comparison Program data
"""
import http.client
import json
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser

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
WAGE_API = "https://en.wikipedia.org/w/api.php?action=parse&page=List_of_countries_by_minimum_wage&prop=text&format=json"
WAGE_SOURCE = {"name": "Wikipedia: List of countries by minimum wage", "url": "https://en.wikipedia.org/wiki/List_of_countries_by_minimum_wage", "note": "各国の法令や ILO をまとめた一覧で、公的統計ではありません。更新不足の注意書きがあります"}
WAGE_NAMES = {"Vietnam": "Viet Nam", "South Korea": "Korea, Rep.", "Egypt": "Egypt, Arab Rep.", "Russia": "Russian Federation", "Iran": "Iran, Islamic Rep.", "Turkey": "Turkiye", "The Bahamas": "Bahamas, The", "The Gambia": "Gambia, The", "Gambia": "Gambia, The",
              "Yemen": "Yemen, Rep.", "Venezuela": "Venezuela, RB", "Kyrgyzstan": "Kyrgyz Republic", "Laos": "Lao PDR", "Slovakia": "Slovak Republic", "Czech Republic": "Czechia", "Syria": "Syrian Arab Republic", "Hong Kong": "Hong Kong SAR, China", "Macau": "Macao SAR, China",
              "Democratic Republic of the Congo": "Congo, Dem. Rep.", "Republic of the Congo": "Congo, Rep.", "Ivory Coast": "Cote d'Ivoire", "Cape Verde": "Cabo Verde", "East Timor": "Timor-Leste", "Swaziland": "Eswatini", "Micronesia": "Micronesia, Fed. Sts.", "Saint Lucia": "St. Lucia", "Saint Kitts and Nevis": "St. Kitts and Nevis", "Saint Vincent and the Grenadines": "St. Vincent and the Grenadines", "Taiwan": "Taiwan, China"}
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


class WageTable(HTMLParser):
    """The rows of Wikipedia's minimum wage table: nine cells, footnotes left out."""

    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell, self.skip = [], None, None, 0

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = ""
        elif tag in ("sup", "style"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("sup", "style"):
            self.skip -= 1
        elif tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(" ".join(self.cell.split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None and not self.skip:
            self.cell += data


def wage_number(text):
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def parse_wages(page, places):
    """{ISO3: {month, monthPpp, date}} from the rendered table. A name that cannot be matched to a country is left out."""
    table = WageTable()
    table.feed(page)
    by_name = {re.sub(r"[^a-z]", "", name.lower()): code for code, name in places.items()}
    out = {}
    for row in table.rows:
        if len(row) != 9:
            continue
        name = re.sub(r"[^a-z]", "", (WAGE_NAMES.get(row[0]) or row[0]).lower())
        code, yearly = by_name.get(name), wage_number(row[2])
        if not code or not yearly or code in out:
            continue
        ppp = wage_number(row[3])
        out[code] = {"month": round(yearly / MONTHS, 2), "monthPpp": round(ppp / MONTHS, 2) if ppp else None, "date": row[8]}
    return out


def fetch_wages(places):
    request = urllib.request.Request(WAGE_API, headers={"User-Agent": "tatsuki-desk"})
    with urllib.request.urlopen(request, timeout=60) as r:
        return parse_wages(json.load(r)["parse"]["text"]["*"], places)


def update(get=get_json, wage_fetch=None):
    """Keeps the old table when the network says no."""
    wage_fetch = wage_fetch or fetch_wages
    try:
        countries = fetch(get)
    except (OSError, ValueError, LookupError, TypeError):
        return False
    if not countries:
        return False
    saved = wise_sync.read_json("world.json", {})
    try:
        places = {p["id"]: p["name"] for p in get("/country?format=json&per_page=400")[1] if p["region"]["value"] != "Aggregates"}
        wages = wage_fetch(places) or saved.get("minwage") or {}
    except (OSError, ValueError, LookupError, TypeError, http.client.HTTPException):
        wages = saved.get("minwage") or {}
    wise_sync.write_json("world.json", {"at": int(time.time() * 1000), "countries": countries, "minwage": wages})
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
    return {"at": saved.get("at"), "countries": saved.get("countries") or {}, "source": SOURCE, "useNames": {k: name for k, (_, name) in USES.items()}, "me": mine(rows, today), "minwage": saved.get("minwage") or {}, "minwageSource": WAGE_SOURCE}


if __name__ == "__main__":
    ok = update()
    print("ok" if ok else "stopped: 統計を取れませんでした", len(wise_sync.read_json("world.json", {}).get("countries") or {}), "countries", flush=True)
    sys.exit(0 if ok else 1)
