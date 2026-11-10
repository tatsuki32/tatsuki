#!/usr/bin/env python3
"""Tatsuki's Desk — the bridge to the long-recording transcriber in desk/transcribe/.

The transcriber is its own server on 127.0.0.1:8791, so a Desk restart (pull.sh) never cuts a recording.
The Desk server starts it, keeps it running, and reads its sessions for the Transcribe view. Audio and text
stay in desk/data/transcribe/; backup_data.py never sends that folder anywhere.

  python3 transcribe_desk.py          what the page shows
  python3 transcribe_desk.py start    start the transcriber if it is not running
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "transcribe")
DIR = os.environ.get("TRANSCRIBE_DIR") or os.path.join(ROOT, "data", "transcribe")
PORT = int(os.environ.get("TRANSCRIBE_PORT", "8791"))
URL = f"http://localhost:{PORT}/"
ENGINE_FILES = ("whisper-cli", "models/ggml-large-v3-turbo.bin", "models/ggml-silero-v5.1.2.bin")
ACTIVE = ("queued", "converting", "transcribing")
STALE = 30  # seconds without audio before a recording counts as stopped (the same rule as transcribe/server.py)
_lock = threading.Lock()


def running():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/status", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def engine_ready():
    engine = os.environ.get("TRANSCRIBE_ENGINE") or os.path.join(APP, "engine")
    return all(os.path.exists(os.path.join(engine, f)) for f in ENGINE_FILES)


def start():
    """Starts the transcriber in its own session, so it outlives the Desk server. Returns True when it answers."""
    with _lock:
        if running():
            return True
        os.makedirs(DIR, exist_ok=True)
        env = {**os.environ, "PORT": str(PORT), "TRANSCRIBE_DIR": DIR}
        with open(os.path.join(DIR, "server.log"), "ab") as log:
            subprocess.Popen([sys.executable, os.path.join(APP, "server.py")], cwd=APP, env=env,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        for _ in range(40):
            time.sleep(0.25)
            if running():
                return True
        return False


def watch():
    """Run by the Desk server on 4747: start the transcriber, and start it again if it ever stops.
    That is also what resumes a transcription cut off by a restart of the Mac."""
    while True:
        try:
            if not running():
                start()
        except OSError:
            pass
        time.sleep(60)


def sessions(limit=20):
    out = []
    base = os.path.join(DIR, "sessions")
    try:
        names = os.listdir(base)
    except FileNotFoundError:
        names = []
    for sid in names:
        try:
            with open(os.path.join(base, sid, "meta.json"), encoding="utf-8") as f:
                m = json.load(f)
            size = os.path.getsize(os.path.join(base, sid, "audio.pcm"))
        except (OSError, ValueError):
            continue
        status = m.get("status")
        if status == "deleted":
            continue
        if status == "recording" and time.time() - m.get("last_chunk", 0) > STALE:
            status = "interrupted"
        out.append({"id": m["id"], "title": m.get("title", ""), "created": m.get("created", 0),
                    "duration": size / 32000, "status": status, "done": m.get("done_chunks", 0),
                    "total": m.get("total_chunks", 0), "failed": len(m.get("failed_chunks") or [])})
    out.sort(key=lambda x: x["created"], reverse=True)
    return out[:limit]


def view():
    return {"running": running(), "engine": engine_ready(), "url": URL, "sessions": sessions()}


if __name__ == "__main__":
    if sys.argv[1:] == ["start"]:
        print("running" if start() else "did not start")
    else:
        print(json.dumps(view(), ensure_ascii=False, indent=2))
