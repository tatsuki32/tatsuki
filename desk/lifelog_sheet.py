#!/usr/bin/env python3
"""One-time import of the old "Life Dashboard" sheet (Aug–Sep 2026) into the Desk's body records (2026-11-20).

Save each tab of the sheet as CSV (File → Download → CSV) and run, on the Mac:

  python3 desk/lifelog_sheet.py <file.csv> [<file.csv> ...]       what would be imported (writes nothing)
  python3 desk/lifelog_sheet.py --write <file.csv> [...]          import

The tab is told by its header row: sleep, meal_daily, 体重 (weight and body composition), steps, 血糖値 (glucose).
What is fixed on the way in:
  - dates in four shapes (2026-08-09, 2026/08/09, 26/08/2026, 13/08/2026 17:00:00) become YYYY-MM-DD. A time of
    17:00:00 is midnight in Ho Chi Minh written in UTC, so that row is the next day.
  - a 0 is "not recorded", not zero; an empty row is skipped.
  - two rows for the same day: the later row wins.
  - sleep rows whose bedtime and wake time slid into the night-sleep and nap columns are put back.
  - every value goes through lifelog.check(), the same range checks as a photo; an odd one waits for review.
Each record's id is sheet-<tab>-<date>, so running it twice changes nothing. Nothing is written to the sheet.
The values are personal: the CSV files stay on the Mac and never go into the repository.
"""
import csv
import json
import os
import re
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lifelog as ll  # noqa: E402

SLEEP = {"総睡眠時間(分)": "total_min", "夜間睡眠時間(分)": "night_min", "昼寝時間(分)": "nap_min", "呼吸品質(点)": "breath_quality",
         "平均心拍(bpm)": "hr_avg", "平均HRV(ms)": "hrv_avg", "平均SpO2(%)": "spo2_avg", "平均呼吸数(brpm)": "breath_rate",
         "就寝時刻": "bedtime", "起床時刻": "wake_time"}
BODY = {"体重(kg)": "weight_kg", "BMI": "bmi", "体脂肪率(%)": "body_fat_pct", "骨格筋率(%)": "skeletal_muscle_pct", "除脂肪体重(kg)": "lean_mass_kg",
        "皮下脂肪率(%)": "subcutaneous_fat_pct", "内臓脂肪": "visceral_fat", "体水分率(%)": "body_water_pct", "筋肉量(kg)": "muscle_mass_kg",
        "骨量(kg)": "bone_mass_kg", "たんぱく質率(%)": "protein_pct", "BMR(kcal)": "bmr_kcal", "代謝年齢": "metabolic_age"}
MEAL = {"エネルギー(kcal)": "energy_kcal", "たんぱく質(g)": "protein_g", "脂質(g)": "fat_g", "炭水化物(g)": "carb_g", "カルシウム(mg)": "calcium_mg",
        "マグネシウム(mg)": "magnesium_mg", "鉄(mg)": "iron_mg", "亜鉛(mg)": "zinc_mg", "ビタミンA(µg)": "vitamin_a_ug", "ビタミンD(µg)": "vitamin_d_ug",
        "ビタミンB1(mg)": "vitamin_b1_mg", "ビタミンB2(mg)": "vitamin_b2_mg", "ビタミンB6(mg)": "vitamin_b6_mg", "ビタミンC(mg)": "vitamin_c_mg",
        "食物繊維(g)": "fiber_g", "飽和脂肪酸(g)": "saturated_fat_g", "塩分(g)": "salt_g"}
STEPS = {"歩数": "steps"}
GLUCOSE = {"平均(mg/dL)": "glucose_avg", "最高(mg/dL)": "glucose_max", "最低(mg/dL)": "glucose_min"}
# tab: (record kind, columns, a column only that tab has)
TABS = {"sleep": ("sleep", SLEEP, "総睡眠時間(分)"), "weight": ("body", BODY, "体重(kg)"), "meal": ("meal_screen", MEAL, "エネルギー(kcal)"),
        "steps": ("steps", STEPS, "歩数"), "glucose": ("glucose", GLUCOSE, "平均(mg/dL)")}
TIME = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")


def parse_day(text):
    t = str(text or "").strip()
    m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", t)
    if m:
        y, mo, d, hour = int(m[1]), int(m[2]), int(m[3]), None
    else:
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):\d{2}(?::\d{2})?)?$", t)
        if not m:
            return None
        d, mo, y, hour = int(m[1]), int(m[2]), int(m[3]), m[4]
    try:
        day = date(y, mo, d)
    except ValueError:
        return None
    if hour is not None and int(hour) == 17:
        day += timedelta(days=1)  # midnight in Ho Chi Minh, written in UTC
    return day.isoformat()


def number(text):
    t = str(text or "").strip().replace(",", "").rstrip("%")
    try:
        v = float(t)
    except ValueError:
        return None
    return None if v == 0 else v


def which_tab(header):
    for tab, (_, cols, mark) in TABS.items():
        if mark in header and (tab != "weight" or "体重(kg)" in header):
            return tab
    return None


def rows_of(path):
    """(tab, {day: {key: value}}) from one CSV. The header is the first row that has 日付."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    head_i = next((i for i, r in enumerate(rows) if r and r[0].strip() == "日付"), None)
    if head_i is None:
        return None, {}
    header = [c.strip() for c in rows[head_i]]
    tab = which_tab(header)
    if not tab:
        return None, {}
    _, cols, _ = TABS[tab]
    out = {}
    for r in rows[head_i + 1:]:
        if not r:
            continue
        day = parse_day(r[0])
        if not day:
            continue
        cell = {header[i]: r[i].strip() for i in range(min(len(header), len(r)))}
        vals = {}
        if tab == "sleep":
            # bedtime / wake time that slid one and two columns to the left
            if TIME.match(cell.get("夜間睡眠時間(分)", "")) and not cell.get("就寝時刻"):
                cell["就寝時刻"], cell["夜間睡眠時間(分)"] = cell["夜間睡眠時間(分)"], ""
            if TIME.match(cell.get("昼寝時間(分)", "")) and not cell.get("起床時刻"):
                cell["起床時刻"], cell["昼寝時間(分)"] = cell["昼寝時間(分)"], ""
        for col, key in cols.items():
            raw = cell.get(col, "")
            if key in ("bedtime", "wake_time"):
                if TIME.match(raw):
                    vals[key] = raw[:5].zfill(5)
            else:
                v = number(raw)
                if v is not None:
                    vals[key] = v
        if vals:
            out[day] = vals  # a later row for the same day wins
    return tab, out


def records(paths):
    """The checked records from all files, by id."""
    out = {}
    for p in paths:
        tab, days = rows_of(p)
        if not tab:
            print(f"skipped (not a known tab): {p}", file=sys.stderr)
            continue
        kind = TABS[tab][0]
        for day, vals in sorted(days.items()):
            raw = {"kind": kind, "date": day, "label": "From the Life Dashboard sheet",
                   "values": [{"key": k, "number": v if not isinstance(v, str) else None, "text": v if isinstance(v, str) else None} for k, v in vals.items()]}
            rec = ll.check(raw, day, True)
            if not rec:
                continue
            rec["doubts"] = [d for d in rec["doubts"] if d != "date"]  # an old date is expected here
            rec["status"] = "review" if rec["doubts"] else "confirmed"
            rec["estimated"] = False
            rid = f"sheet-{tab}-{day}"
            out[rid] = {**rec, "id": rid, "photo": None, "source": "sheet", "note": "", "at": int(time.time())}
    return out


def write(recs):
    with ll.locked():
        body = ll.read_json(ll.bp("records.json"), {})
        body.update(recs)
        ll.write_json(ll.bp("records.json"), body)
    ll.mark_log_habits(list(recs.values()))
    return len(recs)


def main(argv):
    go = "--write" in argv
    paths = [a for a in argv if a != "--write"]
    if not paths:
        print(__doc__)
        return 2
    recs = records(paths)
    summary = {}
    for r in recs.values():
        s = summary.setdefault(r["kind"], {"days": 0, "review": 0, "from": r["date"], "to": r["date"]})
        s["days"] += 1
        s["review"] += r["status"] == "review"
        s["from"], s["to"] = min(s["from"], r["date"]), max(s["to"], r["date"])
    print(json.dumps({"write": go, "kinds": summary}, ensure_ascii=False, indent=1))
    if go:
        print(f"written: {write(recs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
