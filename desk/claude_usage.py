#!/usr/bin/env python3
"""Claude の利用量を Desk に渡す。AI は使わず、手元の記録を数えるだけ。

- 今日・昨日・直近5時間・会話ごと：~/.claude/projects の会話の記録（*.jsonl）の各応答のトークン数を読む。
  Desk のサーバー（4747）が5分ごとに `refresh()` を呼び、結果を desk/data/claude-usage/live.json に置く。
  前回から増えた分だけを読む（ファイルごとに読んだ位置を cache.json に覚える）。最近3日に変わっていないファイルは開かない。
  読むのは応答のトークン数・時刻・モデル、会話の題、作業フォルダ、最初の発言（定期タスクの名前を見分けるため）だけ。本文は残さない。
- 定期作業の一覧：Claude の定期タスク（Claude アプリの scheduled-tasks.json と各 SKILL.md）、
  Mac の常駐ジョブ（~/Library/LaunchAgents/com.tatsuki.*.plist）、Desk サーバーの中の定期処理（下の SERVER_JOBS）。
- 日別の表：~/ClaudeUsage/ledger.json（usage_ledger.py が毎朝 7:00 に作る）をそのまま渡す。

金額は API 単価からの目安。実際の請求や Max プランの消費とは一致しない。日付はホーチミン時間（UTC+7）で区切る。
Max プランの上限の何％かは手元にデータがないので出さない。
"""
import glob
import json
import os
import plistlib
import re
import threading
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DESK_DATA") or os.path.join(HERE, "data")
OUT = os.path.join(DATA, "claude-usage")
LEDGER_DIR = os.path.expanduser(os.environ.get("CLAUDE_USAGE_DIR") or "~/ClaudeUsage")
PROJECTS = os.path.expanduser("~/.claude/projects")
SKILLS = os.path.expanduser("~/.claude/scheduled-tasks")
APP = os.path.expanduser("~/Library/Application Support/Claude")
AGENTS = os.path.expanduser("~/Library/LaunchAgents")
LIFELOG_USAGE = os.path.join(DATA, "lifelog", "usage.json")
HCM = timezone(timedelta(hours=7))
KEEP_DAYS = 3          # 今日・昨日を数えるのに足りる分だけ覚える
NOTE_DAYS = 8          # 上限のお知らせ（週の上限）は1週間と少し覚える
EVERY = 300            # 5分ごと
LOCK = threading.Lock()

# 単価（$ / 100万トークン）：入力, 出力, キャッシュ書込5分, 書込1時間, 読込。~/ClaudeUsage/usage_ledger.py と同じ値。
PRICE = {
    "claude-opus-5-5":   (4.00, 20.00, 5.00, 8.00, 0.20),
    "claude-fable-5-1":  (10.00, 50.00, 12.50, 20.00, 0.25),
    "claude-fable-5-2":  (10.00, 50.00, 12.50, 20.00, 0.25),
    "claude-opus-5":     (5.00, 25.00, 6.25, 10.00, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 4.00, 0.20),
    "claude-haiku-5-5":  (0.10, 0.50, 0.125, 0.20, 0.01),
}
TASK_RE = re.compile(r"scheduled-tasks/([\w\-]+)")

# Mac の常駐ジョブの説明（画面はアメリカ英語）。claude: Claude を使うかどうか。
LAUNCHD = {
    "com.tatsuki.desk.server": ("Desk server", "Serves the Desk on localhost:4747 and runs the jobs listed under Desk server.", ""),
    "com.tatsuki.desk.pull": ("Code update", "Pulls the latest Desk code from GitHub (main only).", ""),
    "com.tatsuki.desk.backup": ("Data backup", "Sends desk/data to the private backup repository.", ""),
    "com.tatsuki.desk.line": ("LINE replies", "Reads unread LINE chats from the screen and turns them into reply tasks.", "Claude API reads the screenshots"),
    "com.tatsuki.desk.linenight": ("LINE night wake", "Keeps the Mac awake at night while plugged in, so LINE can be read.", ""),
    "com.tatsuki.desk.screentime": ("Screen Time", "Adds app and site time from macOS to the Desk.", ""),
    "com.tatsuki.desk.tabwatch": ("Browser tab time", "Notes the site in the front Chrome or Brave tab, once a minute.", ""),
    "com.tatsuki.desk.telegram": ("Telegram bot", "Receives photos and notes for the Body screens.", "Claude API reads the photos"),
    "com.tatsuki.desk.webhistory": ("Web history", "Copies Chrome history into the Desk.", ""),
    "com.tatsuki.desk.wise": ("Wise import", "Reads Wise transactions for the Money screen.", ""),
    "com.tatsuki.claude-usage": ("Claude usage ledger", "Rebuilds the daily table at the bottom of this screen.", ""),
}

# Desk のサーバーの中で回っている処理（server.py の __main__）。
SERVER_JOBS = [
    ("Claude usage (this screen)", "Every 5 min", "Counts tokens from the conversation records.", "4747 only"),
    ("Notifications", "Every 1 min", "Mac notifications for replies, events and summaries.", "4747 only"),
    ("Routine alarm", "Every 1 min", "Starts the morning and evening routine alarm.", "4747 only"),
    ("Routine sync", "Every 2 min", "Syncs routines with the Routine app on the phone.", "4747 only"),
    ("Transcribe watchdog", "Every 1 min", "Keeps the recorder on port 8791 running.", "4747 only"),
    ("PC monitor", "Every 1 min", "Watches memory pressure for the PC screen.", ""),
    ("Life map question", "Every 15 min", "Life OS 14: the daily question.", ""),
]

DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _usd(model, inp, out, cw5, cw1, cr):
    p = PRICE.get(model)
    if not p:
        return 0.0
    return (inp * p[0] + out * p[1] + cw5 * p[2] + cw1 * p[3] + cr * p[4]) / 1e6


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(x.get("text", "") for x in content if isinstance(x, dict) and isinstance(x.get("text"), str))
    return ""


def _known_tasks():
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(SKILLS, "*")) if os.path.isdir(p))


def _label(txt, known):
    m = TASK_RE.search(txt)
    if m:
        return m.group(1)
    for name in known:
        if name in txt:
            return name
    return ""


def _ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def _read_more(fp, ent, known, floor, now):
    """ファイルの続き（前回読んだ位置から）を読み、ent を更新する。"""
    with open(fp, "rb") as f:
        f.seek(ent["off"])
        data = f.read()
    end = data.rfind(b"\n")
    if end < 0:
        return
    ent["off"] += end + 1
    for raw in data[:end].split(b"\n"):
        if not raw:
            continue
        line = raw.decode("utf-8", "ignore")
        fast = line[:400]
        if '"custom-title"' in fast or '"ai-title"' in fast or '"type":"summary"' in fast:
            try:
                d = json.loads(line)
                t = d.get("customTitle") or d.get("aiTitle") or d.get("summary")
                if isinstance(t, str) and t.strip():
                    ent["title"] = t.strip()[:120]
            except ValueError:
                pass
            continue
        if ent.get("label") is None and '"type":"user"' in line:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "user":
                ent["label"] = _label(_text((d.get("message") or {}).get("content")), known)
                if d.get("cwd"):
                    ent["cwd"] = d["cwd"]
            continue
        if '"isApiErrorMessage":true' in line:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            ts = _ts(d.get("timestamp"))
            text = _text((d.get("message") or {}).get("content")).strip()
            if ts and ts >= now - NOTE_DAYS * 86400 and text:
                ent.setdefault("notes", []).append([round(ts), text[:140]])
            continue
        if '"usage"' not in line or '"assistant"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        m = d.get("message") or {}
        u = m.get("usage")
        if d.get("type") != "assistant" or not u:
            continue
        ts = _ts(d.get("timestamp"))
        if ts is None or ts < floor:
            continue
        if not ent.get("cwd") and d.get("cwd"):
            ent["cwd"] = d["cwd"]
        cc = u.get("cache_creation") or {}
        rec = [round(ts), m.get("model") or "?", u.get("input_tokens") or 0, u.get("output_tokens") or 0,
               cc.get("ephemeral_5m_input_tokens") or 0, cc.get("ephemeral_1h_input_tokens") or 0,
               u.get("cache_read_input_tokens") or 0]
        mid = m.get("id") or d.get("uuid") or str(ts)
        prev = ent["msgs"].get(mid)
        if prev is None or rec[3] >= prev[3]:
            ent["msgs"][mid] = rec


def _load_cache():
    try:
        with open(os.path.join(OUT, "cache.json"), encoding="utf-8") as f:
            c = json.load(f)
        if c.get("v") == 1:
            return c
    except (OSError, ValueError):
        pass
    return {"v": 1, "files": {}}


def _write(name, obj):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def _scan(now):
    floor = now - KEEP_DAYS * 86400
    cache = _load_cache()
    files = cache["files"]
    known = _known_tasks()
    seen = set()
    for fp in glob.glob(os.path.join(PROJECTS, "**", "*.jsonl"), recursive=True):
        try:
            st = os.stat(fp)
        except OSError:
            continue
        if st.st_mtime < now - NOTE_DAYS * 86400:
            continue
        seen.add(fp)
        ent = files.get(fp)
        if ent is None or st.st_size < ent["off"]:
            ent = files[fp] = {"off": 0, "msgs": {}, "label": None}
        if st.st_size > ent["off"]:
            try:
                _read_more(fp, ent, known, floor, now)
            except OSError:
                continue
        ent["msgs"] = {k: v for k, v in ent["msgs"].items() if v[0] >= floor}
        if ent.get("notes"):
            ent["notes"] = [n for n in ent["notes"] if n[0] >= now - NOTE_DAYS * 86400]
    for fp in list(files):
        if fp not in seen:
            del files[fp]
    _write("cache.json", cache)
    return files


def _blank():
    return {"msgs": 0, "tok": 0, "out": 0, "cr": 0, "usd": 0.0}


def _add(a, r):
    a["msgs"] += 1
    a["tok"] += r[2] + r[3] + r[4] + r[5] + r[6]
    a["out"] += r[3]
    a["cr"] += r[6]
    a["usd"] += _usd(*r[1:])


def _folder(cwd):
    if not cwd:
        return ""
    if "/scratch-workspaces/" in cwd:
        return "Scratch"
    home = os.path.expanduser("~")
    return "~" if cwd == home else os.path.basename(cwd.rstrip("/"))


def _cron_text(expr):
    """よく使う形の cron を読める英語にする。読めない形はそのまま返す。"""
    p = (expr or "").split()
    if len(p) != 5:
        return expr or ""
    mi, hr, dom, mon, dow = p
    def hm(h):
        return f"{int(h)}:{int(mi):02d}" if mi.isdigit() else h
    if hr.startswith("*/") and mi.isdigit():
        return f"Every {hr[2:]} hours at :{int(mi):02d}"
    if hr == "*" and mi.isdigit():
        return f"Every hour at :{int(mi):02d}"
    if not mi.isdigit():
        return expr
    times = ", ".join(hm(h) for h in hr.split(",")) if all(h.isdigit() for h in hr.split(",")) else None
    if times is None:
        return expr
    if dom.startswith("*/") and dow == "*":
        return f"Every {dom[2:]} days at {times}"
    if dom == "*" and dow == "*":
        return f"Daily at {times}"
    if dom == "*" and all(x.isdigit() for x in dow.split(",")):
        return ", ".join(DOW[int(x) % 7] for x in dow.split(",")) + f" at {times}"
    return expr


def _skill_desc(path):
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(3000)
    except OSError:
        return ""
    m = re.search(r"^description:\s*(.+)$", head, re.M)
    return m.group(1).strip() if m else ""


def _claude_jobs():
    tasks = {}
    for fp in glob.glob(os.path.join(APP, "*", "*", "*", "scheduled-tasks.json")):
        try:
            with open(fp, encoding="utf-8") as f:
                for t in json.load(f).get("scheduledTasks") or []:
                    if t.get("id"):
                        tasks[t["id"]] = t
        except (OSError, ValueError, AttributeError):
            continue
    for name in _known_tasks():
        tasks.setdefault(name, {"id": name})
    out = []
    for tid, t in tasks.items():
        skill = t.get("filePath") or os.path.join(SKILLS, tid, "SKILL.md")
        last = _ts(t.get("lastRunAt") or "")
        out.append({
            "id": tid,
            "name": t.get("displayName") or tid,
            "desc": _skill_desc(skill),
            "when": _cron_text(t.get("cronExpression")) if t.get("cronExpression") else "No schedule",
            "cron": t.get("cronExpression") or "",
            "enabled": bool(t.get("enabled")) if "enabled" in t else None,
            "lastRun": datetime.fromtimestamp(last, HCM).isoformat(timespec="minutes") if last else "",
        })
    return out


def _interval(sec):
    sec = int(sec)
    if sec % 3600 == 0:
        return f"Every {sec // 3600} hour" + ("s" if sec // 3600 > 1 else "")
    if sec % 60 == 0:
        return f"Every {sec // 60} min"
    return f"Every {sec} s"


def _launchd_jobs():
    out = []
    for fp in sorted(glob.glob(os.path.join(AGENTS, "com.tatsuki.*.plist"))):
        try:
            with open(fp, "rb") as f:
                p = plistlib.load(f)
        except Exception:
            continue
        label = p.get("Label") or os.path.basename(fp)[:-6]
        name, what, claude = LAUNCHD.get(label, (label.split(".")[-1].capitalize(), "", ""))
        when = []
        if p.get("StartInterval"):
            when.append(_interval(p["StartInterval"]))
        cal = p.get("StartCalendarInterval")
        if cal:
            for c in cal if isinstance(cal, list) else [cal]:
                h, m = c.get("Hour"), c.get("Minute", 0)
                day = DOW[c["Weekday"] % 7] + " " if "Weekday" in c else "Daily "
                when.append(f"{day}{h}:{m:02d}" if h is not None else f"Every hour at :{m:02d}")
        if p.get("KeepAlive"):
            when.append("Always running")
        if p.get("RunAtLoad"):
            when.append("At login")
        prog = p.get("ProgramArguments") or [p.get("Program") or ""]
        script = next((os.path.basename(a) for a in prog if a.endswith((".py", ".sh"))), os.path.basename(prog[0] or ""))
        last = 0
        for k in ("StandardOutPath", "StandardErrorPath"):
            try:
                last = max(last, os.stat(os.path.expanduser(p.get(k) or "")).st_mtime)
            except OSError:
                pass
        out.append({"id": label, "name": name, "desc": what, "when": ", ".join(when) or "On demand",
                    "script": script, "claude": claude,
                    "lastLog": datetime.fromtimestamp(last, HCM).isoformat(timespec="minutes") if last else ""})
    return out


def refresh():
    """記録を読み直して live.json を作る。同時に2回は動かさない。"""
    with LOCK:
        t0 = time.time()
        now = t0
        files = _scan(now)
        today = datetime.fromtimestamp(now, HCM).date()
        yday = today - timedelta(days=1)
        days = {today.isoformat(): "today", yday.isoformat(): "yesterday"}
        cards = {"today": _blank(), "yesterday": _blank(), "last5h": _blank()}
        split = {k: {"routine": 0.0, "chat": 0.0} for k in ("today", "yesterday", "last5h")}
        hours = {k: [{"routine": 0.0, "chat": 0.0, "tok": 0} for _ in range(24)] for k in ("today", "yesterday")}
        sessions = {}
        seen = set()
        parents = {fp: e for fp, e in files.items() if "/subagents/" not in fp}
        for fp, ent in files.items():
            key = fp.split("/subagents/")[0] + ".jsonl" if "/subagents/" in fp else fp
            head = parents.get(key) or ent
            label = head.get("label") or ""
            kind = "routine" if label else "chat"
            for mid, r in ent["msgs"].items():
                if mid in seen:
                    continue
                seen.add(mid)
                dt = datetime.fromtimestamp(r[0], HCM)
                usd = _usd(*r[1:])
                if now - r[0] <= 5 * 3600:
                    _add(cards["last5h"], r)
                    split["last5h"][kind] += usd
                which = days.get(dt.date().isoformat())
                if not which:
                    continue
                _add(cards[which], r)
                split[which][kind] += usd
                hours[which][dt.hour][kind] += usd
                hours[which][dt.hour]["tok"] += r[2] + r[3] + r[4] + r[5] + r[6]
                s = sessions.setdefault((which, key), {
                    "title": head.get("title") or "", "label": label, "kind": kind,
                    "folder": _folder(head.get("cwd")), "start": r[0], "end": r[0],
                    "models": {}, "agents": 0, **_blank()})
                _add(s, r)
                s["start"] = min(s["start"], r[0])
                s["end"] = max(s["end"], r[0])
                s["models"][r[1]] = s["models"].get(r[1], 0) + 1
            if "/subagents/" in fp and ent["msgs"]:
                for (which, k), s in sessions.items():
                    if k == key:
                        s["agents"] += 1
        notes = {}
        for fp, ent in files.items():
            key = fp.split("/subagents/")[0] + ".jsonl" if "/subagents/" in fp else fp
            for ts, text in ent.get("notes") or []:
                which = days.get(datetime.fromtimestamp(ts, HCM).date().isoformat())
                if which:  # a run that only hit the limit still shows, at $0 with its error
                    head = parents.get(key) or ent
                    s = sessions.setdefault((which, key), {
                        "title": head.get("title") or "", "label": head.get("label") or "",
                        "kind": "routine" if head.get("label") else "chat",
                        "folder": _folder(head.get("cwd")), "start": ts, "end": ts,
                        "models": {}, "agents": 0, **_blank()})
                    s["errors"] = s.get("errors", 0) + 1
                    s["start"] = min(s["start"], ts)
                    s["end"] = max(s["end"], ts)
                kind = "limit" if "limit" in text.lower() else "error"
                n = notes.setdefault((kind, text), {"kind": kind, "text": text, "n": 0, "first": ts, "last": ts})
                n["n"] += 1
                n["first"] = min(n["first"], ts)
                n["last"] = max(n["last"], ts)
        notices = sorted(notes.values(), key=lambda n: -n["last"])[:8]
        for n in notices:
            n["first"] = datetime.fromtimestamp(n["first"], HCM).isoformat(timespec="minutes")
            n["last"] = datetime.fromtimestamp(n["last"], HCM).isoformat(timespec="minutes")
        lists = {"today": [], "yesterday": []}
        for (which, _), s in sessions.items():
            s["models"] = [m for m, _ in sorted(s["models"].items(), key=lambda kv: -kv[1])]
            s["start"] = datetime.fromtimestamp(s["start"], HCM).isoformat(timespec="minutes")
            s["end"] = datetime.fromtimestamp(s["end"], HCM).isoformat(timespec="minutes")
            s["usd"] = round(s["usd"], 4)
            lists[which].append(s)
        for v in lists.values():
            v.sort(key=lambda s: -s["usd"])
        jobs = _claude_jobs()
        for j in jobs:
            for which in ("today", "yesterday"):
                mine = [s for s in lists[which] if s["label"] == j["id"]]
                j[which] = {"runs": len(mine), "usd": round(sum(s["usd"] for s in mine), 4),
                            "tok": sum(s["tok"] for s in mine),
                            "stopped": sum(1 for s in mine if s.get("errors") and not s["msgs"])}
        jobs.sort(key=lambda j: (j["enabled"] is False, -(j["today"]["usd"] + j["yesterday"]["usd"]), j["id"]))
        for k in cards:
            cards[k]["usd"] = round(cards[k]["usd"], 4)
            cards[k].update({"routine": round(split[k]["routine"], 4), "chat": round(split[k]["chat"], 4)})
        unknown = sorted({r[1] for e in files.values() for r in e["msgs"].values() if r[1] not in PRICE and r[1] != "<synthetic>"})
        live = {
            "generated": datetime.fromtimestamp(now, HCM).isoformat(timespec="seconds"),
            "took": round(time.time() - t0, 2),
            "today": today.isoformat(), "yesterday": yday.isoformat(),
            "cards": cards, "hours": hours, "sessions": lists,
            "jobs": {"claude": jobs, "launchd": _launchd_jobs(),
                     "server": [dict(zip(("name", "when", "desc", "note"), j)) for j in SERVER_JOBS]},
            "notices": notices,
            "unpriced": unknown,
        }
        _write("live.json", live)
        return live


def _read_live():
    try:
        with open(os.path.join(OUT, "live.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def view():
    live = _read_live()
    if not live or time.time() - os.stat(os.path.join(OUT, "live.json")).st_mtime > EVERY + 60:
        try:
            live = refresh()
        except Exception as e:  # 記録が読めなくても前回の結果を出す
            live = live or {"error": type(e).__name__}
    try:
        with open(os.path.join(LEDGER_DIR, "ledger.json"), encoding="utf-8") as f:
            ledger = json.load(f)
        if not isinstance(ledger, dict):
            raise ValueError
        ledger["missing"] = False
    except (OSError, ValueError):
        ledger = {"missing": True}
    try:
        with open(LIFELOG_USAGE, encoding="utf-8") as f:
            api = json.load(f)
    except (OSError, ValueError):
        api = None
    return {"live": live, "ledger": ledger, "lifelogApi": api}


def watch():
    """Desk のサーバーの中で5分ごとに数え直す。失敗しても次の回にまた試す。"""
    time.sleep(30)
    while True:
        try:
            refresh()
        except Exception:
            pass
        time.sleep(EVERY)


if __name__ == "__main__":
    v = refresh()
    print(json.dumps({"took": v["took"], "cards": v["cards"], "sessions": {k: len(x) for k, x in v["sessions"].items()},
                      "claudeJobs": len(v["jobs"]["claude"]), "launchd": len(v["jobs"]["launchd"]),
                      "unpriced": v["unpriced"]}, ensure_ascii=False, indent=1))
