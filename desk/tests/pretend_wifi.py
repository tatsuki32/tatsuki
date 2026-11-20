#!/usr/bin/env python3
"""A pretend free Wi-Fi on this Mac, to watch Wi-Fi Login work without going to a cafe:

  python3 desk/tests/pretend_wifi.py

For 30 seconds a page on this Mac answers the test page the way a cafe's Wi-Fi does: it sends it to a sign-in page.
Wi-Fi Login is then run once. Safari should come forward showing "Pretend free Wi-Fi". Nothing on the Mac is changed,
and the Mac has to be on some Wi-Fi (any) for the check to start.
"""
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import wifi_login  # noqa: E402

PAGE = (b"<!doctype html><meta charset=utf-8><title>Pretend free Wi-Fi</title>"
        b"<body style='font:20px -apple-system;margin:15vh auto;max-width:28em;text-align:center'>"
        b"<h1>Pretend free Wi-Fi</h1><p>If Safari opened this by itself, Wi-Fi Login works.</p>"
        b"<p>A real one would have a Connect button here. This page goes away in 30 seconds.</p>")


class Cafe(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/login"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)
        else:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    srv = HTTPServer(("127.0.0.1", 0), Cafe)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    wifi_login.PROBE = "http://127.0.0.1:%d/hotspot-detect.html" % srv.server_port
    v = wifi_login.run()
    print("%s — %s" % (v["headline"], v["line"]))
    if v["opened"]:
        time.sleep(30)
