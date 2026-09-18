"""A knowledge clause's numeric authority closes only when it invokes supplied evidence.

MEASURED on the final-build pack (98f6ed63, turn 3, "Please answer all eight briefly: ... 2) How
many continents are there? ..."): the conductor claimed the turn, the continents node ran under
CLOSED authority because the clause says "how many", the briefing told the author to state a
number only if it was a message fact, and the served line was "The number of continents is not
determinable from the provided facts." The frozen build answered the same turn in full only
because its planner timed out and the plain lane took the turn.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.conductor.operations import (
    _CLOSED_NUMERIC_AUTHORITY,
    _OPEN_KNOWLEDGE_AUTHORITY,
    _explanation_numeric_authority as authority,
)


@pytest.mark.parametrize("clause", [
    "How many continents are there?",
    "How many sides does a hexagon have?",
    "How many bones are in the human body?",
    "How long is a marathon?",
    "How much does a litre of water weigh?",
    "What is the freezing point of water in Celsius?",
])
def test_a_stable_knowledge_count_with_no_referent_is_open(clause: str) -> None:
    assert authority(clause) == _OPEN_KNOWLEDGE_AUTHORITY, clause


@pytest.mark.parametrize("clause", [
    "How much is that in USD?",                   # references a supplied value
    "How many of those can I afford?",
    "Now take that result and divide it by 6.",   # computation
    "Calculate the total for both.",
    "What is 15 - 9?",                             # carries numbers
    "Estimate how many I need per person.",
])
def test_a_clause_that_invokes_supplied_figures_is_closed(clause: str) -> None:
    assert authority(clause) == _CLOSED_NUMERIC_AUTHORITY, clause


def test_an_unresolved_ambiguity_token_closes_the_clause() -> None:
    shared = SimpleNamespace(
        facts=(), unresolved_ambiguities=(SimpleNamespace(token="Springfield"),),
        missing=(),
    )
    assert authority("How big is Springfield?", shared) == _CLOSED_NUMERIC_AUTHORITY
    assert authority("How big is Paris?", shared) == _OPEN_KNOWLEDGE_AUTHORITY
