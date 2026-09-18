"""Semantic conductor admission: real operation does not mean right operation."""

from __future__ import annotations

import json

import pytest

from core.conductor.capabilities import (
    OperationCapability,
    OperationEffect,
    decide_operation_compatibility,
)
from core.conductor.node import NodeLifecycle
from core.conductor.planner import build_plan_from_clauses, parse_clauses
from core.conductor.registry import UNRESOLVED_OPERATION, NodeContext, operation_spec
from core.conductor.scheduler import run_conductor_plan
from core.turn_ir import ClauseKind, parse_turn_ir
from tests.conductor_product import compose_product


def _clause(text: str):
    clauses = parse_turn_ir(text).clauses
    assert len(clauses) == 1
    return clauses[0]


@pytest.mark.parametrize(
    ("operation", "clause_text", "allowed"),
    [
        ("calculation", "Calculate 58 × 7", True),
        ("weather_lookup", "get the current weather for Kaunas", True),
        ("market_quote", "what is the current BTC price", True),
        ("workspace_investigation", "Inspect the provider retry implementation", True),
        ("factual_explanation", "Explain why closing a valve stops water flow", True),
        ("workspace_investigation", "Shut off the overflowing sink", False),
        ("workspace_investigation", "Find a tyre shop near me", False),
        ("workspace_investigation", "Find the current EUR/USD rate", False),
        ("weather_lookup", "Shut off the overflowing sink", False),
        ("factual_explanation", "Shut off the overflowing sink", False),
        ("factual_explanation", "what is the current EUR/USD rate", False),
        ("factual_explanation", "suggest a tyre shop near me", False),
        ("conclusion", "whether Kaunas has rain", False),
    ],
)
def test_builtin_operation_kind_domain_effect_matrix(
    operation: str, clause_text: str, allowed: bool
) -> None:
    spec = operation_spec(operation)
    assert spec is not None

    decision = decide_operation_compatibility(spec.capability, _clause(clause_text))

    assert decision.allowed is allowed, decision.reason


def test_declaring_act_as_accepted_cannot_disguise_workspace_evidence_as_an_effect() -> None:
    """Accepted kinds do not override the effect matrix."""

    deceptive = OperationCapability(
        effect=OperationEffect.WORKSPACE_EVIDENCE,
        domain="deceptive workspace operation",
        accepted_kinds=frozenset({ClauseKind.ACT}),
    )

    decision = decide_operation_compatibility(deceptive, _clause("Shut off the sink"))

    assert not decision.allowed
    assert "workspace_evidence cannot satisfy a act request" in decision.reason


@pytest.mark.parametrize(
    "clause_text",
    [
        "Shut off the overflowing sink in my kitchen",
        "Find a tyre shop near me",
        "Find the current EUR/USD rate",
    ],
)
def test_wrong_domain_real_workspace_tool_is_unresolved_without_execution(clause_text: str) -> None:
    prompt = f"A) {clause_text}. B) Explain why closing a valve stops water flow."
    reply = json.dumps(
        [
            {"request": clause_text, "operation": "workspace_investigation", "depends_on": []},
            {
                "request": "Explain why closing a valve stops water flow",
                "operation": "factual_explanation",
                "depends_on": [],
            },
        ]
    )
    plan = build_plan_from_clauses(
        parse_clauses(reply), original_request=prompt, plan_id="wrong-domain"
    )

    wrong_domain = plan.nodes[0]
    assert wrong_domain.operation == UNRESOLVED_OPERATION
    assert wrong_domain.tool_intent == ""
    assert "unavailable for this request" in wrong_domain.unresolved_reason
    assert all(node.tool_intent != "workspace.search_text" for node in plan.nodes)


def test_failed_workspace_node_blocks_its_dependent_not_independent_knowledge() -> None:
    prompt = (
        "A) Inspect the sink source implementation. "
        "B) Explain the physics of why closing the valve stops the flow. "
        "C) Check whether the sink source has valve handling."
    )
    reply = json.dumps(
        [
            {
                "request": "Inspect the sink source implementation",
                "operation": "workspace_investigation",
                "depends_on": [],
            },
            {
                "request": "Explain the physics of why closing the valve stops the flow",
                "operation": "factual_explanation",
                "depends_on": [],
            },
            {
                "request": "Check whether the sink source has valve handling",
                "operation": "conclusion",
                "depends_on": [0],
            },
        ]
    )
    plan = build_plan_from_clauses(
        parse_clauses(reply), original_request=prompt, plan_id="dependency-matrix"
    )
    tool_calls: list[dict] = []

    def _tool(payload):
        tool_calls.append(payload)
        raise RuntimeError("workspace unavailable")

    outcomes = run_conductor_plan(
        plan,
        context=NodeContext(
            run_tool_intent=_tool,
            run_generation=lambda _system, _prompt: (
                "Closing the valve blocks the flow path, so pressure can no longer drive water "
                "through that opening."
            ),
        ),
    )
    composed = compose_product(plan, outcomes)

    assert [outcome.state for outcome in outcomes] == [
        NodeLifecycle.FAILED,
        NodeLifecycle.SUCCEEDED,
        NodeLifecycle.DEPENDENCY_FAILED,
    ]
    assert len(tool_calls) == 1
    assert tool_calls[0]["intent"] == "workspace.search_text"
    assert composed.complete
    assert composed.answered_count == 1
    assert composed.unserved_count == 2
    assert "Closing the valve blocks the flow path" in composed.text
    assert "Could not be answered:" in composed.text
    # R3 (AUD-20260829-003): the raw exception text must NOT reach the served answer any more --
    # this assertion used to check for "workspace unavailable" (the tool's raw message) verbatim,
    # which is exactly the leak class R3 closes. The failure is still named, just not verbatim.
    assert "workspace unavailable" not in composed.text
    assert "internal fault" in composed.text
    assert "not attempted" in composed.text


def test_derived_workspace_conclusion_rejects_a_live_weather_dependency_effect() -> None:
    prompt = (
        "A) Get weather for Kaunas. "
        "B) Check whether Kaunas has rain handling."
    )
    reply = json.dumps(
        [
            {
                "request": "Get weather for Kaunas",
                "operation": "weather_lookup",
                "depends_on": [],
            },
            {
                "request": "Check whether Kaunas has rain handling",
                "operation": "conclusion",
                "depends_on": [0],
            },
        ]
    )

    plan = build_plan_from_clauses(
        parse_clauses(reply), original_request=prompt, plan_id="wrong-dependency-effect"
    )

    assert plan.nodes[0].operation == "weather_lookup"
    assert plan.nodes[1].operation == UNRESOLVED_OPERATION
    assert "cannot consume dependency effects: live_observation" in (
        plan.nodes[1].unresolved_reason
    )


def test_independent_partial_composition_is_not_replaced_by_a_generic_failure() -> None:
    prompt = (
        "A) Shut off the overflowing sink in my kitchen. "
        "B) Explain the physics of why closing the valve stops the flow."
    )
    reply = json.dumps(
        [
            {
                "request": "Shut off the overflowing sink in my kitchen",
                "operation": "workspace_investigation",
                "depends_on": [],
            },
            {
                "request": "Explain the physics of why closing the valve stops the flow",
                "operation": "factual_explanation",
                "depends_on": [],
            },
        ]
    )
    plan = build_plan_from_clauses(
        parse_clauses(reply), original_request=prompt, plan_id="progressive"
    )
    outcomes = run_conductor_plan(
        plan,
        context=NodeContext(
            run_generation=lambda _system, _prompt: (
                "The valve closes the available flow path, preventing pressure from moving water "
                "through the pipe."
            )
        ),
    )

    composed = compose_product(plan, outcomes)

    assert outcomes[0].state is NodeLifecycle.UNRESOLVED
    assert "workspace source capability does not serve act requests" in outcomes[0].failure_reason
    assert outcomes[1].state is NodeLifecycle.SUCCEEDED
    assert composed.complete
    assert composed.answered_count == 1
    assert composed.unserved_count == 1
    assert composed.text.startswith("The valve closes the available flow path")
    assert "Could not be answered:" in composed.text
    assert "normal chat response" not in composed.text.lower()
