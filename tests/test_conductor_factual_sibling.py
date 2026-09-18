"""Stable factual siblings remain answerable without widening conductor authority."""

from __future__ import annotations

import json
import threading
from unittest import mock

import pytest

from core.conductor.capabilities import decide_operation_compatibility
from core.conductor.node import NodeLifecycle
from core.conductor.planner import build_plan_from_clauses, parse_clauses
from core.conductor.registry import UNRESOLVED_OPERATION, NodeContext, operation_spec
from core.conductor.scheduler import run_conductor_plan
from core.turn_ir import parse_turn_ir
from tests.conductor_product import compose_product

SET5_21 = (
    '"The API requires a TCP handshake before the DAO votes." Explain what DAO means in crypto '
    "and what API means in software. Do NOT search for live DAO governance votes or crypto prices."
)
SET5_21_PLAN = json.dumps(
    [
        {
            "request": "explain what DAO means in crypto",
            "operation": "factual_explanation",
            "depends_on": [],
        },
        {
            "request": "what API means in software",
            "operation": "factual_explanation",
            "depends_on": [],
        },
    ]
)


def _one_clause(text: str):
    clauses = parse_turn_ir(text).clauses
    assert len(clauses) == 1
    return clauses[0]


def _set5_plan():
    return build_plan_from_clauses(
        parse_clauses(SET5_21_PLAN),
        original_request=SET5_21,
        plan_id="set5-21-factual-sibling",
    )


def _answering_clause(prompt: str) -> str:
    marker = "The part of it you are answering:"
    assert marker in prompt
    return prompt.split(marker, 1)[1].lstrip("\n").split("\n", 1)[0].strip()


def test_exact_set5_21_plan_keeps_both_safe_factual_siblings() -> None:
    plan = _set5_plan()

    assert [node.operation for node in plan.nodes] == [
        "factual_explanation",
        "factual_explanation",
    ]
    assert not plan.unresolved
    assert all(not node.unresolved_reason for node in plan.nodes)


@pytest.mark.parametrize(
    "query",
    (
        "What does API mean in software?",
        "What API means in software?",
        "Tell me what API means in software.",
        "What API stands for in software.",
        "Define API in software.",
        "What's API mean in software?",
    ),
)
def test_clean_and_sloppy_term_definition_forms_are_safe_knowledge(query: str) -> None:
    spec = operation_spec("factual_explanation")
    assert spec is not None
    clause = _one_clause(query)

    assert decide_operation_compatibility(spec.capability, clause).allowed
    assert spec.expand_named_arguments is not None
    assert spec.expand_named_arguments(query, None)


def test_exact_set5_21_executes_each_sibling_once_and_preserves_both() -> None:
    plan = _set5_plan()
    calls: list[str] = []
    lock = threading.Lock()

    def _generate(_system: str, prompt: str) -> str:
        with lock:
            calls.append(prompt)
        clause = _answering_clause(prompt)
        if "DAO" in clause:
            return "DAO means Decentralized Autonomous Organization in crypto."
        if "API" in clause:
            return "API means Application Programming Interface in software."
        raise AssertionError(f"unexpected conductor prompt: {prompt}")

    outcomes = run_conductor_plan(
        plan,
        context=NodeContext(run_generation=_generate),
    )
    answer = compose_product(plan, outcomes)

    assert [outcome.state for outcome in outcomes] == [
        NodeLifecycle.SUCCEEDED,
        NodeLifecycle.SUCCEEDED,
    ]
    assert len(calls) == 2
    assert sum("DAO" in _answering_clause(prompt) for prompt in calls) == 1
    assert sum("API" in _answering_clause(prompt) for prompt in calls) == 1
    assert answer.complete and answer.answered_count == 2 and answer.unserved_count == 0
    assert "Decentralized Autonomous Organization" in answer.text
    assert "Application Programming Interface" in answer.text
    assert "factual_explanation" not in answer.text
    assert "capability domain" not in answer.text


@pytest.mark.parametrize(
    "query",
    (
        "What does API mean right now?",
        "What does API mean in the latest release?",
        "What API means near me?",
        "Do NOT search for live API status.",
    ),
)
def test_fresh_or_forbidden_lookup_text_cannot_borrow_factual_authority(query: str) -> None:
    spec = operation_spec("factual_explanation")
    assert spec is not None

    decision = decide_operation_compatibility(spec.capability, _one_clause(query))

    assert not decision.allowed


@pytest.mark.parametrize(
    "query",
    (
        "Shut down the API server.",
        "Search the workspace for the API implementation.",
        "The API means one service can call another.",
    ),
)
def test_action_observation_and_plain_statement_are_not_definition_requests(query: str) -> None:
    spec = operation_spec("factual_explanation")
    assert spec is not None

    assert not decide_operation_compatibility(spec.capability, _one_clause(query)).allowed


@pytest.mark.parametrize(
    "guarded_sibling",
    (
        "what is the current BTC price",
        "Shut down the API server",
    ),
)
def test_live_or_action_sibling_stays_unresolved_without_execution_or_internal_labels(
    guarded_sibling: str,
) -> None:
    api = "what API means in software"
    original = f"{api}. {guarded_sibling}."
    proposed = json.dumps(
        [
            {"request": api, "operation": "factual_explanation", "depends_on": []},
            {
                "request": guarded_sibling,
                "operation": "factual_explanation",
                "depends_on": [],
            },
        ]
    )
    plan = build_plan_from_clauses(
        parse_clauses(proposed), original_request=original, plan_id="guarded-sibling"
    )
    calls: list[str] = []
    tool = mock.Mock(side_effect=AssertionError("guarded sibling reached a tool"))

    outcomes = run_conductor_plan(
        plan,
        context=NodeContext(
            run_generation=lambda _system, prompt: (
                calls.append(prompt)
                or "API means Application Programming Interface in software."
            ),
            run_tool_intent=tool,
        ),
    )
    answer = compose_product(plan, outcomes)

    assert [outcome.state for outcome in outcomes] == [
        NodeLifecycle.SUCCEEDED,
        NodeLifecycle.UNRESOLVED,
    ]
    assert len(calls) == 1
    tool.assert_not_called()
    assert "Application Programming Interface" in answer.text
    assert guarded_sibling in answer.text
    assert "Could not be answered" in answer.text
    assert "factual_explanation" not in answer.text
    assert "capability domain" not in answer.text


def test_ambiguous_acronym_instruction_requires_clarification_without_a_domain() -> None:
    spec = operation_spec("factual_explanation")
    assert spec is not None and spec.expand_named_arguments is not None
    request = "What does ABC mean?"
    arguments = spec.expand_named_arguments(request, None)[0]
    node = _set5_plan().nodes[0]
    node = type(node)(
        node_id="ambiguous-acronym",
        operation="factual_explanation",
        request_text=request,
        arguments=arguments,
        required_result_fields=("text",),
        needs_generation=True,
    )
    seen_system: list[str] = []

    def _clarify(system: str, _prompt: str) -> str:
        seen_system.append(system)
        return "ABC has several expansions. Which domain do you mean?"

    result = spec.run(node, NodeContext(run_generation=_clarify))

    assert "gives no domain" in seen_system[0]
    assert "which domain" in result["text"].lower()


def test_sabotage_old_cue_only_gate_recreates_the_api_unresolved_node(monkeypatch) -> None:
    from core.conductor import operations

    original = operations._asks_for_explanation
    monkeypatch.setattr(
        operations,
        "_asks_for_explanation",
        lambda text: operations._clause_has(text, operations._EXPLANATION_CUES),
    )
    # AMENDED for F41: sabotaging the cue gate alone no longer recreates the unresolved node,
    # because the named-path KNOW admission is a second, structural route into the same
    # family — the term-definition clause is served either way, which is the product
    # behaviour the original sabotage existed to protect. The sabotage must now remove the
    # KNOW admission (both the capability bypass and the expander's plain-question branch)
    # before the failure it proves can recur.
    original_know = operations._plain_know_question
    original_admits = operations.knowledge_clause_admits_named
    monkeypatch.setattr(operations, "_plain_know_question", lambda text: False)
    monkeypatch.setattr(
        operations, "knowledge_clause_admits_named", lambda clause: False
    )
    try:
        plan = _set5_plan()
    finally:
        monkeypatch.setattr(operations, "_asks_for_explanation", original)
        monkeypatch.setattr(operations, "_plain_know_question", original_know)
        monkeypatch.setattr(operations, "knowledge_clause_admits_named", original_admits)

    assert plan.nodes[0].operation == "factual_explanation"
    assert plan.nodes[1].operation == UNRESOLVED_OPERATION
    assert "outside the knowledge explanation capability domain" in plan.nodes[1].unresolved_reason


def test_real_run_once_returns_both_definitions_without_tools_or_duplicate_nodes(
    make_agent,
    monkeypatch,
) -> None:
    agent = make_agent()
    planner_calls: list[str] = []
    generation_calls: list[str] = []

    def _planner(system: str, prompt: str) -> str:
        # Recorded by SYSTEM prompt, because one injected seam now serves two distinct stages: the
        # clause decomposition, and the bounded semantic classification the requirement proof needs.
        # The guard that matters is that neither stage is entered twice -- a duplicate DECOMPOSITION
        # is the defect this test was written for, and it stays asserted below.
        planner_calls.append((system, prompt))
        return SET5_21_PLAN

    def _generation(_system: str, prompt: str) -> str:
        generation_calls.append(prompt)
        clause = _answering_clause(prompt)
        if "DAO" in clause:
            return "DAO means Decentralized Autonomous Organization in crypto."
        if "API" in clause:
            return "API means Application Programming Interface in software."
        raise AssertionError(f"unexpected conductor prompt: {prompt}")

    tool = mock.Mock(side_effect=AssertionError("Set5-21 reached a tool"))
    monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook.build_planner_ask_model",
        lambda *_args, **_kwargs: _planner,
    )
    monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook.build_conductor_ask_model",
        lambda *_args, **_kwargs: _generation,
    )
    monkeypatch.setattr(agent, "_execute_tool_intent", tool)

    result = agent.run_once(
        SET5_21,
        session_id_override="set5-21-factual-sibling-run-once",
        source_context={
            "surface": "api",
            "platform": "api",
            "operating_mode": "auto",
            "allow_remote_fetch": False,
        },
    )

    assert result["route_reason"] == "conductor_multi_intent_plan"
    assert "Decentralized Autonomous Organization" in result["response"]
    assert "Application Programming Interface" in result["response"]
    assert "factual_explanation" not in result["response"]
    assert "capability domain" not in result["response"]
    decompositions = [
        call for call in planner_calls if call[0].startswith("You split a user's message")
    ]
    classifications = [
        call
        for call in planner_calls
        if call[0].startswith("You label the semantic structure")
    ]
    assert len(decompositions) == 1, "the message was decomposed more than once"
    assert len(classifications) <= 1, "the turn was semantically classified more than once"
    assert len(generation_calls) == 2
    assert result["web_calls"] == 0
    tool.assert_not_called()
