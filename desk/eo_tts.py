"""Read-aloud for languages the Mac has no voice for (Esperanto, Serbian), with OmniVoice running on this Mac.

OmniVoice (k2-fsa) is installed by `bash desk/omnivoice/setup.sh` into desk/omnivoice/engine/ (Python 3.11 + torch, ~2.7 GB in all).
This module (Python 3.9, standard library) starts its small speech server (speak_server.py, 127.0.0.1:8793) when a sentence is
needed; the server quits by itself after 30 idle minutes. Nothing leaves the Mac. Each sentence is kept as m4a in
desk/data/reader/tts/omni/ so it is made once.

  python3 desk/eo_tts.py status
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.join(ROOT, "omnivoice")
ENGINE = os.path.join(HOME, "engine")
PY = os.path.join(ENGINE, "venv", "bin", "python")
MODEL = os.path.join(ENGINE, "hf", "hub", "models--k2-fsa--OmniVoice")
CACHE = os.path.join(os.environ.get("DESK_READER_DIR") or os.path.join(ROOT, "data", "reader"), "tts", "omni")
PORT = int(os.environ.get("OMNIVOICE_PORT", "8793"))
URL = f"http://127.0.0.1:{PORT}"
LANGS = {"eo": "Esperanto", "sr": "Serbian"}  # the Mac has no voice for these
VOICES = [("female", "Female"), ("male", "Male")]


class Unavailable(Exception):
    """Not installed, or the voice did not answer: the Reader falls back to the Mac's voice."""


def installed():
    return os.path.exists(PY) and os.path.isdir(MODEL)


def voices(lang):
    lang = (lang or "").split("-")[0].lower()
    if lang not in LANGS or not installed():
        return []
    return [{"id": "omni:" + k, "name": "OmniVoice " + label, "label": f"{label} · OmniVoice (on this Mac)"} for k, label in VOICES]


def _ping(timeout=2):
    try:
        with urllib.request.urlopen(URL + "/ping", timeout=timeout) as r:
            return json.load(r).get("ok") is True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def ensure_server(wait=120):
    if _ping():
        return
    if not installed():
        raise Unavailable("OmniVoice is not installed")
    log = open(os.path.join(ENGINE, "server.log"), "ab")
    subprocess.Popen([PY, os.path.join(HOME, "speak_server.py")], cwd=HOME, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                     start_new_session=True, env={**os.environ, "HF_HOME": os.path.join(ENGINE, "hf"), "HF_HUB_OFFLINE": "1"})
    end = time.time() + wait
    while time.time() < end:
        time.sleep(1)
        if _ping():
            return
    raise Unavailable("OmniVoice did not start")


def speak(text, voice="female"):
    """One sentence -> m4a bytes (AAC). The cache first."""
    text = " ".join(str(text or "").split())[:1200]
    if not text:
        raise Unavailable("No text")
    voice = voice if voice in dict(VOICES) else "female"
    h = hashlib.sha256(f"{voice}|{text}".encode()).hexdigest()
    path = os.path.join(CACHE, h[:2], h + ".m4a")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    ensure_server()
    req = urllib.request.Request(URL + "/speak", data=json.dumps({"text": text, "voice": voice}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            wav = r.read()
    except (urllib.error.URLError, OSError) as e:
        raise Unavailable(f"OmniVoice: {getattr(e, 'reason', e)}") from None
    with tempfile.TemporaryDirectory() as d:
        src, out = os.path.join(d, "a.wav"), os.path.join(d, "a.m4a")
        with open(src, "wb") as f:
            f.write(wav)
        r = subprocess.run(["/usr/bin/afconvert", "-f", "m4af", "-d", "aac", src, out], capture_output=True, timeout=60)
        if r.returncode != 0 or not os.path.exists(out):
            raise Unavailable("Could not convert the audio")
        with open(out, "rb") as f:
            data = f.read()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return data


if __name__ == "__main__":
    if sys.argv[1:2] == ["status"]:
        print(json.dumps({"installed": installed(), "running": _ping(), "voices": voices("eo")}, indent=1))
    else:
        print(__doc__)
