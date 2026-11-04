#!/usr/bin/env python3
"""Small client for the FocusBlock daemon, used by FocusBlock.app so that a block
can be lifted even when every browser is blocked.

  focusctl.py status        prints "idle" or a short description of the running block
  focusctl.py stop          reads the password from stdin and stops the block
"""
import json
import sys
import time
import urllib.error
import urllib.request

URL = "http://127.0.0.1:47800"
# Never go through the system proxy: while websites are blocked it points at the daemon itself.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(URL + path, data=data, headers={"Content-Type": "application/json"})
    with OPENER.open(req, timeout=10) as r:
        return json.load(r)


def status():
    st = call("/api/state")
    a = st["active"]
    if not a["active"]:
        print("idle")
        return
    targets = "・".join(t for t, on in (("アプリ", a["apps"]), ("Webサイト", a["web"])) if on)
    left = max(0, a["end"] - st["now"])
    h, m = int(left // 3600), int(left % 3600 // 60)
    print("%s：残り %s%d分" % (targets, "%d時間" % h if h else "", m))


def stop():
    try:
        call("/api/stop", {"password": sys.stdin.read()})
    except urllib.error.HTTPError as e:
        sys.exit(json.load(e).get("error", "解除できませんでした"))


if __name__ == "__main__":
    {"status": status, "stop": stop}[sys.argv[1]]()
