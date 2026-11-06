"""Files a memo: inbox/ first, then its parts to Google Calendar, Desk tasks, notes/ or logs/expenses.jsonl.

The language model (analyze.py) reads the memo; the rules in sort.py take over when it cannot. Anything with a date
goes to the calendar, and anything else to do becomes a Desk task. Until the calendar is connected, or when it fails,
a dated part becomes a Desk task with a due time, so nothing is lost.
The words reach inbox/ before anything else can fail, and leave it only when every part is filed. The inbox file also
keeps how the memo was split and logs/voice.jsonl records each filed part, so a retry (sweep) never files a part twice.
"""
import glob
import json
import os
import uuid
from datetime import datetime, timedelta

from lifeos.lib import logs, md, paths, tasks, tools

HERE = os.path.dirname(os.path.abspath(__file__))
sort = tools.load_file(HERE, "sort")
analyze = tools.load_file(HERE, "analyze")
gcal = tools.load_file(HERE, "gcal_writer")

LOG = "voice"
SOURCE = "lifeos-04"
SUMMARY_MAX = 10   # the longest morning summary, header included
SUMMARY_CHARS = 36
_ORDER = ("task", "event", "expense", "idea", "diary")


def _short(text, limit):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _save_inbox(memo_id, text, is_voice, parts=None):
    head = {"at": paths.now().isoformat(timespec="seconds"), "voice": is_voice}
    if parts:
        head["parts"] = [p._asdict() for p in parts]
    return md.save(f"inbox/{memo_id}.md", json.dumps(head, ensure_ascii=False) + "\n" + text)


def _read_inbox(path):
    with open(path, encoding="utf-8") as f:
        head, _, body = f.read().partition("\n")
    try:
        meta = json.loads(head)
    except ValueError:  # not written by us: treat all of it as the memo
        meta, body = {}, head + "\n" + body
    return meta if isinstance(meta, dict) else {}, body.strip()


def _rule_parts(text):
    out = []
    for p in sort.split(text, paths.now()):
        at, timed = sort.when(p.text, paths.now()) if p.kind in ("task", "event") else (None, False)
        out.append(p._replace(title=p.text, at=at.isoformat() if at else "", timed=timed))
    return out


def _clock(at, timed):
    return at.strftime("%-m/%-d %H:%M" if timed else "%-m/%-d")


def _file_task(memo_id, n, part, is_voice):
    """Calendar when it has a date and the calendar answers, else a Desk task. Returns (where, what the reply adds)."""
    title = part.title or part.text
    at = datetime.fromisoformat(part.at) if part.at else None
    origin = "音声" if is_voice else "文字"
    trouble = ""
    if at and gcal.connected():
        try:
            minutes = part.minutes or gcal.DEFAULT_MINUTES
            gcal.add(title, at, part.timed, f"{memo_id}-{n}", part.text, minutes)
            length = f" {minutes}分" if part.timed else ""
            return "calendar", f"{_clock(at, part.timed)}{length} → カレンダー"
        except gcal.CalendarError as e:
            logs.append("bot-errors", {"tool": "04-voice", "where": "calendar", "error": str(e)})
            trouble = "カレンダーに入らず"
    elif at:
        trouble = "カレンダー未接続"
    note = f"種類:{sort.LABELS[part.kind]} 元:{origin}メモ"
    if part.kind == "event" and not at:
        note += " 日時は聞き取れませんでした"
    if part.text != title or len(title) > tasks.TITLE_MAX:  # the task's title gets cut there: the words stay in the note
        note += "\n" + part.text
    t = tasks.add(title, due=int(at.timestamp() * 1000) if at else None, note=note, source=SOURCE, source_ref=memo_id)
    if not at:
        return "tasks:" + t["id"], "→ タスク"
    return "tasks:" + t["id"], f"{_clock(at, part.timed)} → タスク（{trouble}）"


def _file_part(memo_id, n, part, is_voice):
    """Files one part. Returns where it went and what to add to the reply line."""
    if part.kind in ("task", "event"):
        return _file_task(memo_id, n, part, is_voice)
    if part.kind == "expense":
        found = sort.amount(part.text) or sort.amount(part.title)
        logs.append("expenses", {"item": part.title or part.text, "text": part.text, "amount": found[0] if found else None,
                                 "currency": found[1] if found else None, "source": SOURCE, "memo": memo_id})
        return "logs/expenses.jsonl", ""
    rel = f"notes/{memo_id}-{n}-{part.kind}.md"
    origin = "音声" if is_voice else "文字"
    md.save(rel, f"---\nkind: {part.kind}\nat: {paths.now().isoformat(timespec='seconds')}\nfrom: {origin}\n---\n\n{part.text}")
    return rel, ""


def _file_memo(memo_id, parts, is_voice, done=()):
    """Files the parts not in done. (lines for the reply, whether any part failed, unsure)."""
    lines, failed, unsure = [], False, False
    for n, part in enumerate(parts):
        if n in done:
            continue
        try:
            where, extra = _file_part(memo_id, n, part, is_voice)
        except Exception as e:  # a stuck store must not lose the memo: it stays in inbox/
            logs.append("bot-errors", {"tool": "04-voice", "where": "file", "error": type(e).__name__})
            failed = True
            continue
        logs.append(LOG, {"memo": memo_id, "part": n, "kind": part.kind, "title": part.title, "text": part.text,
                          "where": where, "voice": is_voice, "unsure": part.unsure})
        lines.append(f"・{sort.LABELS[part.kind]}：{part.title or part.text}" + (f"（{extra}）" if extra else ""))
        unsure = unsure or part.unsure
    if not failed:
        try:
            os.remove(paths.data_path("inbox", memo_id + ".md"))
        except FileNotFoundError:
            pass
    return lines, failed, unsure


def _reply(lines, failed, unsure, by_rules=False):
    out = []
    if lines:
        out.append("仕分けました")
        out += lines
    if by_rules:
        out.append("AI が使えなかったので、簡単な規則で仕分けました。")
    if unsure:
        out.append("迷ったのでアイデアに置きました。違うときは「タスク：」のように種類を先頭に付けて送り直せます。")
    if failed:
        out.append("保存できなかった分は未整理に残しました。内容は消えていません。")
    return "\n".join(out)


def _split(text):
    """(parts, whether the rules did it). A memo that names its kind (「日記：…」) needs no model."""
    if sort.names_its_kind(text):
        return _rule_parts(text), False
    parts = analyze.parts(text, paths.now())
    if parts:
        return parts, False
    return _rule_parts(text), bool(analyze.transcribe.key())


def take(text, is_voice=False):
    """Handles one memo and returns the reply."""
    memo_id = paths.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    _save_inbox(memo_id, text, is_voice)
    parts, by_rules = _split(text)
    _save_inbox(memo_id, text, is_voice, parts)
    return _reply(*_file_memo(memo_id, parts, is_voice), by_rules)


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
        saved = meta.get("parts")
        parts = [sort.Part(**p) for p in saved] if saved else _split(text)[0]
        _, failed, _ = _file_memo(memo_id, parts, bool(meta.get("voice")), done.get(memo_id, ()))
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
        body += [f"・{sort.LABELS[kind]}：{_short(r.get('title') or r.get('text') or '', SUMMARY_CHARS)}" for r in same]
    tail = [f"未整理 {waiting}件が残っています"] if waiting else []
    room = SUMMARY_MAX - 1 - len(tail)
    if len(body) > room:
        hidden = len(body) - (room - 1)
        body = body[:room - 1] + [f"ほか{hidden}件"]
    return [f"昨日しゃべったこと（{len(recs)}件）"] + body + tail
