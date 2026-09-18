from __future__ import annotations

import socket
from dataclasses import dataclass

from network.nat_probe import NatProbeResult


@dataclass(frozen=True)
class HolePunchAttempt:
    peer_host: str
    peer_port: int
    packets_sent: int
    success: bool
    mode: str


def connectivity_mode(probe: NatProbeResult, relay_available: bool) -> str:
    if probe.mode == "wan_direct":
        return "WAN_DIRECT"
    if probe.mode == "wan_mapped":
        return "WAN_HOLE_PUNCH"
    if relay_available:
        return "RELAY_ASSISTED"
    return "UNREACHABLE"


def attempt_udp_hole_punch(sock: socket.socket, peer_host: str, peer_port: int, *, attempts: int = 3) -> HolePunchAttempt:
    sent = 0
    for _ in range(max(1, attempts)):
        try:
            sock.sendto(b"vool-hole-punch", (peer_host, int(peer_port)))
            sent += 1
        except OSError:
            return HolePunchAttempt(peer_host=peer_host, peer_port=int(peer_port), packets_sent=sent, success=False, mode="failed")
    return HolePunchAttempt(peer_host=peer_host, peer_port=int(peer_port), packets_sent=sent, success=sent > 0, mode="attempted")
