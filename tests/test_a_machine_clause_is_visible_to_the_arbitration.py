"""A clause a machine/tool family owns must count in the whole-turn arbitration.

Measured at e31ddc7d, before the registries were joined:

    "1000 TRY to USD? And how much free disk space do I have?"
        slices: (s1 (currency,), s2 ())  ->  currency covers_whole_turn=True
    "What is your name? And how much RAM does this machine have?"
        ->  identity claim_may_preempt_turn=True

The disk/RAM half was claimed by a family in `core.agent_runtime.intent_claims` -- a second,
disjoint registry the arbitration never read -- so it read as owed to nobody and the first
lane ended the turn on top of it. The turn ended on the conversion and the machine half was
never asked. This is the shape the live-info registration repaired for weather clauses
(`4b8b39e3`), one registry over; the fix is the same: the arbitration reads ONE registry, and
the machine/tool families are in it.

The direction can only ever be fail-closed: registering more families adds conflicting clauses,
it can never remove one, so no whole-turn verdict can be GRANTED by this change. Measured over
334 corpus prompts (ops/semantic_phase0_frozen_corpus.json + the claim census + the arbitration
contract's own prompts): 0 withdrawn, 0 granted.
"""
from __future__ import annotations

import core.agent_runtime.answer_coverage as ac
from core.agent_runtime.answer_coverage import (
    FAMILY_ASSISTANT_IDENTITY,
    FAMILY_CURRENCY,
    FAMILY_LIVE_INFO,
    claim_may_preempt_turn,
    coverage_for,
    is_mixed_intent_turn,
    slice_families,
)


def test_a_currency_clause_beside_a_disk_clause_is_not_the_whole_turn() -> None:
    text = "1000 TRY to USD? And how much free disk space do I have?"
    coverage = coverage_for(text, FAMILY_CURRENCY)
    assert not coverage.covers_whole_turn
    assert is_mixed_intent_turn(text)


def test_a_weather_clause_beside_a_listing_clause_is_not_the_whole_turn() -> None:
    text = "What's the weather in Vilnius? Also list the files in my Downloads folder."
    assert not coverage_for(text, FAMILY_LIVE_INFO).covers_whole_turn
    assert is_mixed_intent_turn(text)


def test_an_identity_clause_beside_a_specs_clause_may_not_preempt() -> None:
    text = "What is your name? And how much RAM does this machine have?"
    assert not claim_may_preempt_turn(text, FAMILY_ASSISTANT_IDENTITY)
    assert is_mixed_intent_turn(text)


def test_the_machine_families_come_from_one_registry() -> None:
    """The arbitration must not restate the family list beside intent_claims."""
    from core.agent_runtime.intent_claims import _PROBE_FAMILIES

    registered = {family for family, _ in _PROBE_FAMILIES}
    visible = {family for family, _ in ac._machine_family_probes()}
    assert visible == registered
    assert len(registered) == 9


def test_positive_controls_keep_their_lanes() -> None:
    # P9: a clause carrying no request of its own must not block the currency lane.
    assert coverage_for("I want to convert money. 1000 TRY to USD?", FAMILY_CURRENCY).covers_whole_turn
    # P8: a single-domain turn keeps its lane.
    assert claim_may_preempt_turn("What is your name?", FAMILY_ASSISTANT_IDENTITY)
    # A single-clause machine turn is one clause; nothing can conflict with it.
    assert not is_mixed_intent_turn("how much free disk space do I have?")


def test_sabotage_without_the_machine_probes_the_wrong_verdict_returns() -> None:
    """Removing the registration must restore the blind verdict, so this cannot pass vacuously."""
    text = "1000 TRY to USD? And how much free disk space do I have?"
    assert ac._machine_family_probes(), "machine probes not registered; sabotage moot"
    real = ac._machine_family_probes
    try:
        ac._machine_family_probes = lambda: ()
        ac.slice_families.cache_clear()
        assert coverage_for(text, FAMILY_CURRENCY).covers_whole_turn
        assert slice_families(text)[1][1] == ()
    finally:
        ac._machine_family_probes = real
        ac.slice_families.cache_clear()
    assert not coverage_for(text, FAMILY_CURRENCY).covers_whole_turn
