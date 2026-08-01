# Development Notes

Design rationale, feature descriptions, and networking caveats for this project live here instead of in code comments (see `CLAUDE.md`).

## combine_connections.py

Load-balances outbound TCP connections across a WiFi adapter and a USB-tethered adapter by installing two equal-metric default routes, letting Windows' multi-path routing spread new connections across both.
