from __future__ import annotations

from network.relay_fallback import choose_relay_mode


def test_choose_relay_mode_reports_hole_punch_for_nat_mapped_public_endpoint() -> None:
    decision = choose_relay_mode(
        direct_reachable=False,
        relay_available=False,
        peer_id="peer-1",
        nat_mode="wan_mapped",
    )

    assert decision.mode == "hole_punch"
    assert "hole-punch" in decision.reason.lower()


def test_choose_relay_mode_reports_lan_only_without_verified_relay() -> None:
    decision = choose_relay_mode(
        direct_reachable=False,
        relay_available=False,
        peer_id="peer-2",
        nat_mode="lan_only",
    )

    assert decision.mode == "lan_only"
    assert "private lan" in decision.reason.lower()


def test_choose_relay_mode_uses_relay_when_explicitly_available() -> None:
    decision = choose_relay_mode(
        direct_reachable=False,
        relay_available=True,
        peer_id="peer-3",
        nat_mode="lan_only",
    )

    assert decision.mode == "relay"
