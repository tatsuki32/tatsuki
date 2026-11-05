#!/usr/bin/env python3
"""Tatsuki's Desk — what the お金 page shows beyond the raw rows: periods, categories, limits, one finding at a time,
and the CSV and PDF files. Reads what wise_sync.py saved; the only thing written here is the person's own settings.

Nothing is sent anywhere. The PDF is made by a small helper using the Mac's own WebKit (app/htmlpdf.swift).
The lessons are general explanations with their sources, never advice on what to buy or how much to invest.
"""
import calendar
import csv
import datetime as dt
import html
import io
import os
import re
import subprocess
import tempfile

import wise_analysis
import wise_plan
import wise_sync

ROOT = os.path.dirname(os.path.abspath(__file__))
TOOL_SOURCE = os.path.join(ROOT, "app", "htmlpdf.swift")
TOOL = os.path.expanduser("~/Library/Application Support/TatsukiDesk/htmlpdf")
PERIODS = ("week", "month", "3m", "6m", "all", "custom")
UNSORTED = "未分類"
CATEGORIES = ("食料品", "外食・カフェ", "コンビニ", "交通", "旅行・宿泊", "通信", "買い物", "日用品・薬", "医療", "サブスク・ツール", "娯楽・文化", "学び", "手続き・手数料", "現金", "送金", "カード", UNSORTED)
BY_TYPE = {"CASH_WITHDRAWAL": "現金", "TRANSFER": "送金", "CARD_ORDER": "カード"}
RULES = (
    ("コンビニ", ("7-eleven", "lawson", "ローソン", "familymart", "ファミリーマート", "circle k")),
    ("食料品", ("coles", "woolworths", "aldi", "supermarket", "スーパー", "grocer", "mercado")),
    ("外食・カフェ", ("starbucks", "mcdonald", "coffee", "cafe", "café", "restaurant", "kfc", "burger", "bakery")),
    ("交通", ("suica", "pasmo", "uber", "grab", "translink", "flixbus", "bookaway", "metro", "railway", "taxi", "12go")),
    ("旅行・宿泊", ("booking.com", "trip.com", "airbnb", "agoda", "expedia", "hotel", "hostel", "airlines", "airways")),
    ("通信", ("optus", "docomo", "softbank", "airalo", "telecom", "viettel")),
    ("買い物", ("amazon", "aliexpress", "mercari", "メルカリ", "rakuten", "uniqlo", "shein")),
    ("医療", ("dental", "clinic", "hospital", "pharmacy", "薬局", "病院", "歯科")),
    ("サブスク・ツール", ("apple", "google", "netflix", "spotify", "openai", "anthropic", "github")),
)
LESSONS = {
    "delay": {"title": "「あとで」より「今」を選びやすいのは、意志だけの問題ではない",
              "body": "21の研究をまとめた分析では、ADHD の人はあとの大きな金額より今の小さな金額を選びやすい傾向がありました（25の比較、効果量 d = 0.43）。だから、頑張って我慢するより、その場で選ばなくて済む形に寄せます。",
              "source": "Jackson & MacKillop, 2016", "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC5049699", "checked": "抄録まで"},
    "default": {"title": "設定を先に決めると、続けやすい",
                "body": "米国の企業年金では、自動加入に変えただけで加入が大きく増え、多くの人が初期設定のまま続けました。毎回決めるより、一度の設定で動く形のほうが残りやすいと分かります。",
                "source": "Madrian & Shea, 2001", "url": "https://www.nber.org/papers/w7682", "checked": "原典で確認"},
    "smart": {"title": "増えた分から、あとで増やす",
              "body": "将来の昇給の一部を貯蓄に回すと先に決める仕組みでは、参加者の貯蓄率が3.5%から13.6%に上がったと報告されています。今の生活を削らずに始められる形です。ただし参加者は自分で選んだ人たちで、因果の強さには限界があります。",
              "source": "Thaler & Benartzi, 2004", "url": "https://econpapers.repec.org/RePEc:ucp:jpolec:v:112:y:2004:i:s1:p:s164-s187", "checked": "二次資料まで"},
    "justintime": {"title": "学ぶのは、必要になったその時に",
                   "body": "金融教育の研究をまとめた分析では、教育が行動の差を説明する割合は0.1%で、効果も時間とともに薄れました。別のまとめ（Kaiser ほか）は効果を約0.1標準偏差と見ていて、結論は割れています。どちらも、知るだけで大きく変わるとは言っていません。",
                   "source": "Fernandes, Lynch & Netemeyer, 2014", "url": "https://econpapers.repec.org/RePEc:inm:ormnsc:v:60:y:2014:i:8:p:1861-1883", "checked": "抄録まで"},
    "early": {"title": "問題が小さいうちに仕組みを作る",
              "body": "スウェーデンの調査では、ADHD の成人の債務不履行は、成人初期には他の人と同じ程度でした。中年期に増え、40歳前後で高まっています。相関の研究で原因までは言えませんが、早めに整える理由にはなります。",
              "source": "Beauchaine ほか, 2020", "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC7527218", "checked": "抄録まで"},
    "fees": {"title": "小さな手数料は、長いと大きい",
             "body": "バフェットは2018年の株主への手紙で、年1%の手数料を払い続けると、77年で増えた分が半分になる例を挙げました。投資の話ですが、毎月の小さな請求を年額で見る理由にもなります。後半はこの画面の類推です。",
             "source": "Berkshire Hathaway 2018年の手紙", "url": "https://www.berkshirehathaway.com/letters/2018ltr.pdf", "checked": "原典で確認"},
    "buffer": {"title": "「生活費3〜6か月」は慣習",
               "body": "よく聞く目安ですが、最適な月数を測った研究は見つかりませんでした。米国の家計の調査では、収入減と出費増が重なると中央値で手取り6.2週分が要るという推計があります。これも参考で、合格の線ではありません。",
               "source": "JPMorgan Chase Institute", "url": "https://www.jpmorganchase.com/institute/all-topics/household-financial-health/report-weathering-volatility-2-a-monthly-stress-test-to-guide-saving", "checked": "原典で確認"},
}
TAKE_NOTE = "一般的な解説で、個別の投資や商品の助言ではありません。"


def today_hcmc():
    return (dt.datetime.now(dt.timezone.utc) + wise_sync.HCMC).date()


def category_of(a, overrides=None, proposals=None):
    """The person's own choice first, then Claude's proposal, the kind of movement, the shop's name. An unknown shop stays 未分類."""
    title = (a.get("title") or "").lower()
    if overrides and title in overrides:
        return overrides[title]
    found = (proposals or {}).get(title)
    if found and found["category"] != UNSORTED:
        return found["category"]
    if a.get("type") in BY_TYPE:
        return BY_TYPE[a["type"]]
    return next((name for name, words in RULES if any(w in title for w in words)), UNSORTED)


def months_back(day, n):
    y, m = day.year, day.month - 1 - n
    y, m = y + m // 12, m % 12 + 1
    return dt.date(y, m, min(day.day, calendar.monthrange(y, m)[1]))


def period_range(period="month", month=None, start=None, end=None, today=None):
    """(first day, last day, label, the same-length range just before, or None). ValueError on a bad choice."""
    today = today or today_hcmc()
    if period not in PERIODS:
        raise ValueError("bad period")
    if period == "week":
        first = today - dt.timedelta(days=today.weekday())
        return first, today, "今週", (first - dt.timedelta(days=7), today - dt.timedelta(days=7))
    if period == "month":
        if month is not None and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise ValueError("bad month")
        y, m = int((month or today.isoformat())[:4]), int((month or today.isoformat())[5:7])
        first, last = dt.date(y, m, 1), dt.date(y, m, calendar.monthrange(y, m)[1])
        before = months_back(first, 1)
        return first, last, f"{y}年{m}月", (before, first - dt.timedelta(days=1))
    if period in ("3m", "6m"):
        n = int(period[0])
        first = months_back(today, n) + dt.timedelta(days=1)
        return first, today, f"直近{'3か月' if n == 3 else '半年'}", (months_back(first, n), first - dt.timedelta(days=1))
    if period == "all":
        dates = [wise_sync.local_date(a.get("createdOn")) for a in wise_sync.read_json("activities.json", {}).values()]
        first = dt.date.fromisoformat(min([d for d in dates if d] or [today.isoformat()]))
        return first, today, "計測の最初から", None
    first, last = dt.date.fromisoformat(start or ""), dt.date.fromisoformat(end or "")
    if first > last:
        raise ValueError("bad range")
    return first, last, f"{first} 〜 {last}", (first - (last - first) - dt.timedelta(days=1), first - dt.timedelta(days=1))


def spending(usd):
    """What was used: card payments less refunds, plus cash. A transfer may only be moving one's own money."""
    t = usd["types"]
    return round(t.get("out:CARD_PAYMENT", 0) + t.get("out:CASH_WITHDRAWAL", 0) - t.get("in:CARD_PAYMENT", 0), 2)


def labelled(items):
    proposals = wise_sync.read_json("guess.json", {}).get("categories")
    return wise_analysis.enrich(items, lambda a, overrides: category_of(a, overrides, proposals))


def pace(limits, today=None):
    """Spent so far against the person's own limits, with where this speed ends up. Empty until a limit is set."""
    today = today or today_hcmc()
    out = {}
    for key, label, first, last in (
        ("week", "今週", today - dt.timedelta(days=today.weekday()), today - dt.timedelta(days=today.weekday()) + dt.timedelta(days=6)),
        ("month", "今月", today.replace(day=1), today.replace(day=calendar.monthrange(today.year, today.month)[1])),
    ):
        limit = limits.get(key)
        if not limit:
            continue
        spent = wise_analysis.spent_total(labelled(wise_sync.summary(start=first.isoformat(), end=today.isoformat(), today=today.isoformat())["items"]))
        elapsed, days = (today - first).days + 1, (last - first).days + 1
        out[key] = {"label": label, "limit": limit, "spent": spent, "left": round(limit - spent, 2), "forecast": round(spent / elapsed * days, 2)}
    return out


def by_category(items):
    totals = {}
    for a in items:
        amount = wise_analysis.spent_amount(a)
        if amount:
            t = totals.setdefault(a["category"], {"name": a["category"], "usd": 0, "count": 0})
            t["usd"] = round(t["usd"] + amount, 2)
            t["count"] += 1
    return sorted(totals.values(), key=lambda t: -t["usd"])


def largest(items, n=10):
    rows = [a for a in items if a.get("kind") == "spend" and a["usd"] is not None]
    return sorted(rows, key=lambda a: -a["usd"])[:n]


def money(v):
    return f"{v:,.2f}"


def plain_number(v):
    """Amounts in their own currency: 1,000 and 8.5, with no made-up decimals."""
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def finding(usd, categories, top, month_pace, spent=None):
    """The one thing worth seeing, with one small next step and one lesson. Facts only: nothing here blames."""
    spent = spending(usd) if spent is None else spent
    if month_pace and month_pace["forecast"] > month_pace["limit"]:
        return {"fact": f"今のペースだと、今月は {money(month_pace['forecast'])} USD になり、自分の上限 {money(month_pace['limit'])} USD を超えそうです。",
                "next": "今月の残りで減らせそうな支出を1つ選ぶ", "lesson": "delay"}
    if spent <= 0:
        return {"fact": "この期間の支出はありません。", "next": "毎月先に分けておく額を1つ決める", "lesson": "default"}
    cash = usd["types"].get("out:CASH_WITHDRAWAL", 0)
    if cash / spent >= 0.2:
        return {"fact": f"現金の引き出しが {money(cash)} USD で、支出の {round(cash / spent * 100)}% です。現金は使い道が記録に残りません。",
                "next": "次に ATM で引き出すとき、使い道を1行メモする", "lesson": "justintime"}
    named = [c for c in categories if c["name"] not in ("送金", UNSORTED)]
    if named and named[0]["usd"] / spent >= 0.3:
        c = named[0]
        return {"fact": f"{c['name']} が {money(c['usd'])} USD で、支出の {round(c['usd'] / spent * 100)}% です。",
                "next": f"{c['name']} の明細を1回だけ見直す", "lesson": "justintime"}
    cards = [a for a in top if a["type"] == "CARD_PAYMENT"]
    if cards and cards[0]["usd"] / spent >= 0.2:
        return {"fact": f"一番大きな支払いは {cards[0]['title']} の {money(cards[0]['usd'])} USD で、支出の {round(cards[0]['usd'] / spent * 100)}% です。",
                "next": "次に同じくらいの支払いが来そうな時期をカレンダーに入れる", "lesson": "early"}
    return {"fact": f"支出は {money(spent)} USD で、大きな偏りはありません。", "next": "入金の日に自動で分ける設定を1つ調べる", "lesson": "smart"}


def reconcile(month, view):
    """The person's correct numbers against what the page counts for that month, in the currency they chose."""
    saved = wise_sync.read_json("recon.json", {}).get(month)
    if not saved:
        return None
    currency = saved.get("currency") or "USD"
    ours = view["usd"] if currency == "USD" else view["paid"].get(currency, {"in": 0, "out": 0})
    diff = lambda side: None if saved.get(side) is None else round(ours.get(side, 0) - saved[side], 2)
    return {**saved, "currency": currency, "oursIn": ours.get("in", 0), "oursOut": ours.get("out", 0), "diffIn": diff("in"), "diffOut": diff("out")}


def balances_now():
    """The newest snapshot wise_sync.py saved, with its USD total."""
    snaps = wise_sync.read_json("balances.json", {})
    if not snaps:
        return None
    day = max(snaps)
    rows = snaps[day]
    known = [r["usd"] for r in rows if r.get("usd") is not None]
    return {"date": day, "rows": sorted(rows, key=lambda r: -(r.get("usd") or 0)), "usd": round(sum(known), 2), "complete": len(known) == len(rows)}


def view(period="month", month=None, start=None, end=None, today=None):
    """Everything the お金 page and the files show for one period."""
    today = today or today_hcmc()
    first, last, label, before = period_range(period, month, start, end, today)
    data = wise_sync.summary(start=first.isoformat(), end=last.isoformat(), today=today.isoformat())
    labelled(data["items"])
    limits = wise_sync.read_json("limits.json", {})
    paces = pace(limits, today)
    top = largest(data["items"])
    cats = by_category(data["items"])
    spent = wise_analysis.spent_total(data["items"])
    earlier = None
    if before:
        prior = labelled(wise_sync.summary(start=before[0].isoformat(), end=before[1].isoformat(), today=today.isoformat())["items"])
        earlier = {"from": before[0].isoformat(), "to": before[1].isoformat(), "spent": wise_analysis.spent_total(prior)}
    chosen = finding(data["usd"], cats, top, paces.get("month") if first <= today <= last else None, spent)
    current = (month or today.isoformat()[:7]) if period == "month" else None
    everything = labelled(wise_sync.summary(start="0000-01-01", end="9999-12-31", today=today.isoformat())["items"])
    seen = wise_sync.read_json("seen.json", {})
    analysis = wise_analysis.analyze(everything, today.isoformat(), current)
    data.update(
        period=period, month=current, start=first.isoformat(), end=last.isoformat(), label=label, spent=spent, earlier=earlier,
        categories=cats, categoryNames=CATEGORIES, largest=[{k: a[k] for k in ("date", "title", "usd", "amountText", "category")} for a in top],
        limits=limits, pace=paces, finding={**chosen, "lesson": LESSONS[chosen["lesson"]]}, lessons=list(LESSONS.values()), takeNote=TAKE_NOTE,
        recon=reconcile(current, data) if current else None, balances=balances_now(), analysis=analysis, ahead=wise_plan.ahead(everything, today.isoformat(), analysis["runway"], analysis["usual"]), tip=wise_plan.today_tip(today.isoformat()), tips=wise_plan.tips()[-30:][::-1],
        kinds=wise_analysis.kind_totals(data["items"]), kindNames=wise_analysis.KINDS, accounts=wise_analysis.accounts(data["items"]),
        currencies=wise_analysis.by_currency(data["items"]), advice=wise_sync.read_json("advice.json", None), seen=wise_analysis.seen_since(everything, seen.get("previous")),
        close=wise_sync.read_json("close.json", {}).get(current or today.isoformat()[:7]) or {})
    return data


def world():
    import wise_world
    today = today_hcmc().isoformat()
    return wise_world.view(labelled(wise_sync.summary(start="0000-01-01", end="9999-12-31", today=today)["items"]), today)


# ---------- files ----------

def cell(v):
    """A title from a shop must not run as a formula when the CSV is opened in a spreadsheet."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


FLOW_JA = {"in": "入金", "out": "出金", None: "合計に入れない"}
SOURCE_JA = {"record": "Wise の記録", "rate": "レートで換算", None: ""}


def to_csv(data):
    """UTF-8 with a BOM, so Excel opens the Japanese as it is."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["日付", "区分", "Wise の種類", "種類", "分類", "用途の推定", "状態", "題名", "説明", "USD", "USD の出どころ", "支払った額", "支払った通貨", "元の表示", "2つ目の表示", "Wise の id"])
    for a in data["items"]:
        w.writerow([a["date"], FLOW_JA[a["flow"]], a.get("type"), wise_analysis.KINDS.get(a.get("kind"), ""), a["category"], a.get("use"), a.get("status"), cell(a.get("title")), cell(a.get("description")),
                    a["usd"] if a["flow"] else "", SOURCE_JA[a["usdSource"]] if a["flow"] else "", a["settledValue"], a["settledCurrency"],
                    cell(a.get("amountText")), cell(a.get("secondaryText")), a.get("id")])
    return ("﻿" + buf.getvalue()).encode("utf-8")


PAPER = """<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>%s</title><style>
body{font:11px/1.6 -apple-system,"Hiragino Sans",sans-serif;color:#111;margin:0}
h1{font-size:20px;margin:0}h2{font-size:13px;margin:24px 0 8px;break-after:avoid}
p{margin:0 0 8px}.sub{color:#555;font-size:10px}.nums{display:flex;gap:40px;margin:16px 0}.nums b{display:block;font-size:18px}
table{width:100%%;border-collapse:collapse}td,th{padding:4px 8px 4px 0;border-bottom:1px solid #ddd;text-align:left;vertical-align:top;overflow-wrap:anywhere}
tr{break-inside:avoid}.n{text-align:right;white-space:nowrap;padding:4px 0 4px 16px}.box{border-left:3px solid #111;padding-left:12px;margin:8px 0;break-inside:avoid}
</style></head><body>%s</body></html>"""


def bars_svg(rows, width=640, height=120):
    """A plain bar chart for the printed page: (label, value) in order, with the largest one named."""
    if not rows:
        return ""
    top = max(max(v for _, v in rows), 1)
    step = width / len(rows)
    bars = "".join(f"<rect x='{i * step + 1:.1f}' y='{height - max(v, 0) / top * height:.1f}' width='{max(step - 2, 1):.1f}' height='{max(v, 0) / top * height:.1f}' fill='#555'/>" for i, (_, v) in enumerate(rows))
    return (f"<svg viewBox='0 0 {width} {height + 16}' width='100%' role='img'>{bars}<text x='0' y='{height + 12}' font-size='10'>{html.escape(rows[0][0])}</text>"
            f"<text x='{width}' y='{height + 12}' font-size='10' text-anchor='end'>{html.escape(rows[-1][0])}（棒の最大 {money(top)} USD）</text></svg>")


def to_html(data, kind="report"):
    """The same page for both PDFs: a short report, or the report's totals with every row."""
    e = lambda v: html.escape("" if v is None else str(v))
    u = data["usd"]
    parts = [f"<h1>お金のレポート</h1><p class='sub'>{e(data['label'])}（{e(data['start'])} 〜 {e(data['end'])}）</p>",
             f"<div class='nums'><div><span class='sub'>入金</span><b>{money(u['in'])}</b></div><div><span class='sub'>出金</span><b>{money(u['out'])}</b></div>"
             f"<div><span class='sub'>差引</span><b>{money(u['net'])}</b></div><div><span class='sub'>支出（種類が支出のもの）</span><b>{money(data['spent'])}</b></div></div>",
             f"<p class='sub'>単位は USD。Wise の USD 記録 {u['recorded']}件、決済時刻の Wise のレートで換算 {u['converted']}件。換算した分は実際に払った額と差が出ることがあります。</p>"]
    if data["unconverted"]:
        parts.append("<p class='sub'>レートを取れず合計に入っていない分：" + e(" · ".join(f"{c} {n}件" for c, n in data["unconverted"].items())) + "</p>")
    if data["earlier"]:
        ea = data["earlier"]
        parts.append(f"<p>前の同じ長さの期間（{e(ea['from'])} 〜 {e(ea['to'])}）の支出は {money(ea['spent'])} USD でした。</p>")
    f = data["finding"]
    parts.append(f"<h2>気づき</h2><div class='box'><p>{e(f['fact'])}</p><p>次の1つ：{e(f['next'])}</p><p class='sub'>{e(f['lesson']['title'])}。{e(f['lesson']['body'])}（{e(f['lesson']['source'])}）{e(data['takeNote'])}</p></div>")
    z = data["analysis"]
    parts.append("<h2>月ごとの支出</h2>" + bars_svg([(m["month"], m["spent"]) for m in z["months"]]))
    parts.append("<h2>日ごとの支出</h2><p class='sub'>" + e(z["month"]) + "</p>" + bars_svg([(d["date"][5:], d["spent"]) for d in z["days"]]))
    if data["categories"]:
        parts.append("<h2>分類ごとの支出</h2><table>" + "".join(f"<tr><td>{e(c['name'])}</td><td class='n'>{c['count']}件</td><td class='n'>{money(c['usd'])}</td></tr>" for c in data["categories"]) + "</table>")
    if data["paid"]:
        parts.append("<h2>支払った通貨ごと</h2><table>" + "".join(f"<tr><td>{e(c)}</td><td class='n'>出金 {plain_number(p['out'])}</td><td class='n'>入金 {plain_number(p['in'])}</td></tr>" for c, p in sorted(data["paid"].items(), key=lambda x: -x[1]["count"])) + "</table>")
    if data["largest"]:
        parts.append("<h2>大きな出金</h2><table>" + "".join(f"<tr><td>{e(a['date'])}</td><td>{e(a['title'])}</td><td class='n'>{e(a['amountText'])}</td><td class='n'>{money(a['usd'])}</td></tr>" for a in data["largest"]) + "</table>")
    if kind == "list":
        rows = "".join(f"<tr><td>{e(a['date'])}</td><td>{e(a.get('title'))}<br><span class='sub'>{e(a['category'])}{' · 取り消し' if a.get('status') == 'CANCELLED' else ''}</span></td>"
                       f"<td class='n'>{e(a.get('amountText'))}<br><span class='sub'>{e(a.get('secondaryText'))}</span></td><td class='n'>{'' if not a['flow'] or a['usd'] is None else ('+ ' if a['flow'] == 'in' else '') + money(a['usd'])}</td></tr>" for a in data["items"])
        parts.append(f"<h2>出入金 {len(data['items'])}件</h2><table><tr><th>日付</th><th>題名</th><th class='n'>元の金額</th><th class='n'>USD</th></tr>{rows}</table>")
    return PAPER % (e("お金のレポート " + data["label"]), "".join(parts))


def pdf_tool():
    """Builds the helper the first time, and again when its source changes. None if this Mac cannot build it."""
    try:
        if os.path.exists(TOOL) and os.path.getmtime(TOOL) >= os.path.getmtime(TOOL_SOURCE):
            return TOOL
        os.makedirs(os.path.dirname(TOOL), exist_ok=True)
        subprocess.run(["swiftc", "-O", "-framework", "Cocoa", "-framework", "WebKit", TOOL_SOURCE, "-o", TOOL], check=True, capture_output=True, timeout=180)
        return TOOL
    except (OSError, subprocess.SubprocessError):
        return None


def to_pdf(page):
    """Bytes of the PDF, or None. The temporary files are this user's only and are removed right away."""
    tool = pdf_tool()
    if not tool:
        return None
    with tempfile.TemporaryDirectory(prefix="deskpdf") as d:
        src, out = os.path.join(d, "page.html"), os.path.join(d, "page.pdf")
        with open(src, "w", encoding="utf-8") as f:
            f.write(page)
        try:
            subprocess.run([tool, src, out], check=True, capture_output=True, timeout=90)
            with open(out, "rb") as f:
                return f.read()
        except (OSError, subprocess.SubprocessError):
            return None


def export(fmt, kind="report", **period):
    """(bytes, content type, file name). ValueError on a bad choice, None bytes if the PDF could not be made."""
    if fmt not in ("csv", "pdf", "html") or kind not in ("report", "list"):
        raise ValueError("bad format")
    data = view(**period)
    name = f"wise-{'report' if kind == 'report' and fmt != 'csv' else 'list'}-{data['start']}_{data['end']}"
    if fmt == "csv":
        return to_csv(data), "text/csv; charset=utf-8", name + ".csv"
    page = to_html(data, kind)
    if fmt == "html":
        return page.encode("utf-8"), "text/html; charset=utf-8", name + ".html"
    return to_pdf(page), "application/pdf", name + ".pdf"
