#!/usr/bin/env python3
"""Tatsuki's Desk — Chrome history on this Mac (launchd, every hour). Reads Chrome only, never writes to it.

Chrome forgets visits after about 90 days, so each pass adds them to desk/data/webhistory/history.db and old ones stay.
Time on a page comes from desk/chrome-extension when that day has any; other days use an estimate from the history.
The two are never added together. Addresses lose everything after ? and #, except the few in KEEP_PARAMS.
backup_data.py never sends this folder anywhere.

  python3 chrome_web_sync.py          one pass
  python3 chrome_web_sync.py status   the last pass
"""
import csv
import datetime as dt
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from urllib.parse import parse_qsl, urlencode, urlsplit
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.abspath(__file__))
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
CHROME_EPOCH = 11644473600  # Chrome counts microseconds from 1601-01-01
EST_CAP = 300  # one visit never counts for more than five minutes when estimated
CHAIN_END = 0x20000000  # the page a redirect chain lands on
SUBFRAMES = (3, 4)  # page transitions for frames inside a page
SYNCED = 0  # visit_source: came from another device
KEEP_PARAMS = {"youtube.com": ("v",)}  # without these, different videos would look like one page
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")
EXTENSION_ID = "plbigkgjmipailajdphadhfamgcjpmhf"  # from the "key" in chrome-extension/manifest.json
MAX_SPAN = 3600
MAX_SPANS = 1000
PAGE = 100
PERIODS = {"today": 1, "week": 7, "month": 30}

SCHEMA = """
CREATE TABLE IF NOT EXISTS visits (id INTEGER PRIMARY KEY, at REAL NOT NULL, date TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL, site TEXT NOT NULL, est_sec REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS visits_at ON visits(at);
CREATE INDEX IF NOT EXISTS visits_date ON visits(date, site);
CREATE INDEX IF NOT EXISTS visits_url ON visits(url, at);
CREATE TABLE IF NOT EXISTS searches (id INTEGER PRIMARY KEY, term TEXT NOT NULL, at REAL NOT NULL, date TEXT NOT NULL, site TEXT NOT NULL, url TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS searches_at ON searches(at);
CREATE TABLE IF NOT EXISTS spans (id TEXT PRIMARY KEY, start REAL NOT NULL, end REAL NOT NULL, date TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL, site TEXT NOT NULL, audible INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS spans_date ON spans(date, site);
CREATE INDEX IF NOT EXISTS spans_url ON spans(url, start);
CREATE TABLE IF NOT EXISTS excluded (site TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def data_dir():
    return os.environ.get("WEBHISTORY_DIR") or os.path.join(ROOT, "data", "webhistory")


def chrome_path():
    return os.environ.get("CHROME_HISTORY") or os.path.expanduser("~/Library/Application Support/Google/Chrome/Default/History")


def connect():
    d = data_dir()
    os.makedirs(d, mode=0o700, exist_ok=True)
    path = os.path.join(d, "history.db")
    con = sqlite3.connect(path, timeout=10)
    os.chmod(path, 0o600)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA secure_delete=ON")  # an excluded site should really be gone from the file
    con.executescript(SCHEMA)
    return con


def date_of(at):
    return dt.datetime.fromtimestamp(at, TZ).strftime("%Y-%m-%d")


def clock(at):
    return dt.datetime.fromtimestamp(at, TZ).strftime("%Y-%m-%d %H:%M")


def clean_url(url):
    """(address without what follows ? and #, site), or None for anything that is not a web page on the net."""
    try:
        p = urlsplit(url or "")
        host = (p.hostname or "").lower()
        port = p.port
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not host or host in LOCAL_HOSTS:
        return None
    site = host[4:] if host.startswith("www.") else host
    keep = KEEP_PARAMS.get(site)
    query = urlencode([(k, v) for k, v in parse_qsl(p.query) if k in keep]) if keep else ""
    return f"{p.scheme}://{host}{':' + str(port) if port else ''}{p.path}{'?' + query if query else ''}", site


def is_excluded(site, excluded):
    return any(site == e or site.endswith("." + e) for e in excluded)


def excluded_sites(con):
    return [r["site"] for r in con.execute("SELECT site FROM excluded ORDER BY site")]


def set_meta(con, **values):
    con.executemany("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", [(k, json.dumps(v, ensure_ascii=False)) for k, v in values.items()])


def get_meta(con):
    return {r["key"]: json.loads(r["value"]) for r in con.execute("SELECT key, value FROM meta")}


def read_chrome(path):
    """(visits, searches) from a copy of Chrome's file: Chrome locks the real one while it runs."""
    d = tempfile.mkdtemp(prefix="desk-web-")
    try:
        copy = os.path.join(d, "History")
        shutil.copyfile(path, copy)
        src = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            cols = {r[1] for r in src.execute("PRAGMA table_info(visits)")}
            tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            foreign = "AND coalesce(v.originator_cache_guid, '') = ''" if "originator_cache_guid" in cols else ""
            synced = f"AND v.id NOT IN (SELECT id FROM visit_source WHERE source = {SYNCED})" if "visit_source" in tables else ""
            visits = src.execute(
                f"""SELECT v.visit_time, u.url, coalesce(u.title, ''), v.visit_duration, v.id
                    FROM visits v JOIN urls u ON u.id = v.url
                    WHERE u.hidden = 0 AND (v.transition & 255) NOT IN {SUBFRAMES} AND (v.transition & {CHAIN_END}) != 0 {foreign} {synced}
                    ORDER BY v.visit_time"""
            ).fetchall()
            searches = src.execute(
                f"""SELECT v.visit_time, k.term, u.url
                    FROM keyword_search_terms k JOIN urls u ON u.id = k.url_id JOIN visits v ON v.url = k.url_id
                    WHERE 1 = 1 {foreign} {synced}"""
            ).fetchall() if "keyword_search_terms" in tables else []
        finally:
            src.close()
        return visits, searches
    finally:
        shutil.rmtree(d, ignore_errors=True)


def estimate(at, next_at, duration):
    """The gap to the next page, or Chrome's own number when smaller, never more than EST_CAP. Open tabs make Chrome's alone far too large."""
    gap = next_at - at if next_at is not None else None
    known = [x for x in (gap, duration if duration > 0 else None) if x is not None]
    return round(min(min(known) if known else 0, EST_CAP), 1)


def import_rows(con, visits, searches):
    excluded = excluded_sites(con)
    times = [v[0] / 1e6 - CHROME_EPOCH for v in visits]
    new = 0
    for i, (micro, url, title, duration, _) in enumerate(visits):
        clean = clean_url(url)
        if not clean or is_excluded(clean[1], excluded):
            continue
        at = times[i]
        est = estimate(at, times[i + 1] if i + 1 < len(times) else None, duration / 1e6)
        cur = con.execute(
            """INSERT INTO visits(id, at, date, url, title, site, est_sec) VALUES(?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET est_sec=excluded.est_sec, title=CASE WHEN excluded.title != '' THEN excluded.title ELSE visits.title END""",
            (micro, at, date_of(at), clean[0], title, clean[1], est),
        )
        new += cur.rowcount > 0
    for micro, term, url in searches:
        clean = clean_url(url)
        if not clean or is_excluded(clean[1], excluded):
            continue
        at = micro / 1e6 - CHROME_EPOCH
        con.execute("INSERT OR IGNORE INTO searches(id, term, at, date, site, url) VALUES(?, ?, ?, ?, ?, ?)", (micro, term, at, date_of(at), clean[1], clean[0]))
    return new


def run():
    at = dt.datetime.now(TZ).isoformat(timespec="seconds")
    con = connect()
    try:
        try:
            visits, searches = read_chrome(chrome_path())
        except (sqlite3.Error, OSError) as e:
            set_meta(con, import_ok=False, import_at=at, import_error=str(e))
            con.commit()
            print(f"Chrome の履歴を読めません（{e}）。", file=sys.stderr)
            return 1
        with con:
            import_rows(con, visits, searches)
            set_meta(con, import_ok=True, import_at=at, import_error="")
        total = con.execute("SELECT count(*) FROM visits").fetchone()[0]
        print(f"Web履歴：{total}件")
        return 0
    finally:
        con.close()


def clean_diag(diag):
    """Counts of why the extension did or did not count time. Short words and numbers only, so nothing of a page can get in."""
    if not isinstance(diag, dict):
        return None
    return {k: v for k, v in diag.items() if isinstance(k, str) and re.fullmatch(r"[a-z-]{1,20}", k) and isinstance(v, int) and not isinstance(v, bool)}


def add_spans(spans, diag=None):
    """Time from the extension. The extension sends the same span again if it never heard back, so the id keeps one copy."""
    if not isinstance(spans, list) or len(spans) > MAX_SPANS:
        return None
    con = connect()
    added = 0
    try:
        excluded = excluded_sites(con)
        with con:
            for s in spans:
                if not isinstance(s, dict):
                    continue
                sid, start, end, title = s.get("id"), s.get("start"), s.get("end"), s.get("title") or ""
                if not (isinstance(sid, str) and 0 < len(sid) <= 64 and isinstance(title, str)):
                    continue
                if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                    continue
                if not 0 < end - start <= MAX_SPAN or end > time.time() + 300:
                    continue
                clean = clean_url(s.get("url")) if isinstance(s.get("url"), str) else None
                if not clean or is_excluded(clean[1], excluded):
                    continue
                cur = con.execute(
                    "INSERT OR IGNORE INTO spans(id, start, end, date, url, title, site, audible) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    (sid, start, end, date_of(start), clean[0], title[:500], clean[1], 1 if s.get("audible") else 0),
                )
                added += cur.rowcount > 0
            note = clean_diag(diag)
            if note is not None:
                set_meta(con, extension_diag=note)
            if spans:  # an empty call proves nothing about the extension
                set_meta(con, extension_at=dt.datetime.now(TZ).isoformat(timespec="seconds"))
    finally:
        con.close()
    return added


def valid_day(s):
    if not isinstance(s, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return False
    try:
        dt.date.fromisoformat(s)
    except ValueError:
        return False
    return True


def period_range(period, start=None, end=None, today=None):
    """(from, to, previous from, previous to). Dates are inclusive; 'all' has no previous period."""
    today = today or dt.datetime.now(TZ).date()
    if period == "custom" and valid_day(start) and valid_day(end) and start <= end:
        a, b = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    elif period == "all":
        return None, None, None, None
    else:
        b = today
        a = today - dt.timedelta(days=PERIODS.get(period, 7) - 1)
    n = (b - a).days + 1
    return a.isoformat(), b.isoformat(), (a - dt.timedelta(days=n)).isoformat(), (a - dt.timedelta(days=1)).isoformat()


def between(column, a, b):
    if a is None:
        return "1 = 1", []
    return f"{column} BETWEEN ? AND ?", [a, b]


def per_day(con, a, b):
    """{(date, site): (measured seconds, estimated seconds, visits)}. A day with any measured time uses only that."""
    where, args = between("date", a, b)
    measured = {(r["date"], r["site"]): r["s"] for r in con.execute(f"SELECT date, site, sum(end - start) s FROM spans WHERE {where} GROUP BY date, site", args)}
    days = {d for d, _ in measured}
    out = {}
    for r in con.execute(f"SELECT date, site, sum(est_sec) s, count(*) n FROM visits WHERE deleted = 0 AND {where} GROUP BY date, site", args):
        out[(r["date"], r["site"])] = (0, 0 if r["date"] in days else r["s"], r["n"])
    for key, s in measured.items():
        out[key] = (s, 0, out.get(key, (0, 0, 0))[2])
    return out


def like(q):
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def status(con):
    m = get_meta(con)
    first = con.execute("SELECT min(date) d FROM visits").fetchone()["d"]
    return {
        "importAt": m.get("import_at"), "importOk": m.get("import_ok"), "importError": m.get("import_error") or "",
        "extensionAt": m.get("extension_at"), "extensionDiag": m.get("extension_diag"), "extensionId": EXTENSION_ID, "firstDate": first,
        "excluded": excluded_sites(con),
    }


def sites_view(period="week", start=None, end=None, q=""):
    a, b, pa, pb = period_range(period, start, end)
    con = connect()
    try:
        cur = per_day(con, a, b)
        prev = per_day(con, pa, pb) if pa else {}
        rows, days = {}, {}
        for (date, site), (measured, est, n) in cur.items():
            r = rows.setdefault(site, {"site": site, "measured": 0, "estimated": 0, "visits": 0, "prev": 0})
            r["measured"] += measured
            r["estimated"] += est
            r["visits"] += n
            days[date] = days.get(date, 0) + measured + est
        for (_, site), (measured, est, _) in prev.items():
            if site in rows:
                rows[site]["prev"] += measured + est
        needle = q.strip().lower()
        out = []
        for r in rows.values():
            r["sec"] = round(r["measured"] + r["estimated"])
            r["measured"], r["estimated"], r["prev"] = round(r["measured"]), round(r["estimated"]), round(r["prev"])
            if r["sec"] >= 30 and (not needle or needle in r["site"]):
                out.append(r)
        out.sort(key=lambda r: -r["sec"])
        total = sum(days.values())
        return {
            "from": a, "to": b, "total": round(total), "measured": round(sum(v[0] for v in cur.values())),
            "prev": round(sum(v[0] + v[1] for v in prev.values())) if pa else None,
            "avg": round(total / len(days)) if days else 0,
            "days": [{"date": d, "sec": round(days[d])} for d in sorted(days)],
            "sites": out, "status": status(con),
        }
    finally:
        con.close()


def visit_filter(period, start, end, q, site, after, before):
    a, b, _, _ = period_range(period, start, end)
    where, args = between("v.date", a, b)
    where = [where]
    if after is not None and before is not None:  # a moment from the search list wins over the period
        where, args = ["v.at BETWEEN ? AND ?"], [after, before]
    if q.strip():
        where.append("(v.title LIKE ? ESCAPE '\\' OR v.url LIKE ? ESCAPE '\\')")
        args += [like(q.strip()), like(q.strip())]
    if site:
        where.append("v.site = ?")
        args.append(site)
    return " AND ".join(where), args


# Measured time goes to the newest visit of that page at or before the span's start.
VISIT_SQL = """SELECT v.id, v.at, v.url, v.title, v.site, v.est_sec,
  (SELECT sum(t.end - t.start) FROM spans t WHERE t.url = v.url AND t.start >= v.at - 5
     AND t.start < coalesce((SELECT min(n.at) FROM visits n WHERE n.url = v.url AND n.at > v.at), 9e12)) AS measured,
  EXISTS(SELECT 1 FROM spans d WHERE d.date = v.date) AS day_measured
  FROM visits v WHERE v.deleted = 0 AND {where} ORDER BY v.at DESC"""


def visit_row(r):
    measured = bool(r["day_measured"])
    return {"id": str(r["id"]), "time": clock(r["at"]), "url": r["url"], "title": r["title"], "site": r["site"], "sec": round((r["measured"] or 0) if measured else r["est_sec"]), "kind": "measured" if measured else "estimated"}


def visits_view(period="week", start=None, end=None, q="", site="", after=None, before=None, offset=0, limit=PAGE):
    where, args = visit_filter(period, start, end, q, site, after, before)
    con = connect()
    try:
        total = con.execute(f"SELECT count(*) FROM visits v WHERE v.deleted = 0 AND {where}", args).fetchone()[0]
        rows = con.execute(VISIT_SQL.format(where=where) + " LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
        return {"total": total, "offset": offset, "rows": [visit_row(r) for r in rows], "status": status(con)}
    finally:
        con.close()


def search_filter(period, start, end, q):
    a, b, _, _ = period_range(period, start, end)
    where, args = between("date", a, b)
    if q.strip():
        where += " AND term LIKE ? ESCAPE '\\'"
        args = args + [like(q.strip())]
    return where, args


def searches_view(period="week", start=None, end=None, q="", offset=0, limit=PAGE):
    where, args = search_filter(period, start, end, q)
    con = connect()
    try:
        total = con.execute(f"SELECT count(*) FROM searches WHERE deleted = 0 AND {where}", args).fetchone()[0]
        rows = con.execute(f"SELECT id, term, at, site FROM searches WHERE deleted = 0 AND {where} ORDER BY at DESC LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
        return {"total": total, "offset": offset, "rows": [{"id": str(r["id"]), "term": r["term"], "at": r["at"], "time": clock(r["at"]), "site": r["site"]} for r in rows], "status": status(con)}
    finally:
        con.close()


def hide(table, row_id):
    """Stays in the file so the next import cannot bring it back, as everywhere in Desk."""
    if table not in ("visits", "searches") or not str(row_id).isdigit():
        return False
    con = connect()
    try:
        with con:
            return con.execute(f"UPDATE {table} SET deleted = 1 WHERE id = ?", (int(row_id),)).rowcount > 0
    finally:
        con.close()


def clean_site(site):
    site = (site or "").strip().lower() if isinstance(site, str) else ""
    site = site[4:] if site.startswith("www.") else site
    return site if re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", site) else None


def set_excluded(site, on=True):
    """Turning a site off really deletes what is held about it, subdomains too. Turning it back on brings back only what Chrome still has."""
    site = clean_site(site)
    if not site:
        return None
    con = connect()
    try:
        with con:
            if on:
                con.execute("INSERT OR IGNORE INTO excluded(site) VALUES(?)", (site,))
                sub = "%." + site
                for table in ("visits", "searches", "spans"):
                    con.execute(f"DELETE FROM {table} WHERE site = ? OR site LIKE ?", (site, sub))
            else:
                con.execute("DELETE FROM excluded WHERE site = ?", (site,))
        return excluded_sites(con)
    finally:
        con.close()


def csv_cell(v):
    """A title from the web should not run as a formula when the file is opened in a spreadsheet."""
    s = str(v if v is not None else "")
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def export_csv(tab, period="week", start=None, end=None, q="", site="", after=None, before=None):
    out = io.StringIO()
    w = csv.writer(out)
    if tab == "sites":
        w.writerow(["サイト", "秒", "実測の秒", "推定の秒", "訪問数", "前の期間の秒"])
        for r in sites_view(period, start, end, q)["sites"]:
            w.writerow([csv_cell(r["site"]), r["sec"], r["measured"], r["estimated"], r["visits"], r["prev"]])
    elif tab == "searches":
        where, args = search_filter(period, start, end, q)
        con = connect()
        try:
            w.writerow(["日時", "検索語", "サイト"])
            for r in con.execute(f"SELECT term, at, site FROM searches WHERE deleted = 0 AND {where} ORDER BY at DESC", args):
                w.writerow([clock(r["at"]), csv_cell(r["term"]), csv_cell(r["site"])])
        finally:
            con.close()
    else:
        where, args = visit_filter(period, start, end, q, site, after, before)
        con = connect()
        try:
            w.writerow(["日時", "題名", "URL", "サイト", "秒", "種類"])
            for r in con.execute(VISIT_SQL.format(where=where), args):
                v = visit_row(r)
                w.writerow([v["time"], csv_cell(v["title"]), csv_cell(v["url"]), csv_cell(v["site"]), v["sec"], "実測" if v["kind"] == "measured" else "推定"])
        finally:
            con.close()
    return "﻿" + out.getvalue()  # the mark tells spreadsheets the file is UTF-8


if __name__ == "__main__":
    if sys.argv[1:] == ["status"]:
        c = connect()
        print(json.dumps(status(c), ensure_ascii=False, indent=2))
        c.close()
    else:
        sys.exit(run())
