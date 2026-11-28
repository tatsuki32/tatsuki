"""Morning Log Bots -> the Desk's body records (lifeos/22-morning-bots).

The two Telegram bots run in Google Apps Script and write to a Google spreadsheet, so they work while the Mac is off.
Once a day (the first round after 5:00 on the Mac's clock), and when "Import now" is pressed, this takes the rows the
Desk hasn't taken yet (op=export), puts them into body/records.json the same way a typed record goes (lifelog.check),
and marks them taken (op=ack). A row undone in Telegram after it was taken comes back marked deleted, and its
record is set to discarded. It also tells the script the Mac's time zone (op=tz), every round where it changed and
once a day, so the 4:00 question follows the Mac's clock.

server.py runs watch() on 4747 only. The web app's address is in desk/data/lifebots/config.json; the key (the
script's DESK_KEY) is in the macOS keychain, service "tatsuki-desk-lifebots". Without both nothing happens.

  python3 desk/lifebots.py setup <web app URL>   keep the address, then paste the key (security puts it in the keychain)
  python3 desk/lifebots.py once                  import now and print what happened
  python3 desk/lifebots.py status
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lifelog  # noqa: E402

KEYCHAIN = "tatsuki-desk-lifebots"
EVERY = 600
IMPORT_FROM_HOUR = 5
SOURCE = "morning-bots"
UA = "tatsuki-desk-lifebots"
RUN = threading.Lock()


def folder(*parts):
    return os.path.join(lifelog.data_dir(), "lifebots", *parts)


def config():
    return lifelog.read_json(folder("config.json"), {})


def state():
    return lifelog.read_json(folder("state.json"), {})


def _save_state(**kw):
    s = {**state(), **kw}
    lifelog.write_json(folder("state.json"), s)
    return s


def key():
    if os.environ.get("LIFEBOTS_KEY"):  # tests only
        return os.environ["LIFEBOTS_KEY"]
    try:
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN, "-w"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def mac_tz():
    """The Mac's zone name from /etc/localtime (macOS sets it from where the Mac is when "Set time zone automatically" is on)."""
    if os.environ.get("LIFEBOTS_TZ"):
        return os.environ["LIFEBOTS_TZ"]
    try:
        real = os.path.realpath("/etc/localtime")
    except OSError:
        return ""
    return real.split("zoneinfo/", 1)[1] if "zoneinfo/" in real else ""


def _get(url, k, op, **params):
    q = urllib.parse.urlencode({"key": k, "op": op, **params})
    req = urllib.request.Request(url + ("&" if "?" in url else "?") + q, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:  # Apps Script answers through a redirect; urllib follows it for GET
        out = json.load(r)
    if not out.get("ok"):
        raise PermissionError("The script did not accept the key")
    return out


def _clock(v):
    v = str(v or "").strip()
    return v if len(v) in (4, 5) and ":" in v else None


# the sheet's meal columns -> the Desk's nutrients (every key of lifelog.NUTRIENTS; the sheet calls energy "kcal").
# An old row has these newer columns empty (""), which is skipped; a 0 the bot estimated is kept.
MEAL_COLS = tuple((k, "kcal" if k == "energy_kcal" else k) for k in lifelog.NUTRIENTS)


def targets_param():
    """body/targets.json -> {"protein_g": [130, "above"], ...} for the bots: the nutrients, steps and sleep only."""
    t = lifelog.read_json(lifelog.bp("targets.json"), {})
    keep = set(lifelog.NUTRIENTS) | {"steps", "total_min"}
    out = {k: [v["target"], v["dir"]] for k, v in sorted(t.items()) if k in keep and isinstance(v, dict)
           and isinstance(v.get("target"), (int, float)) and v.get("dir") in ("above", "below", "near")}
    return json.dumps(out, separators=(",", ":"))


def records_for(export):
    """Rows -> (row id, raw record for lifelog.check, date, note). Deleted rows give raw None."""
    out = []
    for r in export.get("sleep") or []:
        rid, d = str(r.get("id") or ""), str(r.get("date") or "")
        if not rid:
            continue
        if r.get("deleted"):
            out.append((rid, None, d, ""))
            continue
        if r.get("kind") == "sleep":
            values = [{"key": "total_min", "number": r.get("sleep_min"), "text": None}]
            for k in ("bedtime", "wake_time"):
                if _clock(r.get(k)):
                    values.append({"key": k, "number": None, "text": _clock(r.get(k))})
            raw = {"kind": "sleep", "date": d, "label": "", "values": values, "items": [], "sets": [], "uncertain": []}
        elif r.get("kind") == "steps":
            raw = {"kind": "steps", "date": d, "label": "", "values": [{"key": "steps", "number": r.get("steps"), "text": None}], "items": [], "sets": [], "uncertain": []}
        else:
            continue
        out.append((rid, raw, d, str(r.get("text") or "")[:300]))
    for r in export.get("meals") or []:
        rid, d = str(r.get("id") or ""), str(r.get("date") or "")
        if not rid:
            continue
        if r.get("deleted"):
            out.append((rid, None, d, ""))
            continue
        items = [i.strip() for i in str(r.get("items") or "").split(";") if i.strip()]
        values = [{"key": k, "number": r.get(src), "text": None} for k, src in MEAL_COLS
                  if isinstance(r.get(src), (int, float)) and not isinstance(r.get(src), bool)]
        meal = str(r.get("meal") or "").capitalize()
        raw = {"kind": "meal_photo", "date": d, "label": f"{meal}: {', '.join(items)}"[:120] if meal else ", ".join(items)[:120],
               "values": values, "items": items, "sets": [], "uncertain": []}
        out.append((rid, raw, d, str(r.get("text") or "")[:300]))
    return out


def apply(export):
    """Writes the rows into body/records.json. Returns (row ids handled, records added, records discarded)."""
    handled, added, gone, ticks = [], 0, 0, []
    with lifelog.locked():
        body = lifelog.read_json(lifelog.bp("records.json"), {})
        for rid, raw, d, note in records_for(export):
            rec_id = "mb-" + rid
            if raw is None:
                if rec_id in body and body[rec_id].get("status") != "discarded":
                    body[rec_id]["status"] = "discarded"
                    body[rec_id]["at"] = int(time.time())
                    gone += 1
                handled.append(rid)
                continue
            rec = lifelog.check(raw, d, typed=True)
            if rec is None:
                handled.append(rid)  # nothing usable in it; taking it again would not change that
                continue
            if rec_id not in body:
                body[rec_id] = {**rec, "id": rec_id, "photo": None, "source": SOURCE, "note": note, "at": int(time.time())}
                added += 1
                ticks.append(body[rec_id])
            handled.append(rid)
        lifelog.write_json(lifelog.bp("records.json"), body)
    lifelog.mark_log_habits(ticks)
    return handled, added, gone


def run(force=False):
    """One round. force: import whatever the day. Returns the state."""
    url, k = config().get("url", ""), key()
    if not url or not k:
        return _save_state(error="", setup=False)
    with RUN:
        s, today = state(), datetime.now().date().isoformat()
        try:
            tzname, tg = mac_tz(), targets_param()
            if tzname and (tzname != s.get("tzSent") or s.get("tzDay") != today or tg != s.get("targetsSent")):
                got = _get(url, k, "tz", tz=tzname, targets=tg)  # the targets ride along, so replies compare with the Desk's
                s = _save_state(tzSent=tzname, tzDay=today, targetsSent=tg, botTz=got.get("tz"), botTzFrom=got.get("from"))
            if not force and (s.get("lastDay") == today or datetime.now().hour < IMPORT_FROM_HOUR):
                return _save_state(error="", setup=True)
            export = _get(url, k, "export")
            handled, added, gone = apply(export)
            if handled:
                _get(url, k, "ack", ids=",".join(handled))
            return _save_state(lastDay=today, lastImport=int(time.time()), lastAdded=added, lastGone=gone,
                               total=s.get("total", 0) + added, error="", setup=True, botTz=export.get("tz") or s.get("botTz"))
        except PermissionError as e:
            return _save_state(error=str(e), setup=True)
        except (OSError, ValueError) as e:
            return _save_state(error=f"Couldn't reach the spreadsheet ({type(e).__name__}). It tries again in 10 minutes", setup=True)


def view():
    s = state()
    return {"setup": bool(config().get("url")) and bool(key()), "lastImport": s.get("lastImport"), "lastAdded": s.get("lastAdded", 0),
            "total": s.get("total", 0), "error": s.get("error", ""), "tz": s.get("botTz") or "", "tzFrom": s.get("botTzFrom") or ""}


def watch():
    while True:
        try:
            run()
        except Exception as e:  # never stop the server's thread
            print("lifebots:", e, file=sys.stderr)
        time.sleep(EVERY)


def setup(url):
    if not url.startswith("https://script.google.com/"):
        sys.exit("The address should start with https://script.google.com/ (Deploy → Web app → URL)")
    print("Paste the DESK_KEY from the script's log when asked (twice). It goes straight into the keychain.")
    subprocess.run(["/usr/bin/security", "add-generic-password", "-U", "-s", KEYCHAIN, "-a", "api", "-w"], check=True)  # -w last: security asks for it, so it is never in the command line
    os.makedirs(folder(), mode=0o700, exist_ok=True)
    lifelog.write_json(folder("config.json"), {"url": url})
    print(json.dumps(run(force=True), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "setup" and len(sys.argv) > 2:
        setup(sys.argv[2])
    elif cmd == "once":
        print(json.dumps(run(force=True), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(view(), ensure_ascii=False, indent=1))
