#!/usr/bin/env python3
"""The commands behind /where and /lowenergy.

  where.py show 案件名       5 lines from Desk tasks alone (works without Claude, writes nothing)
  where.py list              up to 3 stalled projects
  where.py gather 案件名     the known facts as JSON, plus the Drive and Gmail search words
  where.py render            stdin: the fields as JSON. Prints the 5 lines and remembers them
  where.py draft             stdin: a new draft as JSON ({kind, body, project, title, to, subject, reply_to})
  where.py redraft ID        stdin: the new text
  where.py react ID 反応     OK / 直す＋一言 / やめる
  where.py drafted ID        records that the mail is in Gmail drafts
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from lifeos.lib.tools import info, load_file  # noqa: E402

core = load_file(HERE, "core")
draft = load_file(HERE, "draft")


def _out(v):
    print(json.dumps(v, ensure_ascii=False, indent=1))


def _stdin():
    try:
        v = json.loads(sys.stdin.read())
    except ValueError:
        raise SystemExit("入力が JSON ではありません")
    if not isinstance(v, dict):
        raise SystemExit("入力は JSON のオブジェクトにしてください")
    return v


def main(argv=None):
    ap = argparse.ArgumentParser(description="案件復元")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("show", "gather"):
        sub.add_parser(name).add_argument("name", nargs="+")
    for name in ("list", "render", "draft"):
        sub.add_parser(name)
    for name in ("redraft", "drafted"):
        sub.add_parser(name).add_argument("id")
    p = sub.add_parser("react")
    p.add_argument("id")
    p.add_argument("text", nargs="+")
    a = ap.parse_args(argv)

    if info(HERE).get("status") == "stopped":
        print("案件復元は止めています（lifeos/16-where/tool.json の status）")
        return 0
    try:
        if a.cmd == "show":
            print(core.render(core.summarize(core.gather(" ".join(a.name)))))
        elif a.cmd == "list":
            _out(core.stalled())
        elif a.cmd == "gather":
            _out(core.gather(" ".join(a.name)))
        elif a.cmd == "render":
            f = _stdin()
            print(core.render(f))
            core.remember(f)
        elif a.cmd == "draft":
            f = _stdin()
            _out(draft.create(f.get("kind"), f.get("body"), f.get("project", ""), f.get("title", ""), f.get("to"),
                              f.get("subject", ""), f.get("reply_to", "")))
        elif a.cmd == "redraft":
            _out(draft.redraft(a.id, _stdin().get("body")))
        elif a.cmd == "react":
            _out(draft.react(a.id, " ".join(a.text)))
        elif a.cmd == "drafted":
            _out(draft.drafted(a.id))
    except (draft.DraftError, core.tasks.TaskStoreError) as e:
        raise SystemExit(str(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
