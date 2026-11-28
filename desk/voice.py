"""Speak into a text box: the Mac records the microphone and whisper.cpp on this Mac turns it into text.

The Desk window (WKWebView) cannot use the browser's speech recognition, so the page asks the server:
  POST /api/voice/start          -> {id}       TatsukiMeetWatch (the app that already holds the microphone permission)
                                               starts recording into a fresh folder
  GET  /api/voice/<id>           -> {state, level, seconds}   also keeps the recording alive (it stops by itself
                                               after 60 seconds without this, or at MAX_SEC)
  POST /api/voice/<id>/stop      -> {text}     stops, runs whisper-cli (large-v3-turbo, Metal) and deletes the audio
  POST /api/voice/<id>/cancel    -> {}         stops and deletes, no text
Nothing leaves the Mac. The audio is kept only until the text is made. The mic app accepts only folders named
"<…>/transcribe…/sessions/<32 hex>", so the folders live in desk/data/reflect/transcribe-voice/sessions/.
"""
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import wave

ROOT = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("VOICE_DIR") or os.path.join(ROOT, "data", "reflect", "transcribe-voice")
APP = os.environ.get("VOICE_APP") or os.path.expanduser("~/Applications/TatsukiMeetWatch.app")
ENGINE = os.path.join(ROOT, "transcribe", "engine")
WHISPER = os.path.join(ENGINE, "whisper-cli")
MODEL = os.path.join(ENGINE, "models", "ggml-large-v3-turbo.bin")
VAD = os.path.join(ENGINE, "models", "ggml-silero-v5.1.2.bin")  # skips silence, so a quiet room does not become made-up words
MAX_SEC = 300                       # one answer: 5 minutes at most
_lock = threading.Lock()


class VoiceError(Exception):
    pass


def _folder(vid):
    if not (isinstance(vid, str) and len(vid) == 32 and all(c in "0123456789abcdef" for c in vid)):
        raise VoiceError("Unknown recording")
    return os.path.join(BASE, "sessions", vid)


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _touch(path):
    with open(path, "a"):
        os.utime(path, None)


def ready():
    """What is missing, or ''."""
    if not os.path.exists(APP):
        return "The microphone app is not set up. Run bash desk/transcribe/setup.sh once."
    if not (os.path.exists(WHISPER) and os.path.exists(MODEL)):
        return "The speech engine is not set up. Run bash desk/transcribe/setup.sh once."
    perm = _read(os.path.join(ROOT, "data", "transcribe", "mic-permission.json"), {})
    if perm.get("microphone") == "denied":
        return "The microphone is not allowed. System Settings → Privacy & Security → Microphone → TatsukiMeetWatch."
    return ""


def _sweep():
    """Recordings older than an hour (a closed window, a crash) are deleted."""
    root = os.path.join(BASE, "sessions")
    for name in os.listdir(root) if os.path.isdir(root) else []:
        p = os.path.join(root, name)
        try:
            if time.time() - os.path.getmtime(p) > 3600:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


def start():
    why = ready()
    if why:
        raise VoiceError(why)
    _sweep()
    vid = secrets.token_hex(16)
    folder = _folder(vid)
    os.makedirs(folder, exist_ok=True)
    _touch(os.path.join(folder, "mic-alive"))
    with open(os.path.join(folder, "started"), "w") as f:
        f.write(str(time.time()))
    # through `open`, so the permission macOS gave this app is used (not Python's)
    r = subprocess.run(["/usr/bin/open", "-n", "-g", "-a", APP, "--args", "record", folder], capture_output=True, timeout=20)
    if r.returncode != 0:
        shutil.rmtree(folder, ignore_errors=True)
        raise VoiceError("The microphone could not start.")
    return {"id": vid}


def status(vid):
    folder = _folder(vid)
    if not os.path.isdir(folder):
        raise VoiceError("Unknown recording")
    _touch(os.path.join(folder, "mic-alive"))
    m = _read(os.path.join(folder, "mic.json"), {})
    try:
        since = float(open(os.path.join(folder, "started")).read())
    except (OSError, ValueError):
        since = time.time()
    secs = time.time() - since
    if secs > MAX_SEC:
        _touch(os.path.join(folder, "mic-stop"))
    return {"state": m.get("state") or "starting", "level": m.get("level") or 0, "seconds": round(secs), "error": m.get("error") or ""}


def _wait_stopped(folder, limit=6):
    end = time.time() + limit
    while time.time() < end:
        if (_read(os.path.join(folder, "mic.json"), {}) or {}).get("state") not in ("starting", "recording"):
            return
        time.sleep(0.25)


def transcribe(pcm_path, language="auto"):
    """16 kHz mono 16-bit PCM -> text, by whisper.cpp on this Mac."""
    if not os.path.exists(pcm_path) or os.path.getsize(pcm_path) < 16000:  # under half a second
        return ""
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "a.wav")
        with open(pcm_path, "rb") as src, wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(src.read())
        stem = os.path.join(tmp, "a")
        cmd = [WHISPER, "-m", MODEL, "-f", wav, "-l", language, "-t", "4", "-np", "-oj", "-of", stem]
        if os.path.exists(VAD):
            cmd += ["--vad", "--vad-model", VAD]
        r = subprocess.run(cmd, capture_output=True, timeout=600)
        if r.returncode != 0:
            r = subprocess.run(cmd + ["-ng"], capture_output=True, timeout=900)  # the GPU path failed: once on the CPU
        if r.returncode != 0:
            raise VoiceError("The speech could not be turned into text. Try again.")
        items = _read(stem + ".json", {}).get("transcription", [])
    text = " ".join(x.get("text", "").strip() for x in items if x.get("text", "").strip() and "[" not in x.get("text", "")[:2]).strip()
    return text if any(c.isalnum() for c in text) else ""  # only dots or marks: nothing was said


def stop(vid, language="auto"):
    folder = _folder(vid)
    if not os.path.isdir(folder):
        raise VoiceError("Unknown recording")
    _touch(os.path.join(folder, "mic-stop"))
    _wait_stopped(folder)
    try:
        with _lock:  # one whisper at a time
            return {"text": transcribe(os.path.join(folder, "audio.pcm"), language)}
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def cancel(vid):
    folder = _folder(vid)
    if os.path.isdir(folder):
        _touch(os.path.join(folder, "mic-stop"))
        _wait_stopped(folder, 3)
        shutil.rmtree(folder, ignore_errors=True)
    return {}


def handle(method, parts, rec):
    """/api/voice/... -> (code, body)."""
    try:
        if method == "POST" and parts == ["start"]:
            return 200, start()
        if method == "GET" and len(parts) == 1:
            return 200, status(parts[0])
        if method == "POST" and len(parts) == 2 and parts[1] == "stop":
            lang = str((rec or {}).get("lang") or "auto")
            return 200, stop(parts[0], lang if lang in ("auto", "ja", "en", "eo", "ru", "vi", "de", "sr") else "auto")
        if method == "POST" and len(parts) == 2 and parts[1] == "cancel":
            return 200, cancel(parts[0])
    except VoiceError as e:
        return 400, {"error": str(e)}
    return 404, {"error": "not found"}
