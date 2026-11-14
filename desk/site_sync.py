#!/usr/bin/env python3
"""tany.im analytics for Tatsuki's Desk (the "📈 Site" screen).

The site's tag (/t.js) and its Worker store events in Cloudflare D1 for 90 days. This file pulls them to the Mac
every 15 minutes (GET https://tany.im/_t/export with the key in the keychain, service tatsuki-desk-tany), keeps them
for good in desk/data/site/events/<date>.jsonl, and works out every number the screen shows.
Search Console (queries, clicks, rank) is read once a morning with Tatsuki's own Google OAuth (read only).
A weekly check reads the live page and lists SEO fixes. Nothing is ever sent to tany.im except the export request.

  python3 site_sync.py pull            pull new events now
  python3 site_sync.py view [7]        print the numbers as JSON
  python3 site_sync.py gsc-auth        connect Search Console once (opens the browser)
  python3 site_sync.py gsc             pull Search Console now
  python3 site_sync.py audit           check the live page for SEO problems
  python3 site_sync.py utm <source> [medium] [campaign]   print a link with UTM tags

desk/data/site/ holds visitor ids and organisation names, so it stays out of the GitHub backup (backup_data.py IGNORE).
"""
import base64
import collections
import datetime as dt
import glob
import hashlib
import html.parser
import http.server
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DESK_SITE_DIR") or os.path.join(HERE, "data", "site")
EVENTS = os.path.join(DATA, "events")
STATE = os.path.join(DATA, "state.json")
GSC_FILE = os.path.join(DATA, "gsc.json")
AUDIT_FILE = os.path.join(DATA, "audit.json")
SETTINGS = os.path.join(DATA, "settings.json")
SITE = "https://tany.im"
EXPORT = SITE + "/_t/export"
KEY_SERVICE = "tatsuki-desk-tany"
GSC_SERVICE = "tatsuki-desk-gsc"
GMAIL_SERVICE = "tatsuki-desk-gmail"  # the Google Cloud OAuth client already set up for Inbox Cleanup
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
PULL_EVERY = 15 * 60
LOCK = threading.Lock()
CACHE = {}

# --- small helpers ---------------------------------------------------------------------------------------------

def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _kc_get(service, account):
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", service, "-a", account, "-w"],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _kc_set(service, account, value):
    line = 'add-generic-password -U -s "%s" -a "%s" -w "%s"\n' % (service, account, value.replace("\\", "\\\\").replace('"', '\\"'))
    out = subprocess.run(["security", "-i"], input=line, capture_output=True, text=True, timeout=15)
    if out.returncode != 0:
        raise OSError("keychain")


def _local_date(ms):
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")


def _now_iso():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def settings():
    s = _load(SETTINGS, {})
    s.setdefault("clarity", "")
    return s


# --- pull from Cloudflare --------------------------------------------------------------------------------------

def pull():
    """Fetch every event newer than the last one we have. Returns the number of new events."""
    with LOCK:
        st = _load(STATE, {})
        key = _kc_get(KEY_SERVICE, "desk")
        if not key:
            st.update(error="No export key in the keychain (tatsuki-desk-tany).", tried=_now_iso())
            _save(STATE, st)
            return 0
        last, got = int(st.get("last") or 0), 0
        try:
            for _ in range(50):
                req = urllib.request.Request(f"{EXPORT}?since={last}&limit=5000",
                                             headers={"Authorization": "Bearer " + key, "User-Agent": "TatsukiDesk/1"})
                with urllib.request.urlopen(req, timeout=40) as r:
                    data = json.load(r)
                rows = data.get("rows") or []
                if last and data.get("oldest") and data["oldest"] > last + 1:
                    st["gap"] = f"Events {last + 1}–{data['oldest'] - 1} were deleted on Cloudflare before they reached the Mac."
                by_day = collections.defaultdict(list)
                for row in rows:
                    for k in ("d", "geo", "ua"):
                        try:
                            row[k] = json.loads(row.get(k) or "{}")
                        except ValueError:
                            row[k] = {}
                    by_day[_local_date(row["ts"])].append(row)
                os.makedirs(EVENTS, exist_ok=True)
                for day, items in by_day.items():
                    with open(os.path.join(EVENTS, day + ".jsonl"), "a", encoding="utf-8") as f:
                        for row in items:
                            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                got += len(rows)
                last = max(last, int(data.get("last") or last))
                st["last"] = last
                _save(STATE, st)
                if not data.get("more"):
                    break
            st.update(pulled=_now_iso(), error="", new=got)
        except urllib.error.HTTPError as e:
            st.update(error=f"tany.im answered {e.code}" + (" (is the export key right?)" if e.code == 404 else ""), tried=_now_iso())
        except (OSError, ValueError) as e:
            st.update(error=f"Could not reach tany.im ({type(e).__name__})", tried=_now_iso())
        _save(STATE, st)
        CACHE.clear()
        return got


def read_days(start, end):
    """Events with local dates from start to end (inclusive, YYYY-MM-DD)."""
    out = []
    d = dt.date.fromisoformat(start)
    stop = dt.date.fromisoformat(end)
    while d <= stop:
        path = os.path.join(EVENTS, d.isoformat() + ".jsonl")
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            d += dt.timedelta(days=1)
            continue
        hit = CACHE.get(path)
        if not hit or hit[0] != mtime:
            rows, seen = [], set()
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if r.get("id") in seen:
                        continue
                    seen.add(r.get("id"))
                    rows.append(r)
            hit = CACHE[path] = (mtime, rows)
        out.extend(hit[1])
        d += dt.timedelta(days=1)
    return out


# --- classification ---------------------------------------------------------------------------------------------

SEARCH = re.compile(r"(^|\.)(google\.[a-z.]+|bing\.com|duckduckgo\.com|search\.yahoo|yahoo\.co\.jp|yandex\.|baidu\.com|ecosia\.org|naver\.com|search\.brave\.com|startpage\.com|qwant\.com|seznam\.cz)", re.I)
AI = re.compile(r"(chatgpt\.com|chat\.openai\.com|perplexity\.ai|claude\.ai|gemini\.google\.com|bard\.google|copilot\.microsoft\.com|you\.com|phind\.com|poe\.com|meta\.ai|grok\.com|deepseek\.com|mistral\.ai)", re.I)
SOCIAL = re.compile(r"(linkedin\.com|lnkd\.in|(^|\.)x\.com|t\.co$|twitter\.com|facebook\.com|fb\.com|m\.facebook|l\.facebook|instagram\.com|l\.instagram|threads\.net|threads\.com|youtube\.com|youtu\.be|tiktok\.com|reddit\.com|note\.com|qiita\.com|zenn\.dev|hatena|b\.hatena|pinterest\.|telegram|t\.me|whatsapp|line\.me|discord|bsky\.app|mastodon|substack\.com|medium\.com)", re.I)
MAIL = re.compile(r"(mail\.google\.com|outlook\.|mail\.yahoo|webmail|mail\.)", re.I)
ISP = re.compile(r"telecom|telekom|communication|mobile|broadband|cable|internet|wireless|network|\bisp\b|\bnet\b|viettel|vnpt|\bfpt\b|ntt|kddi|softbank|docomo|comcast|verizon|at&t|t-mobile|vodafone|orange|amazon|google|microsoft|cloudflare|digitalocean|ovh|hetzner|akamai|linode|telstra|optus|bharti|jio|china|chunghwa|starlink|magticom|silknet|beeline|rostelecom|\bmts\b|megafon|turk|airtel|telia|telenor|swisscom|bt\b|sky|virgin|charter|spectrum|cox|frontier|centurylink|lumen|rakuten|biglobe|so-net|ocn|plala|asahi|sakura|kagoya|zscaler|apple|meta platforms|facebook|oracle|alibaba|tencent|choopa|vultr|m247|datacamp|hostinger|leaseweb|contabo|ionos|godaddy|private customer|residential", re.I)


def channel(d):
    med = (d.get("u_medium") or "").lower()
    src = (d.get("u_source") or "").lower()
    ref = (d.get("ref") or "").lower()
    host = ref.split("/")[0]
    if d.get("ad") or med in ("cpc", "ppc", "paid", "paidsocial", "paid_social", "display", "ads"):
        return "Paid"
    if med in ("email", "e-mail", "mail", "newsletter") or MAIL.search(host):
        return "Email"
    if AI.search(host) or AI.search(src):
        return "AI assistants"
    if med in ("social", "social-network", "sm") or SOCIAL.search(host) or SOCIAL.search(src):
        return "Social"
    if med == "organic" or SEARCH.search(host):
        return "Search"
    if src or med:
        return "Campaign" if not host else "Referral"
    if host:
        return "Referral"
    return "Direct"


def source_label(d):
    if d.get("u_source"):
        return d["u_source"] + (" / " + d["u_medium"] if d.get("u_medium") else "")
    host = (d.get("ref") or "").split("/")[0]
    return re.sub(r"^(www|m|l|lm)\.", "", host) if host else "(direct)"


# Google's own thresholds for "good" and "poor" (web.dev): LCP, INP, CLS, FCP, TTFB
VITALS = {"lcp": (2500, 4000, "ms"), "inp": (200, 500, "ms"), "cls": (0.1, 0.25, ""), "fcp": (1800, 3000, "ms"), "ttfb": (800, 1800, "ms")}


def pctl(values, p):
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


# --- the numbers ---------------------------------------------------------------------------------------------

def period_range(period, start=None, end=None):
    today = dt.date.today()
    if period == "custom" and start and end:
        a, b = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
        if b < a:
            a, b = b, a
    elif period == "today":
        a = b = today
    elif period == "yesterday":
        a = b = today - dt.timedelta(days=1)
    else:
        n = int(period) if str(period).isdigit() else 7
        a, b = today - dt.timedelta(days=n - 1), today
    span = (b - a).days + 1
    return a, b, a - dt.timedelta(days=span), a - dt.timedelta(days=1)


def summarize(rows):
    """The headline numbers for one period (used for this period and the one before)."""
    pv = [r for r in rows if r["e"] == "pv"]
    named = [r for r in rows if r.get("s")]
    visits = collections.defaultdict(lambda: {"pv": 0, "eng": {}, "contact": False})
    for r in named:
        v = visits[r["s"]]
        if r["e"] == "pv":
            v["pv"] += 1
        elif r["e"] == "eng":
            v["eng"][r["p"]] = max(v["eng"].get(r["p"], 0), int(r["d"].get("ms") or 0))
        elif r["e"] == "contact":
            v["contact"] = True
    engaged = sum(1 for v in visits.values() if v["pv"] >= 2 or v["contact"] or sum(v["eng"].values()) >= 10000)
    eng_ms = [sum(v["eng"].values()) for v in visits.values() if v["eng"]]
    contacts = sum(1 for v in visits.values() if v["contact"])
    visitors = {r["v"] for r in named if r.get("v")}
    new = {r["v"] for r in named if r["e"] == "pv" and r["d"].get("new")}
    anon_pv = sum(1 for r in pv if not r.get("s"))
    return {
        "pageviews": len(pv), "anonPageviews": anon_pv, "visits": len(visits), "visitors": len(visitors),
        "newVisitors": len(new), "returning": len(visitors - new), "engaged": engaged,
        "engagedRate": round(engaged / len(visits), 3) if visits else None,
        "avgEngMs": int(sum(eng_ms) / len(eng_ms)) if eng_ms else None,
        "contacts": contacts, "contactRate": round(contacts / len(visits), 3) if visits else None,
        "reach": len(visitors) + anon_pv,  # people we can count: named visitors plus page views without consent
    }


def top(counter, n=10, key="name"):
    return [{key: k, "n": v} for k, v in counter.most_common(n)]


def view(period="7", start=None, end=None):
    a, b, pa, pb = period_range(period, start, end)
    rows = read_days(a.isoformat(), b.isoformat())
    prev = read_days(pa.isoformat(), pb.isoformat())
    people = [r for r in rows if r["e"] not in ("bot", "nf")]
    st = _load(STATE, {})
    out = {"period": {"from": a.isoformat(), "to": b.isoformat(), "prevFrom": pa.isoformat(), "prevTo": pb.isoformat(), "days": (b - a).days + 1},
           "state": {k: st.get(k) for k in ("pulled", "tried", "error", "gap", "last")},
           "now": summarize(people), "prev": summarize([r for r in prev if r["e"] not in ("bot", "nf")]),
           "events": len(rows), "clarity": settings().get("clarity", "")}
    pv = [r for r in people if r["e"] == "pv"]

    # by day
    days = collections.OrderedDict()
    d = a
    while d <= b:
        days[d.isoformat()] = {"date": d.isoformat(), "pv": 0, "visitors": set(), "anon": 0, "contacts": set()}
        d += dt.timedelta(days=1)
    for r in people:
        day = days.get(_local_date(r["ts"]))
        if not day:
            continue
        if r["e"] == "pv":
            day["pv"] += 1
            if r.get("v"):
                day["visitors"].add(r["v"])
            else:
                day["anon"] += 1
        if r["e"] == "contact" and r.get("s"):
            day["contacts"].add(r["s"])
    out["daily"] = [{"date": x["date"], "pv": x["pv"], "people": len(x["visitors"]) + x["anon"], "contacts": len(x["contacts"])} for x in days.values()]

    # hours of the day (Mac time)
    hours = [0] * 24
    for r in pv:
        hours[dt.datetime.fromtimestamp(r["ts"] / 1000).hour] += 1
    out["hours"] = hours

    # sources: first page view of each visit (or each page view without consent)
    first = {}
    for r in sorted(pv, key=lambda r: r["ts"]):
        k = r.get("s") or ("anon", r["id"])
        first.setdefault(k, r)
    contact_visits = {r["s"] for r in people if r["e"] == "contact" and r.get("s")}
    ch, src, camp = collections.Counter(), collections.Counter(), {}
    ch_contact = collections.Counter()
    for k, r in first.items():
        c = channel(r["d"])
        ch[c] += 1
        src[source_label(r["d"])] += 1
        if isinstance(k, str) and k in contact_visits:
            ch_contact[c] += 1
        if r["d"].get("u_source") or r["d"].get("u_campaign"):
            key = " · ".join(x for x in (r["d"].get("u_source"), r["d"].get("u_medium"), r["d"].get("u_campaign")) if x)
            row = camp.setdefault(key, {"name": key, "visits": 0, "contacts": 0})
            row["visits"] += 1
            if isinstance(k, str) and k in contact_visits:
                row["contacts"] += 1
    out["channels"] = [{"name": k, "n": v, "contacts": ch_contact.get(k, 0)} for k, v in ch.most_common()]
    out["sources"] = top(src, 15)
    out["campaigns"] = sorted(camp.values(), key=lambda x: -x["visits"])[:20]
    out["ads"] = top(collections.Counter(r["d"]["ad"] for r in first.values() if r["d"].get("ad")))

    # where people are, what they use
    def once_per_visit(field):
        seen, c = set(), collections.Counter()
        for r in pv:
            k = r.get("s") or r["id"]
            if k in seen:
                continue
            seen.add(k)
            val = field(r)
            if val:
                c[val] += 1
        return c
    out["countries"] = top(once_per_visit(lambda r: r["geo"].get("cc")), 15)
    out["cities"] = top(once_per_visit(lambda r: ", ".join(x for x in (r["geo"].get("city"), r["geo"].get("cc")) if x)), 15)
    orgs = once_per_visit(lambda r: r["geo"].get("org"))
    out["orgs"] = [{"name": k, "n": v, "isp": bool(ISP.search(k))} for k, v in orgs.most_common(40)]
    out["devices"] = top(once_per_visit(lambda r: r["ua"].get("dev")))
    out["browsers"] = top(once_per_visit(lambda r: r["ua"].get("br")))
    out["os"] = top(once_per_visit(lambda r: r["ua"].get("os")))
    out["langs"] = top(once_per_visit(lambda r: (r["d"].get("lang") or "")[:2]))

    def width_bucket(r):
        w = r["d"].get("vw") or 0
        return "under 400px" if w < 400 else "400–767px" if w < 768 else "768–1199px" if w < 1200 else "1200px and up"
    out["widths"] = top(once_per_visit(width_bucket))
    out["dark"] = sum(1 for r in pv if r["d"].get("dark")), sum(1 for r in pv if "dark" in r["d"])
    out["pages"] = top(collections.Counter(r["p"] for r in pv), 15, "path")

    # reading: scroll depth, sections, copies
    named_pv = [r for r in pv if r.get("s")]
    depth = collections.Counter()
    for r in people:
        if r["e"] == "scroll" and r["p"] == "/":
            depth[r["d"].get("pct")] += 1
    home_pv = sum(1 for r in named_pv if r["p"] == "/")
    out["scroll"] = [{"pct": p, "n": depth.get(p, 0), "share": round(depth.get(p, 0) / home_pv, 3) if home_pv else None} for p in (25, 50, 75, 90, 100)]
    viewers, ms = collections.defaultdict(set), collections.Counter()
    for r in people:
        if r["e"] == "view" and r.get("s"):
            viewers[r["d"].get("sec")].add(r["s"])
        elif r["e"] == "sec":
            for k, v in (r["d"].get("ms") or {}).items():
                ms[k] += int(v or 0)
    secs = []
    for k in set(viewers) | set(ms):
        if not k:
            continue
        n = len(viewers.get(k, ()))
        secs.append({"name": k, "case": k.startswith("case:"), "visits": n, "ms": ms.get(k, 0), "avgMs": int(ms.get(k, 0) / n) if n else None})
    out["sections"] = sorted([s for s in secs if not s["case"]], key=lambda s: -s["ms"])
    out["cases"] = sorted([s for s in secs if s["case"]], key=lambda s: -s["ms"])
    out["copies"] = top(collections.Counter(r["d"].get("txt") for r in people if r["e"] == "copy" and r["d"].get("txt")), 10, "text")
    out["selects"] = sum(1 for r in people if r["e"] == "sel")
    out["prints"] = sum(1 for r in people if r["e"] == "print")

    # clicks
    clicks = [r for r in people if r["e"] == "click"]
    out["clickTop"] = [{"el": k[0], "sec": k[1], "n": v} for k, v in collections.Counter((r["d"].get("el"), r["d"].get("sec")) for r in clicks).most_common(15)]
    heat = {"desktop": [], "mobile": []}
    for r in clicks[-4000:]:
        if r["p"] != "/" or not r["d"].get("h"):
            continue
        heat["mobile" if (r["d"].get("w") or 0) < 768 else "desktop"].append([r["d"].get("x"), round(r["d"]["y"] / r["d"]["h"], 4), r["d"].get("i", 0), r["d"].get("sec", "")])
    out["heat"] = heat
    out["outbound"] = top(collections.Counter(r["d"].get("to") for r in people if r["e"] == "out"), 15, "to")
    out["downloads"] = top(collections.Counter(r["d"].get("file") for r in people if r["e"] == "dl"), 10, "file")
    out["contactKinds"] = top(collections.Counter(r["d"].get("kind") for r in people if r["e"] == "contact"))

    # funnel (visits with consent): arrived -> read a case -> saw the contact section -> got in touch
    vs = {r["s"] for r in named_pv}
    case_v = {r["s"] for r in people if r["e"] == "view" and r.get("s") and str(r["d"].get("sec", "")).startswith("case:")}
    contact_seen = {r["s"] for r in people if r["e"] == "view" and r.get("s") and r["d"].get("sec") == "contact"}
    out["funnel"] = [{"step": "Visited", "n": len(vs)}, {"step": "Read a case", "n": len(vs & case_v)},
                     {"step": "Reached Contact", "n": len(vs & contact_seen)}, {"step": "Got in touch", "n": len(vs & contact_visits)}]
    quits = collections.Counter((r["d"].get("form"), r["d"].get("field")) for r in people if r["e"] == "form_quit")
    out["formQuits"] = [{"form": k[0], "field": k[1], "n": v} for k, v in quits.most_common(10)]
    out["forms"] = {"started": sum(1 for r in people if r["e"] == "form_start"), "sent": sum(1 for r in people if r["e"] == "form_submit")}

    # friction
    def by_el(kind):
        return [{"el": k[0], "sec": k[1], "n": v} for k, v in collections.Counter((r["d"].get("el"), r["d"].get("sec")) for r in people if r["e"] == kind).most_common(8)]
    out["ux"] = {"rage": by_el("rage"), "dead": by_el("dead"), "xscroll": sum(1 for r in people if r["e"] == "xscroll"),
                 "qback": sum(1 for r in people if r["e"] == "qback"),
                 "rageN": sum(1 for r in people if r["e"] == "rage"), "deadN": sum(1 for r in people if r["e"] == "dead"),
                 "visits": out["now"]["visits"]}

    # speed
    vit = collections.defaultdict(list)
    for r in people:
        if r["e"] == "vitals":
            for k in VITALS:
                if isinstance(r["d"].get(k), (int, float)):
                    vit[k].append(r["d"][k])
    vrows = []
    for k, (good, poor, unit) in VITALS.items():
        vals = vit.get(k, [])
        p75 = pctl(vals, 0.75)
        vrows.append({"name": k.upper(), "unit": unit, "p75": round(p75, 3) if p75 is not None else None, "n": len(vals),
                      "good": sum(1 for x in vals if x <= good), "ni": sum(1 for x in vals if good < x <= poor), "poor": sum(1 for x in vals if x > poor),
                      "goodAt": good, "poorAt": poor,
                      "rating": None if p75 is None else "good" if p75 <= good else "needs improvement" if p75 <= poor else "poor"})
    out["vitals"] = vrows
    errs = collections.Counter()
    for r in people:
        if r["e"] == "err":
            errs[r["d"].get("res") or (r["d"].get("m", "") + " " + r["d"].get("src", "")).strip()] += 1
    out["errors"] = top(errs, 10, "what")
    out["notFound"] = top(collections.Counter(r["p"] for r in rows if r["e"] == "nf"), 10, "path")

    # crawlers and AI bots
    bots = [r for r in rows if r["e"] == "bot"]
    ai_names = {"GPTBot", "ChatGPT-User", "OAI-SearchBot", "ClaudeBot", "PerplexityBot", "Google-Extended", "Bytespider", "CCBot", "Meta", "Amazonbot", "Applebot"}
    bc = collections.Counter(r["d"].get("bot") for r in bots)
    out["bots"] = [{"name": k, "n": v, "ai": k in ai_names} for k, v in bc.most_common()]
    out["botPages"] = top(collections.Counter(r["p"] for r in bots), 10, "path")
    out["aiVisits"] = sum(v for k, v in bc.items() if k in ai_names)

    # consent
    shown = sum(1 for r in people if r["e"] == "banner")
    choices = collections.Counter(r["d"].get("c") for r in people if r["e"] == "consent" and r["d"].get("prev") == "u")
    out["consent"] = {"shown": shown, "allowAll": choices.get("a1r1", 0), "analyticsOnly": choices.get("a1r0", 0), "rejected": choices.get("a0r0", 0),
                      "recorded": len({r["s"] for r in people if r.get("s") and r.get("c") == "a1r1"})}

    # recent visits (with consent)
    vis = collections.defaultdict(list)
    for r in people:
        if r.get("s"):
            vis[r["s"]].append(r)
    recent = []
    for s, items in vis.items():
        items.sort(key=lambda r: r["ts"])
        p = [r for r in items if r["e"] == "pv"]
        if not p:
            continue
        f = p[0]
        eng = {}
        for r in items:
            if r["e"] == "eng":
                eng[r["p"]] = max(eng.get(r["p"], 0), int(r["d"].get("ms") or 0))
        cases = [r["d"].get("sec", "")[5:] for r in items if r["e"] == "view" and str(r["d"].get("sec", "")).startswith("case:")]
        org = f["geo"].get("org") or ""
        recent.append({"at": f["ts"], "source": source_label(f["d"]), "channel": channel(f["d"]), "cc": f["geo"].get("cc"), "city": f["geo"].get("city"),
                       "org": "" if ISP.search(org) else org, "dev": f["ua"].get("dev"), "pages": len(p), "ms": sum(eng.values()),
                       "visit": f["d"].get("vn"), "contact": any(r["e"] == "contact" for r in items), "cases": cases[:5],
                       "rec": f.get("c") == "a1r1"})
    out["recent"] = sorted(recent, key=lambda x: -x["at"])[:30]

    out["gsc"] = gsc_view(a, b)
    out["audit"] = _load(AUDIT_FILE, {})
    return out


def home_line():
    """Yesterday in one line for Home."""
    try:
        v = view("yesterday")
    except Exception:
        return None
    n = v["now"]
    src = next((s["name"] for s in v["sources"] if s["name"] != "(direct)"), "")
    return {"date": v["period"]["from"], "people": n["reach"], "visits": n["visits"], "contacts": n["contacts"], "top": src,
            "error": v["state"].get("error") or ""}


# --- Search Console --------------------------------------------------------------------------------------------

def _client():
    raw = _kc_get(GSC_SERVICE, "client") or _kc_get(GMAIL_SERVICE, "client")
    return json.loads(raw) if raw else None


def gsc_connected():
    return bool(_kc_get(GSC_SERVICE, "refresh") and _client())


def gsc_auth():
    client = _client()
    if not client:
        print("No Google Cloud OAuth client in the keychain. Connect Inbox Cleanup first (python3 desk/gmail_clean.py auth).")
        return 1
    state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    got = {}

    class Once(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if q.get("state", [""])[0] == state:
                got["code"] = q.get("code", [""])[0]
                got["error"] = q.get("error", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<p>Search Console connected. You can close this window.</p>".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Once)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client["id"], "redirect_uri": redirect, "response_type": "code", "scope": GSC_SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
    print("Opening the Google sign-in page. If it does not open, visit this URL.\n" + url, flush=True)
    webbrowser.open(url)
    deadline = time.time() + 600
    srv.timeout = 600
    while "code" not in got and time.time() < deadline:
        srv.handle_request()
    srv.server_close()
    if not got.get("code"):
        print("Could not connect: " + (got.get("error") or "timed out"))
        return 1
    data = _form(TOKEN_URL, {"code": got["code"], "client_id": client["id"], "client_secret": client["secret"],
                             "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
    if not data.get("refresh_token"):
        print("No refresh token came back. Please try again.")
        return 1
    _kc_set(GSC_SERVICE, "client", json.dumps(client))
    _kc_set(GSC_SERVICE, "refresh", data["refresh_token"])
    print("Connected.")
    return 0


def _form(url, fields):
    req = urllib.request.Request(url, urllib.parse.urlencode(fields).encode(), {"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _gsc_token():
    client, refresh = _client(), _kc_get(GSC_SERVICE, "refresh")
    if not client or not refresh:
        return ""
    return _form(TOKEN_URL, {"client_id": client["id"], "client_secret": client["secret"], "refresh_token": refresh,
                             "grant_type": "refresh_token"}).get("access_token", "")


def _gsc_post(token, prop, body):
    url = "https://www.googleapis.com/webmasters/v3/sites/" + urllib.parse.quote(prop, safe="") + "/searchAnalytics/query"
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r).get("rows") or []


def gsc_pull():
    """Last 90 days by date, query, page, country and device. Google's numbers run 2-3 days behind."""
    keep = _load(GSC_FILE, {})
    try:
        token = _gsc_token()
        if not token:
            keep["error"] = "not connected"
            _save(GSC_FILE, keep)
            return keep
        req = urllib.request.Request("https://www.googleapis.com/webmasters/v3/sites", headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=30) as r:
            sites = [s["siteUrl"] for s in json.load(r).get("siteEntry", []) if s.get("permissionLevel") != "siteUnverifiedUser"]
        prop = next((s for s in sites if s == "sc-domain:tany.im"), "") or next((s for s in sites if "tany.im" in s), "")
        if not prop:
            keep.update(error="tany.im is not verified in Search Console yet", sites=sites, pulled=_now_iso())
            _save(GSC_FILE, keep)
            return keep
        end = dt.date.today()
        start = end - dt.timedelta(days=90)
        base = {"startDate": start.isoformat(), "endDate": end.isoformat(), "rowLimit": 1000, "dataState": "all"}
        keep = {"property": prop, "pulled": _now_iso(), "error": "", "from": start.isoformat(), "to": end.isoformat()}
        for dims in (["date"], ["date", "query"], ["date", "page"], ["date", "country"], ["date", "device"]):
            keep["_".join(dims)] = [[*r["keys"], r["clicks"], r["impressions"], round(r["position"], 2)]
                                    for r in _gsc_post(token, prop, dict(base, dimensions=dims))]
    except urllib.error.HTTPError as e:
        keep.update(error=f"Search Console answered {e.code}" + (" — turn on the Search Console API in Google Cloud" if e.code == 403 else ""), tried=_now_iso())
    except (OSError, ValueError) as e:
        keep.update(error=f"Could not reach Google ({type(e).__name__})", tried=_now_iso())
    _save(GSC_FILE, keep)
    return keep


def gsc_view(a, b):
    g = _load(GSC_FILE, {})
    out = {"connected": gsc_connected(), "property": g.get("property", ""), "pulled": g.get("pulled"), "error": g.get("error", "")}
    if not g.get("date"):
        return out
    lo, hi = a.isoformat(), b.isoformat()

    def agg(rows, n):
        acc = {}
        for r in rows:
            if lo <= r[0] <= hi:
                x = acc.setdefault(r[1], [0, 0, 0.0])
                x[0] += r[2]
                x[1] += r[3]
                x[2] += r[4] * r[3]
        res = [{"name": k, "clicks": v[0], "impr": v[1], "ctr": round(v[0] / v[1], 4) if v[1] else 0, "pos": round(v[2] / v[1], 1) if v[1] else None}
               for k, v in acc.items()]
        return sorted(res, key=lambda x: (-x["impr"], -x["clicks"]))[:n]
    days = [r for r in g["date"] if lo <= r[0] <= hi]
    clicks, impr = sum(r[1] for r in days), sum(r[2] for r in days)
    out.update(clicks=clicks, impr=impr, ctr=round(clicks / impr, 4) if impr else None,
               pos=round(sum(r[3] * r[2] for r in days) / impr, 1) if impr else None,
               daily=[{"date": r[0], "clicks": r[1], "impr": r[2]} for r in sorted(days)],
               queries=agg(g.get("date_query", []), 30), pages=agg(g.get("date_page", []), 15),
               countries=agg(g.get("date_country", []), 10), devices=agg(g.get("date_device", []), 3),
               lastDate=max((r[0] for r in g["date"]), default=""))
    return out


# --- SEO check of the live page --------------------------------------------------------------------------------

class _Page(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.title, self.meta, self.links, self.h, self.imgs, self.text, self.lang, self.ld = "", {}, {}, [], [], [], "", 0
        self._in, self._buf = None, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang", "")
        if tag == "meta":
            k = a.get("name") or a.get("property")
            if k:
                self.meta[k.lower()] = a.get("content", "")
        if tag == "link" and a.get("rel"):
            self.links[a["rel"].lower()] = a.get("href", "")
        if tag == "img":
            self.imgs.append(a)
        if tag == "script" and a.get("type") == "application/ld+json":
            self.ld += 1
        if tag in ("title", "h1", "h2", "h3"):
            self._in, self._buf = tag, []

    def handle_endtag(self, tag):
        if tag == self._in:
            t = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if tag == "title":
                self.title = t
            else:
                self.h.append((tag, t))
            self._in = None

    def handle_data(self, data):
        if self._in:
            self._buf.append(data)
        self.text.append(data)


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "TatsukiDesk-SEO-check/1"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
        return r.status, body, time.time() - t, dict(r.headers)


def audit(pages=("/", "/news/", "/privacy/")):
    res = {"checked": _now_iso(), "pages": [], "site": []}
    for path in pages:
        items = []
        try:
            status, body, secs, headers = _get(SITE + path)
        except urllib.error.HTTPError as e:
            res["pages"].append({"path": path, "items": [{"level": "fix", "msg": f"The page answers {e.code}."}]})
            continue
        except OSError:
            res["pages"].append({"path": path, "items": [{"level": "fix", "msg": "Could not open the page."}]})
            continue
        p = _Page()
        p.feed(body.decode("utf-8", "replace"))
        desc = p.meta.get("description", "")
        tl = len(p.title)
        if not p.title:
            items.append({"level": "fix", "msg": "No <title>."})
        elif tl < 10 or tl > 60:
            items.append({"level": "check", "msg": f"The title is {tl} characters (\"{p.title[:40]}\"). Search results show about 30 Japanese or 60 English characters."})
        if not desc:
            items.append({"level": "fix", "msg": "No meta description."})
        elif len(desc) < 50 or len(desc) > 160:
            items.append({"level": "check", "msg": f"The description is {len(desc)} characters. 70–120 Japanese characters usually show in full."})
        h1 = [t for tag, t in p.h if tag == "h1"]
        if len(h1) != 1:
            items.append({"level": "check", "msg": f"{len(h1)} <h1> headings (one is usual)."})
        for k in ("og:title", "og:description", "og:image"):
            if not p.meta.get(k):
                items.append({"level": "fix", "msg": f"No {k} (used when the page is shared)."})
        if not p.meta.get("twitter:card"):
            items.append({"level": "check", "msg": "No twitter:card (X shows a small preview without it)."})
        if "canonical" not in p.links:
            items.append({"level": "check", "msg": "No canonical link (tells search engines the main address)."})
        if not p.lang:
            items.append({"level": "fix", "msg": "No lang on <html>."})
        no_alt = [i.get("src", "") for i in p.imgs if not i.get("alt") and i.get("alt") != ""]
        if no_alt:
            items.append({"level": "fix", "msg": f"{len(no_alt)} images without alt text."})
        if path == "/" and not p.ld:
            items.append({"level": "check", "msg": "No structured data (JSON-LD for the organisation helps search and AI answers)."})
        kb = len(body) / 1024
        if secs > 1.5:
            items.append({"level": "check", "msg": f"The HTML took {secs:.1f} s to arrive."})
        res["pages"].append({"path": path, "title": p.title, "desc": desc, "kb": round(kb, 1), "secs": round(secs, 2), "h1": h1[:2], "items": items})
    for path, what in (("/robots.txt", "robots.txt"), ("/sitemap.xml", "sitemap.xml"), ("/llms.txt", "llms.txt")):
        try:
            status, body, _, _ = _get(SITE + path)
            text = body.decode("utf-8", "replace")
            if path == "/robots.txt":
                blocked = [b for b in ("GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended", "CCBot") if re.search(r"User-agent:\s*" + b + r"[\s\S]*?Disallow:\s*/\s", text, re.I)]
                res["site"].append({"level": "ok" if not blocked else "check", "msg": "robots.txt is there." + (f" It blocks {', '.join(blocked)}, so AI answers may not use the site." if blocked else "")})
                if "sitemap" not in text.lower():
                    res["site"].append({"level": "check", "msg": "robots.txt does not point to a sitemap."})
            else:
                res["site"].append({"level": "ok", "msg": f"{what} is there."})
        except urllib.error.HTTPError as e:
            lvl = "fix" if path == "/sitemap.xml" else "check"
            extra = {"/sitemap.xml": " Search engines find new pages faster with one.", "/llms.txt": " An optional summary for AI tools.", "/robots.txt": ""}[path]
            res["site"].append({"level": lvl, "msg": f"No {what} ({e.code}).{extra}"})
        except OSError:
            res["site"].append({"level": "check", "msg": f"Could not open {what}."})
    _save(AUDIT_FILE, res)
    return res


# --- links with UTM tags ----------------------------------------------------------------------------------------

def utm(source, medium="", campaign="", content="", path="/"):
    q = {k: v for k, v in (("utm_source", source), ("utm_medium", medium), ("utm_campaign", campaign), ("utm_content", content)) if v}
    clean = {k: re.sub(r"[^\w\-.~ぁ-んァ-ン一-龥]+", "-", v.strip().lower()).strip("-")[:60] for k, v in q.items()}
    path = path if path.startswith("/") else "/" + path
    return SITE + path + ("?" + urllib.parse.urlencode(clean) if clean else "")


# --- background loop (Desk server, port 4747 only) --------------------------------------------------------------

def watch():
    last_gsc = last_audit = 0
    time.sleep(20)
    while True:
        try:
            pull()
        except Exception:
            pass
        now = time.time()
        hour = dt.datetime.now().hour
        if gsc_connected() and now - last_gsc > 6 * 3600 and hour >= 6:
            try:
                gsc_pull()
            except Exception:
                pass
            last_gsc = now
        if now - last_audit > 7 * 86400:
            a = _load(AUDIT_FILE, {})
            try:
                done = dt.datetime.fromisoformat(a["checked"]).timestamp() if a.get("checked") else 0
            except ValueError:
                done = 0
            if now - done > 7 * 86400:
                try:
                    audit()
                except Exception:
                    pass
            last_audit = now
        time.sleep(PULL_EVERY)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "pull"
    if cmd == "pull":
        print(json.dumps({"new": pull(), **_load(STATE, {})}, ensure_ascii=False))
    elif cmd == "view":
        print(json.dumps(view(sys.argv[2] if len(sys.argv) > 2 else "7"), ensure_ascii=False, indent=1)[:20000])
    elif cmd == "gsc-auth":
        sys.exit(gsc_auth())
    elif cmd == "gsc":
        r = gsc_pull()
        print(json.dumps({k: (v if not isinstance(v, list) else len(v)) for k, v in r.items()}, ensure_ascii=False))
    elif cmd == "audit":
        print(json.dumps(audit(), ensure_ascii=False, indent=1))
    elif cmd == "utm":
        print(utm(*sys.argv[2:6]))
    else:
        print(__doc__)
