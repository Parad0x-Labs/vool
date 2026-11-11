"""Is the person's own VPN app carrying this computer's traffic right now?

Used by Private web's "My system VPN" choice (ExpressVPN and any VPN app VOOL does not run
itself). The check asks the computer which network interface a packet to the internet would
leave by, without sending anything:

* macOS: `route -n get default`
* Linux: `ip route get 1.1.1.1` (follows the policy rules WireGuard and most VPN apps use),
  or the main table in /proc/net/route when `ip` is missing.

A tunnel interface (utun, tun, wg, ppp, ipsec, and the names VPN apps give theirs) means the VPN
is carrying traffic. Anything else, or any doubt, means it is not, and Private web refuses.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

_TUNNEL_PREFIXES = (
    "utun", "tun", "tap", "wg", "ppp", "ipsec", "nordlynx", "proton", "pvpn", "mullvad",
    "expressvpn", "ivpn", "surfshark", "windscribe", "tailscale", "cscotun", "gpd",
)


def is_tunnel_interface(name: str) -> bool:
    lowered = str(name or "").strip().lower()
    return bool(lowered) and lowered.startswith(_TUNNEL_PREFIXES)


def _run(argv: list[str]) -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout if done.returncode == 0 else ""


def default_route_interface() -> str:
    """The interface internet traffic leaves by, or "" when it cannot be told."""
    if sys.platform == "darwin":
        out = _run(["/sbin/route", "-n", "get", "default"])
        match = re.search(r"^\s*interface:\s*(\S+)", out, re.M)
        return match.group(1) if match else ""
    if sys.platform.startswith("linux"):
        ip = shutil.which("ip")
        if ip:
            out = _run([ip, "route", "get", "1.1.1.1"])
            match = re.search(r"\bdev\s+(\S+)", out)
            return match.group(1) if match else ""
        try:
            lines = Path("/proc/net/route").read_text().splitlines()[1:]
        except OSError:
            return ""
        best: tuple[int, str] | None = None
        for line in lines:
            fields = line.split()
            if len(fields) > 7 and fields[1] == "00000000" and fields[7] == "00000000":
                metric = int(fields[6]) if fields[6].isdigit() else 0
                if best is None or metric < best[0]:
                    best = (metric, fields[0])
        return best[1] if best else ""
    return ""


def system_vpn_active() -> bool:
    return is_tunnel_interface(default_route_interface())


__all__ = ["default_route_interface", "is_tunnel_interface", "system_vpn_active"]
