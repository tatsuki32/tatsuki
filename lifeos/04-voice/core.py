"""Files a memo: inbox/ first, then its parts to Desk tasks, notes/ or logs/expenses.jsonl.

The words reach inbox/ before anything else can fail, and leave it only when every part is
filed. logs/voice.jsonl records each filed part, so a retry (sweep) never files a part twice.
An event becomes a Desk task with a due time: the bot does not write to Google Calendar.
"""
import glob
import json
import os
import uuid
from datetime import datetime, timedelta

from lifeos.lib import logs, md, paths, tasks, tools

sort = tools.load_file(os.path.dirname(os.path.abspath(__file__)), "sort")

LOG = "voice"
SOURCE = "lifeos-04"
SUMMARY_MAX = 10   # the longest morning summary, header included
SUMMARY_CHARS = 36
_ORDER = ("task", "event", "expense", "idea", "diary")


def _short(text, limit):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _save_inbox(memo_id, text, is_voice):
    head = json.dumps({"at": paths.now().isoformat(timespec="seconds"), "voice": is_voice})
    return md.save(f"inbox/{memo_id}.md", head + "\n" + text)


def _read_inbox(path):
    with open(path, encoding="utf-8") as f:
        head, _, body = f.read().partition("\n")
    try:
        meta = json.loads(head)
    except ValueError:  # not written by us: treat all of it as the memo
        meta, body = {}, head + "\n" + body
    return meta if isinstance(meta, dict) else {}, body.strip()


def _file_part(memo_id, n, part, is_voice):
    """Files one part. Returns where it went and what to add to the reply line."""
    origin = "音声" if is_voice else "文字"
    if part.kind in ("task", "event"):
        at, timed = sort.when(part.text, paths.now())
        due = int(at.timestamp() * 1000) if at else None
        note = f"種類:{sort.LABELS[part.kind]} 元:{origin}メモ" + ("" if at or part.kind == "task" else " 日時は聞き取れませんでした")
        if len(part.text) > tasks.TITLE_MAX:  # the title gets cut there: the rest of the words stay in the note
            note += "\n" + part.text
        t = tasks.add(part.text, due=due, note=note, source=SOURCE, source_ref=memo_id)
        extra = at.strftime("%-m/%-d %H:%M" if timed else "%-m/%-d") if at else ""
        return "tasks:" + t["id"], extra
    if part.kind == "expense":
        found = sort.amount(part.text)
        logs.append("expenses", {"item": part.text, "text": part.text, "amount": found[0] if found else None,
                                 "currency": found[1] if found else None, "source": SOURCE, "memo": memo_id})
        return "logs/expenses.jsonl", ""
    rel = f"notes/{memo_id}-{n}-{part.kind}.md"
    md.save(rel, f"---\nkind: {part.kind}\nat: {paths.now().isoformat(timespec='seconds')}\nfrom: {origin}\n---\n\n{part.text}")
    return rel, ""


def _file_memo(memo_id, text, is_voice, done=()):
    """Files the parts not in done. (lines for the reply, whether any part failed, unsure)."""
    lines, failed, unsure = [], False, False
    for n, part in enumerate(sort.split(text, paths.now())):
        if n in done:
            continue
        try:
            where, extra = _file_part(memo_id, n, part, is_voice)
        except Exception as e:  # a stuck store must not lose the memo: it stays in inbox/
            logs.append("bot-errors", {"tool": "04-voice", "where": "file", "error": type(e).__name__})
            failed = True
            continue
        logs.append(LOG, {"memo": memo_id, "part": n, "kind": part.kind, "text": part.text, "where": where,
                          "voice": is_voice, "unsure": part.unsure})
        lines.append(f"・{sort.LABELS[part.kind]}：{part.text}" + (f"（{extra}）" if extra else ""))
        unsure = unsure or part.unsure
    if not failed:
        try:
            os.remove(paths.data_path("inbox", memo_id + ".md"))
        except FileNotFoundError:
            pass
    return lines, failed, unsure


def _reply(lines, failed, unsure):
    out = []
    if lines:
        out.append("仕分けました")
        out += lines
    if unsure:
        out.append("迷ったのでアイデアに置きました。違うときは「タスク：」のように種類を先頭に付けて送り直せます。")
    if failed:
        out.append("保存できなかった分は未整理に残しました。内容は消えていません。")
    return "\n".join(out)


def take(text, is_voice=False):
    """Handles one memo and returns the reply."""
    memo_id = paths.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    _save_inbox(memo_id, text, is_voice)
    return _reply(*_file_memo(memo_id, text, is_voice))


def leftovers():
    return sorted(glob.glob(os.path.join(os.path.dirname(paths.data_path("inbox", "x")), "*.md")))


def sweep():
    """Tries the memos left in inbox/ again. Returns (filed, still left)."""
    filed = left = 0
    done = {}
    for r in logs.read(LOG):
        done.setdefault(r.get("memo"), set()).add(r.get("part"))
    for path in leftovers():
        memo_id = os.path.basename(path)[:-3]
        meta, text = _read_inbox(path)
        if not text:
            left += 1
            continue
        _, failed, _ = _file_memo(memo_id, text, bool(meta.get("voice")), done.get(memo_id, ()))
        left += failed
        filed += not failed
    return filed, left


def _expense_line(recs):
    totals = {}
    for r in recs:
        found = sort.amount(r.get("text") or "")
        if found:
            totals[found[1]] = totals.get(found[1], 0) + found[0]
    names = {"JPY": "円", "VND": "ドン", "USD": "ドル"}
    sums = "、".join(f"{v:,}{names[c]}" for c, v in totals.items())
    return f"・支出 {len(recs)}件" + (f"（{sums}）" if sums else "")


def summary(day=None):
    """What was said the day before day, at most SUMMARY_MAX lines. [] when there is nothing."""
    day = day or paths.today()
    target = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    recs = [r for r in logs.read(LOG) if str(r.get("at", "")).startswith(target)]
    waiting = len(leftovers())
    if not recs and not waiting:
        return []
    body = []
    for kind in _ORDER:
        same = [r for r in recs if r.get("kind") == kind]
        if kind == "expense" and same:
            body.append(_expense_line(same))
            continue
        body += [f"・{sort.LABELS[kind]}：{_short(r.get('text') or '', SUMMARY_CHARS)}" for r in same]
    tail = [f"未整理 {waiting}件が残っています"] if waiting else []
    room = SUMMARY_MAX - 1 - len(tail)
    if len(body) > room:
        hidden = len(body) - (room - 1)
        body = body[:room - 1] + [f"ほか{hidden}件"]
    return [f"昨日しゃべったこと（{len(recs)}件）"] + body + tail
