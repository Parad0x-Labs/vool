"""F41 — a plain knowledge clause inside a conductor plan must reach the knowledge family.

Measured served on builds c9200e0c and 24440e8d (two independent texts):

    "In what year did the Berlin Wall fall?"  -> named market_quote -> unresolved
    "who wrote the novel 1984?"               -> "the available answer path does not safely
                                                 match this request"

The conductor's knowledge family (`factual_explanation`) admits only explanation-SHAPED
clauses (`_asks_for_explanation` cue gate). A plain KNOW question has no cue, so EVERY
family refuses it and the clause dies beside siblings that answered. The whole-turn
demotion was measured and made the outcome worse (planner.py F11 note: it traded the
partial answer for a total grounding refusal), so the repair belongs HERE, at per-clause
granularity: the typed KNOW verdict is the discriminator, not word cues.
"""

from __future__ import annotations

import pytest

from core.conductor.capabilities import decide_operation_compatibility
from core.conductor.planner import (
    ProposedClause,
    _resolve_clause,
    build_plan_from_clauses,
    extract_shared_context,
)
from core.conductor.registry import UNRESOLVED_OPERATION, operation_spec
from core.turn_ir import ClauseKind, parse_turn_ir


def _semantic(text: str):
    clauses = parse_turn_ir(text).clauses
    assert len(clauses) == 1, f"expected one clause for {text!r}"
    return clauses[0]


def _resolve(text: str, operation: str):
    return _resolve_clause(
        ProposedClause(0, text, operation, ()),
        extract_shared_context(text),
        _semantic(text),
    )


BERLIN = "In what year did the Berlin Wall fall?"
ORWELL = "who wrote the novel 1984?"
EMPEROR = "What is the exact middle name of the current Emperor of Japan?"
PYTHON_NOW = "What is the latest stable version of Python?"


@pytest.mark.parametrize("text", [BERLIN, ORWELL])
def test_a_know_clause_the_planner_mislabels_is_served_by_the_knowledge_family(text: str) -> None:
    """The planner named a non-knowledge family that cannot expand (market_quote for a
    history question, measured on turn 18). The typed KNOW verdict must re-route the
    clause to the knowledge family instead of leaving it unresolved."""
    spec, argument_sets, error = _resolve(text, "market_quote")
    assert spec is not None and spec.name == "factual_explanation", error
    assert argument_sets, "the knowledge family expanded nothing for a plain KNOW clause"


@pytest.mark.parametrize("text", [BERLIN, ORWELL])
def test_a_know_clause_named_knowledge_directly_is_admitted(text: str) -> None:
    """Even a CORRECT naming died: the capability's explanation-shape cue refused the
    clause before the named expander (itself broader) could run."""
    spec, argument_sets, error = _resolve(text, "factual_explanation")
    assert spec is not None and spec.name == "factual_explanation", error
    assert argument_sets


@pytest.mark.parametrize("text", [EMPEROR, PYTHON_NOW])
def test_the_freshness_guard_survives_the_named_admission(text: str) -> None:
    """Current/latest-bound questions must keep dying at the knowledge family — the
    grounding law is load-bearing (turn 19's refusal is correct). "current Emperor" and
    "latest stable version" both carry freshness cues the admission reuses unchanged."""
    assert _semantic(text).kind is ClauseKind.KNOW
    spec, argument_sets, _error = _resolve(text, "factual_explanation")
    assert spec is None and not argument_sets


def test_the_unclaimed_capability_admission_is_untouched() -> None:
    """Only the NAMED path widens. The unclaimed fallback's capability check keeps its
    explanation-shape gate, so a plain question sitting in a plan nobody labelled is
    still not swallowed by the knowledge family's catch-all tier."""
    spec = operation_spec("factual_explanation")
    decision = decide_operation_compatibility(spec.capability, _semantic(BERLIN))
    assert decision.allowed is False, decision.reason


def test_a_typed_lane_naming_is_not_stolen_by_the_retyping() -> None:
    """The re-route fires only when the named family cannot serve the clause. A weather
    clause named weather_lookup must still resolve to weather_lookup."""
    text = "get the weather for Kaunas and Tallinn"
    spec, argument_sets, error = _resolve(text, "weather_lookup")
    assert spec is not None and spec.name == "weather_lookup", error
    assert argument_sets


def test_turn_18_plan_answers_berlin_wall_and_refuses_only_the_emperor() -> None:
    """Plan level, the served shape of acceptance turn 18: the calculation clause
    calculates, the Berlin Wall clause becomes a knowledge node, and ONLY the
    current-Emperor clause (freshness-guarded) stays unresolved."""
    original = f"Answer all three: What is 5+5? {EMPEROR} {BERLIN}"
    plan = build_plan_from_clauses(
        (
            ProposedClause(0, "What is 5+5?", "calculation", ()),
            ProposedClause(1, EMPEROR, "factual_explanation", ()),
            ProposedClause(2, BERLIN, "market_quote", ()),
        ),
        original_request=original,
        plan_id="conductor-f41-test",
        shared_context=extract_shared_context(original),
    )
    by_operation: dict[str, int] = {}
    unresolved_texts: list[str] = []
    factual_texts: list[str] = []
    for node in plan.nodes:
        by_operation[node.operation] = by_operation.get(node.operation, 0) + 1
        if node.operation == UNRESOLVED_OPERATION:
            unresolved_texts.append(node.request_text)
        if node.operation == "factual_explanation":
            factual_texts.append(node.request_text)
    assert by_operation.get("calculation") == 1
    # The framing span ("Answer all three: What is 5+5?") is an unresolved node at BASE too —
    # pre-existing, recorded, and outside this repair's claim.
    assert factual_texts == [BERLIN], (
        f"the Berlin Wall clause is not a knowledge node: {by_operation}"
    )
    assert any(EMPEROR in text for text in unresolved_texts), (
        f"the freshness-guarded Emperor clause must stay unresolved: {unresolved_texts}"
    )
    assert not any(BERLIN in text for text in unresolved_texts), (
        f"the Berlin Wall clause is still unresolved: {unresolved_texts}"
    )


def test_named_explanation_expand_admits_know_kind_but_not_fresh_or_non_know() -> None:
    """The expander's own gate: explanation-shaped OR a typed KNOW clause that passes the
    freshness guard. An action clause expands to nothing. A fresh-rate clause still expands
    through the LEGACY shape branch ("what is" is an old explanation cue) when called
    directly -- the freshness refusal is the admission layer's job, pinned above and by the
    existing compatibility matrix; the new KNOW branch must simply never open that door."""
    from core.conductor.operations import _named_explanation_expand, _plain_know_question

    assert _named_explanation_expand(ORWELL)
    assert _named_explanation_expand(BERLIN)
    assert _named_explanation_expand("Explain why closing a valve stops water flow")
    assert _named_explanation_expand("Shut off the overflowing sink") == []
    assert _plain_know_question("what is the current EUR/USD rate") is False
    assert _plain_know_question(EMPEROR) is False
