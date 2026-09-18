from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass


@dataclass(frozen=True)
class NatProbeResult:
    local_host: str
    public_host: str | None
    public_port: int | None
    nat_type: str
    mode: str


def _is_private(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return True


def detect_local_host() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"


def classify_nat(local_host: str, public_host: str | None, public_port: int | None) -> NatProbeResult:
    if public_host and not _is_private(public_host):
        if public_host == local_host:
            return NatProbeResult(local_host, public_host, public_port, "open_internet", "wan_direct")
        return NatProbeResult(local_host, public_host, public_port, "nat", "wan_mapped")
    if not _is_private(local_host):
        return NatProbeResult(local_host, local_host, public_port, "open_internet", "wan_direct")
    return NatProbeResult(local_host, public_host, public_port, "private_lan", "lan_only")
