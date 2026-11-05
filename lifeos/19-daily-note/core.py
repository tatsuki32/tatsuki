"""Daily Note by mail. Gmail itself is handled by the Claude scheduled tasks; this file holds the rules.

prepare -> the task sends the mail -> record_sent -> pending -> the task reads the thread -> save_reply.
The reply body goes only into the task's note in Desk. It is never logged or printed.
"""
import json
import os
import re
import tempfile
from datetime import datetime, timedelta

from lifeos.lib import paths, tasks

SOURCE = "lifeos-19"
TITLE = "3min Daily Note"
TOOL = "19-daily-note"
SEND_FROM = (15, 45)  # the 16:00 task can fire a little early; anything earlier is a late run from yesterday
WINDOW_DAYS = 3

ITEMS = (
    ("Daily Log", "Wake-up time, 3meals"),
    ("Today's Wins", "What made me happy and what I achieved for presence Tatsuki"),
    ("Insights", "What I learned and realized today for future Tatsuki"),
    ("Gratitude", "To whom and for what I am deeply grateful today"),
    ("Current Mindset", "My emotional and mental state at this very moment"),
    ("Commitment", "My target bedtime for tonight"),
    ("Tomorrow's Vision", "The definition of a perfect and fulfilling day"),
    ("High Leverage", "What was the most leveraged behavior today?"),
    ("Time Liberation", "On the contrary, what should I stop doing to give me more time?"),
)

_NUMBERED = re.compile(r"^\s*([1-9１-９])\s*[.)．、：:）]\s*(.*)$")
_QUOTE_HEAD = (
    re.compile(r"^On .*\d{4}.*(wrote:|\bat\b)"),
    re.compile(r"^\d{4}年\d{1,2}月\d{1,2}日.*\d{1,2}:\d{2}"),
    re.compile(r"^\d{4}/\d{1,2}/\d{1,2}.*\d{1,2}:\d{2}"),
    re.compile(r"^-{2,}\s*(Original Message|Forwarded message|元のメッセージ|転送)"),
    re.compile(r"^(Sent from my |iPhoneから送信|iPadから送信)"),
    re.compile(r"^--\s*$"),
)


def subject(date):
    return f"{TITLE} {date}"


def template():
    return "\n".join(f"{i}. {name}: {ask}" for i, (name, ask) in enumerate(ITEMS, 1))


def mail_body():
    return ("このメールに返信すると、Desk の今日のタスクに保存されて完了になります。\n"
            "番号を付けて書くと項目別に整います。一部だけでも大丈夫です。\n\n" + template() + "\n")


def task_note(date):
    return f"{date} のメールに返信すると、ここに保存されて完了になります。\n\n" + template()


def strip_quote(text):
    """The part the person wrote: no quoted lines, nothing after the quote header or the signature."""
    out = []
    for line in str(text or "").replace("\r\n", "\n").split("\n"):
        if any(p.match(line.strip()) for p in _QUOTE_HEAD):
            break
        if not line.lstrip().startswith(">"):
            out.append(line)
    return "\n".join(out).strip()


def _without_template(no, body):
    """Removes the item's own wording when the person kept it in the reply."""
    name, ask = ITEMS[no - 1]
    for head in (f"{name}: {ask}", f"{name}:", name):
        if body.lower().startswith(head.lower()):
            return body[len(head):].lstrip(" :：")
    return body


def format_reply(text):
    """Puts numbered lines under their item name. A reply with no numbers is kept as it is."""
    text = strip_quote(text)
    lead, items, current = [], {}, None
    for line in text.split("\n"):
        m = _NUMBERED.match(line)
        if m:
            current = int(m.group(1).translate(str.maketrans("１２３４５６７８９", "123456789")))
            items.setdefault(current, []).append(_without_template(current, m.group(2).strip()))
        elif current is None:
            lead.append(line)
        else:
            items[current].append(line)
    if not items:
        return text
    parts = ["\n".join(lead).strip()] if "\n".join(lead).strip() else []
    for no in sorted(items):
        body = "\n".join(items[no]).strip()
        if body:
            parts.append(f"【{no}. {ITEMS[no - 1][0]}】\n{body}")
    return "\n\n".join(parts)


def _state_file():
    return paths.data_path("tools", TOOL, "state.json")


def _load():
    try:
        with open(_state_file(), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) and isinstance(v.get("days"), dict) else {"days": {}}
    except FileNotFoundError:
        return {"days": {}}


def _save(state):
    p = _state_file()
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".state", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def _daily_tasks():
    return [t for t in tasks.all_tasks(include_deleted=True) if t.get("source") == SOURCE]


def archive_old(date):
    """Yesterday's unanswered card leaves the board quietly, so no red overdue mark stays."""
    moved = 0
    for t in _daily_tasks():
        if not t.get("deleted") and t.get("sourceRef", "") < date and t.get("status") in tasks.OPEN:
            tasks.update(t["id"], status="archived")
            moved += 1
    return moved


def _due_ms(date):
    d = datetime.strptime(date, "%Y-%m-%d").replace(hour=23, minute=59, tzinfo=paths.HCMC)
    return int(d.timestamp() * 1000)


def prepare(now=None):
    """What the 16:00 task should do. Safe to run twice: one task and one mail per day."""
    now = now or paths.now()
    date = now.strftime("%Y-%m-%d")
    archived = archive_old(date)
    if (now.hour, now.minute) < SEND_FROM:
        return {"send": False, "reason": "16時前", "date": date, "archived": archived}
    if _load()["days"].get(date, {}).get("messageId"):
        return {"send": False, "reason": "今日の分は送信済み", "date": date, "archived": archived}
    if not any(t.get("sourceRef") == date for t in _daily_tasks()):
        tasks.add(TITLE, due=_due_ms(date), note=task_note(date), source=SOURCE, source_ref=date)
    return {"send": True, "date": date, "subject": subject(date), "body": mail_body(), "archived": archived}


def record_sent(date, thread_id, message_id, sender, now=None):
    state = _load()
    state["days"][date] = {"threadId": thread_id, "messageId": message_id, "from": _address(sender),
                           "sentAt": (now or paths.now()).isoformat(timespec="seconds"), "replied": []}
    _save(state)
    return {"ok": True, "date": date}


def pending(now=None):
    """Threads the hourly task should read: the last 3 days. Also clears yesterday's unanswered card."""
    now = now or paths.now()
    date = now.strftime("%Y-%m-%d")
    archived = archive_old(date)
    since = (now - timedelta(days=WINDOW_DAYS - 1)).strftime("%Y-%m-%d")
    days = _load()["days"]
    threads = [{"date": d, "threadId": v["threadId"], "skipMessageIds": [v["messageId"], *v.get("replied", [])]}
               for d, v in sorted(days.items()) if d >= since and v.get("threadId")]
    return {"threads": threads, "archived": archived}


def _address(sender):
    m = re.search(r"[^\s<>\"']+@[^\s<>\"']+", str(sender or ""))
    return m.group(0).lower() if m else ""


def save_reply(date, message_id, sender, labels, text):
    """Saves one reply into the day's task and completes it. Returns a status word, never the body."""
    state = _load()
    day = state["days"].get(date)
    if not day:
        return {"status": "unknown-day"}
    if message_id == day.get("messageId") or message_id in day.get("replied", []):
        return {"status": "already-saved"}
    if "SENT" not in (labels or []) or not day.get("from") or _address(sender) != day["from"]:
        return {"status": "not-from-owner"}
    body = format_reply(text)
    if not body:
        return {"status": "empty"}
    task = next((t for t in _daily_tasks() if t.get("sourceRef") == date and not t.get("deleted")), None)
    if task:
        note = task.get("note") or ""
        new = body if note in ("", task_note(date)) else note.rstrip() + "\n\n---\n\n" + body
        tasks.update(task["id"], status="done", note=new)
    day.setdefault("replied", []).append(message_id)
    _save(state)
    return {"status": "saved" if task else "task-gone"}
