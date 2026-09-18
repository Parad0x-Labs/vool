"""A coordination whose conjuncts share ONE predicate is ONE demand.

Measured on the built candidate 60a91da3 (isolated bundle drive, 2026-09-07): "How much gold and
how much silver can I buy with one bitcoin right now?" was minted as two demands -- "How much gold"
and "and how much silver can I buy with one bitcoin right now?". The bare first unit was answered
by a price quote, the second by a quote-only plan, and the turn closed as complete with neither
amount computed. The predicate "can I buy with one bitcoin" belongs to BOTH objects; cutting the
coordination orphans it from the first.

The rule is grammatical, not a phrase list: a VERBLESS left conjunct opening with an interrogative
quantity head ("how much gold") cannot stand as a request on its own, so the coordination that
follows it is shared structure. A left conjunct that carries its own verb ("how much IS gold and
how much is silver") is a complete question and keeps splitting.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.answer_coverage import demand_units


def _units(text: str) -> list[str]:
    return [str(getattr(unit, "text", unit)).strip() for unit in demand_units(text)]


@pytest.mark.parametrize(
    "text",
    [
        "How much gold and how much silver can I buy with one bitcoin right now?",
        "How much gold and silver can I buy with one bitcoin right now?",
        "how many ounces of gold and how many ounces of silver does 2 eth get me",
        "Which laptop and which monitor would you pick for 1500 EUR?",
    ],
)
def test_a_shared_predicate_coordination_stays_one_demand(text: str) -> None:
    assert _units(text) == [text.strip()]


@pytest.mark.parametrize(
    ("text", "expected_count"),
    [
        # The left conjunct carries its own verb: two complete questions.
        ("how much is gold and how much is silver today", 2),
        # The right conjunct opens a DIFFERENT request head: not a shared predicate.
        ("how much gold and what is the weather in Rome", 2),
        ("What is the price of gold and how much silver can I buy with 1 btc?", 2),
    ],
)
def test_complete_conjuncts_and_fresh_heads_keep_splitting(text: str, expected_count: int) -> None:
    assert len(_units(text)) == expected_count
