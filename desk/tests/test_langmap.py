"""python3 -m unittest desk.tests.test_langmap (from the repo root).

No network: the raw answers are small fixtures written to a temporary LANGMAP_DIR, and the fetch tests use fake openers.
Covers desk/langmap_build.py (the map shapes, assembling countries, languages, regions and peoples, failed steps)
and desk/langmap.py (what the page reads, hiding a wrong row).
"""
import http.client
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
TMP = tempfile.mkdtemp(prefix="desk-langmap-test-")
os.environ["LANGMAP_DIR"] = TMP
import langmap_build as B  # noqa: E402
import langmap  # noqa: E402

WD = "http://www.wikidata.org/entity/"


def square(x, y, size=1.23456):
    return [[x, y], [x + size, y], [x + size, y + size], [x, y + size], [x, y]]


def feature(iso2, name, a3, geom, adm0=None, cont="Europe"):
    return {"type": "Feature", "properties": {"ISO_A2_EH": iso2, "ISO_A2": iso2, "ADM0_A3": adm0 or a3, "ISO_A3_EH": a3, "NAME_EN": name,
                                              "NAME_JA": name + "(ja)", "CONTINENT": cont, "SUBREGION": "Sub", "POP_EST": 1000, "POP_YEAR": 2019},
            "geometry": geom}


NE = {"type": "FeatureCollection", "features": [
    feature("RU", "Russia", "RUS", {"type": "Polygon", "coordinates": [square(30.123, 50.987)]}),
    feature("BY", "Belarus", "BLR", {"type": "MultiPolygon", "coordinates": [[square(24.0, 52.0)], [[[1, 1], [1, 1], [1, 1], [1, 1]]]]}),
    feature("-99", "Somaliland", "-99", {"type": "Polygon", "coordinates": [square(45.0, 9.0)]}, adm0="SOL", cont="Africa"),
    feature("AQ", "Antarctica", "ATA", {"type": "Polygon", "coordinates": [square(0.0, -80.0)]}, cont="Antarctica"),
    feature("XX", "Speck", "XXX", {"type": "Polygon", "coordinates": [[[5, 5], [5, 5], [5, 5], [5, 5]]]}),
]}

GLOTTOLOG = {
    "languages": [
        {"glot": "russ1263", "name": "Russian", "iso3": "rus", "area": "Eurasia", "lat": "59.0", "lon": "50.0", "countries": "RU;BY", "family": "indo1319"},
        {"glot": "bela1254", "name": "Belarusian", "iso3": "bel", "area": "Eurasia", "lat": "53.0", "lon": "28.0", "countries": "BY", "family": "indo1319"},
        {"glot": "tata1255", "name": "Tatar", "iso3": "tat", "area": "Eurasia", "lat": "55.0", "lon": "50.0", "countries": "RU", "family": "turk1311"},
        {"glot": "basq1248", "name": "Basque", "iso3": "eus", "area": "Eurasia", "lat": "43.0", "lon": "-2.0", "countries": "", "family": ""},
        {"glot": "noiso001", "name": "No Code", "iso3": "", "area": "Eurasia", "lat": "", "lon": "", "countries": "RU", "family": ""},
    ],
    "names": {"indo1319": "Indo-European", "slav1255": "Slavic", "turk1311": "Turkic", "russ1263": "Russian"},
    "paths": {"russ1263": "indo1319/slav1255", "bela1254": "indo1319/slav1255", "tata1255": "turk1311"},
}

LANGUAGES = [
    {"l": WD + "Q7737", "en": "Russian", "ja": "ロシア語", "native": "русский язык", "iso1": "ru", "iso3": "rus", "glot": "russ1263",
     "speakers": "150000000", "script": "Cyrillic"},
    {"l": WD + "Q7737", "en": "Russian", "iso3": "rus", "speakers": "258000000", "script": "Cyrillic"},
    {"l": WD + "Q9091", "en": "Belarusian", "ja": "ベラルーシ語", "iso1": "be", "iso3": "bel", "speakers": "5100000"},
    {"l": WD + "Q25285", "en": "Tatar", "ja": "タタール語", "iso1": "tt", "glot": "tata1255", "speakers": "5200000"},
]

COUNTRIES = [
    {"c": WD + "Q159", "iso2": "RU", "iso3": "RUS", "en": "Russia", "ja": "ロシア", "native": "Россия", "capital": "Moscow", "lat": "55.7", "lon": "37.6",
     "pop": "140000000"},
    {"c": WD + "Q184", "iso2": "BY", "iso3": "BLR", "en": "Belarus", "ja": "ベラルーシ", "capital": "Minsk", "pop": "9000000"},
    {"c": WD + "Q1", "iso2": "ZZZ", "en": "Not a two-letter code"},
    {"c": WD + "Q2", "iso2": "QQ", "iso3": "QQQ", "en": "Nowhere"},
]

WORLDBANK = {"RUS": {"value": 143000000, "year": "2025"}, "BLR": {"value": 9100000, "year": "2025"}}
OFFICIAL = [
    {"iso2": "RU", "l": WD + "Q7737", "iso3": "rus"},
    {"iso2": "BY", "l": WD + "Q7737", "iso3": "rus"},
    {"iso2": "BY", "l": WD + "Q9091", "iso3": "bel"},
]
USED = [
    {"iso2": "RU", "l": WD + "Q7737", "iso3": "rus"},  # already official: not listed twice
    {"iso2": "RU", "l": WD + "Q25285", "glot": "tata1255"},
    {"iso2": "RU", "l": WD + "Q999999", "iso3": "zzz"},  # unknown language: left out
]
REGIONS = [
    {"r": WD + "Q5481", "iso2": "RU", "en": "Tatarstan", "ja": "タタールスタン共和国", "type": "republic of Russia", "lat": "55.5", "lon": "50.9",
     "pop": "4000000", "l": WD + "Q25285", "glot": "tata1255"},
    {"r": WD + "Q5481", "iso2": "RU", "en": "Tatarstan", "type": "republic of Russia", "pop": "3900000", "l": WD + "Q7737", "iso3": "rus"},
    {"r": WD + "Q649", "iso2": "RU", "en": "Moscow", "type": "federal city", "pop": "13000000", "l": WD + "Q25285", "glot": "tata1255"},
    {"r": WD + "Q1749", "iso2": "RU", "en": "Moscow Oblast", "type": "oblast of Russia", "pop": "8000000", "l": WD + "Q7737", "iso3": "rus"},
    {"r": WD + "Q300", "iso2": "RU", "en": "Former Tatar Region", "type": "former republic", "pop": "1", "l": WD + "Q25285", "glot": "tata1255"},
]
ETHNIC = [
    {"e": WD + "Q484", "en": "Tatars", "ja": "タタール", "pop": "5300000", "l": WD + "Q25285", "country": "RU"},
    {"e": WD + "Q484", "en": "Tatars", "pop": "5000000", "l": WD + "Q25285", "country": "XX"},
    {"e": WD + "Q485", "en": "", "pop": "10", "l": WD + "Q25285", "country": "RU"},  # no name: dropped
    {"e": WD + "Q486", "en": "Ghosts", "pop": "10", "l": WD + "Q404", "country": "RU"},  # language not known: dropped
]

RAW = {"ne": NE, "glottolog": GLOTTOLOG, "languages": LANGUAGES, "countries": COUNTRIES, "worldbank": WORLDBANK,
       "official": OFFICIAL, "used": USED, "regions": REGIONS, "ethnic": ETHNIC}


def write_raw(**over):
    for name, data in {**RAW, **over}.items():
        B._write(f"raw/{name}.json", {"at": "2026-11-20", "data": data})


def clear():
    for root, dirs, files in os.walk(TMP, topdown=False):
        for f in files:
            os.remove(os.path.join(root, f))
    langmap._cache.update(sig=None, data=None)


class R(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


class TestWorld(unittest.TestCase):
    def test_shapes(self):
        w = B.assemble_world(NE)
        ids = [f["id"] for f in w["features"]]
        self.assertIn("RU", ids)
        self.assertIn("SOL", ids)  # no ISO code: the Natural Earth code stands in
        self.assertNotIn("AQ", ids)  # Antarctica is left out
        self.assertNotIn("XX", ids)  # a speck with one distinct point is left out
        ru = next(f for f in w["features"] if f["id"] == "RU")
        self.assertEqual(ru["properties"]["n"], "Russia")
        self.assertEqual(ru["properties"]["cont"], "Europe")
        by = next(f for f in w["features"] if f["id"] == "BY")
        self.assertEqual(len(by["geometry"]["coordinates"]), 1)  # the speck inside the MultiPolygon is dropped

    def test_coordinates_are_rounded(self):
        w = B.assemble_world(NE)

        def walk(c):
            if isinstance(c, (int, float)):
                yield c
            else:
                for x in c:
                    yield from walk(x)
        for f in w["features"]:
            for v in walk(f["geometry"]["coordinates"]):
                self.assertEqual(v, round(v, 2))


class TestAssemble(unittest.TestCase):
    def setUp(self):
        clear()
        write_raw()
        self.meta = B.assemble()
        self.countries = B._read("countries.json", {})
        self.langs = B._read("languages.json", {})
        self.regions = B._read("regions.json", [])
        self.ethnic = B._read("ethnic.json", [])

    def test_counts_and_sources(self):
        self.assertEqual(self.meta["counts"]["countries"], len(self.countries))
        self.assertEqual(self.meta["counts"]["languages"], len(self.langs))
        self.assertEqual(self.meta["counts"]["world"], 3)
        for key in ("ne", "glottolog", "wikidata", "worldbank"):
            self.assertIn(key, self.meta["sources"])
            name, url, licence = self.meta["sources"][key]
            self.assertTrue(url.startswith("https://"))
            self.assertTrue(licence)
        self.assertEqual(self.meta["fetched"]["glottolog"], "2026-11-20")

    def test_languages(self):
        ru = self.langs["rus"]
        self.assertEqual(ru["iso1"], "ru")
        self.assertEqual(ru["ja"], "ロシア語")
        self.assertEqual(ru["native"], "русский язык")
        self.assertEqual(ru["speakers"], 258000000)  # the larger Wikidata value
        self.assertEqual(ru["scripts"], ["Cyrillic"])
        self.assertEqual(ru["family"], "Indo-European")
        self.assertEqual(ru["lineage"], ["Indo-European", "Slavic"])
        self.assertEqual(ru["countries"], ["BY", "RU"])
        self.assertIn("wikidata", ru["src"])
        self.assertIn("glottolog", ru["src"])
        self.assertEqual(self.langs["tat"]["iso1"], "tt")  # matched by Glottolog code when the ISO code is missing
        self.assertEqual(self.langs["eus"]["family"], "Isolate")
        self.assertIsNone(self.langs["eus"]["speakers"])  # no number without a source
        self.assertIn("noiso001", self.langs)  # no ISO 639-3 code: the Glottolog code is the key
        self.assertNotIn("zzz", self.langs)

    def test_countries(self):
        self.assertEqual(sorted(self.countries), ["BY", "RU", "SOL"])  # ZZZ is not a code; QQ is nowhere
        ru = self.countries["RU"]
        self.assertEqual(ru["pop"], {"value": 143000000, "year": "2025", "src": "worldbank"})
        self.assertEqual(ru["ja"], "ロシア")
        self.assertEqual(ru["official"], ["rus"])
        self.assertEqual(ru["used"], ["tat"])  # Russian is official, so not repeated under used
        self.assertIn("noiso001", ru["spoken"])
        self.assertTrue(ru["onMap"])
        self.assertEqual(ru["cont"], "Europe")
        self.assertEqual(self.countries["BY"]["official"], ["rus", "bel"])
        sol = self.countries["SOL"]
        self.assertIsNone(sol["pop"])  # no World Bank row and no Wikidata row: no number
        for c in self.countries.values():
            for k in c["official"] + c["used"]:
                self.assertIn(k, self.langs)

    def test_regions(self):
        names = [r["name"] for r in self.regions]
        self.assertEqual(names, ["Tatarstan"])  # cities, former units and regions with only the official language are left out
        tat = self.regions[0]
        self.assertEqual(tat["id"], "Q5481")
        self.assertEqual(tat["pop"], 4000000)
        self.assertEqual(sorted(tat["langs"]), ["rus", "tat"])
        self.assertEqual(tat["type"], "republic of Russia")
        self.assertIn("wikidata", tat["src"])
        self.assertEqual(self.countries["RU"]["regions"], ["Q5481"])

    def test_ethnic(self):
        self.assertEqual([e["name"] for e in self.ethnic], ["Tatars"])
        e = self.ethnic[0]
        self.assertEqual(e["pop"], 5300000)
        self.assertEqual(e["langs"], ["tat"])
        self.assertEqual(e["countries"], ["RU"])  # XX is not a country here
        self.assertEqual(self.countries["RU"]["ethnic"], ["Q484"])

    def test_missing_shapes_keep_the_old_map(self):
        write_raw(ne={})
        B.assemble()
        self.assertEqual(len(B._read("world.json", {})["features"]), 3)


class TestFetching(unittest.TestCase):
    def setUp(self):
        clear()

    def test_failed_step_keeps_the_previous_raw(self):
        meta = {"steps": {}}
        B.step("worldbank", lambda: WORLDBANK, meta)
        self.assertTrue(meta["steps"]["worldbank"]["ok"])
        self.assertEqual(meta["steps"]["worldbank"]["n"], 2)

        def boom():
            raise RuntimeError("Wikidata: HTTP Error 429")
        B.step("worldbank", boom, meta)
        self.assertFalse(meta["steps"]["worldbank"]["ok"])
        self.assertIn("429", meta["steps"]["worldbank"]["error"])
        self.assertEqual(meta["steps"]["worldbank"]["at"], B.today())  # when it last worked
        self.assertEqual(B._read("raw/worldbank.json", {})["data"], WORLDBANK)
        self.assertFalse(B._read("meta.json", {})["steps"]["worldbank"]["ok"])

    def test_fetch_tries_again_after_a_cut_download(self):
        calls = []

        def opener(req, timeout):
            calls.append(req.full_url)
            if len(calls) == 1:
                raise http.client.IncompleteRead(b"half")
            return R(b"whole")
        with mock.patch.object(B.time, "sleep"):
            self.assertEqual(B.fetch("https://example.org/x", opener=opener), b"whole")
        self.assertEqual(len(calls), 2)

    def test_sparql_waits_out_a_429(self):
        answers = [urllib.error.HTTPError("u", 429, "slow down", {}, None),
                   R(json.dumps({"results": {"bindings": [{"iso2": {"value": "RU"}}]}}).encode())]

        def opener(req, timeout):
            a = answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return a
        with mock.patch.object(B.time, "sleep"):
            self.assertEqual(B.sparql("SELECT 1", opener=opener, gap=0), [{"iso2": "RU"}])

    def test_sparql_gives_up_on_a_bad_query(self):
        tries = []

        def opener(req, timeout):
            tries.append(1)
            raise urllib.error.HTTPError("u", 400, "bad query", {}, None)
        with mock.patch.object(B.time, "sleep"):
            with self.assertRaises(RuntimeError):
                B.sparql("SELECT nonsense", opener=opener, gap=0)
        self.assertEqual(len(tries), 1)

    def test_ethnic_goes_on_from_yesterdays_part(self):
        yesterday = (B.dt.date.today() - B.dt.timedelta(days=1)).isoformat()
        B._write("raw/ethnic.part.json", {"at": yesterday, "done": {"v2:Q1-1": [{"e": "kept"}]}})
        asked = []
        with mock.patch.object(B, "ETHNIC_BATCH", 1), \
                mock.patch.object(B, "sparql", side_effect=lambda q, opener=None: asked.append(q) or [{"e": "new"}]):
            out = B.fetch_ethnic(["Q1", "Q2"])
        self.assertEqual(out, [{"e": "kept"}, {"e": "new"}])
        self.assertEqual(len(asked), 1)  # only the batch not answered yet

    def test_ethnic_drops_a_part_older_than_a_week(self):
        old = (B.dt.date.today() - B.dt.timedelta(days=9)).isoformat()
        B._write("raw/ethnic.part.json", {"at": old, "done": {"Q1-1": [{"e": "old"}]}})
        with mock.patch.object(B, "ETHNIC_BATCH", 1), \
                mock.patch.object(B, "sparql", side_effect=lambda q, opener=None: [{"e": "new"}]):
            self.assertEqual(B.fetch_ethnic(["Q1"]), [{"e": "new"}])

    def test_qid(self):
        self.assertEqual(B.qid(WD + "Q7737"), "Q7737")
        self.assertEqual(B.qid(None), "")


class TestPage(unittest.TestCase):
    def setUp(self):
        clear()
        write_raw()
        B.assemble()

    def test_summary(self):
        s = langmap.summary()
        self.assertEqual(sorted(s["countries"]), ["BY", "RU", "SOL"])
        self.assertEqual(set(s["countries"]["RU"]), set(langmap.SLIM_COUNTRY))
        self.assertEqual(set(s["languages"]["rus"]) - {"key"}, set(langmap.SLIM_LANG))
        self.assertTrue(s["languages"]["rus"]["key"])  # official somewhere
        self.assertTrue(s["languages"]["tat"]["key"])  # used somewhere
        self.assertNotIn("key", s["languages"]["eus"])
        self.assertNotIn("lineage", s["languages"]["rus"])  # the full row only comes with /language/<id>
        self.assertEqual(s["meta"]["counts"]["countries"], 3)
        self.assertIn("running", s["build"])

    def test_world(self):
        self.assertEqual(len(langmap.world()["features"]), 3)

    def test_country(self):
        code, c = langmap.country("ru")
        self.assertEqual(code, 200)
        self.assertEqual(c["country"]["id"], "RU")
        self.assertEqual([r["name"] for r in c["regions"]], ["Tatarstan"])
        self.assertEqual([e["name"] for e in c["ethnic"]], ["Tatars"])
        self.assertIn("rus", c["languages"])
        self.assertIn("tat", c["languages"])
        self.assertEqual(langmap.country("QQ")[0], 404)

    def test_language_by_any_code(self):
        for key in ("rus", "ru", "russ1263"):
            code, L = langmap.language(key)
            self.assertEqual(code, 200, key)
            self.assertEqual(L["language"]["id"], "rus")
        code, L = langmap.language("tt")
        self.assertTrue(L["countries"]["RU"]["used"])
        self.assertFalse(L["countries"]["RU"]["official"])
        self.assertEqual([r["name"] for r in L["regions"]], ["Tatarstan"])
        self.assertEqual([e["name"] for e in L["ethnic"]], ["Tatars"])
        self.assertTrue(langmap.language("ru")[1]["countries"]["BY"]["official"])
        self.assertEqual(langmap.language("xx")[0], 404)

    def test_hide_a_wrong_row_and_bring_it_back(self):
        self.assertEqual(langmap.set_hidden({"kind": "lang", "country": "ru", "id": "tat"})[0], 200)
        langmap._cache.update(sig=None)
        self.assertNotIn("tat", langmap.summary()["countries"]["RU"]["used"])
        self.assertNotIn("tat", langmap.country("RU")[1]["country"]["used"])
        langmap.set_hidden({"kind": "region", "country": "RU", "id": "Q5481"})
        langmap._cache.update(sig=None)
        self.assertEqual(langmap.country("RU")[1]["regions"], [])
        langmap.set_hidden({"kind": "lang", "country": "RU", "id": "tat", "on": False})
        langmap._cache.update(sig=None)
        self.assertIn("tat", langmap.summary()["countries"]["RU"]["used"])
        self.assertEqual(len(langmap.hidden()), 1)

    def test_hide_survives_a_rebuild(self):
        langmap.set_hidden({"kind": "lang", "country": "RU", "id": "tat"})
        B.assemble()
        langmap._cache.update(sig=None)
        self.assertNotIn("tat", langmap.summary()["countries"]["RU"]["used"])

    def test_hide_needs_kind_country_and_id(self):
        for rec in ({}, {"kind": "lang", "country": "RU"}, {"kind": "word", "country": "RU", "id": "tat"}, {"kind": "lang", "id": "tat"}):
            self.assertEqual(langmap.set_hidden(rec)[0], 400, rec)

    def test_build_runs_once_at_a_time(self):
        started = []
        with mock.patch.object(langmap.B, "run", side_effect=lambda: started.append(1)), \
                mock.patch.object(langmap.threading, "Thread") as T:
            self.assertTrue(langmap.start_build()[1]["build"]["running"])
            self.assertTrue(langmap.start_build()[1]["build"]["running"])
            self.assertEqual(T.call_count, 1)  # the second press does not start another fetch
            langmap._run_build()
        self.assertEqual(started, [1])
        self.assertFalse(langmap._build["running"])
        self.assertEqual(langmap._build["error"], "")

    def test_build_error_is_shown(self):
        langmap._build.update(running=True)
        with mock.patch.object(langmap.B, "run", side_effect=RuntimeError("Wikidata down")):
            langmap._run_build()
        self.assertFalse(langmap._build["running"])
        self.assertIn("Wikidata down", langmap._build["error"])
        langmap._build.update(error="")

    def test_empty_folder(self):
        clear()
        s = langmap.summary()
        self.assertEqual(s["countries"], {})
        self.assertEqual(langmap.world()["features"], [])


# ---------- the first set (written with the feature): one fixture, assembled once ----------

SQ = [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]
NE_B = {"type": "FeatureCollection", "features": [
    {"properties": {"ISO_A2": "RU", "ISO_A2_EH": "RU", "ADM0_A3": "RUS", "ISO_A3_EH": "RUS", "NAME_EN": "Russia", "NAME_JA": "ロシア", "CONTINENT": "Europe"},
     "geometry": {"type": "Polygon", "coordinates": SQ}},
    {"properties": {"ISO_A2": "-99", "ISO_A2_EH": "FR", "ADM0_A3": "FRA", "ISO_A3_EH": "FRA", "NAME_EN": "France", "CONTINENT": "Europe"},
     "geometry": {"type": "Polygon", "coordinates": SQ}},
    {"properties": {"ISO_A2": "AQ", "ADM0_A3": "ATA", "NAME_EN": "Antarctica"}, "geometry": {"type": "Polygon", "coordinates": SQ}},
]}
GLOT_B = {"languages": [
    {"glot": "russ1263", "name": "Russian", "iso3": "rus", "lat": "59", "lon": "50", "countries": "RU;KZ", "family": "indo1319"},
    {"glot": "tata1255", "name": "Tatar", "iso3": "tat", "lat": "55.8", "lon": "49", "countries": "RU", "family": "turk1311"},
    {"glot": "stan1290", "name": "French", "iso3": "fra", "lat": "48", "lon": "2", "countries": "FR", "family": "indo1319"}],
    "names": {"indo1319": "Indo-European", "turk1311": "Turkic", "slav1255": "Slavic"},
    "paths": {"russ1263": "indo1319/slav1255", "tata1255": "turk1311"}}
def write_raw_b():
    for name, data in {
        "ne": NE_B, "glottolog": GLOT_B, "worldbank": {"RUS": {"value": 143000000, "year": "2025"}},
        "countries": [{"c": WD + "Q159", "iso2": "RU", "iso3": "RUS", "en": "Russia", "ja": "ロシア", "native": "Россия", "pop": "140000000", "lat": "60", "lon": "90"},
                      {"c": WD + "Q142", "iso2": "FR", "iso3": "FRA", "en": "France", "pop": "68000000"},
                      {"c": WD + "Q999", "iso2": "XX", "iso3": "XXX", "en": "Nowhere"}],
        "official": [{"iso2": "RU", "l": WD + "Q7737", "iso3": "rus"}, {"iso2": "FR", "l": WD + "Q150", "iso3": "fra"}],
        "used": [{"iso2": "RU", "l": WD + "Q25285", "iso3": "tat"}, {"iso2": "RU", "l": WD + "Q7737", "iso3": "rus"}],
        "languages": [{"l": WD + "Q7737", "iso3": "rus", "iso1": "ru", "ja": "ロシア語", "native": "русский язык", "speakers": "154000000", "script": "Cyrillic script"},
                      {"l": WD + "Q25285", "iso3": "tat", "iso1": "tt", "speakers": "5200000"},
                      {"l": WD + "Q150", "iso3": "fra", "iso1": "fr", "speakers": "77200000"}],
        "regions": [{"iso2": "RU", "r": WD + "Q5481", "en": "Tatarstan", "pop": "4003016", "lat": "55.5", "lon": "50.5", "l": WD + "Q25285", "iso3": "tat", "type": "republic of Russia"},
                    {"iso2": "RU", "r": WD + "Q5481", "en": "Tatarstan", "l": WD + "Q7737", "iso3": "rus", "type": "republic of Russia"},
                    {"iso2": "RU", "r": WD + "Q649", "en": "Moscow", "pop": "13000000", "l": WD + "Q7737", "iso3": "rus", "type": "city or town"},
                    {"iso2": "RU", "r": WD + "Q1", "en": "Old Oblast", "l": WD + "Q25285", "iso3": "tat", "type": "former administrative territorial entity"}],
        "ethnic": [{"e": WD + "Q485", "en": "Tatars", "pop": "6000000", "l": WD + "Q25285", "country": "RU"},
                   {"e": WD + "Q485", "en": "Tatars", "pop": "5300000", "l": WD + "Q25285", "country": "KZ"}],
    }.items():
        B._write(f"raw/{name}.json", {"at": "2026-11-20", "data": data})


class LangmapTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        clear()
        write_raw_b()
        B.assemble()

    def test_world_shapes(self):
        w = B._read("world.json", {})
        self.assertEqual(sorted(f["id"] for f in w["features"]), ["FR", "RU"])  # France's ISO_A2 is -99 in Natural Earth; Antarctica is left out

    def test_countries(self):
        c = B._read("countries.json", {})
        self.assertNotIn("XX", c)  # not on the map, not in the World Bank list, no official language
        ru = c["RU"]
        self.assertEqual(ru["pop"], {"value": 143000000, "year": "2025", "src": "worldbank"})
        self.assertEqual(ru["official"], ["rus"])
        self.assertEqual(ru["used"], ["tat"])  # Russian is official, so it is not listed twice
        self.assertEqual(ru["native"], "Россия")
        self.assertEqual(c["FR"]["pop"]["src"], "wikidata")

    def test_languages(self):
        L = B._read("languages.json", {})
        ru = L["rus"]
        self.assertEqual((ru["iso1"], ru["ja"], ru["speakers"], ru["family"]), ("ru", "ロシア語", 154000000, "Indo-European"))
        self.assertEqual(ru["lineage"], ["Indo-European", "Slavic"])
        self.assertEqual(ru["scripts"], ["Cyrillic script"])
        self.assertIn("KZ", ru["countries"])

    def test_regions_keep_only_own_languages_and_no_cities(self):
        R = B._read("regions.json", [])
        self.assertEqual([r["name"] for r in R], ["Tatarstan"])
        self.assertEqual(sorted(R[0]["langs"]), ["rus", "tat"])
        self.assertEqual(R[0]["pop"], 4003016)

    def test_ethnic(self):
        E = B._read("ethnic.json", [])
        self.assertEqual(E[0]["name"], "Tatars")
        self.assertEqual(E[0]["pop"], 6000000)
        self.assertEqual(E[0]["countries"], ["RU"])  # KZ is not in the country list here

    def test_api_and_hidden_rows(self):
        code, out = langmap.country("ru")
        self.assertEqual(code, 200)
        self.assertEqual([r["name"] for r in out["regions"]], ["Tatarstan"])
        self.assertIn("tat", out["languages"])
        code, out = langmap.language("tt")
        self.assertEqual((code, out["language"]["name"]), (200, "Tatar"))
        self.assertEqual(out["countries"]["RU"]["used"], True)
        self.assertEqual(langmap.set_hidden({"kind": "lang", "country": "RU", "id": "tat"})[0], 200)
        self.assertNotIn("tat", langmap.summary()["countries"]["RU"]["used"])
        langmap.set_hidden({"kind": "lang", "country": "RU", "id": "tat", "on": False})
        self.assertIn("tat", langmap.summary()["countries"]["RU"]["used"])
        self.assertEqual(langmap.country("ZZ")[0], 404)

    def test_a_failed_step_keeps_the_old_raw(self):
        meta = {"steps": {}}
        B.step("countries", lambda: (_ for _ in ()).throw(RuntimeError("Wikidata: 429")), meta)
        self.assertFalse(meta["steps"]["countries"]["ok"])
        self.assertEqual(len(B._read("raw/countries.json", {})["data"]), 3)

    def test_ethnic_needs_languages(self):
        with self.assertRaises(RuntimeError):
            B.fetch_ethnic([])


if __name__ == "__main__":
    unittest.main()
