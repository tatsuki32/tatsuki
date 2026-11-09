"""PC 画面: sorting what runs, what may be closed, and when memory pressure speaks up. ps and friends are faked."""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desk"))
import pc_monitor as pc

ME = 501
APPS = [
    {"name": "Chrome", "pid": 100, "type": "Foreground", "bundle": "com.google.Chrome", "bundlePath": "/Applications/Google Chrome.app"},
    {"name": "Finder", "pid": 110, "type": "Foreground", "bundle": "com.apple.finder", "bundlePath": "/System/Library/CoreServices/Finder.app"},
    {"name": "Claude", "pid": 120, "type": "Foreground", "bundle": "com.anthropic.claude", "bundlePath": "/Applications/Claude.app"},
    {"name": "Menu", "pid": 130, "type": "UIElement", "bundle": "com.example.menu", "bundlePath": "/Applications/Menu.app"},
    {"name": "Claude Helper", "pid": 121, "type": "UIElement", "bundle": "com.anthropic.claude.helper", "bundlePath": "/Applications/Claude.app/Contents/Frameworks/Claude Helper.app"},
    {"name": "ControlCenter", "pid": 140, "type": "UIElement", "bundle": "com.apple.controlcenter", "bundlePath": "/System/Library/CoreServices/ControlCenter.app"},
]


def proc(pid, path, mem=100, cpu=1.0, uid=ME, ppid=1):
    return {"pid": pid, "ppid": ppid, "mem": mem, "cpu": cpu, "uid": uid, "started": 1000 + pid, "path": path}


PROCS = [
    proc(100, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", 300),
    proc(101, "/Applications/Google Chrome.app/Contents/Frameworks/Helper.app/Contents/MacOS/Helper", 200, ppid=100),
    proc(110, "/System/Library/CoreServices/Finder.app/Contents/MacOS/Finder"),
    proc(120, "/Applications/Claude.app/Contents/MacOS/Claude"),
    proc(121, "/Applications/Claude.app/Contents/Frameworks/Claude Helper.app/Contents/MacOS/Claude Helper", ppid=120),
    proc(130, "/Applications/Menu.app/Contents/MacOS/Menu"),
    proc(140, "/System/Library/CoreServices/ControlCenter.app/Contents/MacOS/ControlCenter", 50),
    proc(200, "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python", 70),
    proc(201, "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python", 80),
    proc(202, "/opt/homebrew/bin/node", 60),
    proc(300, "/usr/libexec/trustd", 40),
    proc(301, "/bin/zsh", 10),
    proc(303, "<defunct>", 0),
    proc(1, "/sbin/launchd", 30, uid=0),
    proc(302, "/Applications/Other.app/Contents/MacOS/Root", 20, uid=0),
]


def sorted_items(own=frozenset()):
    return pc.group(PROCS, APPS, set(own), ME)


class Group(unittest.TestCase):
    def setUp(self):
        self.items, self.system = sorted_items()
        self.by = {i["name"]: i for i in self.items}

    def test_helpers_count_towards_their_app(self):
        chrome = self.by["Chrome"]
        self.assertEqual((chrome["kind"], chrome["mem"], chrome["count"], chrome["pid"]), ("app", 500, 2, 100))

    def test_a_registered_helper_stays_with_its_app(self):
        self.assertNotIn("Claude Helper", self.by)
        self.assertEqual((self.by["Claude"]["count"], self.by["Claude"]["pid"]), (2, 120))

    def test_menu_bar_apps_are_tools_closed_as_apps(self):
        self.assertEqual((self.by["Menu"]["kind"], self.by["Menu"]["appkit"]), ("tool", True))

    def test_each_python_run_is_its_own_tool(self):
        pythons = [i for i in self.items if i["name"] == "Python"]
        self.assertEqual(sorted(i["pid"] for i in pythons), [200, 201])
        self.assertTrue(all(not i["appkit"] for i in pythons))

    def test_system_only_counts(self):
        self.assertNotIn("ControlCenter", self.by)
        self.assertNotIn("trustd", self.by)
        self.assertEqual(self.system["mem"], 50 + 40 + 10 + 30 + 20)

    def test_protection_and_warning(self):
        self.assertTrue(self.by["Finder"]["protected"])
        self.assertFalse(self.by["Chrome"]["protected"])
        self.assertTrue(self.by["Claude"]["warn"])
        self.assertFalse(self.by["Chrome"]["warn"])

    def test_this_server_is_protected(self):
        items, _ = sorted_items(own={202})
        self.assertTrue(next(i for i in items if i["name"] == "node")["protected"])


class Close(unittest.TestCase):
    def setUp(self):
        pc.ASKED.clear()
        self.listing = mock.patch.object(pc, "listing", side_effect=lambda: sorted_items())
        self.listing.start()
        self.addCleanup(self.listing.stop)

    def test_refuses_what_it_should(self):
        self.assertEqual(pc.close("100", 1100)[0], 400)
        self.assertEqual(pc.close(1, 1001)[0], 400)
        self.assertEqual(pc.close(999, 1999)[0], 404)
        self.assertEqual(pc.close(300, 1300)[0], 404)  # the system is not listed
        self.assertEqual(pc.close(110, 1110)[0], 403)

    def test_reused_pid_closes_nothing(self):
        with mock.patch.object(pc, "stop") as stop:
            self.assertEqual(pc.close(202, 9999)[0], 409)
        stop.assert_not_called()

    def test_force_only_after_five_seconds(self):
        with mock.patch.object(pc, "stop", return_value=True) as stop:
            self.assertEqual(pc.close(202, 1202, force=True, now=100)[0], 409)
            self.assertEqual(pc.close(202, 1202, now=100)[0], 200)
            self.assertEqual(pc.close(202, 1202, force=True, now=104)[0], 409)
            self.assertEqual(pc.close(202, 1202, force=True, now=105)[0], 200)
        self.assertEqual([c.args[1] for c in stop.call_args_list], [False, True])

    def test_tool_gets_the_signal(self):
        with mock.patch.object(pc.os, "kill") as kill:
            self.assertEqual(pc.close(202, 1202, now=100)[0], 200)
        kill.assert_called_once_with(202, pc.signal.SIGTERM)

    def test_missing_helper_says_so(self):
        with mock.patch.object(pc, "helper", return_value=None):
            self.assertEqual(pc.close(100, 1100, now=100)[0], 503)


class Memory(unittest.TestCase):
    def test_reads_like_activity_monitor(self):
        vm = "Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages wired down:   10.\nPages purgeable:   5.\nAnonymous pages:   100.\nPages occupied by compressor:   20.\n"
        answers = {"hw.memsize": "17179869184", "vm.swapusage": "total = 2048.00M  used = 1024.00M  free = 1024.00M", "kern.memorystatus_vm_pressure_level": "2"}
        with mock.patch.object(pc, "run", return_value=vm), mock.patch.object(pc, "sysctl", side_effect=answers.get):
            m = pc.memory()
        self.assertEqual(m, {"used": 125 * 16384, "total": 17179869184, "swap": 1024 << 20, "pressure": "warn"})

    def test_unreadable_is_empty_not_a_crash(self):
        with mock.patch.object(pc, "run", return_value=""), mock.patch.object(pc, "sysctl", return_value=""):
            self.assertEqual(pc.memory(), {"used": None, "total": None, "swap": None, "pressure": None})


class Alert(unittest.TestCase):
    def setUp(self):
        pc.ALERT.update(since=None, alert=False, notified=0)
        self.notify = mock.Mock()

    def test_speaks_after_two_minutes_and_not_again_for_thirty(self):
        start = 10000
        pc.tick("warn", start, self.notify)
        pc.tick("warn", start + 60, self.notify)
        self.assertFalse(pc.ALERT["alert"])
        pc.tick("warn", start + 120, self.notify)
        self.assertTrue(pc.ALERT["alert"])
        pc.tick("critical", start + 1000, self.notify)
        self.assertEqual(self.notify.call_count, 1)
        pc.tick("critical", start + 120 + 1800, self.notify)
        self.assertEqual(self.notify.call_count, 2)

    def test_calming_down_clears_it(self):
        pc.tick("warn", 10000, self.notify)
        pc.tick("warn", 10120, self.notify)
        pc.tick("normal", 10180, self.notify)
        self.assertFalse(pc.ALERT["alert"])
        pc.tick("warn", 10240, self.notify)
        self.assertFalse(pc.ALERT["alert"])


if __name__ == "__main__":
    unittest.main()
