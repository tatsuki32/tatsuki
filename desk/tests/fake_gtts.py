"""A fake Google Cloud Text-to-Speech for tests: GET /voices, POST /text:synthesize. Never calls Google.

  python3 desk/tests/fake_gtts.py [port]     serves on 127.0.0.1:<port> (default 8797) for a test Desk with GTTS_URL
"""
import base64
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

NAMES = ["en-US-Chirp3-HD-Aoede", "en-US-Chirp3-HD-Charon", "en-US-Chirp3-HD-Zubenelgenubi", "en-US-Neural2-A", "en-US-Standard-B",
         "en-GB-Chirp3-HD-Kore", "ru-RU-Chirp3-HD-Leda", "ru-RU-Wavenet-A", "sr-RS-Standard-A"]
SOUND = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 64


def serve(port=8797, log=None):
    log = log if log is not None else []

    class H(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            out = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self):
            u = urlsplit(self.path)
            log.append(("GET", u.path, self.headers.get("x-goog-api-key"), u.query))
            if self.headers.get("x-goog-api-key") != "test-key":
                return self._send(403, {"error": {"message": "API key not valid"}})
            reg = parse_qs(u.query).get("languageCode", [""])[0]
            return self._send(200, {"voices": [{"name": n, "languageCodes": ["-".join(n.split("-")[:2])]} for n in NAMES if n.startswith(reg)]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            log.append(("POST", urlsplit(self.path).path, self.headers.get("x-goog-api-key"), body))
            if self.headers.get("x-goog-api-key") != "test-key":
                return self._send(403, {"error": {"message": "API key not valid"}})
            if "speakingRate" in body["audioConfig"] and "Zubenelgenubi" in body["voice"]["name"]:
                return self._send(400, {"error": {"message": "This voice does not support speaking rate"}})
            return self._send(200, {"audioContent": base64.b64encode(SOUND).decode()})

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, log


if __name__ == "__main__":
    srv, _ = serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8797)
    print("fake Google TTS on", srv.server_address, flush=True)
    threading.Event().wait()
