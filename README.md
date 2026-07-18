# combine_connections.py

Load-balance internet traffic across your **WiFi** and **USB mobile hotspot (tethering)** simultaneously on Windows. Multiple connections run in parallel across both adapters, effectively combining their bandwidth for multi-connection workloads like downloads, streaming, and browsing.

---

## How it works

Windows supports multi-path routing: two default gateway routes with equal metrics cause the OS to distribute outgoing TCP connections across both adapters. This script automates that process.

> **Note:** This is **connection-level** bonding — each individual TCP connection still uses one adapter, but parallel connections (e.g. multiple downloads, browser tabs, API calls) are spread across both. True byte-level bonding requires a third-party VPN service such as [Speedify](https://speedify.com).

---

## Requirements

| Requirement | Detail |
|---|---|
| OS | Windows 10 or Windows 11 |
| Python | 3.10 or later |
| Privileges | **Administrator** (required to modify routing tables) |
| Phone | USB tethering enabled in phone settings |
| Adapters | Both WiFi and USB tethering must be connected and active |

---

## Installation

**1. Install Python (if not already installed)**

Download from [python.org](https://www.python.org/downloads/) and ensure "Add Python to PATH" is checked during setup.

**2. Install dependencies**

Open a terminal and run:

```
pip install psutil requests
```

**3. Place the script**

Save `combine_connections.py` anywhere convenient, for example:

```
C:\Tools\combine_connections.py
```

---

## Setup — Enable USB tethering on your phone

**Android:**
Settings → Network & Internet → Hotspot & tethering → USB tethering → toggle ON

**iPhone:**
Settings → Personal Hotspot → Also allow others to join → connect via USB

Windows will install the adapter automatically. It usually appears as **Remote NDIS** or **Ethernet 2** in Device Manager.

---

## Running the script

Always run from an **Administrator terminal**.

**Open Administrator terminal:**
- Press `Win + S`, search for **Command Prompt** or **PowerShell**
- Right-click → **Run as administrator**

**Basic usage (auto-detect adapters):**

```
python combine_connections.py
```

**Specify adapter names manually:**

```
python combine_connections.py --wifi "Wi-Fi" --usb "Remote NDIS"
```

**Monitor only (no routing changes):**

```
python combine_connections.py --no-route
```

**Stop the script:**

Press `Ctrl+C` — original routes are restored automatically.

---

## Command-line options

| Flag | Default | Description |
|---|---|---|
| `--wifi <name>` | `Wi-Fi` | Partial name of the WiFi adapter |
| `--usb <name>` | `USB` | Partial name of the USB tethering adapter |
| `--interval <seconds>` | `2.0` | Speed monitor refresh interval |
| `--no-route` | off | Skip routing changes; monitor throughput only |

---

## Supported commands

### Find your adapter names

List all network adapters and their status:

```
netsh interface show interface
```

Or using PowerShell:

```powershell
Get-NetAdapter | Select-Object Name, Status, LinkSpeed
```

### View the current routing table

```
route print
```

Look for `0.0.0.0` entries under **IPv4 Route Table → Active Routes** to see default gateway routes.

### Manually add a route (what the script does internally)

```
route add 0.0.0.0 mask 0.0.0.0 <GATEWAY_IP> metric 1 IF <INTERFACE_IP>
```

Example:

```
route add 0.0.0.0 mask 0.0.0.0 192.168.1.1   metric 1 IF 192.168.1.100
route add 0.0.0.0 mask 0.0.0.0 192.168.42.129 metric 1 IF 192.168.42.100
```

### Manually delete a route

```
route delete 0.0.0.0 mask 0.0.0.0 <GATEWAY_IP>
```

### Reset all routes to default (if something goes wrong)

```
netsh int ip reset
netsh winsock reset
```

Then reboot. Windows will re-establish routes from each connected adapter automatically.

### Flush DNS after switching routes

```
ipconfig /flushdns
```

### Release and renew an adapter's IP

```
ipconfig /release "Wi-Fi"
ipconfig /renew "Wi-Fi"
```

### Ping through a specific adapter

```
ping 8.8.8.8 -S <ADAPTER_IP>
```

Example to test USB tethering path:

```
ping 8.8.8.8 -S 192.168.42.100
```

---

## Troubleshooting

**Adapter not detected**

Run `netsh interface show interface` and pass the exact name fragment:

```
python combine_connections.py --usb "NDIS"
```

**No internet after running the script**

The script may have removed your default route before adding new ones. Run:

```
route delete 0.0.0.0
ipconfig /release
ipconfig /renew
```

Or simply reboot — Windows restores adapter routes on reconnect.

**USB tethering adapter not appearing in Windows**

- Try a different USB cable (data cable, not charge-only)
- On Android, toggle USB tethering off and on again
- Check Device Manager for a "Remote NDIS" or "Android" device with a warning icon — reinstall its driver if needed

**Routes added but speed not improved**

- Open multiple simultaneous downloads (e.g. via a download manager) to generate parallel connections
- Run `route print` and confirm two `0.0.0.0` default routes with metric `1` are listed
- Use `--no-route` mode to verify both adapters are showing traffic in the monitor

---

## Example output

```
────────────────────────────────────────────────────────────────
 Adapter                        ↑ Upload      ↓ Download
────────────────────────────────────────────────────────────────
 Wi-Fi                           128.4 KB/s    3.2 MB/s
 USB Tethering (Remote NDIS)      64.1 KB/s    1.8 MB/s
────────────────────────────────────────────────────────────────
 COMBINED                        192.5 KB/s    5.0 MB/s
────────────────────────────────────────────────────────────────
```

---

## Uninstall / cleanup

The script restores routes on exit (`Ctrl+C`). No permanent system changes are made. To fully remove:

1. Delete `combine_connections.py`
2. Run `pip uninstall psutil requests` if no longer needed
