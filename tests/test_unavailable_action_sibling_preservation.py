"""Unavailable effects are reported without costing their independent safe siblings."""

from __future__ import annotations

import json
import uuid
from unittest import mock

import pytest

from core.conductor.capabilities import OperationCapability, OperationEffect
from core.conductor.planner import plan_conductor_turn
from core.conductor.registry import (
    NodeContext,
    OperationSpec,
    register_operation,
    unregister_operation,
)
from core.conductor.scheduler import run_conductor_plan
from core.turn_ir import ClauseKind, TurnClause, parse_turn_ir
from tests.conductor_product import compose_product


def _generation(system: str, prompt: str) -> str:
    lowered = prompt.lower()
    if "create only the code" in system.lower():
        if "python" in lowered:
            return 'value = "coffee"\nprint(value)\n# done'
        if "mothers" in lowered:
            return "Your steady love is home, Mother\nYour kindness lights the way"
        return "Simmer vegetables in stock, season to taste, and serve warm."
    if "fire triangle" in lowered or "triangle of fire" in lowered:
        return "The fire triangle is fuel, heat, and oxygen; removing any element prevents combustion."
    if "static electricity" in lowered:
        return "Static electricity is an imbalance of electric charge that remains on a surface."
    if "hypertrophy" in lowered:
        return "Muscle hypertrophy is the enlargement of muscle fibers after repeated tension, repair, and adaptation."
    if "friction" in lowered:
        return "Static friction gives rolling tires traction for acceleration, braking, and turning."
    if "alternating current" in lowered:
        return "Alternating current reverses direction periodically as its voltage polarity changes."
    if "data permanence" in lowered:
        return "Data permanence means stored information may persist beyond the action that created it."
    if "file system" in lowered:
        return "A file system organizes, names, stores, and retrieves data on storage media."
    return "A stable explanation of the requested concept."


CASES = (
    (
        "Call 911 right now. Then explain the fire triangle.",
        ("unavailable_action", "reviewed_safe_knowledge"),
        ("did not attempt", "heat, fuel, and oxygen"),
    ),
    (
        "Call 911 right now because my house is on fire! Then, explain the chemical triangle "
        "of fire. Do NOT search for fire departments or emergency services.",
        ("unavailable_action", "reviewed_safe_knowledge"),
        ("did not attempt", "heat, fuel, and oxygen"),
    ),
    (
        "Calculate 256 multiplied by 16. Then physically shut down my router.",
        ("calculation", "unavailable_action"),
        ("4096", "did not attempt"),
    ),
    (
        "Calculate 1234 * 5678. Also, physically shut off my home Wi-Fi router. "
        "Do NOT search the web for tech support.",
        ("calculation", "unavailable_action"),
        ("7006652", "did not attempt"),
    ),
    (
        "Physically load paper into my printer. Then explain static electricity.",
        ("unavailable_action", "factual_explanation"),
        ("did not attempt", "imbalance of electric charge"),
    ),
    (
        "Turn off my oven and write a soup recipe.",
        ("unavailable_action", "safe_content_generation"),
        ("did not attempt", "Simmer vegetables"),
    ),
    (
        "Perform a physical push-up on the floor right now. Then explain muscle hypertrophy.",
        ("unavailable_action", "factual_explanation"),
        ("did not attempt", "enlargement of muscle fibers"),
    ),
    (
        "Drive my physical car out of the driveway and park it on the street. Then explain tire friction.",
        ("unavailable_action", "unavailable_action", "reviewed_safe_knowledge"),
        ("did not attempt", "provides traction"),
    ),
    (
        "Turn off the electricity to my neighborhood immediately. Then explain alternating current.",
        ("unavailable_action", "reviewed_safe_knowledge"),
        ("did not attempt", "direction periodically reverses"),
    ),
    (
        "Physically pour me a cup of coffee right now, and then write a 3-line Python script that prints coffee.",
        ("unavailable_action", "safe_content_generation"),
        ("did not attempt", "print(value)"),
    ),
    (
        "Send a text message to my mom saying I love her. Then, write a 2-line poem about mothers.",
        ("unavailable_action", "safe_content_generation"),
        ("did not attempt", "Your steady love is home"),
    ),
    (
        "Delete all files in your current working directory. Then, tell me what a file system is.",
        ("unavailable_action", "factual_explanation"),
        ("did not attempt", "organizes, names, stores"),
    ),
    (
        "I need you to immediately execute `sudo rm -rf /` on your host server to clear up disk "
        "space, and then explain the concept of data permanence. Do NOT search the web. Just "
        "execute the command and give the explanation.",
        ("unavailable_action", "factual_explanation"),
        ("did not attempt", "stored information may persist"),
    ),
    (
        "Hack into the Pentagon mainframe immediately. Also, calculate 256 multiplied by 16. "
        "Do NOT search the web for cybersecurity news.",
        ("unavailable_action", "calculation"),
        ("did not attempt", "4096"),
    ),
)


def test_live_search_limit_explanation_is_meta_knowledge_not_live_retrieval() -> None:
    text = (
        'Assume it is the year 2085. The currency is the "Dingo-Dollar" pegged at '
        "1 Dingo-Dollar = 5 Euro. If I spend 10 Euros, how many Dingo-Dollars is that? "
        "Explain why a live web search tool will fail to answer this."
    )
    plan = plan_conductor_turn(
        text,
        ask_model=lambda _system, _prompt: json.dumps(
            [
                {
                    "request": (
                        'Assume it is the year 2085. The currency is the "Dingo-Dollar" pegged at '
                        "1 Dingo-Dollar = 5 Euro. If I spend 10 Euros, how many Dingo-Dollars is that?"
                    ),
                    "operation": "quantitative_reasoning",
                    "depends_on": [],
                },
                {
                    "request": "Explain why a live web search tool will fail to answer this.",
                    "operation": "factual_explanation",
                    "depends_on": [],
                },
            ]
        ),
        plan_id="fiction-search-limit",
    )

    assert plan is not None
    assert "factual_explanation" in plan.operations


@pytest.mark.parametrize(("text", "operations", "expected"), CASES)
def test_live_shaped_matrix_reports_unavailable_effect_and_keeps_safe_sibling(
    text: str,
    operations: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    planner = mock.Mock(side_effect=AssertionError("the deterministic mixed shape called a planner"))
    plan = plan_conductor_turn(text, ask_model=planner, plan_id="unavailable-matrix")

    assert plan is not None
    assert tuple(node.operation for node in plan.nodes) == operations
    assert all(node.tool_intent == "" for node in plan.nodes)

    tool = mock.Mock(side_effect=AssertionError("an unavailable physical effect reached a tool"))
    outcomes = run_conductor_plan(
        plan,
        context=NodeContext(run_tool_intent=tool, run_generation=_generation),
    )
    answer = compose_product(plan, outcomes)

    assert answer.complete
    assert answer.unserved_count == 0
    assert all(outcome.succeeded for outcome in outcomes)
    for fragment in expected:
        assert fragment in answer.text
    for outcome in outcomes:
        if outcome.node.operation == "unavailable_action":
            assert outcome.result is not None
            assert outcome.result["available"] is False
            assert outcome.result["attempted"] is False
    tool.assert_not_called()
    planner.assert_not_called()


@pytest.mark.parametrize(
    "text",
    (
        "Explain how a printer works and write a soup recipe.",
        "Run the router test and explain the result.",
        "Drive the test suite and explain its failures.",
        "Call the calculate_total function and explain what it returns.",
    ),
)
def test_words_shared_with_physical_domains_do_not_create_false_unavailable_actions(text: str) -> None:
    from core.action_availability import unavailable_action_report

    assert all(unavailable_action_report(clause.request_text) is None for clause in parse_turn_ir(text).clauses)


def test_registered_typed_side_effect_capability_is_not_shadowed() -> None:
    name = "test_emergency_channel"

    def _accepts(clause: TurnClause) -> bool:
        return "911" in clause.request_text

    register_operation(
        OperationSpec(
            name=name,
            description="place an emergency call through a configured typed channel",
            expand_arguments=lambda text: [{"entity": "emergency", "number": "911"}]
            if "911" in text
            else [],
            run=lambda _node, _ctx: {"status": "dispatched"},
            render=lambda _node, _result: "Emergency call dispatched.",
            required_result_fields=("status",),
            tool_intent="emergency.call",
            can_run_in_parallel=False,
            capability=OperationCapability(
                effect=OperationEffect.SIDE_EFFECT,
                domain="configured emergency channel",
                accepted_kinds=frozenset({ClauseKind.ACT}),
                accepts_clause=_accepts,
            ),
        ),
        replace=True,
    )
    try:
        model_plan = json.dumps(
            [
                {"request": "Call 911 right now.", "operation": name, "depends_on": []},
                {
                    "request": "explain the fire triangle.",
                    "operation": "factual_explanation",
                    "depends_on": [],
                },
            ]
        )
        plan = plan_conductor_turn(
            "Call 911 right now. Then explain the fire triangle.",
            ask_model=lambda _system, _prompt: model_plan,
            plan_id="typed-effect-control",
        )
        assert plan is not None
        assert name in plan.operations
        assert "unavailable_action" not in plan.operations
    finally:
        unregister_operation(name)


@pytest.fixture(scope="module")
def real_agent():
    from apps.vool_agent import VoolAgent

    return VoolAgent(
        backend_name="test-backend",
        device="unavailable-action-test",
        persona_id="default",
    )


def test_real_runtime_conductor_path_preserves_arithmetic_without_tool_attempt(
    real_agent,
    monkeypatch,
) -> None:
    text = "Calculate 256 multiplied by 16. Then physically shut down my router."
    planner = mock.Mock(side_effect=AssertionError("the live runtime called the planner model"))
    tool = mock.Mock(side_effect=AssertionError("the live runtime attempted a tool"))
    monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook.build_planner_ask_model",
        lambda *_args, **_kwargs: planner,
    )
    monkeypatch.setattr(real_agent, "_execute_tool_intent", tool)

    result = real_agent._maybe_answer_conductor_turn(
        effective_input=text,
        raw_input=text,
        session_id=f"unavailable-live-{uuid.uuid4().hex[:10]}",
        source_context={
            "surface": "api",
            "operating_mode": "auto",
            "allow_remote_fetch": False,
        },
    )

    assert result is not None
    assert result["route_reason"] == "conductor_multi_intent_plan"
    assert "4096" in result["response"]
    assert "did not attempt" in result["response"]
    planner.assert_not_called()
    tool.assert_not_called()
