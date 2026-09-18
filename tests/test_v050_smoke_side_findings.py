"""QA-050-026 and QA-050-027 — the two side findings from the v0.5.0 smoke run.

QA-050-026  a raw Python `ValueError` reached the chat surface on a Lisbon/Madrid comparison
QA-050-027  "Answer in one sentence: give me the numbered steps to boil an egg." got a plan scaffold
"""
from __future__ import annotations

import pytest

from core.conductor.compose import ComposedAnswer, reader_facing_reason
from core.conductor.graph import ConductorGraph
from core.conductor.node import ConductorNode, NodeLifecycle, NodeOutcome
from core.conductor.planner import ConductorPlan
from core.reasoning_engine import explicit_planner_style_requested
from tests.conductor_product import compose_product

# ---------------------------------------------------------------------------------------------
# QA-050-026 — the runtime's internals stay out of the answer
# ---------------------------------------------------------------------------------------------

# The exact string the scheduler stored for the Lisbon/Madrid turn: one city's temperature never
# arrived, `_comparison_run` raised, and `scheduler.py` recorded `f"{type(exc).__name__}: {exc}"`.
_MEASURED_REASON = (
    "ValueError: comparison needs at least two dependencies carrying 'temperature_c'; got 1"
)


def _node(node_id: str, request_text: str, operation: str = "comparison") -> ConductorNode:
    return ConductorNode(
        node_id=node_id,
        operation=operation,
        request_text=request_text,
        arguments={},
        depends_on=(),
    )


def _plan(*nodes: ConductorNode) -> ConductorPlan:
    return ConductorPlan(
        plan_id="qa-050-026",
        original_request="which city is warmer, Lisbon or Madrid",
        graph=ConductorGraph(nodes=tuple(nodes)),
        clause_count=len(nodes),
    )


def test_a_failed_comparison_does_not_print_a_python_exception_at_the_user() -> None:
    node = _node("cmp", "which city is warmer, Lisbon or Madrid")
    composed = compose_product(
        _plan(node),
        [NodeOutcome(node=node, state=NodeLifecycle.FAILED, failure_reason=_MEASURED_REASON)],
    )

    assert isinstance(composed, ComposedAnswer)
    assert "ValueError" not in composed.text
    assert "temperature_c" not in composed.text
    assert "dependencies" not in composed.text
    # It still says the request failed, and still says which request.
    assert "which city is warmer, Lisbon or Madrid" in composed.text
    assert "could not be answered" in composed.text


def test_the_full_reason_survives_on_the_outcome_for_the_receipt() -> None:
    """Only the reader-facing sentence is rewritten; diagnosis must not get harder."""
    node = _node("cmp", "which city is warmer, Lisbon or Madrid")
    outcome = NodeOutcome(node=node, state=NodeLifecycle.FAILED, failure_reason=_MEASURED_REASON)

    compose_product(_plan(node), [outcome])

    assert outcome.failure_reason == _MEASURED_REASON


@pytest.mark.parametrize(
    "reason",
    [
        _MEASURED_REASON,
        "ValueError: no weather observation returned",
        "TimeoutError: read timed out after 60s",
        "RuntimeError: pool died",
        "scheduler fault: RuntimeError: pool died",
        "KeyError: 'temperature_c'",
    ],
)
def test_no_exception_class_name_survives_into_a_reader_facing_reason(reason: str) -> None:
    rewritten = reader_facing_reason(reason)

    assert "Error" not in rewritten
    assert "Exception" not in rewritten
    assert rewritten.strip()


def test_a_plain_reason_is_left_alone() -> None:
    """A reason already written for a person keeps its specifics — this is not a generic apology."""
    assert reader_facing_reason("the city name was not recognized") == "the city name was not recognized"


def test_an_unserved_request_is_still_named_and_still_reported() -> None:
    """Rewriting the reason must not turn a failure into silence."""
    served = _node("weather", "the weather in Lisbon", operation="weather_lookup")
    failed = _node("cmp", "which city is warmer")
    composed = compose_product(
        _plan(served, failed),
        [
            NodeOutcome(
                node=served,
                state=NodeLifecycle.SUCCEEDED,
                rendered="Lisbon is 19C.",
                result={"temperature_c": 19},
            ),
            NodeOutcome(node=failed, state=NodeLifecycle.FAILED, failure_reason=_MEASURED_REASON),
        ],
    )

    assert "Lisbon is 19C." in composed.text
    assert "Could not be answered:" in composed.text
    assert composed.unserved_count == 1
    assert composed.complete is True


# ---------------------------------------------------------------------------------------------
# QA-050-027 — an explicit answer shape beats the plan scaffold
# ---------------------------------------------------------------------------------------------


def test_a_one_sentence_request_does_not_become_a_plan_document() -> None:
    """`\\bsteps to\\b` matched "steps to boil an egg" and forced output_mode=action_plan."""
    assert not explicit_planner_style_requested(
        "Answer in one sentence: give me the numbered steps to boil an egg."
    )


@pytest.mark.parametrize(
    "turn",
    [
        "summarize the rollout plan in exactly 20 words",
        "give me the action plan in one sentence",
        "describe the workflow in exactly 12 words",
    ],
)
def test_any_stated_answer_shape_beats_the_planner(turn: str) -> None:
    assert not explicit_planner_style_requested(turn)


@pytest.mark.parametrize(
    "turn",
    [
        "give me a step-by-step rollout plan for the migration",
        "give me the numbered steps to boil an egg",
        "make me a plan for the release",
        "show me the steps",
        "give me an execution checklist",
    ],
)
def test_a_real_plan_request_still_gets_the_planner(turn: str) -> None:
    """The control. This fix declines only when the turn states its own answer shape."""
    assert explicit_planner_style_requested(turn)
