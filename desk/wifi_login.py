#!/usr/bin/env python3
"""Tatsuki's Desk — open the Wi-Fi sign-in page when macOS does not show it.

Free Wi-Fi (cafes, restaurants) lets nothing through until a button on its own page is pressed. This finds that page
the way macOS does and opens it in Safari, so the router's number never has to be looked up and pasted by hand:

  1. the address the Wi-Fi hands out for it (DHCP option 114), when it hands one out
  2. an unencrypted test page (Apple's own): where it gets sent instead is the sign-in page
  3. a test page that came back changed: the address written inside it, or the test page itself
  4. no answer at all: the router's own page, http://<router>/

It only opens a page. It never presses Connect or Agree, and it never changes DNS or VPN settings: it says what is
in the way. Everything runs on this Mac; the only traffic is the test page and the Wi-Fi's own pages.
Kept in desk/data/wifilogin/state.json: when the watcher last opened a page, and a hash of which network that was
(a Wi-Fi name says where someone was, so the name itself is not written, and backup_data.py skips this folder).

  python3 wifi_login.py           look, and open the sign-in page in Safari if one is needed
  python3 wifi_login.py status    look only
  python3 wifi_login.py app       what the Desktop icon runs: the first, then a short message on screen
  python3 wifi_login.py auto      what the watcher runs when the network changes: quiet, opens only when needed
"""
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit

ROOT = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("WIFILOGIN_DIR") or os.path.join(ROOT, "data", "wifilogin")
PROBE = os.environ.get("WIFILOGIN_PROBE") or "http://captive.apple.com/hotspot-detect.html"
TIMEOUT = 4          # one request; a button press answers within about five seconds
SETTLE = 8           # seconds the watcher waits for the Wi-Fi to hand out an address
AGAIN = 180          # the watcher does not open the same network's page twice within this
AGAIN_GUESS = 1800   # ... and waits longer when the page was only the router guess (a home line that is down)
TUNNELS = ("utun", "ipsec", "ppp", "tun", "tap", "wg")

IPCONFIG = "/usr/sbin/ipconfig"
NETWORKSETUP = "/usr/sbin/networksetup"
ROUTE = "/sbin/route"


# ---------- reading what macOS prints (plain text in, plain values out: these are what the tests cover) ----------

def parse_ports(text):
    """`networksetup -listallhardwareports` → (service name, device) of the Wi-Fi port."""
    m = re.search(r"Hardware Port:\s*(Wi-Fi|AirPort)\s*\nDevice:\s*(\S+)", text)
    return (m.group(1), m.group(2)) if m else ("Wi-Fi", "en0")


def parse_summary(text):
    """`ipconfig getsummary`: is this Wi-Fi, and what is it called. macOS hides the name from some programs."""
    kind = re.search(r"^\s*InterfaceType\s*:\s*(.+?)\s*$", text, re.M)
    name = re.search(r"^\s*SSID\s*:\s*(.+?)\s*$", text, re.M)
    ssid = name.group(1) if name else None
    return {"wifi": bool(kind and kind.group(1) == "WiFi"), "ssid": None if ssid in (None, "<redacted>") else ssid}


def _ips(text):
    return [x for x in re.findall(r"\d{1,3}(?:\.\d{1,3}){3}", text or "") if all(int(p) < 256 for p in x.split("."))]


def parse_packet(text):
    """`ipconfig getpacket`: what the Wi-Fi handed out — the router, its DNS servers, and a sign-in address if any."""
    def field(name):
        m = re.search(r"^%s \([^)]*\):\s*(.*)$" % name, text, re.M)
        return m.group(1).strip() if m else ""
    portal = field("captive_portal_url") or field("option_114")
    if portal.startswith("{"):  # shown as raw bytes on some versions: {0x68, 0x74, ...}
        try:
            portal = bytes(int(b, 16) for b in re.findall(r"0x([0-9a-fA-F]{2})", portal)).decode("ascii", "ignore")
        except ValueError:
            portal = ""
    return {"router": (_ips(field("router")) or [None])[0], "dns": _ips(field("domain_name_server")),
            "portal": portal if web(portal) else None}


def parse_manual_dns(text):
    """`networksetup -getdnsservers`: the servers typed in by hand; none when the Wi-Fi's own are in use."""
    return [] if "aren't any" in text else [x for x in text.split() if re.match(r"^[0-9a-fA-F:.]+$", x) and (":" in x or _ips(x))]


def parse_route(text):
    """`route -n get default`: where traffic leaves — the gateway, and through which interface."""
    gw = re.search(r"^\s*gateway:\s*(\S+)", text, re.M)
    via = re.search(r"^\s*interface:\s*(\S+)", text, re.M)
    return {"gateway": (_ips(gw.group(1)) or [None])[0] if gw else None, "interface": via.group(1) if via else None}


def web(url):
    """Only ordinary web addresses are ever handed to Safari."""
    try:
        p = urlsplit(url or "")
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.hostname) and not re.search(r"[\s\x00-\x1f]", url)


def read_probe(status, location, body, url=PROBE):
    """One answer from the test page → {"kind": "online" | "redirect" | "page", "url"}."""
    text = body if isinstance(body, str) else (body or b"").decode("utf-8", "replace")
    if 300 <= status < 400 and location:
        to = urljoin(url, location.strip())
        return {"kind": "redirect", "url": to if web(to) else url}
    if status == 200 and re.search(r"<title>\s*Success\s*</title>", text, re.I):
        return {"kind": "online", "url": None}
    for pattern in (r"<LoginURL>\s*(?:<!\[CDATA\[)?\s*([^<\]\s]+)",                          # WISPr
                    r"http-equiv=[\"']?refresh[\"']?[^>]*url\s*=\s*[\"']?([^\"'>\s]+)",        # <meta refresh>
                    r"(?:window\.|document\.)?location(?:\.href)?\s*=\s*[\"']([^\"']+)[\"']",  # a line of script
                    r"location\.(?:replace|assign)\(\s*[\"']([^\"']+)[\"']"):
        m = re.search(pattern, text, re.I)
        if m:
            to = urljoin(url, m.group(1).replace("&amp;", "&"))
            if web(to):
                return {"kind": "page", "url": to}
    return {"kind": "page", "url": url}  # the sign-in page is being served in the test page's place


def read_api(body):
    """RFC 8908: what the address from DHCP option 114 answers. → the sign-in page, or None."""
    try:
        rec = json.loads(body)
    except (ValueError, TypeError):
        return None
    to = rec.get("user-portal-url") if isinstance(rec, dict) and rec.get("captive") else None
    return to if web(to) else None


# ---------- asking this Mac and the network ----------

def _output(cmd, timeout=5):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


class _Stay(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None  # where the test page gets sent is the answer, so do not follow it


def _open(url, headers=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _Stay)
    return opener.open(urllib.request.Request(url, headers=headers or {"User-Agent": "CaptiveNetworkSupport wispr"}), timeout=TIMEOUT)


def probe(url=None):
    url = url or PROBE
    try:
        with _open(url) as r:
            return read_probe(r.status, r.headers.get("Location"), r.read(65536), url)
    except urllib.error.HTTPError as e:
        try:
            body = e.read(65536)
        except OSError:
            body = b""
        return read_probe(e.code, e.headers.get("Location") if e.headers else None, body, url)
    except urllib.error.URLError as e:
        if isinstance(e.reason, socket.gaierror):
            return {"kind": "nodns", "url": None}
        return {"kind": "timeout" if isinstance(e.reason, (socket.timeout, TimeoutError)) else "silent", "url": None}
    except (socket.timeout, TimeoutError):
        return {"kind": "timeout", "url": None}
    except (OSError, ValueError):
        return {"kind": "silent", "url": None}


def ask_api(url):
    try:
        with _open(url, {"Accept": "application/captive+json"}) as r:
            return read_api(r.read(65536))
    except (OSError, ValueError):
        return None


def look():
    """What this Mac is on right now, and the page to open if one is needed. Opens nothing."""
    service, device = parse_ports(_output([NETWORKSETUP, "-listallhardwareports"]))
    link = parse_summary(_output([IPCONFIG, "getsummary", device]))
    addr = (_ips(_output([IPCONFIG, "getifaddr", device])) or [None])[0]
    out = {"state": "nowifi", "ssid": link["ssid"], "router": None, "dns": [], "manualDns": [], "vpn": False,
           "url": None, "found": None, "at": int(time.time())}
    if not addr:
        return out
    pkt = parse_packet(_output([IPCONFIG, "getpacket", device]))
    way = parse_route(_output([ROUTE, "-n", "get", "default"]))
    out.update(router=pkt["router"] or way["gateway"], dns=pkt["dns"],
               manualDns=parse_manual_dns(_output([NETWORKSETUP, "-getdnsservers", service])),
               vpn=bool(way["interface"] and way["interface"].startswith(TUNNELS)))
    told = ask_api(pkt["portal"]) if pkt["portal"] else None
    if told:
        out.update(state="login", url=told, found="network")
        return out
    p = probe()
    if p["kind"] == "online":
        out["state"] = "online"
    elif p["kind"] in ("redirect", "page"):
        out.update(state="login", url=p["url"], found=p["kind"])
    elif out["router"]:
        out.update(state="login", url="http://%s/" % out["router"], found="router")
    else:
        out["state"] = "stuck"
    return out


# ---------- words for the page, the Desktop icon and the terminal (American English, as everywhere in Desk) ----------

HEAD = {"online": "You are online", "nowifi": "Not on Wi-Fi", "stuck": "No sign-in page found",
        "login": "This Wi-Fi needs a sign-in"}


def describe(r, opened=False):
    """Adds the sentences to show. The page prints them as they are."""
    hints = []
    if r["state"] == "online":
        line = "Nothing to open."
    elif r["state"] == "nowifi":
        line = "Join a Wi-Fi network first, then try again."
    elif r["state"] == "stuck":
        line = "The Wi-Fi did not answer and gave no router address. Turn Wi-Fi off and on, then try again."
    elif r["found"] == "router":
        line = ("Opened the router's page in Safari. " if opened else "") + \
            "The Wi-Fi did not show a sign-in page, so this is the router's own page. It may not be the right one."
    else:
        line = "Opened the sign-in page in Safari." if opened else "The sign-in page is ready to open."
    if r["state"] in ("login", "stuck"):
        if r["manualDns"]:
            hints.append("DNS is set by hand (%s). Free Wi-Fi usually needs its own: System Settings > Network > Wi-Fi > "
                         "Details > DNS, and remove the entries until you have signed in." % ", ".join(r["manualDns"]))
        if r["vpn"]:
            hints.append("A VPN is on. Turn it off until you have signed in.")
    return dict(r, headline=HEAD[r["state"]], line=line, hints=hints, opened=bool(opened))


def view():
    return describe(look())


def open_safari(url):
    if not web(url):
        return False
    try:
        return subprocess.run(["/usr/bin/open", "-a", "Safari", url], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def run():
    """The button: look, and open the page when there is one. Every press looks again."""
    r = look()
    return describe(r, opened=r["state"] == "login" and open_safari(r["url"]))


# ---------- the watcher (launchd starts `auto` when the network changes) ----------

def _key(r):
    return hashlib.sha256(("%s|%s" % (r["ssid"] or "", r["router"] or "")).encode()).hexdigest()[:16]


def read_state():
    try:
        with open(os.path.join(DIR, "state.json"), encoding="utf-8") as f:
            rec = json.load(f)
        return rec if isinstance(rec, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(rec):
    os.makedirs(DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=DIR, prefix=".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(rec, f)
    os.replace(tmp, os.path.join(DIR, "state.json"))


def should_open(r, last, now):
    """The watcher's rule. Never when online; never the same network's page twice in a row."""
    if r["state"] != "login":
        return False
    wait = AGAIN_GUESS if r["found"] == "router" else AGAIN
    return not (last.get("key") == _key(r) and now - (last.get("at") or 0) < wait)


def assistant_showing():
    """macOS's own sign-in window is already up: leave it to that."""
    return bool(_output(["/usr/bin/pgrep", "-f", "Captive Network Assistant.app"]).strip())


def auto(sleep=time.sleep):
    r = look()
    for _ in range(SETTLE):  # joining takes a moment: wait for an address, then for the answer to hold
        if r["state"] != "nowifi":
            break
        sleep(1)
        r = look()
    if r["state"] == "login" and r["found"] == "router":
        sleep(2)  # no answer may only mean the network was not ready: ask once more before guessing
        r = look()
    now = int(time.time())
    if not should_open(r, read_state(), now) or assistant_showing():
        return describe(r)
    opened = open_safari(r["url"])
    if opened:
        write_state({"key": _key(r), "at": now, "found": r["found"]})
    return describe(r, opened=opened)


def say(v):
    """The Desktop icon has no window, so the result is one small dialog that closes itself."""
    if v["opened"] and v["found"] != "router" and not v["hints"]:
        return  # Safari coming forward with the page is the message
    text = "\n\n".join([v["line"]] + v["hints"] +
                       (["Router %s\nDNS %s" % (v["router"] or "—", ", ".join(v["dns"]) or "—")] if v["state"] in ("login", "stuck") else []))
    script = ['on run argv', 'display dialog (item 2 of argv) with title (item 1 of argv) buttons {"OK"} default button 1 giving up after 12',
              'end run']
    try:
        subprocess.run(["/usr/bin/osascript"] + [x for line in script for x in ("-e", line)] + ["--", v["headline"], text],
                       capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "open"
    if mode == "auto":  # the log keeps no Wi-Fi name and no address: only what happened
        res = auto()
        print("%s %s%s%s" % (time.strftime("%F %T"), res["state"], " via " + res["found"] if res["found"] else "",
                             " opened" if res["opened"] else ""))
        sys.exit(0)
    elif mode == "status":
        res = view()
    else:
        res = run()
        if mode == "app":
            say(res)
    print(json.dumps(res, ensure_ascii=False, indent=2))
