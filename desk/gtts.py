"""Google Cloud Text-to-Speech for the Desk Reader: the natural Chirp 3: HD voices, one sentence at a time.

Only the sentence being read leaves this Mac. Each spoken sentence is kept in desk/data/reader/tts/cache/ (out of the GitHub
backup with the rest of reader/), so the same sentence is never sent twice. The monthly character count is kept in
tts/usage.json and stops at `limit` (default 1,000,000, Google's free tier for Chirp 3: HD; check the price page), after
which the Reader falls back to the Mac's own voices.

The key is the person's own Google Cloud API key with the Text-to-Speech API enabled, in the keychain:
  service tatsuki-desk-gtts (account api), or else the Translate key tatsuki-desk-gtranslate (same project, both APIs enabled).
It goes in the x-goog-api-key header, never in a URL, and never reaches the page.

  python3 desk/gtts.py status          key present, characters used this month
  python3 desk/gtts.py voices en       the voices offered for a language
"""
import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(os.environ.get("DESK_READER_DIR") or os.path.join(ROOT, "data", "reader"), "tts")
CACHE = os.path.join(DIR, "cache")
USAGE = os.path.join(DIR, "usage.json")
VOICES = os.path.join(DIR, "voices.json")
BASE = os.environ.get("GTTS_URL", "https://texttospeech.googleapis.com/v1")
KEYCHAINS = (("tatsuki-desk-gtts", "api"), ("tatsuki-desk-gtranslate", "api"))
LIMIT = 1_000_000
TEXT_MAX = 1200
CACHE_FILES = 6000
VOICE_DAYS = 7
# the languages' main regions, in the order they are offered
REGIONS = {"en": ["en-US", "en-GB"], "ru": ["ru-RU"], "de": ["de-DE"], "ja": ["ja-JP"], "sr": ["sr-RS"], "fr": ["fr-FR"],
           "es": ["es-ES", "es-US"], "it": ["it-IT"], "pt": ["pt-BR", "pt-PT"], "zh": ["cmn-CN"], "ko": ["ko-KR"],
           "vi": ["vi-VN"], "uk": ["uk-UA"], "pl": ["pl-PL"], "nl": ["nl-NL"], "tr": ["tr-TR"], "id": ["id-ID"], "hr": ["hr-HR"]}
FAVORITES = ["Aoede", "Kore", "Leda", "Zephyr", "Charon", "Puck", "Orus", "Fenrir"]  # a few of each, so the menu stays short
PER_REGION = 6
_lock = threading.Lock()


class Unavailable(Exception):
    """No key, over the monthly limit, or Google did not answer: the Reader uses the Mac's voice instead."""


def key():
    if os.environ.get("GTTS_KEY") is not None:  # tests only
        return os.environ["GTTS_KEY"]
    if sys.platform != "darwin":
        return ""
    for svc, acct in KEYCHAINS:
        try:
            r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", svc, "-a", acct, "-w"],
                               capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return ""


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def month():
    return time.strftime("%Y-%m")


def usage():
    u = _read(USAGE, {})
    return {"used": int((u.get("months") or {}).get(month(), 0)), "limit": int(u.get("limit") or LIMIT)}


def _count(n):
    with _lock:
        u = _read(USAGE, {})
        ms = u.setdefault("months", {})
        ms[month()] = int(ms.get(month(), 0)) + n
        _write(USAGE, u)


def _call(method, path, body=None, k=None, timeout=20):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"x-goog-api-key": k or key(), "Content-Type": "application/json", "User-Agent": "tatsuki-desk"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            msg = ""
        raise Unavailable(f"Google returned {e.code}" + (f": {msg[:160]}" if msg else "")) from None
    except (urllib.error.URLError, OSError, ValueError):
        raise Unavailable("Could not reach Google") from None


def voices(lang):
    """[{id: "google:<name>", name, label, region}] for the language: Chirp 3: HD first, a few per region.
    Empty without a key. The list is kept for a week."""
    lang = (lang or "en").split("-")[0].lower()
    regions = REGIONS.get(lang, [])
    if not regions or not key():
        return []
    cache = _read(VOICES, {})
    hit = cache.get(lang)
    if hit and time.time() - hit.get("at", 0) < VOICE_DAYS * 86400:
        return hit["voices"]
    out = []
    for reg in regions:
        res = _call("GET", "/voices?languageCode=" + reg)
        names = [v["name"] for v in res.get("voices") or [] if reg in (v.get("languageCodes") or [])]
        hd = [n for n in names if "Chirp3-HD" in n]
        pool = hd or [n for n in names if "Neural2" in n] or [n for n in names if "Wavenet" in n]
        pool.sort(key=lambda n: (next((i for i, f in enumerate(FAVORITES) if n.endswith("-" + f)), 99), n))
        for n in pool[:PER_REGION]:
            short = n.split("-")[-1]
            kind = "HD" if "Chirp3-HD" in n else "Neural" if "Neural2" in n else "WaveNet"
            out.append({"id": "google:" + n, "name": n, "region": reg, "label": f"{short} · {reg} · Google {kind}"})
    with _lock:
        cache = _read(VOICES, {})
        cache[lang] = {"at": time.time(), "voices": out}
        _write(VOICES, cache)
    return out


def _cache_path(name, rate, text):
    h = hashlib.sha256(f"{name}|{rate:.2f}|{text}".encode()).hexdigest()
    return os.path.join(CACHE, h[:2], h + ".mp3")


def _prune():
    files = []
    for root, _, fs in os.walk(CACHE):
        files += [os.path.join(root, f) for f in fs if f.endswith(".mp3")]
    if len(files) <= CACHE_FILES:
        return
    files.sort(key=lambda p: os.path.getmtime(p))
    for p in files[: len(files) - CACHE_FILES]:
        try:
            os.remove(p)
        except OSError:
            pass


def speak(text, name, rate=1.0):
    """One sentence -> MP3 bytes. The cache first; then Google, counted against the month's limit."""
    text = " ".join(str(text or "").split())[:TEXT_MAX]
    if not text:
        raise Unavailable("No text")
    if not name or "-" not in name:
        raise Unavailable("No such voice")
    rate = min(1.3, max(0.7, float(rate or 1)))
    path = _cache_path(name, rate, text)
    if os.path.exists(path):
        os.utime(path)
        with open(path, "rb") as f:
            return f.read()
    k = key()
    if not k:
        raise Unavailable("No Google key")
    u = usage()
    if u["used"] + len(text) > u["limit"]:
        raise Unavailable("This month's Google characters are used up")
    region = "-".join(name.split("-")[:2])
    body = {"input": {"text": text}, "voice": {"languageCode": region, "name": name},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": rate}}
    try:
        res = _call("POST", "/text:synthesize", body, k)
    except Unavailable as e:
        if "rate" not in str(e).lower() and "speaking" not in str(e).lower():
            raise
        body["audioConfig"].pop("speakingRate")  # a voice that does not take a speed reads at its own
        res = _call("POST", "/text:synthesize", body, k)
    _count(len(text))
    try:
        data = base64.b64decode(res.get("audioContent") or "")
    except (ValueError, TypeError):
        raise Unavailable("Could not read the audio") from None
    if not data:
        raise Unavailable("Google sent no audio")
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    _prune()
    return data


def status():
    return {"hasKey": bool(key()), **usage()}


if __name__ == "__main__":
    if sys.argv[1:2] == ["status"]:
        print(json.dumps(status(), indent=1))
    elif sys.argv[1:2] == ["voices"]:
        try:
            print(json.dumps(voices(sys.argv[2] if len(sys.argv) > 2 else "en"), ensure_ascii=False, indent=1))
        except Unavailable as e:
            print("Unavailable:", e)
    else:
        print(__doc__)
