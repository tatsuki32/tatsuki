"""What /tsuzuki shows: where the last session stopped and the next move, in three lines.

Sources, newest first: the work log (written by hook.py), then Desk tasks, then memory/
through desk/kickoff.py (the same reading Desk's 次は？ uses, so nothing is parsed twice).
This module only reads.
"""
import os
import re
from datetime import datetime

from lifeos.lib import logs as worklog
from lifeos.lib import paths, tasks
from lifeos.lib.tools import load_file

LOG = "work"
LINE_MAX = 80
OPEN_ORDER = {"doing": 0, "notyet": 1}
SPLIT_SOURCE = "lifeos-15"


def memory_doc():
    """Swappable: tests replace this. Returns kickoff.memory_doc()'s shape; {} if unreadable."""
    try:
        return load_file(os.path.join(paths.REPO, "desk"), "kickoff").memory_doc()
    except Exception:
        return {}


def hitokoto(session, key):
    """The same lookup as Desk: ひとこと's いま / 次, falling back to the long section."""
    sec = (session or {}).get("sections") or {}
    for line in sec.get("ひとこと") or []:
        if line.startswith(key):
            return re.sub(r"^[^：:]+[：:]\s*", "", line).strip()
    fallback = {"いま": (sec.get("やったこと") or [])[-1:], "次": (sec.get("次の一手") or [])[:1]}[key]
    return (fallback[0] if fallback else "").strip().replace("`", "")


def days_ago(at):
    try:
        d = datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=paths.HCMC)
    return (paths.now().date() - d.astimezone(paths.HCMC).date()).days


def when(at):
    n = days_ago(at)
    if n is None:
        return "日時不明"
    return "今日" if n <= 0 else "昨日" if n == 1 else f"{n}日前"


def short(text, limit=LINE_MAX):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def latest_log():
    records = worklog.read(LOG)
    return records[-1] if records else None


def by_session(limit=5):
    """The newest record of each recent session, newest first."""
    seen, out = set(), []
    for r in reversed(worklog.read(LOG)):
        key = r.get("session") or r.get("at")
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out[:limit]


def _task_key(t):
    """Same idea as Desk's order: due first, in progress next, then the list order."""
    return (0 if t.get("due") is not None else 1, t.get("due") or 0, OPEN_ORDER.get(t.get("status"), 2))


def _load_tasks():
    """(open tasks in order, titles of finished ones). A broken task store must not stop /tsuzuki."""
    try:
        items = tasks.all_tasks()
    except tasks.TaskStoreError:
        return [], set()
    return (sorted((t for t in items if t.get("status") in tasks.OPEN), key=_task_key),
            {t.get("title") for t in items if t.get("status") not in tasks.OPEN})


def _ms(at):
    try:
        return datetime.fromisoformat(at).timestamp() * 1000
    except (TypeError, ValueError):
        return 0


def pick_next(log, session, open_list, closed_titles):
    """(next move, where it came from). Always answers, however old the records are."""
    # /split gives step 1 the highest createdAt, so the newest unstarted step is the one to do
    step = max((t for t in open_list if t.get("source") == SPLIT_SOURCE and t.get("status") == "notyet"),
               key=lambda t: t.get("createdAt") or 0, default=None)
    if log and log.get("next") and log["next"] not in closed_titles:
        if not step or (step.get("createdAt") or 0) <= _ms(log.get("at")):
            return log["next"], "作業ログ・" + when(log.get("at"))
    doing = next((t for t in open_list if t.get("status") == "doing"), None)
    if doing:
        return doing["title"], "進行中のタスク"
    if step:
        return step["title"], "分けたステップ"
    nxt = hitokoto(session, "次")
    if nxt:
        return nxt, "セッション記録"
    if open_list:
        return open_list[0]["title"], "Desk のタスク"
    return "やることを1つ決めて、Desk のタスクに足す", "記録なし"


def resume():
    """{last, doing, next, source}: the three lines, plus where the next move came from."""
    log = latest_log()
    sessions = memory_doc().get("sessions") or []
    session = sessions[0] if sessions else None
    open_list, closed = _load_tasks()
    if log and log.get("done"):
        last = f"{log['done']}（{when(log.get('at'))}）"
    elif hitokoto(session, "いま"):
        last = f"{hitokoto(session, 'いま')}（セッション記録）"
    else:
        last = "記録なし"
    doing = [t for t in open_list if t.get("status") == "doing"]
    if doing:
        rest = f"（ほか{len(doing) - 1}件）" if len(doing) > 1 else ""
        now = short(doing[0]["title"], LINE_MAX - 8) + rest
    else:
        now = "進行中のタスクなし"
    move, source = pick_next(log, session, open_list, closed)
    return {"last": short(last), "doing": now, "next": short(move), "source": source}


def lines():
    r = resume()
    return [f"前回：{r['last']}", f"途中：{r['doing']}", f"次の一手：{r['next']}（{r['source']}）"]
