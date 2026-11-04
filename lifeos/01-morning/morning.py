#!/usr/bin/env python3
"""/morning: picks at most 3 things for today, saves them to briefs/YYYY-MM-DD.md and sends them to Telegram.

  python3 lifeos/01-morning/morning.py            pick, save, send
  python3 lifeos/01-morning/morning.py --no-send  pick and save only

Candidates come from desk/kickoff.py and the pick follows the order in
.claude/commands/morning-kickoff.md. Other tools' lines come from tools.brief_parts().
To stop Telegram only, put {"telegram": false} in <data>/tools/01-morning/config.json.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lifeos.bot import telegram  # noqa: E402
from lifeos.lib import logs, md, paths, tools  # noqa: E402

MAX_ITEMS = 3
TITLE_MAX = 80
STALE_HOURS = 3  # same as /morning-kickoff
# /morning must answer within 30 seconds, so a slow tool or network is cut off
BRIEF_LIMIT = 8
SEND_LIMIT = 8
BLOCK = re.compile(r"<!-- morning -->.*?<!-- /morning -->\n?", re.S)
_TIMED_OUT = object()


def gather():
    """Today's candidates. Replace this function to read them from somewhere else."""
    kickoff = tools.load_file(os.path.join(paths.REPO, "desk"), "kickoff")
    kickoff.DATA = paths.desk_data_dir()  # desk/data does not exist inside a worktree
    return kickoff.candidates()


def collect_parts(day):
    return tools.brief_parts(day)


def send_telegram(text):
    return telegram.notify(text)


def _limited(fn, seconds):
    """fn()'s result, or _TIMED_OUT if it takes too long. Its exception is raised here."""
    box = {}

    def run():
        try:
            box["value"] = fn()
        except Exception as e:
            box["error"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        return _TIMED_OUT
    if "error" in box:
        raise box["error"]
    return box["value"]


def _clean(text):
    """One short line; titles come from mail and tasks, so they cannot break the brief's markers."""
    text = " ".join(str(text).replace("`", "").replace("<!--", "").split())
    return text if len(text) <= TITLE_MAX else text[:TITLE_MAX - 1] + "…"


def _same(a, b):
    a, b = re.sub(r"\s+", "", a), re.sub(r"\s+", "", b)
    if a == b:
        return bool(a)
    short, long = sorted((a, b), key=len)
    return len(short) >= 6 and short in long


def _rest_reason(t, mode):
    if t.get("overdue"):
        return "今日片づけると楽になる"
    if t.get("dueText"):
        return f"締切が近い順（{t['dueText']}）"
    if t.get("kind") == mode:
        return f"今は{mode}の時間に合う"
    return "ほかに急ぐものがない"


def pick(c):
    """At most 3 items, in the /morning-kickoff order:
    due today, the first "next step", in progress, the rest. Remaining steps only fill empty places."""
    tasks = c.get("tasks") or []
    steps = [s.get("text", "") for s in c.get("nextSteps") or []]
    items, used, used_steps = [], set(), []

    def take_task(t, reason):
        if t["id"] not in used and len(items) < MAX_ITEMS:
            used.add(t["id"])
            items.append({"title": _clean(t.get("title", "")), "reason": reason})

    def take_step(text, reason):
        if any(_same(text, u) for u in used_steps) or any(_same(text, i["title"]) for i in items):
            return
        used_steps.append(text)
        t = next((t for t in tasks if _same(text, t.get("title", ""))), None)
        if t:
            take_task(t, reason)
        elif len(items) < MAX_ITEMS:
            items.append({"title": _clean(text), "reason": reason})

    for t in tasks:
        if t.get("dueToday"):
            due = (t.get("dueText") or "").split(" ")[-1]
            take_task(t, f"今日が締切（{due}）" if due else "今日が締切")
    if steps:
        take_step(steps[0], "前回の「次の一手」")
    for t in tasks:
        if t.get("status") == "doing":
            take_task(t, "進行中で、続きから入れる")
    for t in tasks:
        take_task(t, _rest_reason(t, c.get("mode")))
    for s in steps[1:]:
        take_step(s, "前回の「次の一手」")
    if c.get("short") and items:
        items[0]["reason"] += f"。次の予定まで{c.get('freeMinutes')}分なので、15分でできる部分だけ"
    return items[:MAX_ITEMS]


def _free_text(c):
    if c.get("nextEvent"):
        h, m = divmod(c.get("freeMinutes") or 0, 60)
        return f"次の予定まで {h}時間{m}分" if h else f"次の予定まで {m}分"
    if (c.get("dataAgeHours") or {}).get("calendar.js") is None:
        return "予定は未確認"
    return "このあと予定なし"


def notices(c):
    ages = c.get("dataAgeHours") or {}
    out = []
    if ages.get("tasks.json", 0) is None:
        out.append("タスクのデータ（tasks.json）が見つかりません")
    if ages.get("calendar.js", 0) is None:
        out.append("予定のデータ（calendar.js）が見つからないので、予定を見ずに選びました")
    stale = [f"{n} が{a}時間前" for n, a in ages.items() if a is not None and a > STALE_HOURS]
    if stale:
        out.append(f"同期が止まっているかもしれません（{'、'.join(stale)}）")
    return out


def render(c, items, parts, parts_timed_out=False):
    lines = [f"☀️ {c.get('now', '')}（{c.get('weekday', '')}）— {c.get('mode', '')}の時間（{_free_text(c)}）", ""]
    if items:
        for n, i in enumerate(items, 1):
            lines += [f"{n}. {i['title']}", f"   理由：{i['reason']}"]
    else:
        lines.append("今日はタスクが見つかりませんでした")
    lines += ["", f"今日の予定 {len(c.get('events') or [])}件"]
    lines += notices(c)
    if parts_timed_out:
        lines.append("ほかのツールの行は、時間がかかったので省きました")
    if parts:
        lines += ["", "ほかのツールから"]
        for slug, part in parts:
            name = tools.info(os.path.join(tools.root_dir(), slug)).get("name") or slug
            lines += [f"・{name}：{' '.join(str(x).split())}" for x in part]
    return "\n".join(lines)


def save_brief(day, text):
    """Replaces only this tool's block: 11-notes keeps its summary in the same file."""
    rel = f"briefs/{day}.md"
    block = f"<!-- morning -->\n# 今日の3つ\n\n{text}\n<!-- /morning -->\n"
    old = md.read(rel)
    if old is None:
        new = block
    elif BLOCK.search(old):
        new = BLOCK.sub(lambda _: block, old, count=1)
    else:
        new = block + "\n" + old
    return md.save(rel, new)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False)
    os.replace(tmp, path)


def deliver(day, text, key, send=None, limit=None):
    """Sends once per content per day. Returns a line for the screen."""
    if _read_json(paths.data_path("tools", "01-morning", "config.json")).get("telegram") is False:
        return "Telegram：止めています（config.json）"
    state_path = paths.data_path("tools", "01-morning", "state.json")
    state = _read_json(state_path)
    if state.get("day") == day and state.get("sent") == key:
        return "Telegram：同じ内容を今日すでに送ったので、送っていません"
    send = send or send_telegram
    try:
        sent = _limited(lambda: send(text), SEND_LIMIT if limit is None else limit)
    except Exception as e:
        logs.append("outbox", {"text": text})
        return f"Telegram：送れませんでした（{type(e).__name__}）。控えを logs/outbox.jsonl に残しました"
    if sent is _TIMED_OUT:
        logs.append("outbox", {"text": text})
        return "Telegram：返事がなく、届いたか確かめられません。控えを logs/outbox.jsonl に残しました"
    if sent:
        _write_json(state_path, {"day": day, "sent": key})
        return "Telegram：送りました"
    return "Telegram：まだ送れません（未接続か通信の失敗）。控えを logs/outbox.jsonl に残しました"


def run(no_send=False, out=None, parts_limit=None, send_limit=None):
    """Returns 0, or 1 if nothing could be picked or the brief could not be saved."""
    out = out or sys.stdout
    day = paths.today()
    try:
        c = gather()
    except Exception as e:
        print(f"今日の候補を読めませんでした（{type(e).__name__}）", file=out)
        return 1
    items = pick(c)
    try:
        parts = _limited(lambda: collect_parts(day), BRIEF_LIMIT if parts_limit is None else parts_limit)
    except Exception:
        parts = []
    timed_out = parts is _TIMED_OUT
    parts = [] if timed_out else parts
    text = render(c, items, parts, timed_out)
    print(text, file=out, flush=True)

    print("", file=out)
    code = 0
    try:
        print(f"保存：{save_brief(day, text)}", file=out, flush=True)
    except OSError as e:
        print(f"保存できませんでした（{type(e).__name__}）", file=out, flush=True)
        code = 1
    if not no_send:
        key = hashlib.sha1(json.dumps([items, parts], ensure_ascii=False).encode("utf-8")).hexdigest()
        print(deliver(day, text, key, limit=send_limit), file=out, flush=True)
    return code


def main(argv=None):
    ap = argparse.ArgumentParser(description="今日やる3件を出す")
    ap.add_argument("--no-send", action="store_true", help="Telegram に送らない")
    return run(no_send=ap.parse_args(argv).no_send)


if __name__ == "__main__":
    sys.exit(main())
