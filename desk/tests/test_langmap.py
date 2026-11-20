"""python3 -m unittest desk.tests.test_langmap (from the repo root). No network: raw/ is written by hand."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
TMP = tempfile.mkdtemp(prefix="desk-langmap-test-")
os.environ["LANGMAP_DIR"] = TMP
import langmap_build as B  # noqa: E402
import langmap  # noqa: E402

SQ = [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]
NE = {"type": "FeatureCollection", "features": [
    {"properties": {"ISO_A2": "RU", "ISO_A2_EH": "RU", "ADM0_A3": "RUS", "ISO_A3_EH": "RUS", "NAME_EN": "Russia", "NAME_JA": "ロシア", "CONTINENT": "Europe"},
     "geometry": {"type": "Polygon", "coordinates": SQ}},
    {"properties": {"ISO_A2": "-99", "ISO_A2_EH": "FR", "ADM0_A3": "FRA", "ISO_A3_EH": "FRA", "NAME_EN": "France", "CONTINENT": "Europe"},
     "geometry": {"type": "Polygon", "coordinates": SQ}},
    {"properties": {"ISO_A2": "AQ", "ADM0_A3": "ATA", "NAME_EN": "Antarctica"}, "geometry": {"type": "Polygon", "coordinates": SQ}},
]}
GLOT = {"languages": [
    {"glot": "russ1263", "name": "Russian", "iso3": "rus", "lat": "59", "lon": "50", "countries": "RU;KZ", "family": "indo1319"},
    {"glot": "tata1255", "name": "Tatar", "iso3": "tat", "lat": "55.8", "lon": "49", "countries": "RU", "family": "turk1311"},
    {"glot": "stan1290", "name": "French", "iso3": "fra", "lat": "48", "lon": "2", "countries": "FR", "family": "indo1319"}],
    "names": {"indo1319": "Indo-European", "turk1311": "Turkic", "slav1255": "Slavic"},
    "paths": {"russ1263": "indo1319/slav1255", "tata1255": "turk1311"}}
WD = "http://www.wikidata.org/entity/"


def write_raw():
    for name, data in {
        "ne": NE, "glottolog": GLOT, "worldbank": {"RUS": {"value": 143000000, "year": "2025"}},
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
        write_raw()
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
