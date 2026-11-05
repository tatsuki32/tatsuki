"""回線の速さ: a run that stopped halfway is never a result, and the judgement follows the published figures."""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desk"))
import netspeed as ns

GOOD = {
    "base_rtt": 35.7, "dl_throughput": 91870296, "ul_throughput": 104689112, "dl_responsiveness": 327.4, "ul_responsiveness": 88.7,
    "dl_bytes_transferred": 209090086, "ul_bytes_transferred": 304087040, "interface_name": "en0",
}
AIRPORT = """Wi-Fi:
      en0:
          Status: Connected
          Current Network Information:
            Cafe:
              PHY Mode: 802.11ac
              Channel: 48 (5GHz, 80MHz)
              Signal / Noise: -48 dBm / -87 dBm
              Transmit Rate: 866
          Other Local Wi-Fi Networks:
            Neighbour:
              PHY Mode: 802.11
              Channel: 6 (2GHz, 20MHz)
              Signal / Noise: -90 dBm / -95 dBm
"""


def row(down, up, **more):
    return {"down": down, "up": up, "at": 1, **more}


class Parse(unittest.TestCase):
    def test_bits_become_mbps(self):
        got, error = ns.parse_result(GOOD)
        self.assertIsNone(error)
        self.assertEqual((got["down"], got["up"], got["rpmUp"]), (91.9, 104.7, 89))
        self.assertEqual(got["bytes"], 513177126)

    def test_a_run_that_stopped_halfway_is_not_a_result(self):
        got, error = ns.parse_result({**GOOD, "error_code": 1003, "error_domain": "NetworkQualityErrorDomain"})
        self.assertIsNone(got)
        self.assertIn("途中", error)

    def test_no_connection_says_so(self):
        got, error = ns.parse_result({"error_code": -1003, "error_domain": "NSURLErrorDomain"})
        self.assertIsNone(got)
        self.assertIn("つながりません", error)

    def test_missing_speed_is_not_a_result(self):
        self.assertIsNone(ns.parse_result({"base_rtt": 30})[0])

    def test_only_the_network_in_use_is_read(self):
        got = ns.parse_airport(AIRPORT)
        self.assertEqual(got["signal"], -48)
        self.assertEqual((got["band"], got["width"], got["rate"], got["phy"]), ("5GHz", 80, 866, "802.11ac"))

    def test_wifi_name(self):
        self.assertEqual(ns.parse_summary("  InterfaceType : WiFi\n  SSID : My Cafe\n"), {"wifi": True, "ssid": "My Cafe"})
        self.assertIsNone(ns.parse_summary("  InterfaceType : WiFi\n  SSID : <redacted>\n")["ssid"])
        self.assertFalse(ns.parse_summary("  InterfaceType : Ethernet\n")["wifi"])


class Judge(unittest.TestCase):
    def verdicts(self, down, up):
        return {u["id"]: ns.judge(row(down, up), u) for u in ns.USES}

    def test_fast(self):
        self.assertEqual(set(self.verdicts(92, 105).values()), {"ok"})

    def test_tight_and_short(self):
        self.assertEqual(self.verdicts(20, 5)["video"], "tight")
        self.assertEqual(self.verdicts(20, 5)["meeting"], "tight")
        self.assertEqual(self.verdicts(10, 5)["video"], "short")

    def test_a_slow_upload_shows_in_the_meeting_only(self):
        got = self.verdicts(100, 2)
        self.assertEqual((got["meeting"], got["video"]), ("short", "ok"))

    def test_weak_signal_is_named_as_the_cause(self):
        slow = ns.describe(row(10, 10, signal=-80), None)
        self.assertIn("電波が弱い", slow["sentence"])
        self.assertIn("回線の側", ns.describe(row(10, 10, signal=-50), None)["sentence"])
        self.assertNotIn("電波", ns.describe(row(10, 10), None)["sentence"])

    def test_how_many_times_the_4k_figure(self):
        self.assertIn("約6倍", ns.describe(row(92, 105), None)["sentence"])

    def test_waiting_time(self):
        self.assertEqual(ns.wait_seconds(88), 0.68)
        self.assertIsNone(ns.wait_seconds(None))


class Store(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.object(ns, "DIR", tmp.name)
        patch.start()
        self.addCleanup(patch.stop)
        ns._state.update(running=False, startedAt=None, error=None)

    def wait(self):
        for _ in range(200):
            if not ns.view()["running"]:
                return
            time.sleep(0.01)
        self.fail("still running")

    def test_compared_with_the_last_run_at_the_same_place(self):
        ns.write_history([row(50, 50, ssid="Home"), row(10, 10, ssid="Cafe"), row(80, 60, ssid="Home")])
        got = ns.view()
        self.assertEqual(got["latest"]["before"]["down"], 50)
        self.assertEqual({p["name"]: (p["count"], p["down"]) for p in got["places"]}, {"Home": (2, 65.0), "Cafe": (1, 10)})

    def test_first_run_at_a_place_has_nothing_to_compare(self):
        ns.write_history([row(50, 50, ssid="Home"), row(10, 10, ssid="Cafe")])
        self.assertNotIn("before", ns.view()["latest"])

    def test_a_failed_run_leaves_the_history_alone(self):
        ns.write_history([row(50, 50, ssid="Home")])
        with mock.patch.object(ns, "measure", return_value=(None, "止まりました")):
            self.assertTrue(ns.start())
            self.wait()
        got = ns.view()
        self.assertEqual((got["count"], got["error"]), (1, "止まりました"))

    def test_a_good_run_is_saved(self):
        with mock.patch.object(ns, "measure", return_value=(row(90, 100, ssid="Home"), None)):
            self.assertTrue(ns.start())
            self.wait()
        got = ns.view()
        self.assertEqual((got["count"], got["latest"]["down"], got["error"]), (1, 90, None))

    def test_only_one_run_at_a_time(self):
        ns._state.update(running=True)
        with mock.patch.object(ns.threading, "Thread") as thread:
            self.assertFalse(ns.start())
        thread.assert_not_called()

    def test_broken_history_reads_as_empty(self):
        with open(os.path.join(ns.DIR, "history.json"), "w") as f:
            f.write("{not json")
        got = ns.view()
        self.assertEqual((got["count"], got["latest"]), (0, None))


if __name__ == "__main__":
    unittest.main()
