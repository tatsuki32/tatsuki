#!/usr/bin/env python3
"""/split: adds a big task's steps to Desk's tasks. Claude does the thinking; this checks and saves.

  python3 lifeos/15-resume/split.py "親タスク" "10分 下書きを開く" "15分 見出しを書く"
  python3 lifeos/15-resume/split.py --dry-run "親タスク" "10分 下書きを開く"

Every step must say its minutes, 15 at most. If any step is bad, nothing is saved.
Running it again skips steps that already exist, so it is safe to repeat.
"""
import hashlib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lifeos.lib import paths, tasks  # noqa: E402

MAX_MINUTES = 15
MAX_STEPS = 12
SOURCE = "lifeos-15"
_STEP = re.compile(r"^\s*(\d+)\s*分\s+(.+?)\s*$")


class StepError(ValueError):
    """The steps cannot be saved as given. Nothing was written."""


def parse_steps(raw):
    if not raw:
        raise StepError("ステップがありません")
    if len(raw) > MAX_STEPS:
        raise StepError(f"ステップは{MAX_STEPS}個までです。先に大きな区切りに分けてください")
    steps = []
    for s in raw:
        m = _STEP.match(s)
        if not m:
            raise StepError(f"「10分 やること」の形にしてください：{s}")
        minutes = int(m.group(1))
        if not 1 <= minutes <= MAX_MINUTES:
            raise StepError(f"{MAX_MINUTES}分以内に分けてください（{minutes}分）：{m.group(2)}")
        steps.append((minutes, m.group(2)))
    return steps


def find_parent(name, open_list):
    """The open task this split belongs to: exact title first, then one clear partial match."""
    exact = [t for t in open_list if t.get("title") == name]
    if exact:
        return exact[0]
    near = [t for t in open_list if name in (t.get("title") or "") and t.get("source") != SOURCE]
    return near[0] if len(near) == 1 else None


def _title(step, i, n, parent):
    label = parent if len(parent) <= 30 else parent[:29] + "…"
    return f"{step}（{i}/{n} {label}）"


def split(parent_name, raw_steps, dry_run=False):
    """Returns {"parent", "linked", "added", "skipped", "first"}. Raises StepError."""
    parent_name = " ".join(str(parent_name or "").split())
    if not parent_name:
        raise StepError("親タスクの名前がありません")
    steps = parse_steps(raw_steps)
    everything = tasks.all_tasks()
    parent = find_parent(parent_name, [t for t in everything if t.get("status") in tasks.OPEN])
    name = parent["title"] if parent else parent_name
    key = parent["id"] if parent else hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    prefix = f"split:{key}:"
    existing = [t for t in everything if str(t.get("sourceRef") or "").startswith(prefix)]
    n = len(steps)
    added, skipped = [], []
    base = paths.now_ms()
    for i, (minutes, step) in enumerate(steps, 1):
        if any((t.get("title") or "").startswith(step + "（") for t in existing):
            skipped.append(step)
            continue
        title = _title(step, i, n, name)
        if not dry_run:
            t = tasks.add(title, note=f"親：{name}\n目安：{minutes}分\nステップ {i}/{n}",
                          source=SOURCE, source_ref=prefix + str(i))
            # Desk's task list shows the newest createdAt first: step 1 must come out on top
            tasks.update(t["id"], createdAt=base - i)
        added.append({"title": title, "minutes": minutes})
    return {"parent": name, "linked": bool(parent), "added": added, "skipped": skipped,
            "first": added[0] if added else None}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    dry = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]
    if len(argv) < 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        r = split(argv[0], argv[1:], dry_run=dry)
    except StepError as e:
        print(f"保存していません：{e}", file=sys.stderr)
        return 2
    verb = "追加する予定" if dry else "Desk に追加"
    print(f"{verb}：{len(r['added'])}件（親：{r['parent']}）" + (f"／重複で飛ばした：{len(r['skipped'])}件" if r["skipped"] else ""))
    if r["first"]:
        print(f"まず：{r['first']['title']}　{r['first']['minutes']}分")
    return 0


if __name__ == "__main__":
    sys.exit(main())
