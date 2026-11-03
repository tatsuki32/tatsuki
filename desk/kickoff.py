#!/usr/bin/env python3
"""/morning-kickoff の下ごしらえ：Mac に同期済みの Desk データと memory/ を読み、
今日の予定と「最初の1つ」の候補を JSON で出す。Python 標準ライブラリだけで動く。

  python3 desk/kickoff.py                 候補を出す（読むだけ。何も書かない）
  python3 desk/kickoff.py start <task id> そのタスクを Desk で「進行中」にする（localhost:4747 経由）
  python3 desk/kickoff.py add "<タイトル>"  新しいタスクを「進行中」で登録する（/begin 用）

時刻はホーチミン時間（UTC+7）。12時より前は「集中」、以降は「事務」の時間帯。
出力には予定やメールの件名が入るが、画面に出すだけでファイルには書かない（公開リポジトリのため）。
"""
import glob
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
REPO = os.path.dirname(ROOT)
HCMC = timezone(timedelta(hours=7))
DAY = 86400000
FOCUS_UNTIL = 12  # 12時より前は集中作業
OPEN = {"notyet", "doing"}
NEEDS = {"Action needed", "Reply drafted"}
API = "http://localhost:4747"


def now_ms():
    return int(time.time() * 1000)


def read_json(name, default):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def read_js(name):
    """data/*.js は window.X=<json>; の1行"""
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            text = f.read().strip()
    except FileNotFoundError:
        return None
    m = re.match(r"^window\.\w+\s*=\s*(.*?);?\s*$", text, re.S)
    try:
        return json.loads(m.group(1)) if m else None
    except json.JSONDecodeError:
        return None


def age_hours(name):
    p = os.path.join(DATA, name)
    return round((time.time() - os.path.getmtime(p)) / 3600, 1) if os.path.exists(p) else None


def to_ms(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v)
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            return int(datetime.fromisoformat(s).replace(tzinfo=HCMC).timestamp() * 1000)
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=HCMC)
        return int(d.timestamp() * 1000)
    except ValueError:
        return None


def due_doc_id(uid):
    """index.html の dueDocId と同じ値（メール由来タスクの ID は "t" + これ）"""
    h, n = 5381, 0
    for ch in uid:
        cp = ord(ch)
        unit = 0xD800 + ((cp - 0x10000) >> 10) if cp > 0xFFFF else cp
        n += 2 if cp > 0xFFFF else 1
        h = ((h * 33) % 2**32) ^ unit
    digits, out = "0123456789abcdefghijklmnopqrstuvwxyz", ""
    while True:
        h, r = divmod(h, 36)
        out = digits[r] + out
        if not h:
            break
    return "d" + out + str(n)


def fmt(ms):
    return datetime.fromtimestamp(ms / 1000, HCMC).strftime("%m/%d %H:%M") if ms else None


def events_today(now):
    cal = read_js("calendar.js") or {}
    day = datetime.fromtimestamp(now / 1000, HCMC).date()
    start = int(datetime(day.year, day.month, day.day, tzinfo=HCMC).timestamp() * 1000)
    end = start + DAY
    out = []
    for e in cal.get("events") or []:
        s, t = to_ms(e.get("start")), to_ms(e.get("end"))
        if s is None or t is None or t <= start or s >= end:
            continue
        out.append({"title": e.get("title") or "", "allDay": bool(e.get("allDay")),
                    "start": fmt(s), "end": fmt(t), "startMs": s, "endMs": t, "past": t <= now})
    return sorted(out, key=lambda x: (not x["allDay"], x["startMs"]))


def mail_tasks(dues, stored):
    days = read_js("mail.js") or []
    seen, closed, out = set(), set(), []
    for d in days:  # 新しい日から
        for i in (d.get("data") or {}).get("items") or []:
            if i.get("priority") not in NEEDS:
                continue
            uid = "mail:" + (i.get("gmail_url") or f"{d.get('id')}/{i.get('key')}")
            if (i.get("status") or "pending") != "pending":
                closed.add(uid)
                continue
            if uid in seen:
                continue
            seen.add(uid)
            manual = (dues.get(due_doc_id(uid)) or {}).get("at")
            base = to_ms(i.get("receivedAt")) or to_ms((d.get("id") or "") + "T00:00:00+07:00") or now_ms()
            due = to_ms(manual) or to_ms(i.get("dueAt")) or base + 3 * DAY
            tid = "t" + due_doc_id(uid)
            st = stored.get(tid, {}).get("status", "notyet")
            out.append({"id": tid, "title": i.get("subject_ja") or i.get("subject") or "（件名なし）", "source": "mail",
                        "sourceRef": uid, "status": st, "due": due, "kind": "事務", "from": i.get("from") or ""})
    return [x for x in out if x["sourceRef"] not in closed and x["status"] in OPEN]


def next_steps():
    files = sorted(glob.glob(os.path.join(REPO, "memory", "episodic", "sessions", "*.md")), reverse=True)[:3]
    steps = []
    for p in files:
        with open(p, encoding="utf-8") as f:
            text = f.read()
        m = re.search(r"^##\s*次の一手\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
        if m:
            for line in m.group(1).splitlines():
                line = line.strip().lstrip("-・* ").strip()
                if line:
                    steps.append({"text": line, "file": os.path.relpath(p, REPO)})
    return steps


def kind_of(t):
    """MVP の見分け方：メール由来・手続き系の言葉は事務、それ以外は集中"""
    if t.get("source") == "mail":
        return "事務"
    return "事務" if re.search(r"返信|連絡|申請|手続|支払|予約|提出|確認|メール|電話", t.get("title", "")) else "集中"


def candidates():
    now = now_ms()
    stored = {t["id"]: t for t in read_json("tasks.json", []) if isinstance(t, dict) and t.get("id") and not t.get("deleted")}
    dues = {d["id"]: d for d in read_json("dues.json", []) if isinstance(d, dict) and d.get("id")}
    tasks = []
    for t in stored.values():
        if t.get("status") not in OPEN or t.get("source") == "mail":
            continue
        tasks.append({"id": t["id"], "title": t.get("title", ""), "source": t.get("source", "manual"), "status": t["status"],
                      "due": to_ms(t.get("due")), "note": (t.get("note") or "")[:300], "kind": kind_of(t)})
    for g in (read_js("gtasks.js") or {}).get("tasks") or []:
        if not g.get("id") or not g.get("title") or g.get("status") == "completed":
            continue
        tid = "tg" + g["id"]
        st = stored.get(tid, {}).get("status", "notyet")
        if st in OPEN:
            due = to_ms(g["due"][:10] + "T23:59:00+07:00") if g.get("due") else None
            tasks.append({"id": tid, "title": g["title"], "source": "gtasks", "sourceRef": g["id"], "status": st,
                          "due": due, "note": (g.get("notes") or "")[:300], "kind": kind_of(g)})
    tasks += mail_tasks(dues, stored)

    events = events_today(now)
    nxt = next((e for e in events if not e["allDay"] and e["startMs"] > now), None)
    free_min = int((nxt["startMs"] - now) / 60000) if nxt else None
    hour = datetime.fromtimestamp(now / 1000, HCMC).hour
    mode = "集中" if hour < FOCUS_UNTIL else "事務"
    day_end = int(datetime.fromtimestamp(now / 1000, HCMC).replace(hour=23, minute=59, second=59).timestamp() * 1000)
    steps = next_steps()

    def score(t):
        due_today = t["due"] is not None and now <= t["due"] <= day_end
        overdue = t["due"] is not None and t["due"] < now
        return (0 if due_today else 1 if overdue else 2,
                0 if t["status"] == "doing" else 1,
                0 if t["kind"] == mode else 1,
                t["due"] or 9e15)

    tasks.sort(key=score)
    for t in tasks:
        t["dueText"] = fmt(t["due"])
        t["dueToday"] = t["due"] is not None and now <= t["due"] <= day_end
        t["overdue"] = t["due"] is not None and t["due"] < now
    return {
        "now": fmt(now), "weekday": "月火水木金土日"[datetime.fromtimestamp(now / 1000, HCMC).weekday()],
        "mode": mode, "nextEvent": nxt and {"title": nxt["title"], "start": nxt["start"]}, "freeMinutes": free_min,
        "short": free_min is not None and free_min < 30,
        "events": [{k: e[k] for k in ("title", "allDay", "start", "end", "past")} for e in events],
        "nextSteps": steps, "tasks": tasks[:15], "taskCount": len(tasks),
        "dataAgeHours": {n: age_hours(n) for n in ("tasks.json", "calendar.js", "mail.js", "gtasks.js")},
    }


def put_task(task_id, rec):
    req = urllib.request.Request(f"{API}/api/tasks/{task_id}", data=json.dumps(rec, ensure_ascii=False).encode(),
                                 method="PUT", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            r.read()
    except OSError as e:
        sys.exit(f"Desk サーバー（localhost:4747）につながりません：{e}")


def add(title):
    title = " ".join(title.split())[:120]
    if not title:
        sys.exit("タイトルが空です")
    tid = "t" + format(now_ms(), "x") + os.urandom(2).hex()
    put_task(tid, {"title": title, "status": "doing", "source": "manual", "sourceRef": "", "due": None, "note": "",
                   "createdAt": now_ms(), "deleted": False, "updatedAt": now_ms()})
    print(json.dumps({"ok": True, "id": tid, "title": title, "status": "doing"}, ensure_ascii=False))


def start(task_id):
    c = candidates()
    t = next((x for x in c["tasks"] if x["id"] == task_id), None)
    if not t:
        sys.exit(f"タスクが見つかりません：{task_id}")
    stored = {x["id"]: x for x in read_json("tasks.json", []) if isinstance(x, dict) and x.get("id")}
    old = stored.get(task_id, {})
    rec = {"title": t["title"], "status": "doing", "source": t["source"], "sourceRef": t.get("sourceRef", old.get("sourceRef", "")),
           "due": None if t["source"] in ("mail", "gtasks", "calendar", "claude", "daily") else old.get("due"),
           "note": old.get("note", ""), "createdAt": old.get("createdAt", now_ms()), "deleted": False, "updatedAt": now_ms()}
    put_task(task_id, rec)
    print(json.dumps({"ok": True, "id": task_id, "title": t["title"], "status": "doing"}, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "start":
        start(sys.argv[2])
    elif len(sys.argv) >= 3 and sys.argv[1] == "add":
        add(" ".join(sys.argv[2:]))
    else:
        print(json.dumps(candidates(), ensure_ascii=False, indent=1))
