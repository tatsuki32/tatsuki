"""Builds routine/dist for Cloudflare Pages: the app from routine/public, plus seed.json made from the Desk's data
(the routine items and the routine settings, not the daily records). dist/ is not in git: the seed holds personal items.

  python3 routine/build.py
"""
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(HERE, "public")
DIST = os.path.join(HERE, "dist")
DATA = os.path.join(HERE, "..", "desk", "data")


def read(name):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, list) else []
    except (OSError, ValueError):
        return []


def main():
    shutil.rmtree(DIST, ignore_errors=True)
    shutil.copytree(PUBLIC, DIST, ignore=shutil.ignore_patterns("icon.svg", ".DS_Store"))
    routines = [x for x in read("routines.json") if isinstance(x, dict) and x.get("id") and not x.get("deleted")]
    settings = [x for x in read("routinelog.json") if isinstance(x, dict) and x.get("id") == "settings"]
    with open(os.path.join(DIST, "seed.json"), "w", encoding="utf-8") as f:
        json.dump({"routines": routines, "routinelog": settings}, f, ensure_ascii=False)
    print(f"dist ready: {len(routines)} items")


if __name__ == "__main__":
    main()
