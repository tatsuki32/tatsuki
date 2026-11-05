"""Follow-up rules. Tracks threads whose last message is ours; draft at 3 days, ask at 14.

Answers only change this tool's state and add a Desk task. Nothing here sends mail.
"""
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta

from lifeos.lib import paths, tasks, tools

SLUG = "02-followup"
DEFAULTS = {"enabled": True, "draftDays": 3, "askDays": 14, "holdDays": 7, "maxDraftsPerRun": 5, "maxAsksPerRun": 3,
            "ignore": ["noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "notifications@"]}
MAX_TASKS_PER_RUN = 5  # the first run may find many old threads at once
OPEN = ("waiting", "held", "send")
BODY = {
    "ja": "お世話になっております。\n先日お送りした件について、その後いかがでしょうか。\nお手すきの際にご確認いただけますと幸いです。\nよろしくお願いいたします。",
    "en": "Hi,\nI wanted to follow up on my earlier message. Have you had a chance to look at it?\nThank you.",
}
CHAT_BODY = {"ja": "先日の件、その後いかがでしょうか？", "en": "Hi, just checking in on my last message. Any update?"}


def connect():
    return tools.load_tool(SLUG, "connect")


class StateError(Exception):
    """state.json exists but cannot be read. Nothing was written."""


def _path(name):
    return paths.data_path("tools", SLUG, name)


def write_json(path, value):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".f", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _saved_config():
    try:
        with open(_path("config.json"), encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def config():
    return {**DEFAULTS, **_saved_config()}


def set_enabled(value):
    write_json(_path("config.json"), {**_saved_config(), "enabled": bool(value)})


def read_state():
    try:
        with open(_path("state.json"), encoding="utf-8") as f:
            s = json.load(f)
    except FileNotFoundError:
        return {"nextNo": 1, "threads": {}, "lastRun": None}
    except ValueError as e:
        raise StateError("state.json を読めません") from e
    if not isinstance(s, dict) or not isinstance(s.get("threads"), dict):
        raise StateError("state.json の形が違います")
    return s


@contextmanager
def state():
    """The state, locked. It is saved when the block ends without an error."""
    with open(_path("state.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        s = read_state()
        yield s
        write_json(_path("state.json"), s)


def parse_at(value):
    """ISO time to an aware datetime (no zone means Ho Chi Minh time), or None."""
    try:
        d = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=paths.HCMC)


def age_days(item, day):
    at = parse_at(item["waitingAt"]).astimezone(paths.HCMC).date()
    return (datetime.strptime(day, "%Y-%m-%d").date() - at).days


def _ignored(addresses, cfg):
    words = [w.lower() for w in cfg["ignore"]]
    return not addresses or all(any(w in a.lower() for w in words) for a in addresses)


def _end_task(item, status):
    """The Desk task for sending this follow-up is over."""
    if item.get("taskId"):
        try:
            tasks.update(item["taskId"], status=status)
        except (KeyError, tasks.TaskStoreError, OSError):
            pass
        item["taskId"] = None


def _start_cycle(item, thread, at):
    item.update(state="waiting", waitingAt=at, source=thread.get("source") or "gmail", messageId=thread.get("messageId") or "", to=thread["to"],
                subject=thread.get("subject") or "（件名なし）", lang=thread.get("lang") or "", viewUrl=thread.get("viewUrl") or "",
                draft=None, draftQueuedOn=None, askedOn=None, askQueuedOn=None, holdUntil=None)


def ingest(s, snapshot, cfg):
    """Applies a snapshot. Threads missing from it stay as they are."""
    for t in snapshot.get("threads") or []:
        if not isinstance(t, dict) or not t.get("threadId"):
            continue
        at = parse_at(t.get("lastAt"))
        if at is None:
            continue
        item = s["threads"].get(t["threadId"])
        known = parse_at(item["waitingAt"]) if item else None
        if t.get("lastFrom") == "me":
            if (item and at <= known) or _ignored(t.get("to") or [], cfg):
                continue
            if item is None:
                item = s["threads"][t["threadId"]] = {"no": s["nextNo"], "threadId": t["threadId"], "taskId": None}
                s["nextNo"] += 1
            _end_task(item, "done")  # we wrote again, so the follow-up went out
            _start_cycle(item, t, at.isoformat())
        elif t.get("lastFrom") == "them" and item and item["state"] in OPEN and at > known:
            _end_task(item, "done")
            item["state"] = "closed"


def _is_chat(item):
    return item.get("source") == "whatsapp"


def _who(item):
    """The name to show: 'Name <address>' without the address."""
    return (item["to"][0].split("<")[0].strip(' "') or item["to"][0]) if item.get("to") else item["subject"]


def _queue_tasks(s, day, cfg):
    """A Desk task for today once someone has not answered for draftDays. It closes when they answer (ingest)."""
    due = sorted((i for i in s["threads"].values() if i["state"] == "waiting" and not i.get("taskId")
                  and age_days(i, day) >= cfg["draftDays"]), key=lambda i: i["waitingAt"])
    for item in due[:MAX_TASKS_PER_RUN]:
        note = "返信待ちの画面で文面をコピーして、WhatsApp で自分で送る。" if _is_chat(item) else "Gmail の下書きを開いて、自分で送る。Life OS は送信しません。"
        rec = tasks.add("催促：" + _who(item), source="lifeos-02", source_ref=item["threadId"], note=note)
        tasks._put({**rec, "planDate": day})
        item["taskId"] = rec["id"]
    return len(due[:MAX_TASKS_PER_RUN])


def apply_chats(threads, day=None):
    """The same rules for chats. They have no draft and no 14-day question: the task and the screen are enough."""
    day = day or paths.today()
    cfg = config()
    if not cfg["enabled"] or tools.info(os.path.join(tools.root_dir(), SLUG)).get("status") == "stopped":
        return {"stopped": True}
    with state() as s:
        ingest(s, {"threads": threads}, cfg)
        return {"tasks": _queue_tasks(s, day, cfg)}


def draft_request(item):
    subject = item["subject"]
    if not subject.lower().startswith(("re:", "re：")):
        subject = "Re: " + subject
    return {"no": item["no"], "to": item["to"], "subject": subject, "body": BODY["ja" if item.get("lang") == "ja" else "en"],
            "lang": item.get("lang") or "", "replyToMessageId": item.get("messageId") or ""}


def _queued(s):
    return [i for i in s["threads"].values() if i.get("draftQueuedOn") and not i.get("draft") and i["state"] in OPEN]


def _queue_drafts(s, day, cfg):
    due = sorted((i for i in s["threads"].values() if i["state"] == "waiting" and not i.get("draft") and not i.get("draftQueuedOn")
                  and not _is_chat(i) and age_days(i, day) >= cfg["draftDays"]), key=lambda i: i["waitingAt"])
    for item in due[:max(0, cfg["maxDraftsPerRun"] - len(_queued(s)))]:
        item["draftQueuedOn"] = day
    made = 0
    for item in _queued(s):
        result = connect().make_draft(draft_request(item))
        if result:
            item["draft"] = {"id": result["id"], "viewUrl": result.get("viewUrl") or "", "on": day}
            made += 1
    return made


def question(item, day):
    return (f"返信待ち {age_days(item, day)}日：{item['subject']}（{item['to'][0]}）\n"
            f"催促の下書きは Gmail にあります。送信はしていません。\n答え方：\n"
            f"催促 送る {item['no']}\n催促 保留 {item['no']}\n催促 捨てる {item['no']}")


def _ask(s, day, cfg):
    conn = connect()
    for item in s["threads"].values():  # a hold that ran out is asked again
        if item["state"] == "held" and item.get("holdUntil") and item["holdUntil"] <= day:
            item.update(state="waiting", holdUntil=None, askedOn=None, askQueuedOn=None)
    due = sorted((i for i in s["threads"].values() if i["state"] == "waiting" and i.get("draft") and not i.get("askedOn")
                  and age_days(i, day) >= cfg["askDays"]), key=lambda i: i["waitingAt"])
    asked = 0
    for item in due:
        if asked >= cfg["maxAsksPerRun"]:
            break
        if item.get("askQueuedOn") and not conn.can_ask():
            continue  # already waiting in the outbox
        if conn.ask(question(item, day)):
            item["askedOn"] = day
        else:
            item["askQueuedOn"] = day
        asked += 1
    return asked


def morning(day=None):
    """The morning pass. Acts on mail only when a snapshot for today exists."""
    day = day or paths.today()
    cfg = config()
    if not cfg["enabled"] or tools.info(os.path.join(tools.root_dir(), SLUG)).get("status") == "stopped":
        return {"stopped": True}
    snapshot = connect().read_waiting(day)
    if snapshot is None:
        return {"stale": True}
    with state() as s:
        ingest(s, snapshot, cfg)
        made = _queue_drafts(s, day, cfg)
        asked = _ask(s, day, cfg)
        added = _queue_tasks(s, day, cfg)
        s["lastRun"] = day
        return {"drafted": made, "asked": asked, "tasks": added, "pendingDrafts": len(_queued(s))}


def pending_drafts():
    return [draft_request(i) for i in _queued(read_state())]


def find(s, no):
    return next((i for i in s["threads"].values() if i["no"] == no), None)


def record_draft(no, draft_id, view_url="", day=None):
    with state() as s:
        item = find(s, no)
        if item is None:
            raise KeyError(no)
        item["draft"] = {"id": draft_id, "viewUrl": view_url, "on": day or paths.today()}


def open_items(s=None):
    s = s or read_state()
    return sorted((i for i in s["threads"].values() if i["state"] in OPEN), key=lambda i: i["waitingAt"])


def asking(s=None):
    return [i for i in open_items(s) if i["state"] == "waiting" and (i.get("askedOn") or i.get("askQueuedOn"))]


def answer(no, choice, day=None):
    """送る / 保留 / 捨てる / 戻す. Returns the reply. 送る only asks the owner to send it themself."""
    day = day or paths.today()
    with state() as s:
        item = find(s, no)
        if item is None:
            return f"{no}番の返信待ちはありません。"
        name = item["subject"]
        if item["state"] == "closed":
            return f"「{name}」は相手から返信が来ています。"
        if choice == "戻す":
            _end_task(item, "archived")
            item.update(state="waiting", holdUntil=None, askedOn=None, askQueuedOn=None)
            return f"「{name}」を返信待ちに戻しました。"
        if choice == "捨てる":
            _end_task(item, "archived")
            item.update(state="dropped", holdUntil=None)
            return f"「{name}」の催促をやめました。Gmail の下書きは消していません。元に戻すなら：催促 戻す {no}"
        if choice == "保留":
            _end_task(item, "archived")
            until = datetime.strptime(day, "%Y-%m-%d") + timedelta(days=config()["holdDays"])
            item.update(state="held", holdUntil=until.strftime("%Y-%m-%d"))
            return f"「{name}」を保留にしました。{until.month}月{until.day}日にまた聞きます。"
        if not item.get("draft"):
            return f"「{name}」の下書きがまだありません。朝の処理で作ったあとに答えてください。"
        if not item.get("taskId"):
            item["taskId"] = tasks.add("催促を送る：" + name, source="lifeos-02", source_ref=item["threadId"],
                                       note="Gmail の下書きを開いて、自分で送る。Life OS は送信しません。")["id"]
        item.update(state="send", holdUntil=None)
        url = item["draft"].get("viewUrl") or "Gmail の下書き"
        return f"送信はしていません。下書きを確かめて、自分で送ってください：{url}\nDesk のタスクにも入れました。"


def _public(item, day, cfg):
    """One follow-up as the Desk shows it. Never holds mail text."""
    draft = item.get("draft") or {}
    return {"id": f"f{item['no']}", "no": item["no"], "subject": item["subject"], "to": item["to"], "state": item["state"],
            "waitingAt": item["waitingAt"], "hasDraft": bool(draft), "draftUrl": draft.get("viewUrl") or item.get("viewUrl") or "",
            "source": item.get("source") or "gmail", "copyText": CHAT_BODY["ja" if item.get("lang") == "ja" else "en"] if _is_chat(item) else "",
            "holdUntil": item.get("holdUntil"),
            "asking": item["state"] == "waiting" and bool(draft) and age_days(item, day) >= cfg["askDays"]}


def desk_view(day=None):
    """Everything the 返信待ち screen needs. Replied threads are left out."""
    day = day or paths.today()
    cfg = config()
    s = read_state()
    items = sorted((i for i in s["threads"].values() if i["state"] != "closed"), key=lambda i: i["waitingAt"])
    return {"enabled": bool(cfg["enabled"]), "lastRun": s.get("lastRun"), "items": [_public(i, day, cfg) for i in items]}


CHOICES = ("送る", "保留", "捨てる", "戻す")


def sync_online(remote, day=None):
    """Applies answers left online, then returns the docs the online desk must set. state.json stays the source."""
    day = day or paths.today()
    online = {d["id"]: d for d in remote if isinstance(d, dict) and d.get("id")}
    answers = sorted((d for d in online.values() if d.get("answer") in CHOICES and isinstance(d.get("no"), int)),
                     key=lambda d: d.get("answeredAt") or 0)
    for d in answers:
        answer(d["no"], d["answer"], day)
    view = desk_view(day)
    local = {d["id"]: d for d in view["items"]}
    local["_meta"] = {"id": "_meta", "enabled": view["enabled"], "lastRun": view["lastRun"]}
    push = [d for i, d in local.items() if online.get(i) != d]
    push += [{"id": i, "deleted": True} for i, d in online.items() if i not in local and not d.get("deleted")]
    return push


def _short(titles, unit=3):
    head = " / ".join(titles[:unit])
    return head + (f"（ほか{len(titles) - unit}件）" if len(titles) > unit else "")


def _old(age):
    return f"（{int(age)}時間前の情報）" if age is not None and age >= 24 else ""


def today_lines(day=None):
    """The list for the morning: today's events, ours to answer, waiting on them. Reads only."""
    day = day or paths.today()
    conn = connect()
    out = []
    cal = conn.read_calendar(day)
    if cal is None:
        out.append("今日の予定：まだ届いていません")
    else:
        rest = [e for e in cal["events"] if not e.get("past")]
        names = [("終日 " if e["allDay"] else e["start"][-5:] + " ") + e["title"] for e in rest]
        out.append("今日の予定：" + (_short(names) if names else "なし") + _old(cal["ageHours"]))
    mail = conn.read_needs_reply(day)
    dms = conn.read_dms(day) or []
    if mail is None and not dms:
        out.append("こちらが返すもの：メールの確認がまだ届いていません")
    else:
        items = (mail["items"] if mail else []) + dms
        age = _old(mail["ageHours"]) if mail else ""
        out.append(f"こちらが返すもの：{len(items)}件" + ("　" + _short([x["title"] for x in items]) if items else "") + age)
    s = read_state()
    waiting = open_items(s)
    if s.get("lastRun") is None and not waiting:
        return out
    named = [f"{i['subject']}（{age_days(i, day)}日）" for i in waiting]
    line = f"相手の返信待ち：{len(waiting)}件" + ("　" + _short(named) if named else "")
    if s.get("lastRun") != day:
        line += "（今朝のメール確認はまだです）"
    out.append(line)
    for item in asking(s)[:3]:
        out.append(f"答えを待っています：{item['subject']} → 催促 送る {item['no']} / 保留 / 捨てる")
    return out
