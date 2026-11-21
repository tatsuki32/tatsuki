"""OmniVoice speech server for the Desk Reader (Esperanto and other languages the Mac has no voice for).

Runs in desk/omnivoice/engine/venv (Python 3.11, torch). The Desk (desk/eo_tts.py) starts it when needed and it quits by itself
after IDLE seconds without a request, so the ~2.5 GB model is in memory only while reading. Listens on 127.0.0.1 only.

  POST /speak {"text": "...", "voice": "female"|"male"|"auto"} -> WAV (24 kHz, 16-bit mono)
  GET  /ping -> {"ok": true}
  python speak_server.py --selftest      loads the model and writes engine/selftest.wav
"""
import io
import json
import os
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "engine")
os.environ.setdefault("HF_HOME", os.path.join(ENGINE, "hf"))
PORT = int(os.environ.get("OMNIVOICE_PORT", "8793"))
IDLE = 1800
VOICES = {"female": "female, middle-aged, moderate pitch", "male": "male, middle-aged, low pitch"}
_model = None
_lock = threading.Lock()
_last = time.time()


def model():
    global _model
    if _model is None:
        import torch
        from omnivoice import OmniVoice
        dev = "mps" if torch.backends.mps.is_available() else "cpu"
        _model = OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map=dev, dtype=torch.float16 if dev == "mps" else torch.float32)
    return _model


def wav_bytes(samples, rate=24000):
    import numpy as np
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def speak(text, voice="female"):
    text = " ".join(str(text or "").split())[:1200]
    if not text:
        raise ValueError("no text")
    with _lock:
        kw = {"instruct": VOICES[voice]} if voice in VOICES else {}
        audio = model().generate(text=text, **kw)
    return wav_bytes(audio[0])


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, kind="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        global _last
        _last = time.time()
        if self.path == "/ping":
            return self._send(200, json.dumps({"ok": True, "loaded": _model is not None}).encode())
        self._send(404, b"{}")

    def do_POST(self):
        global _last
        _last = time.time()
        if self.path != "/speak" or self.headers.get("Origin"):  # the Desk server only; a web page is never answered
            return self._send(404, b"{}")
        try:
            rec = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            out = speak(rec.get("text"), rec.get("voice") or "female")
        except Exception as e:  # noqa: BLE001 -- the Desk falls back to the Mac's voice
            return self._send(500, json.dumps({"error": type(e).__name__}).encode())
        _last = time.time()
        self._send(200, out, "audio/wav")

    def log_message(self, *a):
        pass


def main():
    if "--selftest" in sys.argv:
        data = speak("Saluton! Mi legas tiun ĉi libron por vi.")
        with open(os.path.join(ENGINE, "selftest.wav"), "wb") as f:
            f.write(data)
        print("selftest ok:", len(data), "bytes")
        return
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=model, daemon=True).start()  # load while the first sentence is on its way
    while time.time() - _last < IDLE:
        time.sleep(30)
    srv.shutdown()


if __name__ == "__main__":
    main()
