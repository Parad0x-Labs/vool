from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RelayDecision:
    peer_id: str
    mode: str
    reason: str


def choose_relay_mode(
    *,
    direct_reachable: bool,
    relay_available: bool,
    peer_id: str,
    nat_mode: str | None = None,
) -> RelayDecision:
    normalized_nat_mode = str(nat_mode or "").strip().lower()
    if normalized_nat_mode == "wan_direct" or (direct_reachable and normalized_nat_mode not in {"wan_mapped", "lan_only"}):
        return RelayDecision(peer_id=peer_id, mode="direct", reason="Direct connectivity available.")
    if normalized_nat_mode == "wan_mapped":
        return RelayDecision(
            peer_id=peer_id,
            mode="hole_punch",
            reason="Public endpoint is NAT-mapped; hole-punch connectivity is the honest primary transport mode.",
        )
    if normalized_nat_mode == "lan_only" and not relay_available:
        return RelayDecision(
            peer_id=peer_id,
            mode="lan_only",
            reason="Only private LAN reachability is known and no verified relay path is configured.",
        )
    if relay_available:
        return RelayDecision(peer_id=peer_id, mode="relay", reason="Direct connectivity unavailable; relay fallback selected.")
    return RelayDecision(peer_id=peer_id, mode="unreachable", reason="No direct path or relay available.")
