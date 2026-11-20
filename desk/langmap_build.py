"""Language Map: every country, the languages spoken there, speakers, population, regions and peoples, from public data.

Requirements: https://claude.ai/artifact/RuQijhZjnPu8w6mTECaLwo

Sources (each row keeps its source and the day it was fetched):
- Natural Earth 1:110m Admin 0 countries (public domain): the shapes, English and Japanese names, continent.
- Glottolog (CC BY 4.0, glottolog-cldf): every language with an ISO 639-3 code, its family lineage, coordinates, countries.
- Wikidata (CC0): countries (names, native name, capital, coordinates), official languages (P37), languages used (P2936),
  language names in Japanese, native names, speakers (P1098), scripts (P282); regions = administrative divisions (P150)
  with an official language of their own; ethnic groups (Q41710) by the language they use (P2936) or their native language (P103), population if known.
- World Bank (CC BY 4.0): population, latest year (SP.POP.TOTL).

Everything lands in desk/data/langmap/ (LANGMAP_DIR in tests): raw/*.json = what each source answered, then
world.json, countries.json, languages.json, regions.json, ethnic.json, meta.json. A step that fails keeps the previous
raw answer, so the page never loses a file. Nothing personal is sent: the queries name countries and languages only.

  python3 desk/langmap_build.py            everything (a few minutes; Wikidata is the slow part)
  python3 desk/langmap_build.py assemble   rebuild the JSON files from raw/ without fetching
  python3 desk/langmap_build.py status     what is there and when it was fetched
"""
import csv
import datetime as dt
import http.client
import io
import json
import math
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
UA = "tatsuki-desk-langmap/0.1 (personal dashboard; standard library urllib)"
NE_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_0_countries.geojson"
GLOTTOLOG_LANGS = "https://raw.githubusercontent.com/glottolog/glottolog-cldf/master/cldf/languages.csv"
GLOTTOLOG_VALUES = "https://raw.githubusercontent.com/glottolog/glottolog-cldf/master/cldf/values.csv"
WIKIDATA = "https://query.wikidata.org/sparql"
WORLDBANK = "https://api.worldbank.org/v2/country/all/indicator/SP.POP.TOTL?format=json&per_page=400&mrv=1"
SOURCES = {
    "ne": ("Natural Earth 1:110m Admin 0", "https://www.naturalearthdata.com/", "public domain"),
    "glottolog": ("Glottolog", "https://glottolog.org/", "CC BY 4.0"),
    "wikidata": ("Wikidata", "https://www.wikidata.org/", "CC0"),
    "worldbank": ("World Bank", "https://data.worldbank.org/indicator/SP.POP.TOTL", "CC BY 4.0"),
}
ETHNIC_BATCH = 300
# Wikidata asks for at most one query a minute while its query service is under load (a 429 says so); we always keep that pace
WIKIDATA_GAP = 90
_last_sparql = [0.0]
ETHNIC_MAX_LANGS = 2400
# a first-level region we keep: its type says so, and it is not a city
REGION_TYPES = re.compile(r"republic|autonomous|federal subject|state|province|region|oblast|krai|okrug|canton|community|"
                          r"territory|country of|constituent|emirate|prefecture|county|department|island|entity|district|"
                          r"governorate|division|voivodeship|land of|federal", re.I)
REGION_NOT = re.compile(r"former|historical|\bcity\b|\btown\b|municipal|capital|megacity|settlement|village|borough|urban", re.I)
STOP = object()


def data_dir():
    return os.environ.get("LANGMAP_DIR") or os.path.join(ROOT, "data", "langmap")


def _path(*p):
    return os.path.join(data_dir(), *p)


def _read(name, default):
    try:
        with open(_path(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(name, value):
    d = os.path.dirname(_path(name))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".lm", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, _path(name))


def today():
    return dt.date.today().isoformat()


def log(msg):
    print(time.strftime("%H:%M:%S ") + msg, file=sys.stderr, flush=True)


# ---------- fetching ----------

def fetch(url, timeout=120, accept=None, opener=urllib.request.urlopen, tries=3):
    """The whole body. A download cut short (IncompleteRead) or a dropped connection is tried again."""
    last = None
    for i in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, **({"Accept": accept} if accept else {})})
        try:
            with opener(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError:
            raise
        except (http.client.HTTPException, OSError) as e:
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"download failed: {last}")


def sparql(query, opener=urllib.request.urlopen, tries=5, gap=None):
    """One query, at most one a minute (WIKIDATA_GAP). A 429 waits a full minute and tries again."""
    gap = WIKIDATA_GAP if gap is None else gap
    url = WIKIDATA + "?" + urllib.parse.urlencode({"query": query})
    last = None
    for i in range(tries):
        wait = _last_sparql[0] + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_sparql[0] = time.time()
        try:
            data = fetch(url, timeout=120, accept="application/sparql-results+json", opener=opener)
            return [{k: v.get("value") for k, v in b.items()} for b in json.loads(data)["results"]["bindings"]]
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504):
                break
            try:  # Wikidata says how long to wait; never less than our own gap
                _last_sparql[0] = time.time() + max(0, min(600, int(e.headers.get("Retry-After") or 0)) - gap)
            except (TypeError, ValueError):
                pass
        except (RuntimeError, ValueError, KeyError) as e:
            last = e
    raise RuntimeError(f"Wikidata: {last}")


def qid(uri):
    return (uri or "").rsplit("/", 1)[-1]


Q_COUNTRIES = """
SELECT ?c ?iso2 ?iso3 ?en ?ja ?native ?pop ?lat ?lon ?capital WHERE {
  ?c wdt:P297 ?iso2 .
  FILTER NOT EXISTS { ?c wdt:P576 ?gone }
  OPTIONAL { ?c wdt:P298 ?iso3 }
  OPTIONAL { ?c wdt:P1705 ?native }
  OPTIONAL { ?c wdt:P1082 ?pop }
  OPTIONAL { ?c p:P625/psv:P625 [ wikibase:geoLatitude ?lat ; wikibase:geoLongitude ?lon ] }
  OPTIONAL { ?c wdt:P36 ?cap . ?cap rdfs:label ?capital FILTER(LANG(?capital)="en") }
  OPTIONAL { ?c rdfs:label ?en FILTER(LANG(?en)="en") }
  OPTIONAL { ?c rdfs:label ?ja FILTER(LANG(?ja)="ja") }
}"""
Q_OFFICIAL = """
SELECT ?iso2 ?l ?iso3 ?glot WHERE {
  ?c wdt:P297 ?iso2 ; wdt:P37 ?l .
  FILTER NOT EXISTS { ?c wdt:P576 ?gone }
  OPTIONAL { ?l wdt:P220 ?iso3 } OPTIONAL { ?l wdt:P1394 ?glot }
}"""
Q_USED = """
SELECT ?iso2 ?l ?iso3 ?glot WHERE {
  ?c wdt:P297 ?iso2 ; wdt:P2936 ?l .
  FILTER NOT EXISTS { ?c wdt:P576 ?gone }
  OPTIONAL { ?l wdt:P220 ?iso3 } OPTIONAL { ?l wdt:P1394 ?glot }
}"""
Q_LANGUAGES = """
SELECT ?l ?iso3 ?iso1 ?glot ?en ?ja ?native ?speakers ?script WHERE {
  ?l wdt:P220 ?iso3 .
  OPTIONAL { ?l wdt:P218 ?iso1 } OPTIONAL { ?l wdt:P1394 ?glot } OPTIONAL { ?l wdt:P1098 ?speakers }
  OPTIONAL { ?l wdt:P1705 ?native }
  OPTIONAL { ?l wdt:P282 ?sc . ?sc rdfs:label ?script FILTER(LANG(?script)="en") }
  OPTIONAL { ?l rdfs:label ?en FILTER(LANG(?en)="en") }
  OPTIONAL { ?l rdfs:label ?ja FILTER(LANG(?ja)="ja") }
}"""
Q_REGIONS = """
SELECT ?iso2 ?r ?en ?ja ?native ?pop ?lat ?lon ?l ?iso3 ?glot ?type WHERE {
  ?c wdt:P297 ?iso2 ; wdt:P150 ?r .
  FILTER NOT EXISTS { ?c wdt:P576 ?gone }
  ?r wdt:P37 ?l .
  OPTIONAL { ?l wdt:P220 ?iso3 } OPTIONAL { ?l wdt:P1394 ?glot }
  OPTIONAL { ?r wdt:P1082 ?pop } OPTIONAL { ?r wdt:P1705 ?native }
  OPTIONAL { ?r p:P625/psv:P625 [ wikibase:geoLatitude ?lat ; wikibase:geoLongitude ?lon ] }
  OPTIONAL { ?r wdt:P31 ?t . ?t rdfs:label ?type FILTER(LANG(?type)="en") }
  OPTIONAL { ?r rdfs:label ?en FILTER(LANG(?en)="en") }
  OPTIONAL { ?r rdfs:label ?ja FILTER(LANG(?ja)="ja") }
}"""
Q_ETHNIC = """
SELECT ?e ?en ?ja ?pop ?l ?country WHERE {
  VALUES ?l { %s }
  ?e wdt:P31 wd:Q41710 ; (wdt:P2936|wdt:P103) ?l .
  OPTIONAL { ?e wdt:P1082 ?pop }
  OPTIONAL { ?e wdt:P17 ?cc . ?cc wdt:P297 ?country }
  OPTIONAL { ?e rdfs:label ?en FILTER(LANG(?en)="en") }
  OPTIONAL { ?e rdfs:label ?ja FILTER(LANG(?ja)="ja") }
}"""


# ---------- the steps: each saves raw/<name>.json ----------

def step(name, fn, meta):
    try:
        out = fn()
        _write(f"raw/{name}.json", {"at": today(), "data": out})
        n = len(out) if hasattr(out, "__len__") else 1
        meta["steps"][name] = {"ok": True, "at": today(), "n": n, "error": ""}
        log(f"{name}: {n}")
    except Exception as e:  # keep the previous raw file; say why
        meta["steps"][name] = {**meta["steps"].get(name, {}), "ok": False, "error": f"{type(e).__name__}: {e}"[:300], "triedAt": today()}
        log(f"{name} failed: {e}")
    _write("meta.json", meta)


def fetch_ne(opener=urllib.request.urlopen):
    return json.loads(fetch(NE_URL, opener=opener))


def fetch_glottolog(opener=urllib.request.urlopen):
    """Languages with their family, coordinates and countries, plus each language's classification path."""
    rows = list(csv.DictReader(io.StringIO(fetch(GLOTTOLOG_LANGS, opener=opener).decode("utf-8"))))
    langs = [r for r in rows if r.get("Level") == "language"]
    names = {r["ID"]: r["Name"] for r in rows if r.get("Level") in ("family", "language")}
    paths = {}
    reader = csv.reader(io.StringIO(fetch(GLOTTOLOG_VALUES, timeout=300, opener=opener).decode("utf-8")))
    head = next(reader)
    ix = {k: i for i, k in enumerate(head)}
    for row in reader:
        if row[ix["Parameter_ID"]] == "classification" and row[ix["Value"]]:
            paths[row[ix["Language_ID"]]] = row[ix["Value"]]
    return {"languages": [{"glot": r["ID"], "name": r["Name"], "iso3": r.get("ISO639P3code") or "", "area": r.get("Macroarea") or "",
                           "lat": r.get("Latitude") or "", "lon": r.get("Longitude") or "", "countries": r.get("Countries") or "",
                           "family": r.get("Family_ID") or ""} for r in langs],
            "names": names, "paths": paths}


def fetch_worldbank(opener=urllib.request.urlopen):
    d = json.loads(fetch(WORLDBANK, opener=opener))
    out = {}
    for x in d[1] if len(d) > 1 and d[1] else []:
        if x.get("value") is not None and x.get("countryiso3code"):
            out[x["countryiso3code"]] = {"value": int(x["value"]), "year": x["date"]}
    return out


def fetch_ethnic(lang_qids, opener=urllib.request.urlopen):
    out = []
    qids = list(dict.fromkeys(lang_qids))[:ETHNIC_MAX_LANGS]
    if not qids:
        raise RuntimeError("no languages from Wikidata yet (official/used failed), so the peoples are left as they were")
    # batches already answered in the last week are kept in raw/ethnic.part.json, so a run cut short
    # (Wikidata turning queries away for hours) goes on from there the next day
    part = _read("raw/ethnic.part.json", {}) or {}
    try:
        fresh = (time.time() - time.mktime(time.strptime(part.get("at") or "", "%Y-%m-%d"))) < 7 * 86400
    except ValueError:
        fresh = False
    if not fresh or not isinstance(part.get("done"), dict):
        part = {"at": today(), "done": {}}
    for i in range(0, len(qids), ETHNIC_BATCH):
        batch = qids[i:i + ETHNIC_BATCH]
        key = f"v2:{batch[0]}-{len(batch)}"  # v2: the query with P103 and population optional
        if key not in part["done"]:
            part["done"][key] = sparql(Q_ETHNIC % " ".join("wd:" + q for q in batch), opener=opener)
            _write("raw/ethnic.part.json", part)
        out += part["done"][key]
    try:
        os.remove(_path("raw/ethnic.part.json"))
    except OSError:
        pass
    return out


def run(only=None, opener=urllib.request.urlopen):
    """All steps, then assemble. `only` = a set of step names to fetch again (others reuse raw/)."""
    meta = _read("meta.json", {}) or {}
    meta.setdefault("steps", {})
    want = lambda n: only is None or n in only  # noqa: E731
    if want("ne"):
        step("ne", lambda: fetch_ne(opener), meta)
    if want("glottolog"):
        step("glottolog", lambda: fetch_glottolog(opener), meta)
    if want("worldbank"):
        step("worldbank", lambda: fetch_worldbank(opener), meta)
    if want("countries"):
        step("countries", lambda: sparql(Q_COUNTRIES, opener), meta)
    if want("official"):
        step("official", lambda: sparql(Q_OFFICIAL, opener), meta)
    if want("used"):
        step("used", lambda: sparql(Q_USED, opener), meta)
    if want("languages"):
        step("languages", lambda: sparql(Q_LANGUAGES, opener), meta)
    if want("regions"):
        step("regions", lambda: sparql(Q_REGIONS, opener), meta)
    if want("ethnic"):
        off = (_read("raw/official.json", {}) or {}).get("data", []) + (_read("raw/used.json", {}) or {}).get("data", [])
        qids = [qid(r.get("l")) for r in off if r.get("l")]
        step("ethnic", lambda: fetch_ethnic(qids, opener), meta)
    return assemble()


# ---------- assembling the page's files from raw/ ----------

def _num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _round_coords(c):
    if isinstance(c, (int, float)):
        return round(c, 2)
    return [_round_coords(x) for x in c]


def _simplify(geom):
    """Rounds to 0.01 degrees and drops rings with fewer than 4 distinct points (specks)."""
    def ring_ok(ring):
        return len({tuple(p) for p in ring}) >= 4

    if geom["type"] == "Polygon":
        rings = [r for r in _round_coords(geom["coordinates"]) if ring_ok(r)]
        return {"type": "Polygon", "coordinates": rings} if rings else None
    if geom["type"] == "MultiPolygon":
        polys = []
        for poly in _round_coords(geom["coordinates"]):
            rings = [r for r in poly if ring_ok(r)]
            if rings:
                polys.append(rings)
        return {"type": "MultiPolygon", "coordinates": polys} if polys else None
    return geom


def assemble_world(ne):
    feats = []
    for f in ne.get("features", []):
        p = f.get("properties", {})
        iso2 = p.get("ISO_A2_EH") if p.get("ISO_A2_EH") not in (None, "-99") else p.get("ISO_A2")
        if iso2 in (None, "-99"):
            iso2 = p.get("ADM0_A3")  # Northern Cyprus, Somaliland: no ISO code, the NE code stands in
        if p.get("ADM0_A3") == "ATA":
            continue  # Antarctica: no languages, and it swallows the map
        g = _simplify(f["geometry"])
        if not g:
            continue
        feats.append({"type": "Feature", "id": iso2, "properties": {"n": p.get("NAME_EN") or p.get("NAME"), "ja": p.get("NAME_JA") or "",
                                                                     "a3": p.get("ISO_A3_EH") if p.get("ISO_A3_EH") != "-99" else p.get("ADM0_A3"),
                                                                     "cont": p.get("CONTINENT") or "", "sub": p.get("SUBREGION") or "",
                                                                     "pop": p.get("POP_EST"), "popYear": p.get("POP_YEAR")}, "geometry": g})
    return {"type": "FeatureCollection", "features": feats}


def lang_key(iso3, glot):
    return iso3 or glot or ""


def assemble():
    raw = {n: (_read(f"raw/{n}.json", {}) or {}) for n in ("ne", "glottolog", "worldbank", "countries", "official", "used", "languages", "regions", "ethnic")}
    at = {n: raw[n].get("at", "") for n in raw}
    ne = raw["ne"].get("data") or {}
    world = assemble_world(ne) if ne else _read("world.json", {"type": "FeatureCollection", "features": []})
    _write("world.json", world)
    ne_props = {f["id"]: f["properties"] for f in world["features"]}

    # --- languages: Glottolog is the spine (one row per language with an ISO 639-3 code); Wikidata adds names, speakers, scripts
    gl = raw["glottolog"].get("data") or {}
    names = gl.get("names", {})
    paths = gl.get("paths", {})
    langs = {}
    for r in gl.get("languages", []):
        key = lang_key(r.get("iso3"), r.get("glot"))
        if not key:
            continue
        lineage = [names.get(g, g) for g in (paths.get(r["glot"]) or "").split("/") if g]
        langs[key] = {"id": key, "glot": r["glot"], "iso3": r.get("iso3") or "", "iso1": "", "name": r["name"], "ja": "", "native": "",
                      "family": lineage[0] if lineage else (names.get(r.get("family")) or "Isolate"), "lineage": lineage,
                      "area": r.get("area") or "", "lat": _f(r.get("lat")), "lon": _f(r.get("lon")),
                      "countries": [c for c in (r.get("countries") or "").split(";") if c], "speakers": None, "scripts": [],
                      "wd": "", "src": {"glottolog": at["glottolog"]}}
    by_glot = {v["glot"]: k for k, v in langs.items()}
    wd_lang_key = {}  # Wikidata QID -> our key
    for r in raw["languages"].get("data") or []:
        key = r.get("iso3") if r.get("iso3") in langs else by_glot.get(r.get("glot") or "")
        if not key:
            continue
        L = langs[key]
        q = qid(r.get("l"))
        wd_lang_key[q] = key
        L["wd"] = L["wd"] or q
        if r.get("iso1") and not L["iso1"]:
            L["iso1"] = r["iso1"]
        if r.get("en") and L.get("wdName") is None:
            L["wdName"] = r["en"]
        if r.get("ja") and not L["ja"]:
            L["ja"] = r["ja"]
        if r.get("native") and not L["native"]:
            L["native"] = r["native"]
        sp = _num(r.get("speakers"))
        if sp and (L["speakers"] is None or sp > L["speakers"]):
            L["speakers"] = sp
            L["src"]["wikidata"] = at["languages"]
        if r.get("script") and r["script"] not in L["scripts"]:
            L["scripts"].append(r["script"])
    for L in langs.values():
        if L.pop("wdName", None) and L["wd"]:
            L["src"]["wikidata"] = at["languages"]

    # --- countries: Wikidata rows (several per country: native names, populations); Natural Earth and World Bank fill in
    wb = raw["worldbank"].get("data") or {}
    countries = {}
    for r in raw["countries"].get("data") or []:
        iso2 = r.get("iso2")
        if not iso2 or len(iso2) != 2:
            continue
        c = countries.setdefault(iso2, {"id": iso2, "wd": qid(r.get("c")), "iso3": "", "name": "", "ja": "", "native": "", "capital": "",
                                        "pop": None, "lat": None, "lon": None, "official": [], "used": [], "spoken": [], "regions": [], "ethnic": [], "src": {}})
        c["iso3"] = c["iso3"] or r.get("iso3") or ""
        c["name"] = c["name"] or r.get("en") or ""
        c["ja"] = c["ja"] or r.get("ja") or ""
        c["native"] = c["native"] or r.get("native") or ""
        c["capital"] = c["capital"] or r.get("capital") or ""
        if c["lat"] is None and r.get("lat"):
            c["lat"], c["lon"] = _f(r["lat"]), _f(r["lon"])
        p = _num(r.get("pop"))
        if p and (c["pop"] is None or p > (c["pop"].get("value") or 0)):
            c["pop"] = {"value": p, "year": "", "src": "wikidata"}
            c["src"]["wikidata"] = at["countries"]
    for iso2, p in ne_props.items():
        c = countries.setdefault(iso2, {"id": iso2, "wd": "", "iso3": p.get("a3") or "", "name": p.get("n") or "", "ja": p.get("ja") or "", "native": "",
                                        "capital": "", "pop": None, "lat": None, "lon": None, "official": [], "used": [], "spoken": [], "regions": [], "ethnic": [], "src": {}})
        c["name"] = c["name"] or p.get("n") or ""
        c["ja"] = c["ja"] or p.get("ja") or ""
        c["iso3"] = c["iso3"] or p.get("a3") or ""
        c["cont"], c["sub"] = p.get("cont") or "", p.get("sub") or ""
        c["onMap"] = True
        c["src"]["ne"] = at["ne"]
    # keep real places: on the map, or known to the World Bank, or with an official language
    official_iso2 = {r.get("iso2") for r in raw["official"].get("data") or []}
    for iso2 in list(countries):
        c = countries[iso2]
        if not (c.get("onMap") or c["iso3"] in wb or iso2 in official_iso2):
            del countries[iso2]
            continue
        c.setdefault("onMap", False)
        if c["iso3"] in wb:
            c["pop"] = {"value": wb[c["iso3"]]["value"], "year": wb[c["iso3"]]["year"], "src": "worldbank"}
            c["src"]["worldbank"] = at["worldbank"]

    def add_lang(c, field, key):
        if key and key in langs and key not in c[field]:
            c[field].append(key)
            if c["id"] not in langs[key]["countries"]:
                langs[key]["countries"].append(c["id"])

    def key_of(r):
        key = r.get("iso3") if r.get("iso3") in langs else by_glot.get(r.get("glot") or "")
        return key or wd_lang_key.get(qid(r.get("l")))

    for r in raw["official"].get("data") or []:
        c = countries.get(r.get("iso2"))
        if c:
            add_lang(c, "official", key_of(r))
    for r in raw["used"].get("data") or []:
        c = countries.get(r.get("iso2"))
        if c:
            add_lang(c, "used", key_of(r))
    for key, L in langs.items():
        for iso2 in L["countries"]:
            c = countries.get(iso2)
            if c and key not in c["spoken"]:
                c["spoken"].append(key)
    for c in countries.values():
        c["used"] = [k for k in c["used"] if k not in c["official"]]
        c["src"]["glottolog"] = at["glottolog"]

    # --- regions: first-level divisions with an official language of their own (P37), not cities, language not the country's
    regions = {}
    for r in raw["regions"].get("data") or []:
        c = countries.get(r.get("iso2"))
        key = key_of(r)
        if not c or not key:
            continue
        t = r.get("type") or ""
        rid = qid(r.get("r"))
        reg = regions.get(rid)
        if not reg:
            reg = regions[rid] = {"id": rid, "country": c["id"], "name": r.get("en") or "", "ja": r.get("ja") or "", "native": r.get("native") or "",
                                  "types": [], "pop": _num(r.get("pop")), "lat": _f(r.get("lat")), "lon": _f(r.get("lon")), "langs": [],
                                  "src": {"wikidata": at["regions"]}}
        if t and t not in reg["types"]:
            reg["types"].append(t)
        if key not in reg["langs"]:
            reg["langs"].append(key)
        p = _num(r.get("pop"))
        if p and (reg["pop"] is None or p > reg["pop"]):
            reg["pop"] = p
    kept = []
    for reg in regions.values():
        types = " ".join(reg["types"])
        if REGION_NOT.search(types) or not REGION_TYPES.search(types):
            continue
        c = countries[reg["country"]]
        own = [k for k in reg["langs"] if k not in c["official"]]
        if not own:
            continue
        reg["type"] = next((t for t in reg["types"] if REGION_TYPES.search(t) and not REGION_NOT.search(t)), reg["types"][0] if reg["types"] else "")
        reg.pop("types", None)
        kept.append(reg)
        c["regions"].append(reg["id"])
        for k in reg["langs"]:
            if c["id"] not in langs[k]["countries"]:
                langs[k]["countries"].append(c["id"])
    kept.sort(key=lambda r: (r["country"], -(r["pop"] or 0)))

    # --- ethnic groups: by the language they use or their native language; population when Wikidata has one
    ethnic = {}
    for r in raw["ethnic"].get("data") or []:
        key = wd_lang_key.get(qid(r.get("l")))
        if not key:
            continue
        eid = qid(r.get("e"))
        e = ethnic.setdefault(eid, {"id": eid, "name": r.get("en") or "", "ja": r.get("ja") or "", "pop": None, "langs": [], "countries": [],
                                    "src": {"wikidata": at["ethnic"]}})
        if key not in e["langs"]:
            e["langs"].append(key)
        if r.get("country") and r["country"] in countries and r["country"] not in e["countries"]:
            e["countries"].append(r["country"])
        p = _num(r.get("pop"))
        if p and (e["pop"] is None or p > e["pop"]):
            e["pop"] = p
    eth = [e for e in ethnic.values() if e["name"]]
    eth.sort(key=lambda e: -(e["pop"] or 0))
    for e in eth:
        for iso2 in e["countries"]:
            countries[iso2]["ethnic"].append(e["id"])

    for L in langs.values():
        L["countries"].sort()
    _write("countries.json", countries)
    _write("languages.json", langs)
    _write("regions.json", kept)
    _write("ethnic.json", eth)
    meta = _read("meta.json", {}) or {}
    meta.update({"at": today(), "counts": {"countries": len(countries), "languages": len(langs), "regions": len(kept), "ethnic": len(eth),
                                            "world": len(world["features"])}, "sources": SOURCES, "fetched": at})
    _write("meta.json", meta)
    log(f"assembled: {meta['counts']}")
    return meta


def _f(v):
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return None


def status():
    return _read("meta.json", {})


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd == "all":
        print(json.dumps(run(), ensure_ascii=False, indent=1))
    elif cmd == "assemble":
        print(json.dumps(assemble(), ensure_ascii=False, indent=1))
    elif cmd == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=1))
    elif cmd == "only":
        print(json.dumps(run(set(sys.argv[2:])), ensure_ascii=False, indent=1))
    else:
        print(__doc__)
