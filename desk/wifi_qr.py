#!/usr/bin/env python3
"""Tatsuki's Desk — a QR code that joins the Wi-Fi this Mac is on.

The network name comes from networksetup, its kind of security from ipconfig, and the password from the macOS
keychain (`security find-generic-password`; macOS shows its own prompt and Desk never sees what is typed there).
Nothing is kept: no file, no log, and no variable that outlives the request. The page gets only the rows of the
code to draw, never the password as text. The QR drawing itself is utilities.qr_matrix.
"""
import os
import re
import subprocess

import utilities

KEYCHAIN_WAIT = 120  # seconds the macOS prompt may stay open
ESCAPED = re.compile(r'([\\;,:"])')
_device = None


def run(cmd, timeout=5):
    """(exit code, stdout). None as the code means it did not finish or could not start."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env={**os.environ, "LC_ALL": "C"})
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError):
        return None, ""


def device(run=run):
    """The Wi-Fi interface, usually en0."""
    global _device
    if _device:
        return _device
    _, out = run(["networksetup", "-listallhardwareports"])
    m = re.search(r"Hardware Port: (?:Wi-Fi|AirPort)\nDevice: (\S+)", out)
    if m:
        _device = m.group(1)
    return _device or "en0"


def parse_ssid(text):
    """networksetup -getairportnetwork: 'Current Wi-Fi Network: <name>', or a sentence when there is none."""
    m = re.match(r"Current (?:Wi-Fi|AirPort) Network: (.*)", text)
    return m.group(1).rstrip("\n") if m else ""


def parse_summary(text):
    """ipconfig getsummary: the name (a fallback) and the security line."""
    ssid = re.search(r"^\s+SSID : (.*)$", text, re.M)
    sec = re.search(r"^\s+Security : (.*)$", text, re.M)
    return (ssid.group(1) if ssid else ""), (sec.group(1).strip() if sec else "")


def kind_of(security):
    """open (no password), enterprise (a user name too, so no QR can join it), wep, or password (WPA, WPA2, WPA3)."""
    s = security.upper()
    if s in ("NONE", "OPEN", "OWE"):
        return "open"
    if "ENTERPRISE" in s or "802.1X" in s or "EAP" in s:
        return "enterprise"
    if "WEP" in s:
        return "wep"
    return "password"


def current(run=run):
    """{'connected', 'ssid', 'kind'} for the network this Mac is on right now."""
    dev = device(run)
    _, out = run(["networksetup", "-getairportnetwork", dev])
    _, summary = run(["ipconfig", "getsummary", dev])
    fallback, security = parse_summary(summary)
    ssid = parse_ssid(out) or fallback
    if not ssid or ssid == "<redacted>":  # newer macOS hides the name without Location permission
        return {"connected": False, "ssid": "", "kind": ""}
    return {"connected": True, "ssid": ssid, "kind": kind_of(security)}


def keychain(ssid, run=run):
    """('ok', password) or ('missing' | 'denied' | 'timeout', ''). macOS asks the person before it answers."""
    code, out = run(["security", "find-generic-password", "-D", "AirPort network password", "-a", ssid, "-w"], KEYCHAIN_WAIT)
    if code == 0 and out.rstrip("\n"):
        return "ok", out.rstrip("\n")
    if code == 44:  # errSecItemNotFound
        return "missing", ""
    if code is None:
        return "timeout", ""
    return "denied", ""


def escape(text):
    return ESCAPED.sub(r"\\\1", text)


def payload(ssid, kind, password=""):
    """The text phone cameras read as 'join this network'."""
    if kind == "open":
        return f"WIFI:T:nopass;S:{escape(ssid)};;"
    return f"WIFI:T:{'WEP' if kind == 'wep' else 'WPA'};S:{escape(ssid)};P:{escape(password)};;"


def make(password=None, ssid=None, now=current, read=keychain):
    """(status, body) for the page. body['state'] is ok (with rows), offline, changed, enterprise or ask (with a reason).

    `password` is what the person typed after the keychain could not answer; `ssid` is the name the page was showing,
    so a code is never made for a network the Mac has moved to in the meantime.
    """
    cur = now()
    if not cur["connected"]:
        return 200, {"state": "offline"}
    name, kind = cur["ssid"], cur["kind"]
    if isinstance(ssid, str) and ssid and ssid != name:
        return 200, {"state": "changed"}
    if kind == "enterprise":
        return 200, {"state": "enterprise", "ssid": name}
    secret = ""
    if kind != "open":
        if isinstance(password, str) and password:
            if kind == "password" and len(password) < 8:
                return 200, {"state": "ask", "ssid": name, "reason": "short"}
            secret = password
        else:
            status, secret = read(name)
            if status != "ok":
                return 200, {"state": "ask", "ssid": name, "reason": status}
    try:
        rows = utilities.qr_matrix(payload(name, kind, secret))
    except ValueError:
        return 200, {"state": "ask", "ssid": name, "reason": "long"}
    return 200, {"state": "ok", "ssid": name, "kind": kind, "rows": ["".join(map(str, r)) for r in rows]}
