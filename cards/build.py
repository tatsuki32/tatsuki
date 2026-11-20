"""Builds cards/dist for Cloudflare Pages: the app from cards/public, plus the icons drawn by icon.swift when they are missing.
No data goes in: the phone gets its cards from the sync. dist/ is not in git.

  python3 cards/build.py
"""
import os
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(HERE, "public")
DIST = os.path.join(HERE, "dist")


def icons():
    """icon-512.png, icon-192.png, apple-touch-icon.png from icon.swift (macOS only). Skipped when they exist or swift is missing."""
    big = os.path.join(PUBLIC, "icon-512.png")
    if not os.path.exists(big):
        try:
            subprocess.run(["swift", os.path.join(HERE, "icon.swift"), big], check=True, capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            print("icons skipped (swift not available)")
            return
    for name, size in (("icon-192.png", 192), ("apple-touch-icon.png", 180)):
        out = os.path.join(PUBLIC, name)
        if not os.path.exists(out):
            subprocess.run(["swift", os.path.join(HERE, "icon.swift"), out, str(size)], check=True, capture_output=True, timeout=120)


def main():
    icons()
    shutil.rmtree(DIST, ignore_errors=True)
    shutil.copytree(PUBLIC, DIST, ignore=shutil.ignore_patterns(".DS_Store"))
    print("dist ready")


if __name__ == "__main__":
    main()
