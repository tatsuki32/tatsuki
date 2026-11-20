"""Language Map: what the page reads (desk/langmap_build.py makes the files in desk/data/langmap/).

Read-only for the page, except two things the person decides: rows marked "not right" (hidden.json, kept across
updates) and pressing Update. The data are public (Wikidata CC0, Glottolog CC BY 4.0, World Bank CC BY 4.0,
Natural Earth); nothing personal is read or sent here. Learned words are joined on the page from Translate's concepts.

  GET  /api/langmap                       summary: countries, languages (slim), regions, peoples, meta
  GET  /api/langmap/world                 country shapes (GeoJSON)
  GET  /api/langmap/country/<ISO2>        one country in full
  GET  /api/langmap/language/<id>         one language in full (lineage, scripts, sources)
  POST /api/langmap/build                 fetch again in the background (a few minutes to half an hour)
  PUT  /api/langmap/hide                  {kind, id, country, on}: a row that is wrong for that country stays hidden
"""
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import langmap_build as B  # noqa: E402

MONTH = 30 * 86400
_cache = {"sig": None, "data": None}
_build = {"running": False, "started": 0, "error": ""}
_lock = threading.Lock()


def _sig():
    out = []
    for n in ("countries.json", "languages.json", "regions.json", "ethnic.json", "meta.json", "hidden.json"):
        try:
            out.append(os.path.getmtime(B._path(n)))
        except OSError:
            out.append(0)
    return tuple(out)


def hidden():
    h = B._read("hidden.json", {})
    return h if isinstance(h, dict) else {}


def _hkey(kind, cid, rid):
    return f"{kind}:{cid}:{rid}"


def _load():
    sig = _sig()
    if _cache["sig"] == sig and _cache["data"] is not None:
        return _cache["data"]
    countries = B._read("countries.json", {}) or {}
    langs = B._read("languages.json", {}) or {}
    regions = B._read("regions.json", []) or []
    ethnic = B._read("ethnic.json", []) or []
    h = hidden()
    for c in countries.values():
        for f in ("official", "used", "spoken"):
            c[f] = [k for k in c.get(f, []) if not h.get(_hkey("lang", c["id"], k))]
        c["regions"] = [r for r in c.get("regions", []) if not h.get(_hkey("region", c["id"], r))]
        c["ethnic"] = [e for e in c.get("ethnic", []) if not h.get(_hkey("ethnic", c["id"], e))]
    _cache.update(sig=sig, data=(countries, langs, regions, ethnic))
    return _cache["data"]


SLIM_LANG = ("id", "name", "ja", "native", "iso1", "iso3", "family", "lat", "lon", "speakers", "countries")
SLIM_COUNTRY = ("id", "name", "ja", "native", "iso3", "pop", "lat", "lon", "official", "used", "spoken", "regions", "ethnic", "cont", "sub", "onMap", "capital")


def summary():
    countries, langs, regions, ethnic = _load()
    used = set()
    for c in countries.values():
        used.update(c.get("official", []))
        used.update(c.get("used", []))
    for r in regions:
        used.update(r.get("langs", []))
    for e in ethnic:
        used.update(e.get("langs", []))
    # every language goes to the page, but only the fields it draws with
    slim = {k: {f: L.get(f) for f in SLIM_LANG} for k, L in langs.items()}
    for k in used:
        if k in slim:
            slim[k]["key"] = True  # official or used somewhere: labelled on the map first
    meta = B._read("meta.json", {}) or {}
    return {"countries": {k: {f: c.get(f) for f in SLIM_COUNTRY} for k, c in countries.items()},
            "languages": slim, "regions": regions, "ethnic": ethnic,
            "meta": {"at": meta.get("at", ""), "counts": meta.get("counts", {}), "sources": meta.get("sources", B.SOURCES),
                     "steps": {n: {k: v for k, v in s.items() if k in ("ok", "at", "n", "error", "triedAt")} for n, s in (meta.get("steps") or {}).items()},
                     "fetched": meta.get("fetched", {})},
            "build": dict(_build)}


def world():
    return B._read("world.json", {"type": "FeatureCollection", "features": []})


def country(iso2):
    countries, langs, regions, ethnic = _load()
    c = countries.get(str(iso2).upper())
    if not c:
        return 404, {"error": "no such country"}
    regs = [r for r in regions if r["country"] == c["id"] and r["id"] in c.get("regions", [])]
    eth = [e for e in ethnic if e["id"] in c.get("ethnic", [])]
    keys = list(dict.fromkeys(c.get("official", []) + c.get("used", []) + c.get("spoken", []) + [k for r in regs for k in r.get("langs", [])]))
    return 200, {"country": c, "regions": regs, "ethnic": eth, "languages": {k: langs[k] for k in keys if k in langs}}


def language(key):
    countries, langs, regions, ethnic = _load()
    L = langs.get(key) or next((x for x in langs.values() if x.get("iso1") == key or x.get("glot") == key), None)
    if not L:
        return 404, {"error": "no such language"}
    cs = {iso2: {"name": countries[iso2]["name"], "pop": countries[iso2].get("pop"), "official": L["id"] in countries[iso2].get("official", []),
                 "used": L["id"] in countries[iso2].get("used", [])} for iso2 in L.get("countries", []) if iso2 in countries}
    regs = [r for r in regions if L["id"] in r.get("langs", [])]
    eth = [e for e in ethnic if L["id"] in e.get("langs", [])]
    return 200, {"language": L, "countries": cs, "regions": regs, "ethnic": eth}


def set_hidden(rec):
    kind, cid, rid = str(rec.get("kind") or ""), str(rec.get("country") or "").upper(), str(rec.get("id") or "")
    if kind not in ("lang", "region", "ethnic") or not cid or not rid:
        return 400, {"error": "kind, country and id are needed"}
    h = hidden()
    k = _hkey(kind, cid, rid)
    if rec.get("on") is False:
        h.pop(k, None)
    else:
        h[k] = int(time.time() * 1000)
    B._write("hidden.json", h)
    return 200, {"ok": True, "hidden": len(h)}


def _run_build(only=None):
    try:
        B.run(only) if only else B.run()
        _build["error"] = ""
    except Exception as e:  # the page shows why; the old files stay
        _build["error"] = f"{type(e).__name__}: {e}"[:300]
    finally:
        _build["running"] = False


def start_build(only=None):
    with _lock:
        if _build["running"]:
            return 200, {"build": dict(_build)}
        _build.update(running=True, started=int(time.time() * 1000), error="")
    threading.Thread(target=_run_build, args=(only,), daemon=True).start()
    return 200, {"build": dict(_build)}


def watch():
    """Once a month: fetch again (4747 only). A step that failed is tried again once a day.
    The first run waits for the person or `python3 desk/langmap_build.py`."""
    time.sleep(600)
    while True:
        try:
            meta = B._read("meta.json", {}) or {}
            last = meta.get("at") or ""
            age = (time.time() - time.mktime(time.strptime(last, "%Y-%m-%d"))) if last else None
            failed = {n for n, st in (meta.get("steps") or {}).items() if not st.get("ok") and st.get("triedAt") != B.today()}
            if age is not None and age > MONTH and not _build["running"]:
                start_build()
            elif failed and not _build["running"]:  # a step Wikidata turned away: once a day until it comes in
                start_build(failed)
        except Exception as e:
            print("langmap:", type(e).__name__, e, file=sys.stderr, flush=True)
        time.sleep(6 * 3600)
