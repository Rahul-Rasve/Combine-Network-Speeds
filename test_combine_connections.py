"""Tests for combine_connections.py. Run: python -m unittest test_combine_connections -v"""

import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import combine_connections as cc


class FakeRun:
    """Stand-in for combine_connections.run(): records calls, returns canned (code, out)."""

    def __init__(self, responses=None, default=(0, "")):
        self.calls: list[str] = []
        self.responses = responses or {}
        self.default = default

    def __call__(self, cmd: str, silent: bool = True):
        self.calls.append(cmd)
        return self.responses.get(cmd, self.default)


ROUTE_PRINT_TWO_DEFAULTS = """
===========================================================================
Interface List
 15...00 15 5d 01 02 03 ......Wi-Fi
 12...00 15 5d 04 05 06 ......Ethernet
===========================================================================

IPv4 Route Table
===========================================================================
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0      192.168.1.1    192.168.1.50     25
          0.0.0.0          0.0.0.0       10.0.0.1        10.0.0.5     35
===========================================================================
Persistent Routes:
  Network Address          Netmask  Gateway Address  Metric
          0.0.0.0          0.0.0.0      192.168.1.1  Default
===========================================================================
"""


def fake_snic(address: str):
    return SimpleNamespace(family=SimpleNamespace(name="AF_INET"), address=address)


class DefaultRoutesTests(unittest.TestCase):
    def test_parses_active_default_routes_only(self):
        fake_run = FakeRun({"route print 0.0.0.0": (0, ROUTE_PRINT_TWO_DEFAULTS)})
        with patch("combine_connections.run", fake_run):
            routes = cc._default_routes()
        self.assertEqual(
            routes,
            [("192.168.1.1", "192.168.1.50", "25"), ("10.0.0.1", "10.0.0.5", "35")],
        )


class GatewayLookupTests(unittest.TestCase):
    def test_matches_by_local_ip(self):
        routes = [("192.168.1.1", "192.168.1.50", "25"), ("10.0.0.1", "10.0.0.5", "35")]
        self.assertEqual(cc.get_default_gateway_for_interface("192.168.1.50", routes), "192.168.1.1")
        self.assertEqual(cc.get_default_gateway_for_interface("10.0.0.5", routes), "10.0.0.1")

    def test_returns_none_when_no_match(self):
        routes = [("192.168.1.1", "192.168.1.50", "25")]
        self.assertIsNone(cc.get_default_gateway_for_interface("9.9.9.9", routes))


class FmtSpeedTests(unittest.TestCase):
    def test_kilobytes(self):
        self.assertEqual(cc.fmt_speed(2048).split(), ["2.0", "KB/s"])

    def test_megabytes(self):
        self.assertEqual(cc.fmt_speed(3 * 1024**2).split(), ["3.0", "MB/s"])

    def test_terabytes_fallback(self):
        self.assertEqual(cc.fmt_speed(2 * 1024**4).split(), ["2.0", "TB/s"])


class DetectAdaptersTests(unittest.TestCase):
    def test_finds_wifi_and_lan_by_hint_with_single_route_scan(self):
        addrs = {"Wi-Fi": [fake_snic("192.168.1.50")], "Ethernet": [fake_snic("10.0.0.5")]}
        stats = {name: SimpleNamespace(isup=True) for name in addrs}
        fake_run = FakeRun({"route print 0.0.0.0": (0, ROUTE_PRINT_TWO_DEFAULTS)})
        with patch("combine_connections.psutil.net_if_addrs", return_value=addrs), \
             patch("combine_connections.psutil.net_if_stats", return_value=stats), \
             patch("combine_connections.run", fake_run):
            wifi, usb = cc.detect_adapters("Wi-Fi", "Ethernet")
        self.assertEqual((wifi.name, wifi.gateway), ("Wi-Fi", "192.168.1.1"))
        self.assertEqual((usb.name, usb.gateway), ("Ethernet", "10.0.0.1"))
        self.assertEqual(fake_run.calls.count("route print 0.0.0.0"), 1)

    def test_skips_virtual_adapter_and_finds_real_ethernet(self):
        addrs = {
            "Wi-Fi": [fake_snic("192.168.1.50")],
            "vEthernet (Default Switch)": [fake_snic("172.20.0.1")],
            "Ethernet": [fake_snic("10.0.0.5")],
        }
        stats = {name: SimpleNamespace(isup=True) for name in addrs}
        fake_run = FakeRun({"route print 0.0.0.0": (0, ROUTE_PRINT_TWO_DEFAULTS)})
        with patch("combine_connections.psutil.net_if_addrs", return_value=addrs), \
             patch("combine_connections.psutil.net_if_stats", return_value=stats), \
             patch("combine_connections.run", fake_run):
            _, usb = cc.detect_adapters("Wi-Fi", "Ethernet")
        self.assertEqual(usb.name, "Ethernet")

    def test_falls_back_to_lan_when_usb_hint_not_found(self):
        addrs = {"Wi-Fi": [fake_snic("192.168.1.50")], "Ethernet": [fake_snic("10.0.0.5")]}
        stats = {name: SimpleNamespace(isup=True) for name in addrs}
        fake_run = FakeRun({"route print 0.0.0.0": (0, ROUTE_PRINT_TWO_DEFAULTS)})
        with patch("combine_connections.psutil.net_if_addrs", return_value=addrs), \
             patch("combine_connections.psutil.net_if_stats", return_value=stats), \
             patch("combine_connections.run", fake_run):
            _, usb = cc.detect_adapters("Wi-Fi", "USB")
        self.assertEqual(usb.name, "Ethernet")

    def test_wifi_and_usb_never_resolve_to_same_adapter(self):
        addrs = {"NetAdapter": [fake_snic("192.168.1.50")]}
        stats = {"NetAdapter": SimpleNamespace(isup=True)}
        fake_run = FakeRun({"route print 0.0.0.0": (0, "")})
        with patch("combine_connections.psutil.net_if_addrs", return_value=addrs), \
             patch("combine_connections.psutil.net_if_stats", return_value=stats), \
             patch("combine_connections.run", fake_run):
            wifi, usb = cc.detect_adapters("Net", "Net")
        self.assertEqual(wifi.name, "NetAdapter")
        self.assertIsNone(usb)

    def test_skips_down_adapter(self):
        addrs = {"Wi-Fi": [fake_snic("192.168.1.50")]}
        stats = {"Wi-Fi": SimpleNamespace(isup=False)}
        fake_run = FakeRun({"route print 0.0.0.0": (0, "")})
        with patch("combine_connections.psutil.net_if_addrs", return_value=addrs), \
             patch("combine_connections.psutil.net_if_stats", return_value=stats), \
             patch("combine_connections.run", fake_run):
            wifi, _ = cc.detect_adapters("Wi-Fi", "USB")
        self.assertIsNone(wifi)

    def test_skips_apipa_address(self):
        addrs = {"Wi-Fi": [fake_snic("169.254.1.2")]}
        stats = {"Wi-Fi": SimpleNamespace(isup=True)}
        fake_run = FakeRun({"route print 0.0.0.0": (0, "")})
        with patch("combine_connections.psutil.net_if_addrs", return_value=addrs), \
             patch("combine_connections.psutil.net_if_stats", return_value=stats), \
             patch("combine_connections.run", fake_run):
            wifi, _ = cc.detect_adapters("Wi-Fi", "USB")
        self.assertIsNone(wifi)


class RoutingManagerApplyTests(unittest.TestCase):
    def test_no_gateway_on_either_adapter_leaves_routing_untouched(self):
        wifi = cc.Adapter(name="Wi-Fi", ip="192.168.1.50", gateway="")
        usb = cc.Adapter(name="Ethernet", ip="10.0.0.5", gateway="")
        fake_run = FakeRun()
        router = cc.RoutingManager(wifi, usb)
        with patch("combine_connections.run", fake_run):
            router.apply()
        self.assertEqual(fake_run.calls, [])
        self.assertEqual(router._original, [])
        self.assertEqual(router._added, [])

    def test_adds_route_per_adapter_and_snapshots_original(self):
        wifi = cc.Adapter(name="Wi-Fi", ip="192.168.1.50", gateway="192.168.1.1")
        usb = cc.Adapter(name="Ethernet", ip="10.0.0.5", gateway="10.0.0.1")
        fake_run = FakeRun({"route print 0.0.0.0": (0, ROUTE_PRINT_TWO_DEFAULTS)})
        router = cc.RoutingManager(wifi, usb)
        with patch("combine_connections.run", fake_run):
            router.apply()
        self.assertEqual(
            router._original,
            [("192.168.1.1", "192.168.1.50", "25"), ("10.0.0.1", "10.0.0.5", "35")],
        )
        self.assertEqual(router._added, ["192.168.1.1", "10.0.0.1"])
        self.assertIn("route delete 0.0.0.0 mask 0.0.0.0", fake_run.calls)
        self.assertIn(
            "route add 0.0.0.0 mask 0.0.0.0 192.168.1.1 metric 1 IF 192.168.1.50", fake_run.calls
        )
        self.assertIn(
            "route add 0.0.0.0 mask 0.0.0.0 10.0.0.1 metric 1 IF 10.0.0.5", fake_run.calls
        )

    def test_only_successful_route_add_is_recorded(self):
        wifi = cc.Adapter(name="Wi-Fi", ip="192.168.1.50", gateway="192.168.1.1")
        usb = cc.Adapter(name="Ethernet", ip="10.0.0.5", gateway="10.0.0.1")
        fail_cmd = "route add 0.0.0.0 mask 0.0.0.0 10.0.0.1 metric 1 IF 10.0.0.5"
        fake_run = FakeRun({"route print 0.0.0.0": (0, ""), fail_cmd: (1, "error")})
        router = cc.RoutingManager(wifi, usb)
        with patch("combine_connections.run", fake_run):
            router.apply()
        self.assertEqual(router._added, ["192.168.1.1"])


class RoutingManagerRestoreTests(unittest.TestCase):
    def test_noop_when_nothing_was_applied(self):
        router = cc.RoutingManager(
            cc.Adapter(name="Wi-Fi", ip="1.1.1.1", gateway=""),
            cc.Adapter(name="Ethernet", ip="2.2.2.2", gateway=""),
        )
        fake_run = FakeRun()
        with patch("combine_connections.run", fake_run):
            out = io.StringIO()
            with redirect_stdout(out):
                router.restore()
        self.assertEqual(fake_run.calls, [])
        self.assertEqual(out.getvalue(), "")

    def test_restores_original_routes_after_removing_added_ones(self):
        router = cc.RoutingManager(
            cc.Adapter(name="Wi-Fi", ip="192.168.1.50", gateway="192.168.1.1"),
            cc.Adapter(name="Ethernet", ip="10.0.0.5", gateway="10.0.0.1"),
        )
        router._added = ["192.168.1.1", "10.0.0.1"]
        router._original = [("192.168.1.1", "192.168.1.50", "25")]
        fake_run = FakeRun()
        with patch("combine_connections.run", fake_run):
            out = io.StringIO()
            with redirect_stdout(out):
                router.restore()
        self.assertIn("route delete 0.0.0.0 mask 0.0.0.0 192.168.1.1", fake_run.calls)
        self.assertIn("route delete 0.0.0.0 mask 0.0.0.0 10.0.0.1", fake_run.calls)
        self.assertIn(
            "route add 0.0.0.0 mask 0.0.0.0 192.168.1.1 metric 25 IF 192.168.1.50", fake_run.calls
        )
        self.assertIn("restored", out.getvalue().lower())
        self.assertNotIn("failed", out.getvalue().lower())

    def test_reports_failure_instead_of_claiming_success(self):
        router = cc.RoutingManager(
            cc.Adapter(name="Wi-Fi", ip="192.168.1.50", gateway="192.168.1.1"),
            cc.Adapter(name="Ethernet", ip="10.0.0.5", gateway="10.0.0.1"),
        )
        router._added = []
        router._original = [("192.168.1.1", "192.168.1.50", "25")]
        fail_cmd = "route add 0.0.0.0 mask 0.0.0.0 192.168.1.1 metric 25 IF 192.168.1.50"
        fake_run = FakeRun({fail_cmd: (1, "error")})
        with patch("combine_connections.run", fake_run):
            out = io.StringIO()
            with redirect_stdout(out):
                router.restore()
        self.assertIn("failed", out.getvalue().lower())
        self.assertNotIn("done. original", out.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
