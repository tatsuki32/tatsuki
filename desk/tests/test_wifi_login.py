"""python3 -m unittest desk.tests.test_wifi_login (from the repo root).

No real network and no Safari: macOS's answers are pasted text, the Wi-Fi is a pretend sign-in page on 127.0.0.1,
and the state file goes to a temporary folder.
"""
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["WIFILOGIN_DIR"] = tempfile.mkdtemp(prefix="wifilogin-test-")
import wifi_login as w  # noqa: E402

PORTS = """
Hardware Port: Ethernet Adapter (en4)
Device: en4
Ethernet Address: 00:00:00:00:00:01

Hardware Port: Wi-Fi
Device: en1
Ethernet Address: 00:00:00:00:00:02
"""
PACKET = """op = BOOTREPLY
htype = 1
options:
dhcp_message_type (uint8): ACK 0x5
server_identifier (ip): 192.168.10.1
subnet_mask (ip): 255.255.255.0
router (ip_mult): {192.168.10.1}
domain_name_server (ip_mult): {192.168.10.1, 192.168.10.2}
end (none):
"""
SUCCESS = b"<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>"


def found(state="login", how="redirect", ssid="Cafe", router="192.168.10.1"):
    return {"state": state, "ssid": ssid, "router": router, "dns": [router], "manualDns": [], "vpn": False,
            "url": "http://192.168.10.1/login", "found": how, "at": 0}


class Reading(unittest.TestCase):
    def test_wifi_port_is_found_by_name_not_by_number(self):
        self.assertEqual(w.parse_ports(PORTS), ("Wi-Fi", "en1"))
        self.assertEqual(w.parse_ports(""), ("Wi-Fi", "en0"))

    def test_packet_gives_router_and_dns(self):
        p = w.parse_packet(PACKET)
        self.assertEqual((p["router"], p["dns"], p["portal"]), ("192.168.10.1", ["192.168.10.1", "192.168.10.2"], None))
        self.assertEqual(w.parse_packet("")["router"], None)

    def test_packet_sign_in_address_as_text_or_bytes(self):
        self.assertEqual(w.parse_packet("captive_portal_url (string): https://wifi.example.com/api\n")["portal"],
                         "https://wifi.example.com/api")
        raw = "{" + ", ".join("0x%02x" % b for b in b"https://a.example/p") + "}"
        self.assertEqual(w.parse_packet("option_114 (opaque): %s\n" % raw)["portal"], "https://a.example/p")
        self.assertIsNone(w.parse_packet("captive_portal_url (string): urn:ietf:params:capport:unrestricted\n")["portal"])

    def test_dns_typed_by_hand(self):
        self.assertEqual(w.parse_manual_dns("There aren't any DNS Servers set on Wi-Fi.\n"), [])
        self.assertEqual(w.parse_manual_dns("1.1.1.1\n2606:4700:4700::1111\n"), ["1.1.1.1", "2606:4700:4700::1111"])

    def test_route(self):
        r = w.parse_route("   route to: default\n    gateway: 10.0.0.1\n  interface: utun4\n")
        self.assertEqual((r["gateway"], r["interface"]), ("10.0.0.1", "utun4"))

    def test_only_web_addresses_reach_safari(self):
        for bad in ("file:///etc/passwd", "javascript:alert(1)", "-a Terminal", "http://", "x-apple:foo", "", None,
                    "http://a.example/\nb"):
            self.assertFalse(w.web(bad), bad)
        self.assertTrue(w.web("http://192.168.10.1/"))
        self.assertFalse(w.open_safari("file:///Applications"))


class Answers(unittest.TestCase):
    def test_success_page_means_online(self):
        self.assertEqual(w.read_probe(200, None, SUCCESS)["kind"], "online")

    def test_sent_elsewhere_is_the_sign_in_page(self):
        r = w.read_probe(302, "https://wifi.example.com/login?mac=1", b"")
        self.assertEqual(r, {"kind": "redirect", "url": "https://wifi.example.com/login?mac=1"})
        self.assertEqual(w.read_probe(307, "/portal", b"")["url"], "http://captive.apple.com/portal")
        self.assertEqual(w.read_probe(302, "javascript:x", b"")["url"], w.PROBE)

    def test_changed_page_with_the_address_inside(self):
        wispr = b"<html><!-- <WISPAccessGatewayParam><Redirect><LoginURL>https://gw.example/l?a=1&amp;b=2</LoginURL></Redirect> --></html>"
        self.assertEqual(w.read_probe(200, None, wispr)["url"], "https://gw.example/l?a=1&b=2")
        meta = b'<meta http-equiv="refresh" content="0; url=http://10.0.0.1/start">'
        self.assertEqual(w.read_probe(200, None, meta)["url"], "http://10.0.0.1/start")
        js = b'<script>window.location.href = "http://10.0.0.1/js";</script>'
        self.assertEqual(w.read_probe(200, None, js)["url"], "http://10.0.0.1/js")

    def test_changed_page_with_no_address_opens_the_test_page(self):
        self.assertEqual(w.read_probe(200, None, b"<html><form>Agree</form></html>"), {"kind": "page", "url": w.PROBE})
        self.assertEqual(w.read_probe(511, None, b"sign in")["kind"], "page")

    def test_network_told_us_the_page(self):
        self.assertEqual(w.read_api('{"captive": true, "user-portal-url": "https://p.example/"}'), "https://p.example/")
        self.assertIsNone(w.read_api('{"captive": false, "user-portal-url": "https://p.example/"}'))
        self.assertIsNone(w.read_api("not json"))


class PretendWifi(unittest.TestCase):
    """A sign-in page on this Mac stands in for the cafe: the test page gets sent to /login until 'connected'."""

    def setUp(self):
        test = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                if test.mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "/login?from=probe")
                    self.end_headers()
                    return
                body = SUCCESS if test.mode == "online" else b"<html><h1>Free Wi-Fi</h1><button>Connect</button></html>"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        try:
            self.srv = HTTPServer(("127.0.0.1", 0), H)
        except PermissionError:
            self.skipTest("cannot listen on a local port here")
        self.url = "http://127.0.0.1:%d/hotspot-detect.html" % self.srv.server_port
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)

    def test_three_kinds_of_wifi(self):
        self.mode = "redirect"
        self.assertEqual(w.probe(self.url), {"kind": "redirect", "url": self.url.rsplit("/", 1)[0] + "/login?from=probe"})
        self.mode = "inline"
        self.assertEqual(w.probe(self.url), {"kind": "page", "url": self.url})
        self.mode = "online"
        self.assertEqual(w.probe(self.url)["kind"], "online")

    def test_nothing_listening_is_not_online(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.assertIn(w.probe(self.url)["kind"], ("silent", "timeout"))

    def test_name_that_does_not_resolve(self):
        self.assertEqual(w.probe("http://no-such-host.invalid/")["kind"], "nodns")


class Watcher(unittest.TestCase):
    def test_never_when_online_or_off_wifi(self):
        self.assertFalse(w.should_open(found("online"), {}, 1000))
        self.assertFalse(w.should_open(found("nowifi"), {}, 1000))

    def test_once_per_network_within_three_minutes(self):
        r = found()
        last = {"key": w._key(r), "at": 1000}
        self.assertTrue(w.should_open(r, {}, 1000))
        self.assertFalse(w.should_open(r, last, 1000 + 179))
        self.assertTrue(w.should_open(r, last, 1000 + 181))
        self.assertTrue(w.should_open(found(ssid="Other"), last, 1001))

    def test_router_guess_waits_longer(self):
        r = found(how="router")
        self.assertFalse(w.should_open(r, {"key": w._key(r), "at": 1000}, 1000 + 600))
        self.assertTrue(w.should_open(r, {"key": w._key(r), "at": 1000}, 1000 + 1801))

    def test_state_file_keeps_no_wifi_name(self):
        r = found(ssid="Starbucks Shibuya")
        w.write_state({"key": w._key(r), "at": 5, "found": "redirect"})
        with open(os.path.join(w.DIR, "state.json")) as f:
            text = f.read()
        self.assertNotIn("Starbucks", text)
        self.assertEqual(w.read_state()["at"], 5)

    def test_auto_opens_once_then_stays_quiet(self):
        opened, real = [], (w.look, w.open_safari, w.assistant_showing)
        w.look, w.open_safari, w.assistant_showing = (lambda: found(ssid="Once")), (lambda u: opened.append(u) or True), (lambda: False)
        try:
            self.assertTrue(w.auto(sleep=lambda s: None)["opened"])
            self.assertFalse(w.auto(sleep=lambda s: None)["opened"])
            w.assistant_showing = lambda: True
            w.look = lambda: found(ssid="Elsewhere")
            self.assertFalse(w.auto(sleep=lambda s: None)["opened"])
        finally:
            w.look, w.open_safari, w.assistant_showing = real
        self.assertEqual(opened, ["http://192.168.10.1/login"])


class Words(unittest.TestCase):
    def test_reasons_only_when_something_is_in_the_way(self):
        r = dict(found(), manualDns=["1.1.1.1"], vpn=True)
        v = w.describe(r, opened=True)
        self.assertEqual(v["headline"], "This Wi-Fi needs a sign-in")
        self.assertEqual(len(v["hints"]), 2)
        self.assertIn("1.1.1.1", v["hints"][0])
        self.assertEqual(w.describe(dict(r, state="online"))["hints"], [])

    def test_router_guess_says_it_is_a_guess(self):
        self.assertIn("may not be the right one", w.describe(found(how="router"), opened=True)["line"])


if __name__ == "__main__":
    unittest.main()
