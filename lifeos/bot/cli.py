#!/usr/bin/env python3
"""Try the bot without Telegram or a token.

  python3 lifeos/bot/cli.py "text"              handles the text and prints the reply
  python3 lifeos/bot/cli.py --which "text"      shows which tool would take it (runs nothing)
  python3 lifeos/bot/cli.py --voice FILE        treats FILE as a voice message
  python3 lifeos/bot/cli.py --list              shows the routing order and broken handlers
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lifeos.bot import router, voice  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="Try the Life OS bot without Telegram")
    ap.add_argument("text", nargs="*", help="text to send")
    ap.add_argument("--which", action="store_true", help="only show which tool would take it")
    ap.add_argument("--voice", metavar="FILE", help="treat FILE as a voice message")
    ap.add_argument("--list", action="store_true", help="show the routing order")
    ap.add_argument("--bot", choices=("records", "secretary"), help="try with that bot's tools only")
    a = ap.parse_args(argv)
    found = router.discover()
    if a.bot:
        found.routes = router.for_bot(found.routes, a.bot)
    if a.list:
        for r in found.routes:
            print(f"{r.priority:>3}  {r.slug}  {r.name}")
        if not found.routes:
            print("No tool has a handler.py yet")
        for slug, err in found.errors:
            print(f"Could not load  {slug}  {err}")
        return 0
    if a.voice:
        if not voice.connected():
            print(voice.NOT_CONNECTED)
            return 0
        print(router.dispatch(voice.transcribe(a.voice), router.Ctx(is_voice=True), found.routes))
        return 0
    text = " ".join(a.text)
    if not text:
        ap.print_usage()
        return 2
    if a.which:
        r = router.which(text, found.routes)
        print(r.slug if r else "No tool takes it (it stays in Unsorted)")
        return 0
    print(router.dispatch(text, router.Ctx(), found.routes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
