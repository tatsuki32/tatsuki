#!/usr/bin/env python3
"""Life OS 02: follow-ups. Mail goes through MORNING_RUN.md; this never sends any.

  run.py snapshot FILE|-          saves today's sent-thread snapshot
  run.py morning                  applies it: queues drafts at 3 days, asks at 14 days
  run.py pending-drafts           the drafts to create, as JSON
  run.py drafted NO ID [URL]      records a draft that now exists in Gmail
  run.py today                    prints the morning list
  run.py list                     prints every open follow-up
  run.py stop | start             turns the tool off and on
  run.py demo                     tries everything on sample data in a temporary folder
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lifeos.lib import paths  # noqa: E402
from lifeos.lib.tools import load_tool  # noqa: E402


def core():
    return load_tool("02-followup", "core")


def save_snapshot(threads):
    if not isinstance(threads, list):
        raise ValueError('{"threads": [...]} の形にしてください')
    core().write_json(paths.data_path("tools", "02-followup", "snapshot.json"), {"day": paths.today(), "threads": threads})
    return len(threads)


def read_snapshot(source):
    if source == "-":
        return json.load(sys.stdin).get("threads")
    with open(source, encoding="utf-8") as f:
        return json.load(f).get("threads")


def demo():
    os.environ["LIFEOS_DATA"] = tmp = tempfile.mkdtemp(prefix="lifeos-02-demo-")
    try:
        c = core()
        now = paths.now()
        sample = [("d1", "見本：5日前に送ったメール", 5), ("d2", "見本：15日前に送ったメール", 15), ("d3", "見本：昨日送ったメール", 1)]
        threads = [{"threadId": i, "subject": s, "lastFrom": "me", "lastAt": (now - timedelta(days=n)).isoformat(timespec="seconds"),
                    "to": ["someone@example.com"], "lang": "ja"} for i, s, n in sample]
        for _ in range(2):  # the second morning asks, once the draft exists
            save_snapshot(threads)
            print("朝の処理：", c.morning())
            for r in c.pending_drafts():
                print("下書きを作る：", r["subject"])
                c.record_draft(r["no"], "demo-" + str(r["no"]))
        print("\n".join(c.today_lines()))
        for item in c.asking():
            print("--- Telegram に送る問い（見本では送りません）")
            print(c.question(item, paths.today()))
            print("--- 「催促 送る」の返事")
            print(c.answer(item["no"], "送る"))
        print("見本はここで消します。本物のメールには触っていません。")
    finally:
        shutil.rmtree(tmp, True)
    return 0


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    cmd = a[0] if a else ""
    if cmd == "demo":
        return demo()
    if cmd == "snapshot" and len(a) == 2:
        print(f"{save_snapshot(read_snapshot(a[1]))}件を保存しました")
    elif cmd == "morning":
        print(json.dumps(core().morning(), ensure_ascii=False))
    elif cmd == "pending-drafts":
        print(json.dumps(core().pending_drafts(), ensure_ascii=False, indent=1))
    elif cmd == "drafted" and len(a) >= 3:
        core().record_draft(int(a[1]), a[2], a[3] if len(a) > 3 else "")
        print("記録しました")
    elif cmd == "today":
        print("\n".join(core().today_lines()))
    elif cmd == "list":
        today = paths.today()
        for i in core().open_items():
            print(f"{i['no']}番 {i['subject']}（{core().age_days(i, today)}日）{i['state']}")
    elif cmd in ("stop", "start"):
        core().set_enabled(cmd == "start")
        print("止めました" if cmd == "stop" else "動かします")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
