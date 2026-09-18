"""R1f — ONE lane registry owns demand-coverage capabilities.

WHAT THIS FILE PINS
-------------------
At 2b7b6385 `core/agent_runtime/demand_ownership.py` holds a private `_LANE_PROBES`
tuple (live-data + currency only) while `core/lane_registry.py` separately owns
precedence and mediation. Adding another whole-turn deterministic lane means editing
multiple registries, and a lane omitted from the private list can still swallow mixed
demand — the exact defect R1e closed for two families, reopenable by the next lane.

THE CONTRACT UNDER TEST
-----------------------
`core.lane_registry` is the single typed catalog: every lane that can end an external
turn declares a `LaneSpec` (id, precedence, terminal eligibility, demand-coverage
capability, role). `demand_ownership` CONSUMES the active catalog and holds no private
lane list. Coverage implementations resolve lazily by capability NAME; an unknown name
fails typed, never "covers everything". Registry precedence stays the only
contested-claim ordering. Test injection is pure — scoped catalogs and scoped
capability tables (ContextVars), never process-global mutation.

RED at 2b7b6385: the scoped-catalog API and `LaneSpec` do not exist (AttributeError),
`lane_may_claim_whole_turn` returns True for unregistered and coverage-undeclared
lanes on mixed turns, and the coverage-participating currency lane is absent from
LANE_REGISTRY — the two registries can disagree.
"""
from __future__ import annotations

import contextlib

import pytest

from core.turn_contract import LaneProposal

_MIXED = "Explain how a hash table works and tell me the weather in Kaunas"
_MIXED_CURRENCY = "convert 100 usd to eur and explain what a hedge fund is"


# ------------------------------------------------- 1. the single catalog (RED)


def test_a_synthetic_registered_lane_participates_without_editing_demand_ownership():
    """Requirement 7. A synthetic lane declared in a scoped catalog — with its own
    scoped capability implementation — must participate in demand coverage AND in
    mediation, ranked by its declared precedence. At base this needs an edit to
    demand_ownership's private `_LANE_PROBES`, which a test cannot make."""
    from core import lane_registry
    from core.agent_runtime import demand_ownership

    synthetic = lane_registry.LaneSpec(
        lane_id="synthetic_weather_lane",
        precedence=15,  # between live_info_fast_path (10) and live_data_typed_plan (20)
        terminal=True,
        coverage="synthetic_weather",
    )
    with lane_registry.scoped_catalog(
        (*lane_registry.LANE_CATALOG, synthetic)
    ), demand_ownership.scoped_coverage_capabilities(
        {"synthetic_weather": lambda text: "weather in kaunas" in str(text).lower()}
    ):
        coverage = demand_ownership.demand_coverage(_MIXED)
        verdict = lane_registry.mediate(
            [
                LaneProposal(
                    lane_id="synthetic_weather_lane", obligations_claimed=("u2",)
                )
            ],
            "live_data_typed_plan",
            ("u2",),
        )

    weather_lanes = None
    for (_unit_id, text), lanes in zip(coverage.units, coverage.per_unit_lanes, strict=True):
        if "weather" in text.lower():
            weather_lanes = lanes
    assert weather_lanes and "synthetic_weather_lane" in weather_lanes, (
        f"the synthetic registered lane did not participate in demand coverage: "
        f"{coverage.units} / {coverage.per_unit_lanes}"
    )
    assert not verdict.allowed and verdict.superseded_by == "synthetic_weather_lane", (
        f"mediation did not rank the synthetic lane by its declared precedence: {verdict}"
    )


def test_registry_order_and_demand_coverage_are_one_catalog():
    """Requirement 2/8. Every lane that participates in demand coverage is
    precedence-ranked in the ONE catalog, and LANE_REGISTRY is DERIVED from it —
    never a second ordered list. At base the currency lane participates in coverage
    while being absent from LANE_REGISTRY: the two registries disagree."""
    from core import lane_registry
    from core.agent_runtime import demand_ownership

    capable = {
        spec.lane_id
        for spec in lane_registry.active_catalog()
        if demand_ownership.is_coverage_capability(spec.coverage)
    }
    assert capable, "no lane declares a demand-coverage capability at all"
    missing = capable - set(lane_registry.LANE_REGISTRY)
    assert not missing, (
        f"lanes participate in demand coverage but carry no registry precedence: "
        f"{missing} — coverage and ordering live in two disagreeing registries"
    )
    derived = tuple(
        spec.lane_id
        for spec in sorted(lane_registry.active_catalog(), key=lambda s: s.precedence)
        if spec.role != lane_registry.ROLE_FALLBACK
    )
    assert tuple(lane_registry.LANE_REGISTRY) == derived, (
        f"LANE_REGISTRY is not the derived catalog order: "
        f"{tuple(lane_registry.LANE_REGISTRY)} vs {derived}"
    )


def test_the_catalog_inventories_every_terminal_lane():
    """Requirement 5. Every production lane capable of ending an external turn is
    declared, and every declaration states the trichotomy: a demand-coverage
    capability, or single-unit-limited ('none'), or a composite planner/fallback
    role that owns a complete unit plan."""
    from core import lane_registry

    expected = {
        "live_info_fast_path",
        "live_data_typed_plan",
        "currency_value_contract",
        "currency_frontdoor",
        "turn_frontdoor_deterministic",
        "conductor_multi_intent_plan",
        "demand_owned_mixed_turn",
        "attempt_followup",
        "model_lane",
    }
    declared = {spec.lane_id for spec in lane_registry.LANE_CATALOG}
    assert expected <= declared, f"terminal lanes missing from the catalog: {expected - declared}"
    for spec in lane_registry.LANE_CATALOG:
        assert isinstance(spec.precedence, int) and isinstance(spec.terminal, bool)
        assert spec.role in (
            lane_registry.ROLE_DETERMINISTIC,
            lane_registry.ROLE_COMPOSITE_PLANNER,
            lane_registry.ROLE_FALLBACK,
        ), f"{spec.lane_id} declares no explicit role: {spec!r}"
        assert spec.coverage, f"{spec.lane_id} declares no coverage capability"


# ------------------------------------------------- 2. the finalize law (RED)


def test_an_unregistered_lane_cannot_finalize_a_mixed_turn():
    """Requirement 6. A lane with no catalog entry cannot end a MULTI-unit turn —
    at base `lane_may_claim_whole_turn` returned True for any unknown id ('the lane
    claims nothing here'), which is exactly how an omitted lane swallows mixed
    demand. Single-unit turns keep today's freedom."""
    from core.agent_runtime import demand_ownership

    assert demand_ownership.lane_may_claim_whole_turn(_MIXED, "not_a_registered_lane") is False
    assert (
        demand_ownership.lane_may_claim_whole_turn(
            "weather in Kaunas", "not_a_registered_lane"
        )
        is True
    )


def test_a_coverage_undeclared_lane_cannot_finalize_a_multi_unit_turn():
    """Requirement 6, registered flavor: a lane declared with coverage='none' (the
    single-unit-limited trichotomy arm) cannot finalize a multi-unit turn, while a
    declared composite planner may — it owns a complete unit plan."""
    from core.agent_runtime import demand_ownership

    assert demand_ownership.lane_may_claim_whole_turn(_MIXED, "attempt_followup") is False
    assert (
        demand_ownership.lane_may_claim_whole_turn(_MIXED, "conductor_multi_intent_plan")
        is True
    )
    assert (
        demand_ownership.lane_may_claim_whole_turn(_MIXED, "demand_owned_mixed_turn")
        is True
    )


# ------------------------------------------------- 3. typed failure (RED)


def test_a_catalog_entry_with_missing_or_broken_coverage_fails_typed():
    """Requirement 3. A spec with a MISSING coverage name is refused at
    construction; a well-formed spec whose coverage name nobody implements makes
    demand_coverage RAISE — unknown names must never silently read as 'covers
    everything' (or 'covers nothing' and drift past the inventory)."""
    from core import lane_registry
    from core.agent_runtime import demand_ownership

    with pytest.raises(ValueError):
        lane_registry.LaneSpec(
            lane_id="broken", precedence=10, terminal=True, coverage=""
        )
    bogus = lane_registry.LaneSpec(
        lane_id="bogus_capability_lane", precedence=10, terminal=True,
        coverage="no_such_capability",
    )
    with lane_registry.scoped_catalog((bogus,)):
        with pytest.raises(TypeError):
            demand_ownership.demand_coverage("weather in Kaunas and explain entropy")


# ------------------------------------------------- 4. controls and pins


def test_pure_turn_verdicts_are_unchanged_controls():
    """Requirement 9. The catalog path must reproduce the R1e verdicts exactly:
    pure live and pure general are not mixed, the live and currency incidents
    are, and the currency family participates through its catalog declaration."""
    from core.agent_runtime import demand_ownership

    assert not demand_ownership.demand_coverage("weather in Kaunas").mixed
    assert not demand_ownership.demand_coverage("explain hash tables").mixed
    assert demand_ownership.demand_coverage(_MIXED).mixed
    currency_coverage = demand_ownership.demand_coverage(_MIXED_CURRENCY)
    assert currency_coverage.mixed
    converting_lanes = currency_coverage.per_unit_lanes[0]
    assert "currency_frontdoor" in converting_lanes or "currency_value_contract" in converting_lanes, (
        f"the currency family no longer participates in demand coverage: {converting_lanes}"
    )


def test_precedence_not_arrival_decides_contested_claims():
    """Requirement 8 (sabotage target). Two synthetic lanes, the LATER-recorded one
    ranking EARLIER: mediation must supersede on declared precedence regardless of
    the order proposals arrived in."""
    from core import lane_registry

    # Names chosen so ALPHABETICAL order DISAGREES with precedence: zeta ranks
    # earlier by declaration but later alphabetically, so a rank that falls back
    # to names (arrival order's usual disguise) picks the wrong winner.
    early = lane_registry.LaneSpec(
        lane_id="synthetic_zeta", precedence=5, terminal=True, coverage="none"
    )
    late = lane_registry.LaneSpec(
        lane_id="synthetic_alpha", precedence=6, terminal=True, coverage="none"
    )
    with lane_registry.scoped_catalog((late, early)):  # arrival order inverted on purpose
        superseded = lane_registry.mediate(
            [LaneProposal(lane_id="synthetic_zeta", obligations_claimed=("u1",))],
            "synthetic_alpha",
            ("u1",),
        )
        allowed = lane_registry.mediate(
            [LaneProposal(lane_id="synthetic_alpha", obligations_claimed=("u1",))],
            "synthetic_zeta",
            ("u1",),
        )
    assert not superseded.allowed and superseded.superseded_by == "synthetic_zeta"
    assert allowed.allowed


def test_scoped_catalog_injection_leaves_no_process_global_residue():
    """Pure injection: a scoped catalog (and scoped capabilities) vanish on exit —
    the production catalog is untouched for whatever runs next."""
    from core import lane_registry
    from core.agent_runtime import demand_ownership

    residue = lane_registry.LaneSpec(
        lane_id="residue_lane", precedence=1, terminal=True, coverage="none"
    )
    with contextlib.ExitStack() as stack:
        stack.enter_context(
            lane_registry.scoped_catalog((*lane_registry.LANE_CATALOG, residue))
        )
        stack.enter_context(
            demand_ownership.scoped_coverage_capabilities({"temp": lambda _t: True})
        )
        assert lane_registry.find_spec("residue_lane") is not None
        assert demand_ownership.is_coverage_capability("temp")
    assert lane_registry.find_spec("residue_lane") is None
    assert not demand_ownership.is_coverage_capability("temp")
    assert lane_registry.find_spec("live_data_typed_plan") is not None


# ======================================================================
# R1f AMENDMENT — four independently reproduced contract holes
# ======================================================================


def test_a_non_terminal_lane_cannot_finalize_any_external_turn():
    """Defect 1. `LaneSpec.terminal` must be ENFORCED: a lane declared
    terminal=False cannot end an external turn at all — not on a single-unit
    turn, not on a multi-unit turn, and not through the composite-planner or
    fallback roles. At base the field was decorative: every path ignored it."""
    from core import lane_registry
    from core.agent_runtime import demand_ownership

    non_terminal = (
        lane_registry.LaneSpec(
            "synthetic_internal", 11, terminal=False, coverage="live_data"
        ),
        lane_registry.LaneSpec(
            "synthetic_dead_planner",
            61,
            terminal=False,
            coverage=lane_registry.COVERAGE_COMPOSITE_PLAN,
            role=lane_registry.ROLE_COMPOSITE_PLANNER,
        ),
        lane_registry.LaneSpec(
            "synthetic_dead_fallback",
            101,
            terminal=False,
            coverage=lane_registry.COVERAGE_FALLBACK_PLAN,
            role=lane_registry.ROLE_FALLBACK,
        ),
    )
    with lane_registry.scoped_catalog((*lane_registry.LANE_CATALOG, *non_terminal)):
        for spec in non_terminal:
            for text in ("weather in Kaunas", _MIXED):
                assert (
                    demand_ownership.lane_may_claim_whole_turn(text, spec.lane_id)
                    is False
                ), (
                    f"a terminal=False lane ({spec.lane_id}, role={spec.role}) may "
                    f"finalize {text!r} — the terminal field is not enforced"
                )
        # controls inside the same scope: the terminal lanes keep their law
        assert demand_ownership.lane_may_claim_whole_turn(
            "weather in Kaunas", "live_data_typed_plan"
        )
        assert demand_ownership.lane_may_claim_whole_turn(
            _MIXED, "conductor_multi_intent_plan"
        )


def test_zero_coverage_on_a_multi_unit_turn_cannot_finalize():
    """Defect 2. On a multi-unit turn a coverage-declaring lane may finalize
    ONLY when it covers EVERY unit — zero coverage is not 'its own admission
    still decides', it is a lane that covers none of the demand trying to end
    the turn. At base `if not mine: return True` let it."""
    from core.agent_runtime import demand_ownership

    pure_general_multi = (
        "explain gravity and explain entropy briefly and also explain tides"
    )
    coverage = demand_ownership.demand_coverage(pure_general_multi)
    assert coverage.unit_count >= 2, (
        f"the control text must mint multiple units: {coverage.units}"
    )
    assert demand_ownership.lane_may_claim_whole_turn(
        pure_general_multi, "live_data_typed_plan"
    ) is False, (
        "a live-data lane covering ZERO units of a three-unit general turn may "
        "finalize it"
    )
    assert demand_ownership.lane_may_claim_whole_turn(
        pure_general_multi, "currency_frontdoor"
    ) is False
    # single-unit freedom is untouched: any lane may serve one request
    assert demand_ownership.lane_may_claim_whole_turn(
        "explain gravity", "live_data_typed_plan"
    )


def test_an_empty_scoped_catalog_is_active_not_the_production_one():
    """Defect 3. `scoped_catalog(())` must ACTIVATE the empty catalog — an
    embedder declaring 'no lanes' — not silently fall back to the production
    catalog because the empty tuple is falsy."""
    from core import lane_registry
    from core.agent_runtime import demand_ownership

    with lane_registry.scoped_catalog(()):
        assert lane_registry.active_catalog() == (), (
            "the empty scoped catalog leaked the production catalog through"
        )
        assert lane_registry.find_spec("live_data_typed_plan") is None
        covered = demand_ownership.demand_coverage(_MIXED)
        assert covered.per_unit_lanes and all(
            lanes == () for lanes in covered.per_unit_lanes
        ), f"lanes still participate under an empty catalog: {covered.per_unit_lanes}"
        assert not covered.mixed
    # and the scope restores the production catalog on exit
    assert lane_registry.find_spec("live_data_typed_plan") is not None


def test_the_catalog_rejects_duplicate_precedence_values():
    """Defect 4. Duplicate lane ids are refused; duplicate PRECEDENCE values
    must be too — two lanes sharing a rank make contested-claim ordering
    non-deterministic (sorted() would tie-break by arrival). At base the second
    duplicate was silently accepted."""
    from core import lane_registry

    first = lane_registry.LaneSpec("synthetic_rank_a", 10, True, "none")
    second = lane_registry.LaneSpec("synthetic_rank_b", 10, True, "none")
    with pytest.raises(ValueError):
        with lane_registry.scoped_catalog((first, second)):
            pass
    # distinct precedences are fine, and the id-duplicate arm still fires
    ok_second = lane_registry.LaneSpec("synthetic_rank_b", 12, True, "none")
    with lane_registry.scoped_catalog((first, ok_second)):
        assert len(lane_registry.active_catalog()) == 2
    same_id = lane_registry.LaneSpec("synthetic_rank_a", 14, True, "none")
    with pytest.raises(ValueError):
        with lane_registry.scoped_catalog((first, same_id)):
            pass
