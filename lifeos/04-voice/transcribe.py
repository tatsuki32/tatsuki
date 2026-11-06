"""Speech to text through Groq's Whisper. The audio leaves this Mac; the key stays in the keychain.

    security add-generic-password -a groq -s tatsuki-desk-groq -w

Another Whisper model can be set in <data>/tools/04-voice/settings.json as {"model": "..."}.
"""
import json
import os
import subprocess
import urllib.error
import urllib.request
import uuid

from lifeos.lib import paths

URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = "whisper-large-v3-turbo"
KEYCHAIN = ("tatsuki-desk-groq", "groq")
MAX_BYTES = 25 * 1024 * 1024  # the most a direct upload may hold
TIMEOUT = 45  # the reply has to arrive within a minute of sending
# Telegram's download has no file name, and Groq goes by the name's extension
_SUFFIXES = ((b"OggS", "ogg"), (b"ID3", "mp3"), (b"RIFF", "wav"), (b"fLaC", "flac"), (b"\xff\xfb", "mp3"), (b"\xff\xf3", "mp3"))


class TranscribeError(Exception):
    """Nothing came back. The message names the cause and never holds the key or the words."""


def key():
    v = os.environ.get("LIFEOS_GROQ_API_KEY")
    if v or paths.is_sandbox():
        return v or None
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN[0], "-a", KEYCHAIN[1], "-w"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def model():
    try:
        with open(paths.data_path("tools", "04-voice", "settings.json", mkdir=False), encoding="utf-8") as f:
            return json.load(f).get("model") or MODEL
    except (OSError, ValueError, AttributeError):
        return MODEL


def _suffix(audio):
    if audio[4:8] == b"ftyp":
        return "m4a"
    return next((s for head, s in _SUFFIXES if audio.startswith(head)), "ogg")


def _body(audio, boundary):
    parts = []
    for name, value in (("model", model()), ("language", "ja"), ("response_format", "json")):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="voice.{_suffix(audio)}"\r\n'
                 "Content-Type: application/octet-stream\r\n\r\n".encode())
    parts += [audio, f"\r\n--{boundary}--\r\n".encode()]
    return b"".join(parts)


def transcribe(path):
    """The text of an audio file. Raises TranscribeError when it cannot get one."""
    api_key = key()
    if not api_key:
        raise TranscribeError("no key")
    with open(path, "rb") as f:
        audio = f.read()
    if len(audio) > MAX_BYTES:
        raise TranscribeError("too large")
    boundary = uuid.uuid4().hex
    req = urllib.request.Request(URL, data=_body(audio, boundary), method="POST", headers={
        "Authorization": "Bearer " + api_key,
        "Content-Type": "multipart/form-data; boundary=" + boundary,
        "User-Agent": "tatsuki-desk-lifeos",  # the default Python one is refused at Groq's front door
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8")).get("text") or ""
    except urllib.error.HTTPError as e:
        raise TranscribeError(f"groq {e.code}") from None
    except (OSError, ValueError, AttributeError) as e:
        raise TranscribeError("groq " + type(e).__name__) from None
