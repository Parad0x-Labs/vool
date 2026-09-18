"""A memory-recall clause is one slice of the turn, never an unarbitrated whole-turn exit.

Measured from source at 87e80a9b: the private-memory lane in `core/web/api/runtime.py` was the
only pre-agent exit with no coverage arbitration -- the assistant-identity lane directly above
it asks `claim_may_preempt_turn`; the memory lane returned unconditionally, so a stored-field
answer ended a turn that carried other requests. And because `memory_recall` was registered in
NO claim registry, a memory clause read as owed to nobody: another family's lane could end the
turn on top of it from the other direction.

The repair is the same shape as every other family: the detector lives ONCE in
`core.memory_recall_intent` (moved verbatim out of the API module), the family is registered in
`core.agent_runtime.answer_coverage`, and the lane gates on `coverage_for` with a slice-answer
fallback -- no second detector, no second vocabulary.
"""
from __future__ import annotations

import core.agent_runtime.answer_coverage as ac
from core.agent_runtime.answer_coverage import (
    FAMILY_CURRENCY,
    FAMILY_LIVE_INFO,
    FAMILY_MEMORY_RECALL,
    claim_may_preempt_turn,
    coverage_for,
    is_mixed_intent_turn,
    slice_families,
)

MIXED_TURNS = [
    # human-shaped: stored-fact question beside another request
    "What is my name? Also 1000 TRY to USD?",
    "what's my name? also what's the weather in Vilnius?",
    "What is my preference? And convert 1000 TRY to USD.",
    "do you remember my codename? plus the weather in Oslo",
    "what is my answer style? also price of btc",
]

SINGLE_DOMAIN = [
    "what is my name?",
    "what's my project codename",
    "do you know my response style?",
    "what are my preferences?",
    "what do you remember about me",
]

NEGATIVE_CONTROLS = [
    # the measured false-claim family: the field is a modifier of another noun
    "what is my preference file in vscode",
    "what's my codename column in users.csv",
    # no recall intent at all
    "set your response style",
    # a question ABOUT remembering is not a recall request (P13 shape)
    "Do you remember that I mentioned a cat?",
]

ADVERSARIAL_NEAR_MISS = [
    # "who am I" embedded in a larger question is not whole-profile recall
    "and who am I meeting tomorrow?",
]


def test_the_family_is_registered_once() -> None:
    registered = [family for family, _probe in ac._PROBES]
    assert registered.count(FAMILY_MEMORY_RECALL) == 1


def test_a_mixed_turn_may_not_be_ended_by_the_memory_lane() -> None:
    for text in MIXED_TURNS:
        assert not claim_may_preempt_turn(text, FAMILY_MEMORY_RECALL), text
        assert is_mixed_intent_turn(text), f"the sibling clause is invisible: {text}"


def test_the_sibling_lane_also_may_not_end_a_mixed_turn() -> None:
    """The registration is symmetric: a memory clause now counts as conflicting for OTHER lanes."""
    assert not coverage_for(
        "What is my name? Also 1000 TRY to USD?", FAMILY_CURRENCY
    ).covers_whole_turn
    assert not coverage_for(
        "what's my name? also what's the weather in Vilnius?", FAMILY_LIVE_INFO
    ).covers_whole_turn


def test_a_single_domain_recall_keeps_its_lane() -> None:
    for text in SINGLE_DOMAIN:
        assert claim_may_preempt_turn(text, FAMILY_MEMORY_RECALL), text


def test_no_recall_claim_where_none_exists() -> None:
    for text in NEGATIVE_CONTROLS + ADVERSARIAL_NEAR_MISS:
        families = [family for _slice, families in slice_families(text) for family in families]
        assert FAMILY_MEMORY_RECALL not in families, text


def test_sabotage_removing_the_family_restores_the_blind_verdict() -> None:
    """Un-registering memory_recall must let the currency lane preempt the mixed turn again."""
    text = "What is my name? Also 1000 TRY to USD?"
    assert (FAMILY_MEMORY_RECALL, ac._claims_memory_recall) in list(ac._PROBES), (
        "memory_recall probe not registered; sabotage moot"
    )
    without = tuple(row for row in ac._PROBES if row[0] != FAMILY_MEMORY_RECALL)
    real = ac._PROBES
    try:
        ac._PROBES = without
        ac.slice_families.cache_clear()
        assert coverage_for(text, FAMILY_CURRENCY).covers_whole_turn
    finally:
        ac._PROBES = real
        ac.slice_families.cache_clear()
    assert not coverage_for(text, FAMILY_CURRENCY).covers_whole_turn
