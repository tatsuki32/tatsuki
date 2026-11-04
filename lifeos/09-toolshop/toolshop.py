#!/usr/bin/env python3
"""Tool workshop: scaffolds a new lifeos/NN-slug/, lists the tools, changes their state.

  python3 lifeos/09-toolshop/toolshop.py new "名前" "やりたいこと" [--slug english-name]
  python3 lifeos/09-toolshop/toolshop.py list [--json]
  python3 lifeos/09-toolshop/toolshop.py finish 19
  python3 lifeos/09-toolshop/toolshop.py set 19 stopped
  python3 lifeos/09-toolshop/toolshop.py ideas

The list is lifeos/*/tool.json, the same files the Desk's Life OS screen reads, so a scaffolded
tool is registered the moment its tool.json exists. Nothing here deletes a tool.
"""
import argparse
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lifeos.lib import logs, paths, tools  # noqa: E402

SELF = "09-toolshop"
LOG = "toolshop"
LIMIT = 3  # unfinished tools allowed at once
TODO = "【未記入】"  # a scaffolded file still holds this until it is written; finish refuses it
DEFAULT_HOURS = {"endTime": "23:00", "resumeTime": "05:00"}
LABELS = {"building": "作成中", "working": "稼働中", "stopped": "停止", "blocked": "止まっている", "planned": "予定"}
TOOL = "lifeos/09-toolshop/toolshop.py"


class Stop(Exception):
    """A refusal to show as it is: the rules said no, or the input cannot be used."""


# --- the list ---

def catalog():
    out = []
    for d in tools.tool_dirs():
        slug = os.path.basename(d)
        rec = tools.info(d)
        status = rec.get("status")
        out.append({"slug": slug, "dir": d, "no": str(rec.get("no") or slug[:2]), "name": rec.get("name") or slug,
                    "status": status, "label": LABELS.get(status, "読めません"), "oneline": rec.get("oneline") or ""})
    return out


def unfinished(items=None):
    """Only `building` counts: `planned` was never started, and `blocked` waits on someone else."""
    return [t for t in (catalog() if items is None else items) if t["status"] == "building"]


def resolve(ref):
    ref = str(ref).strip()
    for t in catalog():
        if ref in (t["slug"], t["no"], t["name"], t["slug"][3:]) or (ref.isdigit() and ref.zfill(2) == t["slug"][:2]):
            return t
    raise Stop(f"「{ref}」というツールは一覧にありません。")


def show(items=None):
    items = catalog() if items is None else items
    width = max([len(t["name"]) for t in items] or [0])
    lines = [f"ツール一覧 {len(items)}個（作りかけ {len(unfinished(items))} / {LIMIT}）"]
    lines += [f"{t['no']}  {t['name'].ljust(width, '　')}  {t['label']}  {t['oneline']}".rstrip() for t in items]
    return "\n".join(lines)


# --- the limits ---

def hours():
    """(end, resume) as HH:MM. A missing or broken settings file means the defaults."""
    try:
        with open(paths.data_path("tools", SELF, "config.json", mkdir=False), encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    cfg = cfg if isinstance(cfg, dict) else {}
    good = lambda v: isinstance(v, str) and re.fullmatch(r"([01][0-9]|2[0-3]):[0-5][0-9]", v)
    return tuple(cfg[k] if good(cfg.get(k)) else DEFAULT_HOURS[k] for k in ("endTime", "resumeTime"))


def resting():
    """True from the end time until the resume time, also when that range crosses midnight."""
    end, resume = hours()
    t = paths.now().strftime("%H:%M")
    return (end <= t < resume) if end < resume else (t >= end or t < resume)


def park(name, purpose, why):
    logs.append(LOG, {"event": "parked", "name": name, "purpose": purpose, "why": why})


def ideas():
    """Parked ideas not built yet, oldest first. Same name twice keeps the newest wording."""
    made = {t["name"].casefold() for t in catalog()}
    kept = {}
    for x in logs.read(LOG):
        key = str(x.get("name", "")).casefold()
        if x.get("event") == "parked" and key not in made:
            kept[key] = x
    return list(kept.values())


# --- making a tool ---

def clean(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def make_slug(name, slug=None):
    s = re.sub(r"[^a-z0-9]+", "-", (slug or name).lower()).strip("-")
    return s[:30].strip("-") or "tool"


def next_no(items):
    used = [int(t["slug"][:2]) for t in items]
    n = max(used or [0]) + 1
    if n > 99:
        raise Stop("番号を使い切りました（99まで）。止めたツールを整理してから作ってください。")
    return f"{n:02d}"


README = """# {name}

## 目的

{purpose}

## 使い方

```sh
python3 lifeos/{folder}/core.py "試しの文字"
```

""" + TODO + """ 実際の使い方を書く

## 止め方

```sh
python3 """ + TOOL + """ set {no} stopped
```

ファイルは消えません。再開は `set {no} building` です。

## メモ

共通の約束は `lifeos/README.md` にあります。個人データは `paths.data_path("tools", "{folder}", ...)` に置きます。
このフォルダは公開リポジトリに入るので、実名や案件名は書きません。
"""

CORE = """#!/usr/bin/env python3
\"\"\"The tool's own code. Runs alone: python3 lifeos/{folder}/core.py text\"\"\"
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def run(text):
    # """ + TODO + """ write what the tool does here. Keep personal data in lifeos.lib.paths.data_path.
    return "まだ処理がありません：" + text


if __name__ == "__main__":
    print(run(" ".join(sys.argv[1:])))
"""

TEST = """import os

from lifeos.lib.tools import load_file
from lifeos.tests.sandbox import SandboxCase

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "{folder}")


class TestCore(SandboxCase):
    def test_run_answers(self):
        self.assertIn("試し", load_file(TOOL, "core").run("試し"))
"""


def fill(template, **values):
    # str.replace, not str.format: values may hold anything, so the user's text goes in last
    for k, v in values.items():
        template = template.replace("{" + k + "}", v)
    return template


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def new(name, purpose, slug=None):
    """Scaffolds the tool and returns its catalog entry. Raises Stop when the rules say not now."""
    name, purpose = clean(name), clean(purpose)
    if not name or not purpose:
        raise Stop("ツール名とやりたいことの両方を書いてください。例：/newtool 領収書箱 領収書の写真を月ごとにまとめる")
    if len(name) > 40:
        raise Stop("ツール名は40字までにしてください。")
    if tools.info(os.path.join(tools.root_dir(), SELF)).get("status") == "stopped":
        raise Stop(f"ツール工房は止めてあります。再開するときは `python3 {TOOL} set 09 working` です。")
    items = catalog()
    if any(t["name"].casefold() == name.casefold() for t in items):
        raise Stop(f"「{name}」はもう一覧にあります。")
    if resting():
        end, resume = hours()
        park(name, purpose, "time")
        raise Stop(f"{end}を過ぎたので、新しいツールは{resume}から作れます。\n案は残しました（ideas で見られます）。今日はここまでにしましょう。")
    busy = unfinished(items)
    if len(busy) >= LIMIT:
        park(name, purpose, "limit")
        names = "\n".join(f"  {t['no']} {t['name']}" for t in busy)
        raise Stop(f"作りかけが{len(busy)}つあるので、新しいツールは作りません。先に1つ仕上げましょう。\n{names}\n"
                   f"仕上げる：finish 番号　／　やめる：set 番号 stopped\n案は残しました（ideas で見られます）。")
    return scaffold(name, purpose, slug, items)


def scaffold(name, purpose, slug, items):
    no = next_no(items)
    slug = make_slug(name, slug)
    folder = f"{no}-{slug}"
    root = tools.root_dir()
    target = os.path.join(root, folder)
    test_dir = os.path.join(root, "tests")
    test_path = os.path.join(test_dir, f"test_{no}_{slug.replace('-', '_')}.py")
    os.makedirs(target)
    try:
        os.makedirs(test_dir, exist_ok=True)
        write(os.path.join(target, "README.md"), fill(README, folder=folder, no=no, name=name, purpose=purpose))
        write(os.path.join(target, "core.py"), fill(CORE, folder=folder))
        write(test_path, fill(TEST, folder=folder))
        rec = {"no": no, "name": name, "oneline": purpose[:60], "status": "building",
               "usage": [f"{TODO} 使い方を書く"], "howToCheck": [f"{TODO} 本人が試す手順を1行に1つ書く"],
               "blockers": [], "commands": [], "updated": paths.today()}
        # last, so the Desk never lists a half-made folder
        write(os.path.join(target, "tool.json"), json.dumps(rec, ensure_ascii=False, indent=2) + "\n")
    except OSError:
        shutil.rmtree(target, ignore_errors=True)
        if os.path.exists(test_path):
            os.remove(test_path)
        raise
    logs.append(LOG, {"event": "made", "name": name, "folder": folder})
    return resolve(folder)


# --- changing state ---

def todo_left(tool_dir):
    """Files that still need writing before the tool can be called finished."""
    left = []
    for name in ("README.md", "tool.json", "core.py"):
        path = os.path.join(tool_dir, name)
        try:
            with open(path, encoding="utf-8") as f:
                if TODO in f.read():
                    left.append(name)
        except OSError:
            if name != "core.py":  # a tool may keep its code under other names
                left.append(name)
    return left


def set_status(ref, status):
    status = {v: k for k, v in LABELS.items()}.get(status, status)
    if status not in ("building", "working", "stopped"):
        raise Stop("状態は 作成中（building）・稼働中（working）・停止（stopped）のどれかです。")
    t = resolve(ref)
    if t["status"] not in LABELS:
        raise Stop(f"{t['slug']} の tool.json を読めないので、変えませんでした。先に直してください。")
    if status == "working":
        left = todo_left(t["dir"])
        if left:
            raise Stop(f"{t['name']} はまだ書くところが残っています：{'、'.join(left)}（{TODO}を埋めてください）")
    if status == "building" and t["status"] != "building" and len(unfinished()) >= LIMIT:
        raise Stop(f"作りかけが{LIMIT}つあるので、作成中には戻しません。先に1つ仕上げましょう。")
    path = os.path.join(t["dir"], "tool.json")
    rec = tools.info(t["dir"])
    rec.update(status=status, updated=paths.today())
    tmp = path + ".tmp"
    write(tmp, json.dumps(rec, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)
    logs.append(LOG, {"event": "status", "folder": t["slug"], "status": status})
    return resolve(t["slug"])


# --- command line ---

def main(argv=None):
    ap = argparse.ArgumentParser(description="ツール工房")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new", help="新しいツールを作る")
    p.add_argument("name")
    p.add_argument("purpose", nargs="*")
    p.add_argument("--slug", help="フォルダ名に使う英小文字とハイフン")
    p = sub.add_parser("list", help="一覧を出す")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("finish", help="仕上げて稼働中にする")
    p.add_argument("tool")
    p = sub.add_parser("set", help="状態を変える")
    p.add_argument("tool")
    p.add_argument("status")
    sub.add_parser("ideas", help="後回しにした案を出す")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "new":
            t = new(args.name, " ".join(args.purpose), args.slug)
            print(f"作りました：lifeos/{t['slug']}/（{t['label']}）\n"
                  f"次は README.md・core.py・tool.json の{TODO}を埋めて、finish {t['no']} で仕上げます。\n"
                  f"公開リポジトリに入るので、実名や案件名は書きません。\n\n{show()}")
        elif args.cmd == "list":
            items = catalog()
            if args.json:
                print(json.dumps([{k: t[k] for k in ("no", "name", "status", "label", "oneline")} for t in items],
                                 ensure_ascii=False, indent=2))
            else:
                print(show(items))
        elif args.cmd == "finish":
            t = set_status(args.tool, "working")
            print(f"{t['name']} を{t['label']}にしました。")
        elif args.cmd == "set":
            t = set_status(args.tool, args.status)
            print(f"{t['name']} を{t['label']}にしました。")
        else:
            kept = ideas()
            print("\n".join(f"{x['name']}：{x['purpose']}" for x in kept) or "後回しにした案はありません。")
    except Stop as e:
        print(e)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
