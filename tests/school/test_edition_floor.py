"""VOOL School edition boundary tests (goal §34 items 12–13 + §4).

The SCHOOL edition must MECHANICALLY lack the money lanes: no supported
contract, no offer seat for a "send SOL" demand, no dispatch, and no service
that can be re-enabled by preference. PERSONAL must be unchanged.
"""

from __future__ import annotations

import pytest

from core.product_edition import (
    ProductEdition,
    active_edition,
    edition_allows,
    edition_intent_allowed,
    edition_profanity_ceiling,
    reset_edition_cache,
)


@pytest.fixture
def school_edition(monkeypatch):
    monkeypatch.setenv("VOOL_EDITION", "school")
    reset_edition_cache()
    yield
    reset_edition_cache()


@pytest.fixture
def personal_edition(monkeypatch):
    monkeypatch.delenv("VOOL_EDITION", raising=False)
    reset_edition_cache()
    yield
    reset_edition_cache()


MONEY_SURFACES = {"wallet", "marketplace", "web0"}
MONEY_INTENTS = (
    "wallet.status", "wallet.propose", "wallet.spend", "wallet.simulate",
    "x402.propose", "pay.x402", "marketplace.purchase_knowledge",
    "marketplace.search_listings", "sell.quote", "web0.publish",
)


def test_school_active(school_edition):
    assert active_edition() is ProductEdition.SCHOOL


def test_personal_default(personal_edition):
    assert active_edition() is ProductEdition.PERSONAL


def test_school_money_census(school_edition):
    """Goal §34 #12: crypto capability absent in SCHOOL — zero supported money contracts."""
    from core.runtime_tool_contracts import runtime_tool_contracts

    contracts = runtime_tool_contracts()
    money = [c for c in contracts if c.tool_surface in MONEY_SURFACES]
    assert money, "the money lanes should exist as (disabled) contracts"
    assert not any(c.supported for c in money)
    for contract in money:
        assert contract.unsupported_reason, "every floored contract names its reason"
        assert "School" in contract.unsupported_reason


def test_school_pay_x402_specifically_floored(school_edition):
    from core.runtime_tool_contracts import runtime_tool_contract_map

    contract = runtime_tool_contract_map()["pay.x402"]
    assert contract.supported is False


def test_school_intent_verdicts(school_edition):
    for intent in MONEY_INTENTS:
        ok, reason = edition_intent_allowed(intent)
        assert not ok, intent
        assert reason


def test_school_send_sol_seats_nothing(school_edition):
    """Goal §4: 'send SOL' must not discover a hidden wallet capability."""
    from core.capability_graph import model_visible_specs
    from core.tool_demand_signals import resolve_demand_signals

    text = "please send 5 SOL to my friend's wallet"
    signals = resolve_demand_signals(text)
    specs = model_visible_specs(user_text=text)
    offered = {s.get("function", {}).get("name") or s.get("name") or "" for s in specs}
    assert not any(
        name.startswith(("wallet.", "pay.", "x402.", "marketplace.", "sell.", "web0."))
        for name in offered
    ), f"money tool seated in SCHOOL: {sorted(offered)}"


def test_school_edition_allows_map(school_edition):
    for surface in ("wallet", "marketplace", "web0", "mesh_daemon", "hive_task_intake",
                    "meet_server", "web0_announce", "earnings_page"):
        ok, reason = edition_allows(surface)
        assert not ok and reason, surface
    # governed-but-present surfaces stay allowed at the edition layer
    for surface in ("web", "workspace", "sandbox"):
        ok, _ = edition_allows(surface)
        assert ok, surface


def test_personal_money_lane_unchanged(personal_edition):
    """PERSONAL edition: the wallet lane keeps its own (default-off) switch."""
    from core.runtime_tool_contracts import runtime_tool_contract_map

    contract = runtime_tool_contract_map()["pay.x402"]
    assert contract.supported is False  # wallet off by default in PERSONAL too


def test_school_profanity_clamped_to_zero(school_edition):
    """Operator law: SCHOOL pins profanity 0 and refuses raises."""
    from core.user_preferences import UserPreferences, load_preferences, save_preferences

    assert edition_profanity_ceiling() == 0
    prefs = UserPreferences(profanity_level=80)
    save_preferences(prefs)
    loaded = load_preferences()
    assert loaded.profanity_level == 0


def test_personal_profanity_preserved(personal_edition):
    from core.user_preferences import UserPreferences, load_preferences, save_preferences

    save_preferences(UserPreferences(profanity_level=40))
    assert load_preferences().profanity_level == 40


def test_school_hive_intake_refused_before_decode(school_edition):
    """Goal §34 #1-adjacent: hive task intake does not exist in SCHOOL — the
    capsule is refused BEFORE envelope decode, whatever the daemon says."""

    class ExplodingDaemon:
        def _decode_verified_assist_envelope(self, *a, **k):
            raise AssertionError("SCHOOL must refuse the capsule before decode")

    from core.daemon.tasks import maybe_execute_local_assignment_from_raw

    # Must return without touching the daemon.
    maybe_execute_local_assignment_from_raw(
        ExplodingDaemon(), b"raw", ("127.0.0.1", 1), hooks=None
    )


def test_school_accept_hive_tasks_forced_off(school_edition):
    from core.user_preferences import UserPreferences, load_preferences

    prefs = UserPreferences(accept_hive_tasks=True)
    clamped = load_preferences()  # load path clamps
    assert clamped.accept_hive_tasks is False


def test_school_mesh_boot_decision(school_edition):
    ok, reason = edition_allows("mesh_daemon")
    assert not ok and "local network" in reason.lower()
