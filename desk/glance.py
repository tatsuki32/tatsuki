#!/usr/bin/env python3
"""Tatsuki's Desk — Glance: every 15 minutes or so, one photo from the Mac's camera and one picture of the screen,
and one short remark about what they show (what I am doing, drifting from today's tasks, how I am sitting).

server.py runs watch() every 60 seconds, on 4747 only. Off until it is turned on in the Desk (Today > Glance).

- When: 6:00-22:00 by this Mac's own clock (after that, desk/bedtime.py speaks instead), only while the Mac is in use
  (a key or the mouse in the last 10 minutes, or a video playing; screen not locked), not while paused.
- Taking the pictures: ~/Applications/TatsukiGlance.app (desk/app/glance.swift), started with `open` so Camera and
  Screen Recording belong to that app alone. It takes nothing while one of the "never capture" apps is in front, while
  a private browser window is on screen, or while another app uses a camera or a microphone (a call or a recording),
  and leaves the never-capture apps' windows out of the picture. Built once, on Set up.
- Reading them: one request to the Claude API with the two small pictures, the titles of today's tasks and the last
  few remarks. The key is the keychain item the photo import uses (lifelog.py). Nothing else leaves this Mac.
- The remark is the model's text about a screen that anyone's web page or message can put words on, so it is treated
  as untrusted: cleaned to one plain line (clean_text) and handed to the notification as a value, never as code.
- The remark goes out as a silent Mac notification and into log.json (kept 90 days). During a FocusBlock session only
  a drift remark is shown; the rest is only logged.
- The pictures stay in desk/data/glance/shots/ for 24 hours, then they are deleted (also on the first run after the
  server was down). A request that failed keeps no picture. All of desk/data/glance/ is outside the GitHub copy and
  the private backup, and only this Mac's user can open it.
- A monthly limit in dollars stops it for the rest of the month, "Take one now" included. The month's spend is counted
  in state.json apart from the log, so deleting a remark does not lower it.

  python3 desk/glance.py plan     whether it would take one now, and why not (takes nothing)
  python3 desk/glance.py sweep    delete pictures older than 24 hours now
"""
import base64
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import bedtime

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DESK_DATA") or os.environ.get("DESK_DATA_DIR") or os.path.join(ROOT, "data")
DIR = os.environ.get("GLANCE_DIR") or os.path.join(DATA, "glance")
APP = os.environ.get("GLANCE_APP") or os.path.expanduser("~/Applications/TatsukiGlance.app")
SRC = os.path.join(ROOT, "app", "glance.swift")
PLIST = os.path.join(ROOT, "app", "glance-info.plist")
API = "https://api.anthropic.com/v1/messages"
API_VERSION = "2026-10-21"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0)}  # USD per million tokens, read 2026-11-05
MODELS = tuple(PRICES)
APP_VERSION = 2  # `version` in desk/app/glance.swift: an app that answers with less is missing rules and is not used
MIN = 60
DAY = 86400
KEEP_SHOTS = DAY
KEEP_LOG = 90 * DAY
IN_USE = 10 * MIN  # a key or the mouse this recently; a playing video stretches it (bedtime.in_use)
SLACK = 45  # the watch wakes about once a minute: due this many seconds early rather than a minute late
EVERY = (10, 15, 20)
RECENT = 3  # earlier remarks the model sees, so it does not say the same thing again
TEXT_MAX = 160
SHOT = re.compile(r"^\d{9,11}-(face|screen)\.jpg$")
BUNDLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,119}$")
# LINE, password managers, Keychain Access, System Settings. A prefix covers an app's versions and helpers.
SKIP = ["jp.naver.line.mac", "com.1password", "com.agilebits.onepassword", "com.apple.Passwords", "com.apple.keychainaccess",
        "com.bitwarden.desktop", "com.apple.systempreferences"]
DEFAULTS = {"on": False, "camera": True, "mic": True, "every": 15, "from": 6 * 60, "to": 22 * 60, "skip": SKIP, "model": "claude-haiku-5-5",
            "monthlyLimitUsd": 3.0, "lang": "ja", "notify": "all", "pauseUntil": 0}
WHY = {"locked": "Screen locked", "app": "A never-capture app was in front", "private": "A private browser window was open",
       "call": "A camera or microphone was in use"}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["kind", "text", "activity"],
          "properties": {"kind": {"type": "string", "enum": ["doing", "drift", "body"]}, "text": {"type": "string"}, "activity": {"type": "string"}}}
LANG = {"ja": ("Write the remark in natural, friendly Japanese (です・ます).", "80 characters at most"),
        "en": ("Write the remark in plain American English.", "25 words at most")}
GUIDE = """You are Glance, a small part of one person's private dashboard on their own Mac. They asked for it themselves: every 15 minutes or so the Mac takes one photo with its own camera and one picture of the screen, and you tell them in one short remark what you see. While they work they lose track of what they are doing and how they are sitting, and a remark from outside helps them notice. They read it as a Mac notification, so it has to fit in one or two short lines.

What to look for, most important first:
1. Drift. You get the titles of the tasks they planned for today. If the screen clearly shows something that has nothing to do with any of them and looks like a distraction (videos, social media, shopping, news), say so as a plain fact and name the task they could return to. When you are not sure, it is not drift: looking something up, a short reply to a message, or a break after long work is normal. With no tasks given, do not call anything drift.
2. Their body, from the camera photo: a face very close to the screen, a slumped or twisted posture, the head resting on a hand, visible tiredness such as yawning or rubbing the eyes, or nobody in the seat. Mention it only when the photo shows it clearly. If the photo is dark, blurred or missing, or the face is out of frame, leave the body out and do not guess. Describe what is visible, not how they feel and not their health.
3. What they are doing right now, from the screenshot, in plain words at the level of "writing an email" or "arranging ad images".

Choose what is worth saying this time. Usually that is what they are doing, plus drift or the body when there is something to notice. If nothing has changed since your last remark, say so briefly instead of repeating it.

How to say it:
- {language}
- One or two short sentences, {limit}. No greeting, no emoji, no exclamation mark.
- Calm and on their side. Never blame or judge, and never say what they should have done.
- Keep what is on the screen private, because the remark is stored and shown as a notification: no names of other people, no words from messages or documents, no email addresses, no amounts of money, account numbers or passwords. Say "replying to a chat", not to whom or about what. If other people are in the photo, say nothing about them.

Answer with:
- kind: "drift" when you point out drift, "body" when the remark is mainly about their body, otherwise "doing".
- text: the remark.
- activity: two or three English words for what is on the screen ("Email", "Ad design", "YouTube"), used for a daily summary. The same privacy rule applies."""
LOCK = threading.Lock()  # the files
RUN = threading.Lock()  # one picture at a time


class Failed(Exception):
    """This Glance gives no remark. The text is shown on the Glance screen. `usage` is set when Anthropic answered
    and the request was billed anyway."""

    def __init__(self, why, usage=None):
        super().__init__(why)
        self.usage = usage


def clean_text(text, limit):
    """The model's words, made safe to show and to store: one line, no control characters, never starting with "-"
    (a value that starts with "-" could be read as an option by a command it is handed to)."""
    text = "".join(c if c.isprintable() else " " for c in str(text or ""))
    return " ".join(text.split()).lstrip("-–— ")[:limit]


# ---------- files ----------

def path(*parts):
    return os.path.join(DIR, *parts)


def private_dir(p):
    """A folder only this Mac's user can open: the remarks and the pictures say what was on the screen."""
    os.makedirs(p, mode=0o700, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass


def read_json(name, default):
    try:
        with open(path(name), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return default


def write_json(name, value):
    private_dir(DIR)
    fd, tmp = tempfile.mkstemp(dir=DIR, prefix=".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path(name))


def clean_settings(raw, base=None):
    """Only known keys, only sane values. Anything else keeps what was there."""
    out = json.loads(json.dumps(base or DEFAULTS))
    raw = raw if isinstance(raw, dict) else {}
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    for k in ("on", "camera", "mic"):
        if isinstance(raw.get(k), bool):
            out[k] = raw[k]
    if num(raw.get("every")) and raw["every"] in EVERY:
        out["every"] = int(raw["every"])
    for k in ("from", "to"):
        if isinstance(raw.get(k), int) and not isinstance(raw.get(k), bool) and 0 <= raw[k] <= 24 * 60:
            out[k] = raw[k]
    if isinstance(raw.get("skip"), list):
        out["skip"] = sorted({b for b in raw["skip"] if isinstance(b, str) and BUNDLE.match(b)})[:80]
    out["skip"] = sorted(set(out["skip"]) | set(SKIP))  # the built-in ones cannot be taken off: the app refuses them anyway
    if raw.get("model") in MODELS:
        out["model"] = raw["model"]
    if num(raw.get("monthlyLimitUsd")) and 0 <= raw["monthlyLimitUsd"] <= 100:
        out["monthlyLimitUsd"] = round(float(raw["monthlyLimitUsd"]), 2)
    if raw.get("lang") in LANG:
        out["lang"] = raw["lang"]
    if raw.get("notify") in ("all", "some"):
        out["notify"] = raw["notify"]
    if num(raw.get("pauseUntil")):
        out["pauseUntil"] = int(raw["pauseUntil"])
    return out


def settings():
    return clean_settings(read_json("settings.json", {}))


def read_log(now):
    return [e for e in read_json("log.json", []) if isinstance(e, dict) and now - e.get("at", 0) < KEEP_LOG]


def add_log(entry, now):
    """Appends, or moves the last entry forward when the same thing went wrong again (no wall of the same line)."""
    with LOCK:
        log = read_log(now)
        last = log[-1] if log else {}
        if entry["status"] != "ok" and (last.get("status"), last.get("why")) == (entry["status"], entry.get("why")):
            log[-1] = {**entry, "times": last.get("times", 1) + 1}
        else:
            log.append(entry)
        write_json("log.json", log)
    return entry


def shot_path(name):
    """The file for a name the page asked for, or None. Only this folder, only names this program gives."""
    if not isinstance(name, str) or not SHOT.match(name):
        return None
    p = path("shots", name)
    return p if os.path.isfile(p) else None


def sweep(now=None):
    """Deletes pictures older than 24 hours, and work folders a stopped run left behind. Returns how many it deleted."""
    now = now or time.time()
    gone = 0
    try:
        names = os.listdir(path("shots"))
    except OSError:
        names = []
    for n in names:
        m = SHOT.match(n)
        p = path("shots", n)
        try:
            taken = int(n.split("-", 1)[0]) if m else os.path.getmtime(p)
            if now - taken >= KEEP_SHOTS:
                os.remove(p)
                gone += 1
        except (OSError, ValueError):
            pass
    try:
        for n in os.listdir(DIR):
            p = path(n)
            if n.startswith(".shot-") and now - os.path.getmtime(p) > 10 * MIN:
                shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass
    return gone


def month_usage(now):
    """What this month has cost so far: {"usd", "count"}. Kept in state.json apart from the log, so deleting a remark
    does not lower it, and a billed answer that gave no remark still counts."""
    m = (read_json("state.json", {}).get("spend") or {}).get(datetime.fromtimestamp(now).strftime("%Y-%m")) or {}
    num = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else 0
    return {"usd": round(num(m.get("usd")), 6), "count": int(num(m.get("count")))}


def add_spend(now, usd):
    month = datetime.fromtimestamp(now).strftime("%Y-%m")
    with LOCK:
        st = read_json("state.json", {})
        spend = st.get("spend") if isinstance(st.get("spend"), dict) else {}
        cur = spend.get(month) if isinstance(spend.get(month), dict) else {}
        spend[month] = {"usd": round((cur.get("usd") or 0) + usd, 6), "count": (cur.get("count") or 0) + 1}
        st["spend"] = {k: spend[k] for k in sorted(spend)[-12:]}
        write_json("state.json", st)


# ---------- the app that takes the pictures ----------

def binary():
    return os.path.join(APP, "Contents", "MacOS", "TatsukiGlance")


def app_version():
    """The `version` the built app answers with, 0 when it gives none. Asked once per build (the answer is kept with the
    binary's modification time), so an app built from an older glance.swift is never asked to take a picture."""
    try:
        mtime = int(os.path.getmtime(binary()))
    except OSError:
        return 0
    known = read_json("state.json", {}).get("app") or {}
    if known.get("mtime") == mtime and isinstance(known.get("v"), int):
        return known["v"]
    res, work = run_app("status", timeout=30)
    shutil.rmtree(work, ignore_errors=True)
    if res.get("error"):
        return 0  # no answer this time: ask again next time, use nothing now
    v = res.get("v") if isinstance(res.get("v"), int) and not isinstance(res.get("v"), bool) else 0
    remember_permissions(res)
    with LOCK:
        st = read_json("state.json", {})
        st["app"] = {"mtime": mtime, "v": v}
        write_json("state.json", st)
    return v


def stale():
    """True when an app is there but is older than this file needs. Looks only at what is already known."""
    try:
        mtime = int(os.path.getmtime(binary()))
    except OSError:
        return False
    known = read_json("state.json", {}).get("app") or {}
    return known.get("mtime") == mtime and isinstance(known.get("v"), int) and known["v"] < APP_VERSION


def build(replace=False):
    """Builds TatsukiGlance.app. Returns "" or what went wrong. An app that is there is left alone (a rebuild loses its
    permissions) unless `replace`: an out-of-date app is replaced, and macOS asks for the permissions again."""
    if os.path.exists(binary()) and not replace:
        return ""
    swiftc = shutil.which("swiftc") or "/usr/bin/swiftc"
    work = tempfile.mkdtemp(prefix="glanceapp.")
    try:
        app = os.path.join(work, "TatsukiGlance.app")
        os.makedirs(os.path.join(app, "Contents", "MacOS"))
        r = subprocess.run([swiftc, "-O", "-parse-as-library", "-framework", "AppKit", "-framework", "AVFoundation", "-framework", "ScreenCaptureKit",
                            "-framework", "CoreMedia", "-framework", "CoreAudio", SRC, "-o", os.path.join(app, "Contents", "MacOS", "TatsukiGlance")],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            return "Could not build the capture app. Run xcode-select --install in Terminal, then try again."
        shutil.copy(PLIST, os.path.join(app, "Contents", "Info.plist"))
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", app], capture_output=True, timeout=120)
        os.makedirs(os.path.dirname(APP), exist_ok=True)
        if os.path.isdir(APP) and os.path.basename(APP) == "TatsukiGlance.app":  # only ever this one bundle
            shutil.rmtree(APP)
        shutil.move(app, APP)
    except (OSError, subprocess.SubprocessError) as e:
        return f"Could not build the capture app ({type(e).__name__})."
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return ""


def run_app(cmd, extra=(), timeout=45):
    """Starts the app through `open` and waits for it. Returns (its result.json, the folder it wrote into).
    The caller removes the folder."""
    private_dir(DIR)
    work = tempfile.mkdtemp(dir=DIR, prefix=".shot-")
    try:
        subprocess.run(["/usr/bin/open", "-W", "-g", "-n", "-a", APP, "--args", cmd, work, *extra], capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}, work
    except OSError:
        return {"error": "could not start"}, work
    try:
        with open(os.path.join(work, "result.json"), encoding="utf-8") as f:
            res = json.load(f)
    except (OSError, ValueError):
        res = None
    return (res if isinstance(res, dict) else {"error": "no answer"}), work


def remember_permissions(res, now=None):
    """Keeps what the app last said about Camera and Screen Recording, for the Glance screen. `status` and `setup` answer
    with the two values; `shoot` puts them under "permissions" (not when it skipped before looking)."""
    perm = res.get("permissions") if isinstance(res.get("permissions"), dict) else res
    if not isinstance(perm.get("screen"), bool) or perm.get("camera") not in ("allowed", "denied", "ask"):
        return
    with LOCK:
        st = read_json("state.json", {})
        st["permissions"] = {"camera": perm["camera"], "screen": perm["screen"], "at": int(now or time.time())}
        write_json("state.json", st)


def check(cmd="status"):
    """`status` only looks; `setup` builds the app if needed and lets macOS ask for Camera and Screen Recording."""
    if cmd == "setup":
        app_version()  # learn what is there; an app that merely did not answer is not replaced
        problem = build(replace=stale())
        if problem:
            return 503, {"error": problem}
    if not os.path.exists(binary()):
        return 200, view()
    if app_version() < APP_VERSION:
        return 200, view()  # out of date: nothing is asked of it; the page offers Set up again
    res, work = run_app(cmd, timeout=130 if cmd == "setup" else 30)
    shutil.rmtree(work, ignore_errors=True)
    remember_permissions(res)
    return 200, view()


def shoot(s):
    """Asks the app for the pictures. An out-of-date app is not asked; and should one answer anyway, its pictures are
    deleted here, unread and unsent."""
    if app_version() < APP_VERSION:
        return {"error": "old app"}, None
    extra = ["--skip", ",".join(s["skip"])] if s["skip"] else []
    res, work = run_app("shoot", extra + ([] if s["camera"] else ["--no-face"]) + ([] if s["mic"] else ["--allow-mic"]))
    v = res.get("v")
    if not isinstance(v, int) or isinstance(v, bool) or v < APP_VERSION:
        shutil.rmtree(work, ignore_errors=True)
        return {"error": "old app"}, None
    return res, work


# ---------- reading the pictures ----------

def api_key():
    import lifelog  # the same keychain item as the photo import; tests never touch the keychain
    return lifelog.api_key()


def today_tasks():
    """Titles only: what is in progress, then the order the Desk would start today's tasks in."""
    try:
        import kickoff
        tasks = kickoff.candidates()["tasks"]
    except Exception:  # the list is a help, not a need
        return []
    doing = [t for t in tasks if t.get("status") == "doing"]
    rest = [t for t in tasks if t.get("status") != "doing"]
    out = []
    for t in doing + rest:
        title = " ".join(str(t.get("title") or "").split())[:80]
        if title and title not in out:
            out.append(title)
    return out[:8]


def context_text(now, shot, tasks, recent, has_face):
    dt = datetime.fromtimestamp(now)
    lines = [f"Local time: {dt.strftime('%A %-I:%M %p')}."]
    front = (shot.get("front") or {}).get("name")
    if front:
        lines.append(f"App in front: {front}.")
    if shot.get("hidden"):
        lines.append("Left out of the screenshot on purpose (private apps): " + ", ".join(str(n) for n in shot["hidden"][:6]) + ".")
    if not has_face:
        lines.append("There is no camera photo this time, so say nothing about their body.")
    lines.append("Today's tasks:\n" + "\n".join(f"- {t}" for t in tasks) if tasks else "No tasks are planned for today.")
    if recent:
        lines.append("Your last remarks, oldest first:\n" + "\n".join(
            f"- {datetime.fromtimestamp(e['at']).strftime('%-I:%M %p')} [{e.get('kind')}] {e.get('text')}" for e in recent))
    return "\n\n".join(lines)


def image_block(p):
    with open(p, "rb") as f:
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(f.read()).decode()}}


def ask_claude(face, screen, text, s, key):
    """One request. Returns ({kind, text, activity}, usage). Raises Failed."""
    language, limit = LANG[s["lang"]]
    content = []
    if face:
        content += [{"type": "text", "text": "Photo from the Mac's camera:"}, image_block(face)]
    content += [{"type": "text", "text": "Picture of the screen:"}, image_block(screen), {"type": "text", "text": text}]
    model = s["model"]
    body = {"model": model, "max_tokens": 2000, "system": GUIDE.format(language=language, limit=limit),
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            "messages": [{"role": "user", "content": content}]}
    headers = {"x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json", "user-agent": "tatsuki-desk"}
    if model.startswith(("claude-opus", "claude-sonnet")):  # Haiku has no server-side fallback
        body["fallbacks"] = "default"
        headers["anthropic-beta"] = FALLBACK_BETA
    req = urllib.request.Request(API, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise Failed("Anthropic did not accept the API key") from None
        if e.code in (429, 500, 502, 503, 504, 529):
            raise Failed(f"Anthropic was busy ({e.code})") from None
        raise Failed(f"Anthropic returned an error ({e.code})") from None
    except (http.client.HTTPException, OSError, ValueError):
        raise Failed("Could not reach Anthropic") from None
    u = res.get("usage") if isinstance(res.get("usage"), dict) else {}
    usage = {"model": res.get("model") or model, "input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0)}
    if res.get("stop_reason") == "refusal":
        raise Failed("Reading these pictures was declined", usage)
    out = next((b.get("text") for b in res.get("content", []) if b.get("type") == "text"), None)
    try:
        rec = json.loads(out)
        kind, remark = rec["kind"], clean_text(rec["text"], TEXT_MAX)
    except (TypeError, ValueError, KeyError):
        raise Failed("Could not make sense of the answer", usage) from None
    if kind not in ("doing", "drift", "body") or not remark:
        raise Failed("Could not make sense of the answer", usage)
    return {"kind": kind, "text": remark, "activity": clean_text(rec.get("activity"), 40)}, usage


def cost(usage):
    """Dollars for one answer. A model this file has no price for is counted at the highest price, never the lowest."""
    name = str(usage.get("model") or "")
    price = PRICES.get(name) or next((v for k, v in PRICES.items() if name.startswith(k)), max(PRICES.values()))
    tokens = lambda k: usage.get(k) if isinstance(usage.get(k), (int, float)) and not isinstance(usage.get(k), bool) and usage.get(k) > 0 else 0
    return round((tokens("input") * price[0] + tokens("output") * price[1]) / 1e6, 6)


# ---------- deciding ----------

def in_hours(minute, s):
    a, b = s["from"], s["to"]
    return a <= minute < b if a <= b else (minute >= a or minute < b)


def why_not(now, s, st, using, spent):
    """"" when a picture is due now, otherwise the reason. Pure."""
    dt = datetime.fromtimestamp(now)
    if not s["on"]:
        return "off"
    if now < s["pauseUntil"]:
        return "paused"
    if not in_hours(dt.hour * 60 + dt.minute, s):
        return "outside hours"
    if now - st.get("last", 0) < s["every"] * MIN - SLACK:
        return "not yet"
    if spent >= s["monthlyLimitUsd"]:
        return "monthly limit"
    if not using:
        return "nobody at the Mac"
    return ""


def show_mac(text):
    return bedtime.show_mac({"title": "Glance", "body": text, "tier": "quiet"}, True)  # "quiet": no sound


def focus_on():
    import notify
    return notify.focus_on()


def take(now=None, manual=False, shoot=shoot, ask=ask_claude, show=show_mac, key=api_key, focus=focus_on, tasks=today_tasks):
    """One Glance, start to finish. Returns the log entry, or None when another one is already running."""
    now = int(now or time.time())
    if not RUN.acquire(blocking=False):
        return None
    work = None
    try:
        s = settings()
        with LOCK:
            st = read_json("state.json", {})
            st["last"] = now  # before the pictures: a run that dies must not start again a minute later
            write_json("state.json", st)
        failed = lambda why: add_log({"at": now, "status": "failed", "why": why}, now)
        if month_usage(now)["usd"] >= s["monthlyLimitUsd"]:  # "Take one now" is held to the limit too
            return failed("This month's limit is reached")
        k = key()
        if not k:
            return failed("No Anthropic API key in the keychain")  # no picture is taken that nobody could read
        shot, work = shoot(s)
        remember_permissions(shot, now)
        if shot.get("skipped"):
            why = WHY.get(shot["skipped"], "Not taken")
            by = [clean_text(n, 40) for n in shot.get("by") or [] if isinstance(n, str)][:3]
            if shot["skipped"] == "call" and by:
                why += ": " + ", ".join(by)  # which program, so a wrong guess can be seen and the rule switched off
            return add_log({"at": now, "status": "skipped", "why": why, "front": clean_text((shot.get("front") or {}).get("name"), 60)}, now)
        screen = os.path.join(work, "screen.jpg") if work and shot.get("ok") else ""
        if not screen or not os.path.isfile(screen):
            err = clean_text(shot.get("error") or "no picture", 40)
            return failed({"screen permission": "Screen Recording is not allowed for TatsukiGlance", "timeout": "The capture app did not answer",
                           "could not start": "The capture app is missing. Press Set up",
                           "old app": "The capture app is out of date. Press Set up again"}.get(err, f"Could not take the picture ({err})"))
        face = os.path.join(work, "face.jpg") if shot.get("face") and os.path.isfile(os.path.join(work, "face.jpg")) else None
        recent = [e for e in read_log(now) if e.get("status") == "ok" and now - e.get("at", 0) < 2 * 3600][-RECENT:]
        try:
            remark, usage = ask(face, screen, context_text(now, shot, tasks(), recent, bool(face)), s, k)
        except Failed as e:
            if e.usage:
                add_spend(now, cost(e.usage))  # answered and billed, though it gave no remark
            return failed(str(e))  # the pictures go with the work folder, below
        usd = cost(usage)
        add_spend(now, usd)
        private_dir(path("shots"))
        entry = {"at": now, "status": "ok", **remark, "front": clean_text((shot.get("front") or {}).get("name"), 60), "screen": f"{now}-screen.jpg",
                 "model": clean_text(usage["model"], 60), "in": usage["input"], "out": usage["output"], "usd": usd}
        os.replace(screen, path("shots", entry["screen"]))
        if face:
            entry["face"] = f"{now}-face.jpg"
            os.replace(face, path("shots", entry["face"]))
        elif shot.get("faceWhy"):
            entry["faceWhy"] = shot["faceWhy"]
        quiet = (s["notify"] == "some" and remark["kind"] == "doing") or (not manual and remark["kind"] != "drift" and focus())
        entry["shown"] = not quiet
        add_log(entry, now)
        if not quiet:
            show(remark["text"])
        return entry
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)
        RUN.release()


def tick(now=None, using=None, **fakes):
    now = now or time.time()
    try:
        time.tzset()  # the Mac may have moved to another time zone since the server started
    except AttributeError:
        pass
    sweep(now)
    s, st = settings(), read_json("state.json", {})
    if why_not(now, s, st, True, 0):
        return None  # off, paused, night or not due: do not even ask macOS about the keyboard
    if using is None:
        using = bedtime.in_use(bedtime.idle_seconds(), bedtime.screen_locked(), bedtime.display_kept_awake(), IN_USE)
    if why_not(now, s, st, using, month_usage(now)["usd"]):
        return None
    return take(now, **fakes)


def watch():
    while True:
        try:
            tick()
        except Exception as e:  # one bad file must not end the watch
            print("glance:", type(e).__name__, e, file=sys.stderr, flush=True)
        time.sleep(60)


# ---------- for the page ----------

def running_apps():
    try:
        import pc_monitor
        apps = pc_monitor.registered_apps()
    except Exception:
        return []
    seen = {}
    for a in apps:
        if a.get("type") == "Foreground" and a.get("bundle") and BUNDLE.match(a["bundle"]):
            name = (a.get("name") or "").replace(chr(0x200E), "").strip()  # some app names start with an invisible left-to-right mark
            seen.setdefault(a["bundle"], name or a["bundle"])
    return [{"bundle": b, "name": n} for b, n in sorted(seen.items(), key=lambda x: x[1].lower())]


def view(now=None):
    now = now or time.time()
    s, st = settings(), read_json("state.json", {})
    every = read_log(now)
    spent = month_usage(now)
    log = sorted(every, key=lambda e: -e.get("at", 0))[:300]
    for e in log:  # a picture is offered only while its file is still there
        for k in ("face", "screen"):
            if e.get(k) and not shot_path(e[k]):
                e.pop(k)
    problem = ""
    if s["on"] and spent["usd"] >= s["monthlyLimitUsd"]:
        problem = f"This month's limit (${s['monthlyLimitUsd']:.2f}) is reached. Glance rests until next month, or raise the limit below."
    return {"settings": s, "bedtime": bedtime.view(now), "log": log, "now": int(now), "paused": now < s["pauseUntil"],
            "next": int(max(now, st.get("last", 0) + s["every"] * MIN)) if s["on"] and now >= s["pauseUntil"] else 0,
            "built": os.path.exists(binary()), "stale": stale(), "permissions": st.get("permissions") or {}, "month": spent, "problem": problem,
            "apps": running_apps(), "locked": SKIP, "models": [{"id": m, "in": PRICES[m][0], "out": PRICES[m][1]} for m in MODELS],
            "every": list(EVERY), "clock": datetime.fromtimestamp(now).strftime("%-I:%M %p")}


def put_settings(rec):
    with LOCK:
        s = clean_settings(rec, settings())
        write_json("settings.json", s)
    return 200, view()


def pause(rec, now=None):
    """{"minutes": 60}, {"tomorrow": true} until the hours begin again tomorrow, or {"minutes": 0} to resume."""
    now = now or time.time()
    rec = rec if isinstance(rec, dict) else {}
    if rec.get("tomorrow") is True:
        s = settings()
        start = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1, minutes=s["from"])
        until = int(start.timestamp())
    elif isinstance(rec.get("minutes"), int) and not isinstance(rec.get("minutes"), bool) and 0 <= rec["minutes"] <= 24 * 60:
        until = int(now + rec["minutes"] * 60) if rec["minutes"] else 0
    else:
        return 400, {"error": "minutes or tomorrow"}
    return put_settings({"pauseUntil": until})


def take_now():
    """The "Take one now" button. Works while Glance is off too, so it can be tried before it is turned on."""
    if not os.path.exists(binary()):
        return 409, {"error": "Press Set up first"}
    entry = take(manual=True)
    if entry is None:
        return 409, {"error": "One is being taken right now"}
    return 200, {**view(), "took": entry}


def forget(rec, now=None):
    """{"at": 1700000000} deletes that entry and its pictures; {"pictures": true} deletes every picture now."""
    now = now or time.time()
    rec = rec if isinstance(rec, dict) else {}
    if rec.get("pictures") is True:
        shutil.rmtree(path("shots"), ignore_errors=True)
        return 200, view()
    at = rec.get("at")
    if not isinstance(at, int) or isinstance(at, bool):
        return 400, {"error": "at or pictures"}
    with LOCK:
        log = read_log(now)
        for e in log:
            if e.get("at") == at:
                for k in ("face", "screen"):
                    p = shot_path(e.get(k))
                    if p:
                        os.remove(p)
        write_json("log.json", [e for e in log if e.get("at") != at])
    return 200, view()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "plan":
        t = time.time()
        idle, locked, watching = bedtime.idle_seconds(), bedtime.screen_locked(), bedtime.display_kept_awake()
        s, st = settings(), read_json("state.json", {})
        why = why_not(t, s, st, bedtime.in_use(idle, locked, watching, IN_USE), month_usage(t)["usd"])
        print(json.dumps({"take": not why, "why": why, "idle": idle, "locked": locked, "watching": watching, "built": os.path.exists(binary())}, indent=1))
    elif cmd == "sweep":
        print(sweep(), "pictures deleted")
    else:
        print(__doc__)
