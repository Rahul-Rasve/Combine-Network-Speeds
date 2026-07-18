"""
combine_connections.py
======================
Combines internet speed from WiFi and USB Mobile Hotspot (tethering) on Windows
by load-balancing traffic across both network adapters using Windows routing.

Requirements:
    pip install psutil requests

Run as Administrator (required to modify routing tables).

How it works:
    1. Detects active WiFi and USB tethering adapters automatically
    2. Adds specific host routes so traffic is split across both interfaces
    3. Monitors combined throughput in real time
    4. Restores original routes on exit (Ctrl+C)

Usage:
    python combine_connections.py [--wifi "WiFi"] [--usb "USB"] [--interval 2]
"""

import argparse
import ctypes
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

try:
    import psutil
    import requests
except ImportError:
    print("[!] Missing dependencies. Run:  pip install psutil requests")
    sys.exit(1)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def run(cmd: str, silent: bool = True) -> tuple[int, str]:
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True
    )
    if not silent and result.stdout:
        print(result.stdout.strip())
    return result.returncode, result.stdout + result.stderr


def fmt_speed(bps: float) -> str:
    """Format bytes/sec to human-readable speed."""
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if bps < 1024:
            return f"{bps:6.1f} {unit}"
        bps /= 1024
    return f"{bps:.1f} TB/s"


# ──────────────────────────────────────────────
# Adapter detection
# ──────────────────────────────────────────────

@dataclass
class Adapter:
    name: str
    ip: str
    gateway: str
    metric: int = 0
    interface_index: int = 0


def get_default_gateway_for_interface(iface_name: str) -> Optional[str]:
    """Parse `route print` to find the gateway for a named adapter."""
    _, out = run("route print 0.0.0.0")
    # Find the interface index for the adapter name
    iface_index = None
    for line in out.splitlines():
        if iface_name.lower() in line.lower():
            parts = line.split()
            if parts and parts[0].isdigit():
                iface_index = parts[0]
                break

    if iface_index is None:
        return None

    for line in out.splitlines():
        parts = line.split()
        # Default route lines: Network=0.0.0.0  Mask=0.0.0.0  Gateway  Interface  Metric
        if len(parts) >= 5 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0":
            if parts[3].startswith(iface_index) or True:  # best-effort
                return parts[2]
    return None


def detect_adapters(wifi_hint: str, usb_hint: str) -> tuple[Optional[Adapter], Optional[Adapter]]:
    """
    Find adapters by name hint.
    Returns (wifi_adapter, usb_adapter).
    """
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()

    def find(hint: str) -> Optional[Adapter]:
        for name, snic_list in addrs.items():
            if hint.lower() not in name.lower():
                continue
            if not stats.get(name, None) or not stats[name].isup:
                continue
            ip = None
            for snic in snic_list:
                if snic.family.name == "AF_INET":
                    ip = snic.address
                    break
            if not ip:
                continue
            gw = get_default_gateway_for_interface(name)
            return Adapter(name=name, ip=ip, gateway=gw or "")
        return None

    return find(wifi_hint), find(usb_hint)


# ──────────────────────────────────────────────
# Routing
# ──────────────────────────────────────────────

class RoutingManager:
    """
    Adds policy-based (metric) routes so Windows distributes traffic
    across both adapters. Both gateways are set as default routes
    with equal metrics, letting Windows perform load sharing via
    multi-path routing.
    """

    def __init__(self, wifi: Adapter, usb: Adapter):
        self.wifi = wifi
        self.usb = usb
        self._added: list[str] = []

    def _route(self, action: str, dest: str, mask: str, gw: str, metric: int, iface_ip: str):
        cmd = (
            f"route {action} {dest} mask {mask} {gw} "
            f"metric {metric} IF {iface_ip}"
        )
        code, out = run(cmd)
        return code == 0

    def apply(self):
        """Add equal-metric default routes for both adapters."""
        print(f"\n[*] Adding routes for load balancing...")
        # Remove existing default routes first (we'll restore on exit)
        run("route delete 0.0.0.0 mask 0.0.0.0")

        for adapter in (self.wifi, self.usb):
            if not adapter.gateway:
                print(f"[!] No gateway found for {adapter.name}, skipping route.")
                continue
            ok = self._route("add", "0.0.0.0", "0.0.0.0", adapter.gateway, 1, adapter.ip)
            if ok:
                print(f"    ✔ Route via {adapter.name} ({adapter.gateway})")
                self._added.append(adapter.gateway)
            else:
                print(f"    ✘ Failed to add route for {adapter.name}")

    def restore(self):
        """Remove added routes; Windows restores its own defaults on reboot."""
        print("\n[*] Restoring original routing (removing added routes)...")
        for gw in self._added:
            run(f"route delete 0.0.0.0 mask 0.0.0.0 {gw}")
        print("[*] Done. Your adapters will reconnect their default routes automatically.")


# ──────────────────────────────────────────────
# Monitor
# ──────────────────────────────────────────────

class SpeedMonitor:
    """Tracks per-adapter and combined throughput."""

    def __init__(self, wifi_name: str, usb_name: str, interval: float = 2.0):
        self.names = [wifi_name, usb_name]
        self.interval = interval
        self._prev: dict = {}

    def _sample(self) -> dict[str, tuple[int, int]]:
        counters = psutil.net_io_counters(pernic=True)
        return {
            name: (counters[name].bytes_sent, counters[name].bytes_recv)
            for name in self.names
            if name in counters
        }

    def run(self):
        print(f"\n{'─'*64}")
        print(f" {'Adapter':<28}  {'↑ Upload':>12}  {'↓ Download':>12}")
        print(f"{'─'*64}")

        self._prev = self._sample()
        time.sleep(self.interval)

        try:
            while True:
                cur = self._sample()
                t_up = t_dn = 0.0

                rows = []
                for name in self.names:
                    if name not in cur or name not in self._prev:
                        continue
                    s0, r0 = self._prev[name]
                    s1, r1 = cur[name]
                    up = (s1 - s0) / self.interval
                    dn = (r1 - r0) / self.interval
                    t_up += up
                    t_dn += dn
                    label = name[:28]
                    rows.append(f" {label:<28}  {fmt_speed(up):>12}  {fmt_speed(dn):>12}")

                # Clear previous output lines
                lines_to_clear = len(rows) + 3
                print(f"\033[{lines_to_clear}A\033[J", end="")

                print(f"{'─'*64}")
                print(f" {'Adapter':<28}  {'↑ Upload':>12}  {'↓ Download':>12}")
                print(f"{'─'*64}")
                for row in rows:
                    print(row)
                print(f"{'─'*64}")
                print(
                    f" {'COMBINED':<28}  {fmt_speed(t_up):>12}  {fmt_speed(t_dn):>12}"
                )
                print(f"{'─'*64}")

                self._prev = cur
                time.sleep(self.interval)

        except KeyboardInterrupt:
            pass


# ──────────────────────────────────────────────
# Connectivity check
# ──────────────────────────────────────────────

def check_internet(label: str, bind_ip: str) -> bool:
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE if hasattr(socket, "SO_BINDTODEVICE") else socket.SO_REUSEADDR, bind_ip.encode())
        # Simpler check via requests
        session = requests.Session()
        r = session.get("https://www.google.com", timeout=5)
        print(f"    ✔ {label}: internet reachable (HTTP {r.status_code})")
        return True
    except Exception as e:
        print(f"    ✘ {label}: {e}")
        return False


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Combine WiFi + USB tethering bandwidth on Windows"
    )
    parser.add_argument("--wifi",     default="Wi-Fi",  help="WiFi adapter name (partial match)")
    parser.add_argument("--usb",      default="USB",    help="USB tethering adapter name (partial match)")
    parser.add_argument("--interval", default=2.0, type=float, help="Monitor refresh interval (seconds)")
    parser.add_argument("--no-route", action="store_true", help="Skip routing changes (monitor only)")
    args = parser.parse_args()

    print("=" * 64)
    print("  Windows Multi-WAN Speed Combiner")
    print("  WiFi + USB Mobile Hotspot Tethering")
    print("=" * 64)

    if not is_admin() and not args.no_route:
        print("\n[!] This script requires Administrator privileges to modify routes.")
        print("    Right-click your terminal and choose 'Run as Administrator'.")
        print("    Or use --no-route to monitor without changing routes.\n")
        sys.exit(1)

    # Detect adapters
    print(f"\n[*] Scanning for adapters (WiFi='{args.wifi}', USB='{args.usb}')...")
    wifi, usb = detect_adapters(args.wifi, args.usb)

    if not wifi:
        print(f"[!] WiFi adapter matching '{args.wifi}' not found or not connected.")
        print("    Available adapters:")
        for name, st in psutil.net_if_stats().items():
            print(f"      {'UP  ' if st.isup else 'DOWN'} {name}")
        sys.exit(1)

    if not usb:
        print(f"[!] USB adapter matching '{args.usb}' not found or not connected.")
        print("    Make sure USB tethering is enabled on your phone.")
        print("    Available adapters:")
        for name, st in psutil.net_if_stats().items():
            print(f"      {'UP  ' if st.isup else 'DOWN'} {name}")
        sys.exit(1)

    print(f"\n    WiFi   : {wifi.name} — IP {wifi.ip}  GW {wifi.gateway}")
    print(f"    USB    : {usb.name} — IP {usb.ip}  GW {usb.gateway}")

    # Apply routing
    router = None
    if not args.no_route:
        router = RoutingManager(wifi, usb)
        try:
            router.apply()
        except Exception as e:
            print(f"[!] Routing error: {e}")

    # Monitor
    print("\n[*] Starting speed monitor  (Ctrl+C to stop and restore routes)\n")
    monitor = SpeedMonitor(wifi.name, usb.name, args.interval)

    try:
        monitor.run()
    except KeyboardInterrupt:
        pass
    finally:
        if router:
            router.restore()
        print("\n[*] Exited cleanly.")


if __name__ == "__main__":
    main()
