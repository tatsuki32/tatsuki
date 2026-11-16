#!/usr/bin/env python3
"""Tatsuki's Desk — Footprint: a rough daily CO2e estimate from records Desk already keeps.

Five parts, each read from a file that is already here (nothing new is collected, nothing is sent anywhere):
  digital  Mac screen time (desk/data/screentime/days.json) x laptop power x Vietnam's grid, plus the network
  ai       Claude tokens (~/ClaudeUsage/ledger.json, then desk/data/claude-usage/cache.json for the days after it)
  food     meal photos (desk/data/body/records.json): each item's grams x a per-kg factor
  spend    Wise spending by category (wise_report), minus what food and travel already count
  travel   trips typed in on the Footprint page (desk/data/footprint/trips.json)

Every number is a range (low, mid, high). Every factor comes from desk/footprint_factors.json, which names the
page it was read on; a factor that is not in that file is never used, and its part shows "No data".
No AI is used here: the one-line remarks are fixed sentences with the numbers put in.

  python3 footprint.py            yesterday and the last 7 days, printed
  python3 footprint.py view 30    what the page shows
"""
import datetime as dt
import json
import math
import os
import re
import sys
import tempfile
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
DIR = os.environ.get("FOOTPRINT_DIR") or os.path.join(DATA, "footprint")
FACTORS = os.path.join(ROOT, "footprint_factors.json")
LEDGER = os.path.expanduser("~/ClaudeUsage/ledger.json")
HCMC = dt.timezone(dt.timedelta(hours=7))
PARTS = ("digital", "ai", "food", "spend", "travel")
NAMES = {"digital": "Screens", "ai": "AI", "food": "Food", "spend": "Shopping & services", "travel": "Travel"}
MODES = ("flight", "car", "taxi", "motorbike", "bus", "train", "walk")
MAX_TRIPS = 2000
CACHE_SEC = 60

# Token weights relative to one output token, taken from Anthropic's list prices (claude.com/pricing):
# input costs 1/5 of output, a cache write 1.25x (5 min) or 2x (1 hour) of input, a cache read 1/10 of input.
# Price is used as a stand-in for compute; the real ratio is not published. Low and high widen it.
TOKEN_WEIGHT = {"inp": (0.1, 0.2, 0.4), "cw5": (0.12, 0.25, 0.5), "cw1": (0.2, 0.4, 0.8), "cr": (0.005, 0.02, 0.05)}
PRICING_URL = "https://claude.com/pricing"

# Food words (English and Japanese, as the meal reader writes them) -> factor key. First match wins, so the
# more specific words come first ("beef" before "noodle" catches pho bo as beef).
FOOD_WORDS = (
    ("food_beef", ("beef", "steak", "wagyu", "bulgogi", "brisket", "burger", "牛", "ビーフ", "ステーキ", "bò", "pho bo", "phở bò")),
    ("food_lamb", ("lamb", "mutton", "羊", "ラム", "マトン")),
    ("food_prawns", ("prawn", "shrimp", "えび", "エビ", "海老", "tôm")),
    ("food_cheese", ("cheese", "チーズ")),
    ("food_pork", ("pork", "bacon", "ham", "sausage", "samgyeopsal", "豚", "ポーク", "ベーコン", "ハム", "ソーセージ", "heo", "lợn", "chả")),
    ("food_chicken", ("chicken", "poultry", "duck", "turkey", "鶏", "チキン", "とり", "鴨", "gà")),
    ("food_fish", ("fish", "salmon", "tuna", "mackerel", "sashimi", "sushi", "oyster", "squid", "octopus", "clam", "crab",
                   "魚", "鮭", "サーモン", "まぐろ", "マグロ", "鯖", "刺身", "寿司", "牡蠣", "いか", "イカ", "たこ", "タコ", "あさり", "蟹", "かに", "cá")),
    ("food_eggs", ("egg", "卵", "たまご", "玉子", "trứng")),
    ("food_tofu", ("tofu", "豆腐", "納豆", "natto", "đậu phụ")),
    ("food_beans", ("bean", "lentil", "chickpea", "edamame", "豆", "枝豆")),
    ("food_milk", ("milk", "yogurt", "yoghurt", "latte", "牛乳", "ミルク", "ヨーグルト", "ラテ", "sữa")),
    ("food_rice", ("rice", "risotto", "ご飯", "ごはん", "米", "飯", "おにぎり", "cơm")),
    ("food_wheat", ("bread", "noodle", "pasta", "ramen", "udon", "pho", "banh mi", "bánh mì", "toast", "sandwich", "pizza", "flour",
                    "パン", "麺", "めん", "ラーメン", "うどん", "パスタ", "そば", "トースト", "サンド", "ピザ", "phở", "bún", "mì")),
    ("food_potatoes", ("potato", "fries", "じゃがいも", "ポテト", "芋", "いも", "khoai")),
    ("food_nuts", ("nut", "almond", "cashew", "peanut", "walnut", "ナッツ", "アーモンド", "くるみ", "落花生", "ピーナッツ")),
    ("food_chocolate", ("chocolate", "cocoa", "チョコ", "ココア")),
    ("food_coffee", ("coffee", "espresso", "コーヒー", "珈琲", "cà phê")),
    ("food_sugar", ("sugar", "soda", "cola", "7up", "sprite", "juice", "candied", "candy", "砂糖", "ジュース", "コーラ", "飴")),
    ("food_oil", ("oil", "butter", "mayonnaise", "油", "バター", "マヨ")),
    ("food_fruit", ("fruit", "apple", "banana", "orange", "mango", "lemon", "lime", "grape", "berry", "melon", "pineapple", "papaya", "watermelon",
                    "果物", "りんご", "バナナ", "みかん", "オレンジ", "マンゴー", "レモン", "ぶどう", "いちご", "メロン", "パイナップル")),
    ("food_vegetables", ("vegetable", "salad", "lettuce", "cabbage", "kimchi", "cucumber", "carrot", "tomato", "onion", "garlic", "pepper", "chili",
                         "spinach", "broccoli", "mushroom", "herb", "seaweed", "野菜", "サラダ", "レタス", "キャベツ", "キムチ", "きゅうり", "にんじん",
                         "人参", "トマト", "玉ねぎ", "ねぎ", "にんにく", "唐辛子", "ほうれん草", "ブロッコリー", "きのこ", "海藻", "わかめ", "rau")),
)
GRAMS = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|ml|l|グラム|ミリリットル)\b", re.IGNORECASE)

# Wise categories -> spend factor. Food and transport are counted here only on days the other parts have nothing.
SPEND_MAP = {"外食・カフェ": "spend_restaurant", "食料品": "spend_grocery", "コンビニ": "spend_grocery",
             "交通": "spend_taxi", "旅行・宿泊": "spend_hotel", "買い物": "spend_clothing", "日用品・薬": "spend_services",
             "医療": "spend_services", "娯楽・文化": "spend_services", "学び": "spend_services",
             "通信": "spend_digital", "サブスク・ツール": "spend_digital"}
FOOD_SPEND = ("外食・カフェ", "食料品", "コンビニ")
AIRLINE = re.compile(r"airline|airways|air asia|airasia|vietjet|bamboo|jetstar|scoot|lion air|cebu|peach|zipair|\bair\b|航空", re.IGNORECASE)

# Airports by IATA code: (latitude, longitude). Great-circle distance is enough for a flight's estimate.
AIRPORTS = {
    "SGN": (10.818, 106.652), "HAN": (21.221, 105.807), "DAD": (16.044, 108.199), "CXR": (11.998, 109.219), "PQC": (10.170, 103.993),
    "HPH": (20.819, 106.725), "VCA": (10.085, 105.712), "HUI": (16.401, 107.703), "DLI": (11.750, 108.367), "VII": (18.737, 105.671),
    "UIH": (13.955, 109.042), "BMV": (12.668, 108.120), "VDO": (21.117, 107.414), "THD": (19.902, 105.468),
    "BKK": (13.690, 100.750), "DMK": (13.913, 100.607), "HKT": (8.113, 98.317), "CNX": (18.767, 98.963), "SIN": (1.364, 103.991),
    "KUL": (2.746, 101.710), "PEN": (5.297, 100.277), "CGK": (-6.126, 106.656), "DPS": (-8.748, 115.167), "MNL": (14.509, 121.020),
    "CEB": (10.307, 123.979), "PNH": (11.547, 104.844), "KTI": (11.395, 104.564), "REP": (13.411, 103.813), "VTE": (17.988, 102.563),
    "LPQ": (19.898, 102.161), "RGN": (16.907, 96.133), "HKG": (22.308, 113.918), "MFM": (22.150, 113.592), "TPE": (25.078, 121.233),
    "CAN": (23.392, 113.299), "PVG": (31.143, 121.805), "PEK": (40.080, 116.585), "PKX": (39.509, 116.411), "ICN": (37.460, 126.441),
    "GMP": (37.558, 126.791), "PUS": (35.180, 128.938), "NRT": (35.765, 140.386), "HND": (35.549, 139.780), "KIX": (34.427, 135.244),
    "ITM": (34.785, 135.438), "NGO": (34.858, 136.805), "FUK": (33.586, 130.451), "CTS": (42.775, 141.692), "OKA": (26.196, 127.646),
    "DEL": (28.556, 77.100), "BOM": (19.089, 72.868), "BLR": (13.199, 77.706), "CMB": (7.180, 79.884), "KTM": (27.697, 85.359),
    "DAC": (23.843, 90.398), "DXB": (25.253, 55.364), "AUH": (24.433, 54.651), "DOH": (25.273, 51.608), "IST": (41.262, 28.742),
    "SVO": (55.973, 37.415), "DME": (55.409, 37.906), "LED": (59.800, 30.263), "TAS": (41.258, 69.281), "ALA": (43.352, 77.040),
    "CDG": (49.010, 2.548), "LHR": (51.470, -0.454), "FRA": (50.037, 8.562), "AMS": (52.311, 4.768), "MUC": (48.354, 11.786),
    "FCO": (41.800, 12.239), "MAD": (40.472, -3.561), "BCN": (41.297, 2.078), "ZRH": (47.465, 8.549), "VIE": (48.110, 16.570),
    "HEL": (60.317, 24.963), "CPH": (55.618, 12.656), "WAW": (52.166, 20.967), "PRG": (50.101, 14.260), "BER": (52.366, 13.503),
    "JFK": (40.641, -73.778), "LAX": (33.942, -118.408), "SFO": (37.621, -122.379), "SEA": (47.450, -122.309), "ORD": (41.974, -87.907),
    "YVR": (49.195, -123.184), "YYZ": (43.677, -79.625), "HNL": (21.319, -157.922), "SYD": (-33.940, 151.175), "MEL": (-37.669, 144.841),
    "AKL": (-37.008, 174.792), "PER": (-31.940, 115.967), "BNE": (-27.384, 153.117), "ADD": (8.978, 38.799), "NBO": (-1.319, 36.928),
    "JNB": (-26.139, 28.246), "CAI": (30.112, 31.400), "GRU": (-23.435, -46.473), "MEX": (19.436, -99.072),
}
SHORT_HAUL_KM = 3700  # UK DESNZ: international flights under 3,700 km count as short haul

_lock = threading.Lock()
_cache = {}


# ---------- small helpers ----------

def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(name, value):
    os.makedirs(DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DIR, prefix=".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(DIR, name))


def today_hcmc():
    return dt.datetime.now(HCMC).date()


def days_back(n, today=None):
    end = today or today_hcmc()
    return [(end - dt.timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def factors():
    """{key: {value, low, high, unit, source, url, note}} — only rows that name where they came from."""
    raw = read_json(FACTORS, {}).get("factors", {})
    return {k: v for k, v in raw.items() if isinstance(v, dict) and v.get("url") and isinstance(v.get("value"), (int, float))}


def rng(f, key):
    """(low, mid, high) of one factor; low/high fall back to the middle value."""
    v = f[key]
    return (v.get("low", v["value"]), v["value"], v.get("high", v["value"]))


def mul(a, b):
    return tuple(x * y for x, y in zip(a, b))


def add(a, b):
    return tuple(x + y for x, y in zip(a, b))


ZERO = (0.0, 0.0, 0.0)


def cite(f, key):
    v = f[key]
    return {"key": key, "value": v["value"], "low": v.get("low"), "high": v.get("high"), "unit": v.get("unit", ""),
            "source": v.get("source", ""), "url": v["url"], "note": v.get("note", "")}


# ---------- the five parts ----------

def digital(dates, f):
    """Screen hours on the Mac x laptop watts x Vietnam's grid, plus the network and data centres behind them."""
    need = ("mac_laptop_w", "grid_vn")
    if any(k not in f for k in need):
        return {}, [], None
    days = read_json(os.path.join(DATA, "screentime", "days.json"), {})
    net = rng(f, "digital_kwh_per_hour") if "digital_kwh_per_hour" in f else ZERO
    watts, grid = rng(f, "mac_laptop_w"), rng(f, "grid_vn")
    out = {}
    for d in dates:
        apps = ((days.get(d) or {}).get("mac") or {}).get("apps") or {}
        sec = sum(v for v in apps.values() if isinstance(v, (int, float)))
        if not sec:
            continue
        hours = sec / 3600
        device_kwh = tuple(w / 1000 * hours for w in watts)
        net_kwh = tuple(n * hours for n in net)
        out[d] = {"kg": add(mul(device_kwh, grid), mul(net_kwh, grid)), "amount": round(hours, 2), "unit": "hours"}
    used = [k for k in ("mac_laptop_w", "digital_kwh_per_hour", "grid_vn") if k in f]
    return out, used, "Screen time on this Mac only (the phone is not counted yet). Network and data centres are counted at the same grid."


def ai_tokens(dates):
    """{date: {inp, out, cw5, cw1, cr}} — the daily ledger first, then the per-message cache for days after it."""
    want = set(dates)
    out = {}
    led = read_json(LEDGER, {})
    for r in led.get("rows") or []:
        d = r.get("day")
        if d in want:
            t = out.setdefault(d, {"inp": 0, "out": 0, "cw5": 0, "cw1": 0, "cr": 0})
            for k in t:
                t[k] += r.get(k) or 0
    done = set(out)
    cache = read_json(os.path.join(DATA, "claude-usage", "cache.json"), {})
    seen = set()
    for ent in (cache.get("files") or {}).values():
        for mid, r in (ent.get("msgs") or {}).items():
            if mid in seen or not isinstance(r, list) or len(r) < 7:
                continue
            seen.add(mid)
            d = dt.datetime.fromtimestamp(r[0], HCMC).date().isoformat()
            if d not in want or d in done:
                continue
            t = out.setdefault(d, {"inp": 0, "out": 0, "cw5": 0, "cw1": 0, "cr": 0})
            for k, i in (("inp", 2), ("out", 3), ("cw5", 4), ("cw1", 5), ("cr", 6)):
                t[k] += r[i] or 0
    return out


def ai(dates, f):
    """Output-token equivalents x Wh per token x data-centre overhead x the US grid."""
    need = ("llm_wh_per_1k_output_tokens", "us_grid")
    if any(k not in f for k in need):
        return {}, [], None
    wh = rng(f, "llm_wh_per_1k_output_tokens")
    pue = rng(f, "datacenter_pue") if "datacenter_pue" in f else (1.0, 1.0, 1.0)
    grid = rng(f, "us_grid")
    out = {}
    for d, t in ai_tokens(dates).items():
        eq = tuple(t["out"] + sum(t[k] * TOKEN_WEIGHT[k][i] for k in TOKEN_WEIGHT) for i in range(3))
        if not any(eq):
            continue
        kwh = tuple(eq[i] / 1000 * wh[i] / 1000 * pue[i] for i in range(3))
        total = t["inp"] + t["out"] + t["cw5"] + t["cw1"] + t["cr"]
        out[d] = {"kg": mul(kwh, grid), "amount": round(total / 1e6, 1), "unit": "million tokens",
                  "cacheShare": round(t["cr"] / total, 3) if total else 0}
    used = [k for k in ("llm_wh_per_1k_output_tokens", "datacenter_pue", "us_grid") if k in f]
    note = ("Claude does not publish energy per token. The range is wide on purpose. Input and cached tokens are weighted "
            "against output tokens by Anthropic's list prices (input 1/5, cache read 1/50 of an output token).")
    return out, used, note


def grams_of(item):
    m = GRAMS.search(item.replace("約", " ").replace("about", " "))
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2).lower()
    return n * 1000 if unit in ("kg", "l") else n


SIDE_WORDS = ("broth", "soup", "sauce", "dip", "dressing", "stock", "paste", "スープ", "たれ", "タレ", "汁", "ソース", "nước")
WITH = re.compile(r"\bwith\b|入り|と\s", re.IGNORECASE)
WITH_SHARE = 0.25  # "pancakes with shrimp": the named food is a small part of the weight
DRINK_WORDS = ("soda", "cola", "7up", "sprite", "juice", "ジュース", "コーラ")
DRINK_SUGAR = 0.1  # a soft drink is about a tenth sugar by weight; only the sugar is counted


def food_key(text):
    low = text.lower()
    for key, words in FOOD_WORDS:
        if any(w.lower() in low for w in words):
            return key
    return None


def food_share(text, key):
    """How much of the item's weight is the food it names: none for sauces and broths, a quarter when it comes 'with' it."""
    low = text.lower()
    if any(w in low for w in SIDE_WORDS) and key != "food_sugar":
        return 0
    if key == "food_sugar" and any(w in low for w in DRINK_WORDS):
        return DRINK_SUGAR
    cut = WITH.search(low)
    if cut and key:
        head = low[:cut.start()]
        if not any(w.lower() in head for w in dict(FOOD_WORDS)[key]):
            return WITH_SHARE
    return 1


def food(dates, f):
    """Each meal-photo item: grams x the factor of the food it names. Items without grams or a known food are listed, not guessed."""
    rec = read_json(os.path.join(DATA, "body", "records.json"), {})
    want = set(dates)
    out, missed, used = {}, {}, set()
    for r in rec.values():
        if not isinstance(r, dict) or r.get("kind") != "meal_photo" or r.get("date") not in want or r.get("deleted") or r.get("status") == "rejected":
            continue
        d = r["date"]
        day = out.setdefault(d, {"kg": ZERO, "amount": 0, "unit": "meals", "top": {}})
        day["amount"] += 1
        for item in r.get("items") or []:
            if not isinstance(item, str):
                continue
            g, key = grams_of(item), food_key(item)
            if not g or not key or key not in f:
                missed[d] = missed.get(d, 0) + 1
                continue
            share = food_share(item, key)
            if not share:
                continue
            kg = tuple(g * share / 1000 * x for x in rng(f, key))
            day["kg"] = add(day["kg"], kg)
            day["top"][key] = day["top"].get(key, 0) + kg[1]
            used.add(key)
    for d, day in out.items():
        day["missed"] = missed.get(d, 0)
    note = ("From meal photos only. Weights are the photo reader's estimates. Sauces and broths are left out; in a dish 'with' something, "
            "that thing counts as a quarter of the weight. Items with no weight or an unknown food are left out, not guessed.")
    return out, sorted(used), note


def trips():
    rows = read_json(os.path.join(DIR, "trips.json"), [])
    return [t for t in rows if isinstance(t, dict) and not t.get("deleted")] if isinstance(rows, list) else []


def flight_km(a, b):
    p, q = AIRPORTS.get((a or "").upper()), AIRPORTS.get((b or "").upper())
    if not p or not q:
        return None
    la1, lo1, la2, lo2 = map(math.radians, (p[0], p[1], q[0], q[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return round(2 * 6371 * math.asin(math.sqrt(h)))


MODE_FACTOR = {"car": "car_kg_per_km", "taxi": "car_kg_per_km", "motorbike": "motorbike_kg_per_km", "bus": "bus_kg_per_pkm", "train": "rail_kg_per_pkm"}


def trip_kg(t, f):
    km = t.get("km") or 0
    if t.get("mode") == "flight":
        key = "flight_long_kg_per_pkm" if km >= SHORT_HAUL_KM else "flight_short_kg_per_pkm"
    elif t.get("mode") == "walk":
        return ZERO, None
    else:
        key = MODE_FACTOR.get(t.get("mode"))
    if not key or key not in f:
        return None, key
    return tuple(km * x for x in rng(f, key)), key


def travel(dates, f):
    want = set(dates)
    out, used = {}, set()
    for t in trips():
        if t.get("date") not in want:
            continue
        kg, key = trip_kg(t, f)
        if kg is None:
            continue
        if key:
            used.add(key)
        day = out.setdefault(t["date"], {"kg": ZERO, "amount": 0, "unit": "km", "modes": []})
        day["kg"] = add(day["kg"], kg)
        day["amount"] += t.get("km") or 0
        day["modes"].append(t.get("mode"))
    note = "Typed in on this page. Flights use the great-circle distance between the two airports; flights include the extra warming of contrails where the source counts it."
    return out, sorted(used), note


def spend(dates, f, food_days, travel_days):
    """Wise spending x kg CO2e per US dollar. Food categories only on days with no meal photos; transport only on days
    with no trips typed in; airline tickets never (add the flight as a trip instead)."""
    try:
        import wise_report
        import wise_sync
        import wise_analysis
        rows = wise_report.labelled(wise_sync.summary(start=dates[0], end=dates[-1], today=dates[-1])["items"])
    except Exception:  # no Wise files on this Mac yet
        return {}, [], None, 0
    out, used, flights = {}, set(), 0
    for a in rows:
        d, cat = a.get("date"), a.get("category")
        usd = wise_analysis.spent_amount(a)
        key = SPEND_MAP.get(cat)
        if d not in dates or not usd or not key or key not in f:
            continue
        if cat in FOOD_SPEND and d in food_days:
            continue
        if cat == "交通" and d in travel_days:
            continue
        if cat in ("交通", "旅行・宿泊") and AIRLINE.search(a.get("title") or ""):
            flights += 1
            continue
        day = out.setdefault(d, {"kg": ZERO, "amount": 0, "unit": "USD"})
        day["kg"] = add(day["kg"], tuple(usd * x for x in rng(f, key)))
        day["amount"] += usd
        used.add(key)
    for day in out.values():
        day["amount"] = round(day["amount"], 2)
    note = ("Spending in dollars x US average factors per dollar. Prices in Vietnam are lower, so this tends to count low. "
            "Meals and rides are left out on days the Food and Travel parts already count them.")
    return out, sorted(used), note, flights


# ---------- comparisons and the one-liner ----------

def cow_hours(kg, f):
    return kg / f["cow_kg_per_day"]["value"] * 24 if "cow_kg_per_day" in f and kg else None


def compare(kg, days, f):
    """Plain comparisons for the middle estimate over the period."""
    out = []
    per_day = kg / days if days else 0

    def row(key, label, value, unit):
        if key in f and value is not None:
            out.append({"key": key, "label": label, "value": value, "unit": unit, "source": f[key].get("source", ""), "url": f[key]["url"]})
    if "cow_kg_per_day" in f:
        row("cow_kg_per_day", "One cow's burps and digestion", kg / f["cow_kg_per_day"]["value"], "cow-days")
    if "sheep_kg_per_day" in f:
        row("sheep_kg_per_day", "One sheep", kg / f["sheep_kg_per_day"]["value"], "sheep-days")
    if "vietnam_per_capita_kg_per_day" in f:
        row("vietnam_per_capita_kg_per_day", "An average person in Vietnam, per day", f["vietnam_per_capita_kg_per_day"]["value"], "kg")
    if "world_per_capita_kg_per_day" in f:
        row("world_per_capita_kg_per_day", "An average person in the world, per day", f["world_per_capita_kg_per_day"]["value"], "kg")
    if "tree_kg_per_year" in f:
        row("tree_kg_per_year", "Trees needed for a year to absorb this", kg / f["tree_kg_per_year"]["value"], "tree-years")
    if "car_kg_per_km" in f:
        row("car_kg_per_km", "The same as driving a petrol car", kg / f["car_kg_per_km"]["value"], "km")
    return {"perDay": per_day, "rows": out}


def fmt_time(hours):
    if hours < 1:
        return f"{max(1, round(hours * 60))} minutes"
    if hours < 48:
        return f"{hours:.0f} hours" if hours >= 10 else f"{hours:.1f} hours"
    return f"{hours / 24:.0f} days"


def quip(parts, total, days, f, flights_skipped):
    """One fixed sentence with numbers put in. Aimed at what convenience costs, never at the reader."""
    if not total:
        return "Nothing counted yet. Either a very green day, or the records haven't arrived."
    top = max(parts, key=lambda p: parts[p]["kg"][1]) if parts else None
    share = parts[top]["kg"][1] / total if top else 0
    cow = cow_hours(parts[top]["kg"][1], f) if top else None
    cow_txt = fmt_time(cow) if cow else None
    if top == "travel" and "flight" in parts["travel"].get("modes", []) and share > 0.5:
        return f"One flight. Everything else in this period was a rounding error ({share:.0%} of the total)."
    if top == "ai" and cow_txt:
        return f"Your AI helpers burped as much as a cow does in {cow_txt}. The cow, at least, makes milk."
    if top == "digital" and cow_txt:
        return f"Staring at screens: the same as {cow_txt} of a cow. Neither of you moved much."
    if top == "food" and cow_txt:
        top_foods = parts["food"].get("top") or []
        if top_foods and top_foods[0]["food"] == "beef":
            return f"Dinner had a past: the beef alone outweighed a cow's own {cow_txt} of burping."
        return f"Your meals alone equal {cow_txt} of a cow. Delicious, though."
    if top == "spend" and cow_txt:
        return f"Things you paid for were made somewhere. The shopping alone equals {cow_txt} of a cow."
    if "sheep_kg_per_day" in f and total / max(days, 1) < f["sheep_kg_per_day"]["value"]:
        return "A sheep would be proud. Barely."
    return f"{total:.1f} kg of CO2e. Invisible, odourless, and still there."


# ---------- the page ----------

def compute(period="7", today=None):
    n = {"today": 1, "1": 1, "week": 7, "7": 7, "30": 30, "90": 90}.get(str(period), 7)
    dates = days_back(n, today)
    f = factors()
    dig, dig_used, dig_note = digital(dates, f)
    a, a_used, a_note = ai(dates, f)
    fo, fo_used, fo_note = food(dates, f)
    tr, tr_used, tr_note = travel(dates, f)
    sp, sp_used, sp_note, flights = spend(dates, f, set(fo), set(tr))
    by = {"digital": (dig, dig_used, dig_note), "ai": (a, a_used, a_note), "food": (fo, fo_used, fo_note),
          "spend": (sp, sp_used, sp_note), "travel": (tr, tr_used, tr_note)}
    parts, days_out = {}, []
    for d in dates:
        row = {"date": d}
        for p, (data, _, _) in by.items():
            if d in data:
                row[p] = round(data[d]["kg"][1], 3)
        row["total"] = round(sum(row.get(p, 0) for p in PARTS), 3)
        days_out.append(row)
    for p, (data, used, note) in by.items():
        if note is None:
            parts[p] = {"name": NAMES[p], "missing": "factors", "kg": ZERO}
            continue
        kg = ZERO
        amount = 0
        extra = {}
        for d in dates:
            if d in data:
                kg = add(kg, data[d]["kg"])
                amount += data[d]["amount"]
                for k, v in (data[d].get("top") or {}).items():
                    extra[k] = extra.get(k, 0) + v
        unit = next((data[d]["unit"] for d in dates if d in data), "")
        info = {"name": NAMES[p], "kg": tuple(round(x, 3) for x in kg), "amount": round(amount, 2), "unit": unit, "days": sum(1 for d in dates if d in data),
                "note": note, "factors": [cite(f, k) for k in used]}
        if p == "ai":
            info["factors"].append({"key": "pricing", "source": "Anthropic list prices (token weights)", "url": PRICING_URL, "unit": "", "value": None, "note": "input 1/5, cache read 1/50 of an output token"})
        if p == "food":
            info["missed"] = sum(data[d].get("missed", 0) for d in dates if d in data)
            info["top"] = sorted(({"food": k.replace("food_", ""), "kg": round(v, 2)} for k, v in extra.items()), key=lambda x: -x["kg"])[:5]
        if p == "travel":
            info["modes"] = [m for d in dates if d in data for m in data[d]["modes"]]
        if p == "spend":
            info["flightsSkipped"] = flights
        parts[p] = info
    counted = {p: v for p, v in parts.items() if not v.get("missing") and v["kg"][1] > 0}
    total = tuple(round(sum(v["kg"][i] for v in counted.values()), 3) for i in range(3))
    return {
        "period": str(period), "days": n, "from": dates[0], "to": dates[-1], "dayRows": days_out,
        "parts": parts, "order": sorted(counted, key=lambda p: -counted[p]["kg"][1]),
        "total": total, "cowHours": cow_hours(total[1], f), "cowKgPerDay": f["cow_kg_per_day"]["value"] if "cow_kg_per_day" in f else None,
        "compare": compare(total[1], n, f), "quip": quip(counted, total[1], n, f, parts.get("spend", {}).get("flightsSkipped", 0)),
        "trips": sorted(trips(), key=lambda t: t.get("date", ""), reverse=True)[:30] if n >= 7 else [],
        "factorsAsOf": read_json(FACTORS, {}).get("asOf"),
        "notCounted": "Home electricity and air conditioning, the phone, and anything paid in cash are not counted.",
        "modes": MODES, "airports": sorted(AIRPORTS),
    }


def view(period="7"):
    key = str(period)
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_SEC:
            return hit[1]
    out = compute(period)
    with _lock:
        _cache[key] = (time.time(), out)
    return out


def home():
    """Yesterday, for one line on Home."""
    y = (today_hcmc() - dt.timedelta(days=1))
    data = compute("1", today=y)
    return {"date": y.isoformat(), "kg": data["total"][1], "cowHours": data["cowHours"], "top": data["order"][0] if data["order"] else None}


def put_trip(trip_id, rec):
    """Adds, changes or removes (deleted:true) one trip. Returns (status, body)."""
    if not re.fullmatch(r"[a-z0-9-]{1,40}", trip_id or ""):
        return 400, {"error": "bad id"}
    rows = read_json(os.path.join(DIR, "trips.json"), [])
    rows = rows if isinstance(rows, list) else []
    old = next((t for t in rows if t.get("id") == trip_id), None)
    if rec.get("deleted") is True:
        if old:
            old["deleted"] = True
            old["updatedAt"] = int(time.time() * 1000)
    else:
        date, mode = str(rec.get("date") or ""), rec.get("mode")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or mode not in MODES:
            return 400, {"error": "date and mode are needed"}
        frm, to = str(rec.get("from") or "").strip().upper()[:4], str(rec.get("to") or "").strip().upper()[:4]
        km = None
        if mode == "flight" and frm and to:
            km = flight_km(frm, to)
            if km is None:
                return 400, {"error": "Unknown airport code. Type the distance in km instead."}
        if km is None:
            try:
                km = float(rec.get("km"))
            except (TypeError, ValueError):
                return 400, {"error": "Type the distance in km, or two airport codes for a flight."}
        if not 0 < km <= 40000:
            return 400, {"error": "The distance looks wrong."}
        km = km * (2 if rec.get("roundTrip") is True else 1)
        row = {"id": trip_id, "date": date, "mode": mode, "from": frm, "to": to, "km": round(km, 1),
               "roundTrip": rec.get("roundTrip") is True, "note": str(rec.get("note") or "")[:80], "updatedAt": int(time.time() * 1000)}
        if old:
            old.clear()
            old.update(row)
        else:
            if len(rows) >= MAX_TRIPS:
                return 400, {"error": "too many trips"}
            rows.append(row)
    write_json("trips.json", rows)
    with _lock:
        _cache.clear()
    return 200, {"ok": True}


def new_id():
    return "t-" + uuid.uuid4().hex[:12]


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "view":
        print(json.dumps(compute(sys.argv[2] if len(sys.argv) > 2 else "7"), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(home(), ensure_ascii=False))
        v = compute("7")
        print(v["total"], v["quip"])
        for p in v["order"]:
            print(p, v["parts"][p]["kg"])
