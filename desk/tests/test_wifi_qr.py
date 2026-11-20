"""python3 -m unittest desk.tests.test_wifi_qr (from the repo root). No Wi-Fi and no keychain: both are fakes."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import utilities  # noqa: E402
import wifi_qr  # noqa: E402

SUMMARY = """<dictionary> {
  BSSID : aa:bb:cc:dd:ee:ff
  InterfaceType : WiFi
  SSID : Home; Net
  Security : %s
}
"""


def on(ssid="Home; Net", kind="password"):
    return lambda: {"connected": True, "ssid": ssid, "kind": kind}


def rows(text):
    return ["".join(map(str, r)) for r in utilities.qr_matrix(text)]


def never(ssid):
    raise AssertionError("the keychain must not be asked")


class ParseTest(unittest.TestCase):
    def test_name_and_security(self):
        self.assertEqual(wifi_qr.parse_ssid("Current Wi-Fi Network: Home; Net\n"), "Home; Net")
        self.assertEqual(wifi_qr.parse_ssid("You are not associated with an AirPort network.\n"), "")
        self.assertEqual(wifi_qr.parse_summary(SUMMARY % "WPA2_PSK"), ("Home; Net", "WPA2_PSK"))

    def test_kinds(self):
        for sec, kind in (("NONE", "open"), ("WPA2_PSK", "password"), ("WPA3_SAE", "password"), ("WPA2_ENTERPRISE", "enterprise"),
                          ("WEP", "wep"), ("", "password")):
            self.assertEqual(wifi_qr.kind_of(sec), kind, sec)

    def test_current_reads_both_commands(self):
        def run(cmd, timeout=5):
            if cmd[0] == "networksetup" and cmd[1] == "-listallhardwareports":
                return 0, "Hardware Port: Wi-Fi\nDevice: en7\nEthernet Address: 00\n"
            if cmd[0] == "networksetup":
                return 0, "Current Wi-Fi Network: Cafe\n" if cmd[2] == "en7" else ""
            return 0, SUMMARY % "NONE"
        wifi_qr._device = None
        self.assertEqual(wifi_qr.current(run), {"connected": True, "ssid": "Cafe", "kind": "open"})
        wifi_qr._device = None

    def test_not_connected(self):
        wifi_qr._device = "en0"
        self.assertFalse(wifi_qr.current(lambda cmd, timeout=5: (0, "You are not associated with an AirPort network.\n"))["connected"])
        wifi_qr._device = None

    def test_keychain_answers(self):
        asked = []

        def run(code, out=""):
            def f(cmd, timeout=5):
                asked.append((cmd, timeout))
                return code, out
            return f
        self.assertEqual(wifi_qr.keychain("Net", run(0, "pass word\n")), ("ok", "pass word"))
        self.assertEqual(wifi_qr.keychain("Net", run(44)), ("missing", ""))
        self.assertEqual(wifi_qr.keychain("Net", run(128)), ("denied", ""))
        self.assertEqual(wifi_qr.keychain("Net", run(None)), ("timeout", ""))
        cmd, timeout = asked[0]
        self.assertEqual(cmd[-3:], ["-a", "Net", "-w"])  # the name is one argument, never a shell string
        self.assertEqual(timeout, wifi_qr.KEYCHAIN_WAIT)


class PayloadTest(unittest.TestCase):
    def test_escapes_the_special_characters(self):
        self.assertEqual(wifi_qr.payload('a;b,c:d\\e"f', "password", "p;w"), 'WIFI:T:WPA;S:a\\;b\\,c\\:d\\\\e\\"f;P:p\\;w;;')

    def test_open_and_wep(self):
        self.assertEqual(wifi_qr.payload("Cafe", "open"), "WIFI:T:nopass;S:Cafe;;")
        self.assertEqual(wifi_qr.payload("Old", "wep", "abcde"), "WIFI:T:WEP;S:Old;P:abcde;;")

    def test_japanese_stays_as_is(self):
        self.assertEqual(wifi_qr.payload("カフェ", "password", "ぱすわーど"), "WIFI:T:WPA;S:カフェ;P:ぱすわーど;;")


class MakeTest(unittest.TestCase):
    def test_open_network_needs_no_keychain(self):
        code, body = wifi_qr.make(now=on("Cafe", "open"), read=never)
        self.assertEqual((code, body["state"], body["rows"]), (200, "ok", rows("WIFI:T:nopass;S:Cafe;;")))

    def test_password_from_the_keychain(self):
        code, body = wifi_qr.make(now=on(), read=lambda ssid: ("ok", "s3cret;pass"))
        self.assertEqual(body["rows"], rows("WIFI:T:WPA;S:Home\\; Net;P:s3cret\\;pass;;"))
        self.assertNotIn("s3cret", json.dumps(body))  # only the drawing goes to the page

    def test_refused_or_missing_asks_for_typing(self):
        for status in ("denied", "missing", "timeout"):
            _, body = wifi_qr.make(now=on(), read=lambda ssid, s=status: (s, ""))
            self.assertEqual((body["state"], body["reason"]), ("ask", status))
            self.assertNotIn("rows", body)

    def test_typed_password_skips_the_keychain(self):
        _, body = wifi_qr.make(password="typed-pass", ssid="Home; Net", now=on(), read=never)
        self.assertEqual(body["rows"], rows("WIFI:T:WPA;S:Home\\; Net;P:typed-pass;;"))
        self.assertNotIn("typed-pass", json.dumps(body))

    def test_short_typed_password(self):
        _, body = wifi_qr.make(password="short", now=on(), read=never)
        self.assertEqual((body["state"], body["reason"]), ("ask", "short"))

    def test_network_changed_since_the_page_looked(self):
        _, body = wifi_qr.make(password="typed-pass", ssid="Old Net", now=on(), read=never)
        self.assertEqual(body, {"state": "changed"})

    def test_offline_and_enterprise(self):
        self.assertEqual(wifi_qr.make(now=lambda: {"connected": False, "ssid": "", "kind": ""}, read=never)[1], {"state": "offline"})
        self.assertEqual(wifi_qr.make(now=on("Uni", "enterprise"), read=never)[1], {"state": "enterprise", "ssid": "Uni"})


if __name__ == "__main__":
    unittest.main()
