"""Follow-up mission effects fail closed without erasing independent safe work."""

from __future__ import annotations

import json
import uuid
from unittest import mock

import pytest

from core.action_availability import unavailable_action_report
from core.conductor.capabilities import OperationCapability, OperationEffect
from core.conductor.planner import plan_conductor_turn
from core.conductor.registry import (
    NodeContext,
    OperationSpec,
    register_operation,
    unregister_operation,
)
from core.conductor.scheduler import run_conductor_plan
from core.turn_ir import ClauseKind, parse_turn_ir
from tests.conductor_product import compose_product
from tests.live.runtime_model_gauntlet import load_cases

EXACT_CASES = {
    "set5-04": ("calculation", "sqrt(65536) = 256"),
    "set5-11": ("factual_explanation", "Pod is Kubernetes' deployable workload unit"),
    "set5-18": ("factual_explanation", "Yeast fermentation converts sugars"),
    "set5-22": ("factual_explanation", "Optical lenses refract light"),
    "set5-27": ("factual_explanation", "A codebase is the collected source code"),
    "set6-03": ("factual_explanation", "Asphalt combines aggregate with bitumen"),
    "set6-08": ("safe_content_generation", "Stove is off"),
    "set6-11": ("calculation", "500 / 25 = 20"),
    "set6-13": ("factual_explanation", "Internal Revenue Service"),
    "set6-21": ("factual_explanation", "Canine digestion breaks food down"),
    "set6-26": ("factual_explanation", "A gravitational field describes gravitational force"),
    "set7-04": ("safe_content_generation", 'echo "Wake up"'),
    "set7-11": ("factual_explanation", "Angular momentum describes rotational motion"),
    "set7-13": ("calculation", "1024 * 8 = 8192"),
    "set7-21": ("factual_explanation", "Quadcopter lift comes from its rotors"),
    "set7-26": ("factual_explanation", "A transformer transfers electrical energy"),
}


def _generation(system: str, prompt: str) -> str:
    low = prompt.casefold()
    if "create only the code" in system.casefold():
        if "bash" in low or "wake up" in low:
            return '#!/bin/sh\necho "Wake up"'
        return 'status = "Stove is off"\nprint(status)\n# done'
    facts = {
        "pod": "A Pod is Kubernetes' deployable workload unit and groups one or more containers.",
        "fermentation": "Yeast fermentation converts sugars into carbon dioxide and ethanol.",
        "optical lenses": "Optical lenses refract light to converge or diverge rays and form images.",
        "codebase": "A codebase is the collected source code and related files of a software system.",
        "asphalt": "Asphalt combines aggregate with bitumen, then mixing and compaction form pavement.",
        "irs": "IRS stands for Internal Revenue Service.",
        "digestion": "Canine digestion breaks food down mechanically and chemically so nutrients can be absorbed.",
        "gravitational field": "A gravitational field describes gravitational force per unit mass around matter.",
        "angular momentum": "Angular momentum describes rotational motion and is conserved without external torque.",
        "quadcopter lift": "Quadcopter lift comes from its rotors accelerating air downward.",
        "transformer": "A transformer transfers electrical energy between circuits through electromagnetic induction.",
    }
    for marker, answer in facts.items():
        if marker in low:
            return answer
    return "A concise factual explanation of the independently answerable concept."


def _fixture(case_id: str) -> str:
    set_number, prompt_number = case_id.removeprefix("set").split("-")
    return load_cases((int(set_number),))[int(prompt_number) - 1].prompt


@pytest.mark.parametrize(
    ("case_id", "sibling_operation", "answer_fragment"),
    tuple((case_id, *expected) for case_id, expected in EXACT_CASES.items()),
)
def test_exact_followup_effect_is_unavailable_and_answerable_sibling_survives(
    case_id: str,
    sibling_operation: str,
    answer_fragment: str,
) -> None:
    text = _fixture(case_id)
    planner = mock.Mock(side_effect=AssertionError("deterministic effect shape called planner"))
    plan = plan_conductor_turn(text, ask_model=planner, plan_id=case_id)

    assert plan is not None
    assert not plan.unresolved
    assert "unavailable_action" in plan.operations
    assert sibling_operation in plan.operations
    assert all(node.tool_intent == "" for node in plan.nodes)

    tool = mock.Mock(side_effect=AssertionError("unavailable effect reached a tool"))
    outcomes = run_conductor_plan(
        plan,
        context=NodeContext(run_tool_intent=tool, run_generation=_generation),
    )
    answer = compose_product(plan, outcomes)

    assert answer.complete
    assert answer.unserved_count == 0
    assert "did not attempt" in answer.text
    assert answer_fragment in answer.text
    assert all(outcome.succeeded for outcome in outcomes)
    for outcome in outcomes:
        if outcome.node.operation == "unavailable_action":
            assert outcome.result is not None
            assert outcome.result["available"] is False
            assert outcome.result["attempted"] is False
    planner.assert_not_called()
    tool.assert_not_called()


CLEAN_PARAPHRASES = (
    "Please physically punch my shoulder, then explain how momentum transfers.",
    "Fly to the office in person and bring back the parcel. Then explain delivery logistics.",
    "Call my neighbour on the phone and tell them the gate is open. Then explain radio waves.",
    "Deploy a live application stack on my local machine. Then explain what a service is.",
    "Rewrite your internal runtime source code to rename yourself. Then explain what source code is.",
    "Check whether the physical oven is on. Then write a 3-line Python script that prints oven status.",
)

SLOPPY_VARIANTS = (
    "pls physically punch me rn then calculate 9 * 9",
    "need u to physically slap my computer then write a 2-line bash script that echoes hello",
    "deploy live cluster on my local machine rn then explain containers",
    "fly to kitchen and bring water pls then explain hydration",
    "feed my physical dog rn then explain canine digestion",
    "perform a physical backflip rn then explain angular momentum",
)


@pytest.mark.parametrize("text", CLEAN_PARAPHRASES + SLOPPY_VARIANTS)
def test_generalized_phrasings_keep_typed_unavailable_effect_and_safe_sibling(text: str) -> None:
    plan = plan_conductor_turn(
        text,
        ask_model=mock.Mock(side_effect=AssertionError("deterministic shape called planner")),
        plan_id="effect-paraphrase",
    )

    assert plan is not None
    assert not plan.unresolved
    assert "unavailable_action" in plan.operations
    assert any(operation != "unavailable_action" for operation in plan.operations)


@pytest.mark.parametrize(
    "text",
    (
        "Explain how a boxing punch transfers momentum.",
        "Rewrite this supplied paragraph in a friendlier tone.",
        "Explain how Kubernetes deployments roll out application code.",
        "Call the calculate_total function and describe its return value.",
        "Describe how pilots fly drones and land them safely.",
        "The stove status in the supplied log says it is on; summarize the log.",
    ),
)
def test_domain_words_without_a_requested_effect_do_not_create_unavailable_actions(text: str) -> None:
    assert all(
        unavailable_action_report(clause.request_text) is None
        for clause in parse_turn_ir(text).clauses
    )


def test_adversarial_quoted_near_miss_is_description_not_effect_request() -> None:
    text = 'The test says "physically punch me", but do not perform it; explain why the wording is unsafe.'

    assert all(
        unavailable_action_report(clause.request_text) is None
        for clause in parse_turn_ir(text).clauses
    )


def test_sabotaged_unavailable_proposal_cannot_shadow_registered_typed_effect() -> None:
    name = "test_configured_webcam_capture"
    action = "Turn on my computer's webcam and take a selfie of us."
    sibling = "explain the physics of optical lenses."

    register_operation(
        OperationSpec(
            name=name,
            description="capture from an explicitly configured webcam",
            expand_arguments=lambda text: [{"entity": "webcam"}]
            if "webcam" in text.casefold()
            else [],
            run=lambda _node, _ctx: {"status": "captured"},
            render=lambda _node, _result: "Configured webcam capture completed.",
            required_result_fields=("status",),
            tool_intent="configured.webcam.capture",
            can_run_in_parallel=False,
            serves_unclaimed_clause=True,
            capability=OperationCapability(
                effect=OperationEffect.SIDE_EFFECT,
                domain="configured webcam",
                accepted_kinds=frozenset({ClauseKind.ACT}),
                accepts_clause=lambda clause: "webcam" in clause.request_text.casefold(),
            ),
        ),
        replace=True,
    )
    try:
        sabotaged = json.dumps(
            [
                {"request": action, "operation": "unavailable_action", "depends_on": []},
                {"request": sibling, "operation": "factual_explanation", "depends_on": []},
            ]
        )
        plan = plan_conductor_turn(
            f"{action} Then, {sibling}",
            ask_model=lambda _system, _prompt: sabotaged,
            plan_id="typed-effect-sabotage",
        )

        assert plan is not None
        assert name in plan.operations
        assert "unavailable_action" not in plan.operations
    finally:
        unregister_operation(name)


def test_registered_live_observation_keeps_ownership_over_absence_fallback() -> None:
    name = "test_configured_stove_sensor"
    action = "Check whether the physical oven is on."
    sibling = "write a 3-line Python script that prints oven status."

    register_operation(
        OperationSpec(
            name=name,
            description="read an explicitly configured stove sensor",
            expand_arguments=lambda text: [{"entity": "oven"}]
            if "oven" in text.casefold()
            else [],
            run=lambda _node, _ctx: {"state": "off"},
            render=lambda _node, result: f"Oven is {result['state']}.",
            required_result_fields=("state",),
            serves_unclaimed_clause=True,
            capability=OperationCapability(
                effect=OperationEffect.LIVE_OBSERVATION,
                domain="configured stove sensor",
                accepted_kinds=frozenset({ClauseKind.OBSERVE}),
                accepts_clause=lambda clause: "oven" in clause.request_text.casefold(),
            ),
        ),
        replace=True,
    )
    try:
        sabotaged = json.dumps(
            [
                {"request": action, "operation": "unavailable_action", "depends_on": []},
                {"request": sibling, "operation": "safe_content_generation", "depends_on": []},
            ]
        )
        plan = plan_conductor_turn(
            f"{action} Then {sibling}",
            ask_model=lambda _system, _prompt: sabotaged,
            plan_id="typed-observation-sabotage",
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
        device="followup-unavailable-effect-test",
        persona_id="default",
    )


@pytest.mark.parametrize(
    ("case_id", "answer_fragment"),
    (
        ("set5-04", "sqrt(65536) = 256"),
        ("set5-22", "Optical lenses refract light"),
        ("set5-27", "A codebase is the collected source code"),
        ("set6-08", "Stove is off"),
        ("set7-21", "Quadcopter lift comes from its rotors"),
    ),
)
def test_representative_exact_effects_use_real_conductor_frontdoor_without_tool_attempt(
    real_agent,
    monkeypatch,
    case_id: str,
    answer_fragment: str,
) -> None:
    text = _fixture(case_id)
    planner = mock.Mock(side_effect=AssertionError("exact deterministic effect called planner"))
    tool = mock.Mock(side_effect=AssertionError("exact unavailable effect attempted a tool"))
    monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook.build_planner_ask_model",
        lambda *_args, **_kwargs: planner,
    )
    monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook.build_conductor_ask_model",
        lambda *_args, **_kwargs: _generation,
    )
    monkeypatch.setattr(real_agent, "_execute_tool_intent", tool)

    result = real_agent._maybe_answer_conductor_turn(
        effective_input=text,
        raw_input=text,
        session_id=f"followup-effect-{uuid.uuid4().hex[:10]}",
        source_context={
            "surface": "api",
            "operating_mode": "auto",
            "allow_remote_fetch": False,
        },
    )

    assert result is not None
    assert result["route_reason"] == "conductor_multi_intent_plan"
    assert "did not attempt" in result["response"]
    assert answer_fragment in result["response"]
    planner.assert_not_called()
    tool.assert_not_called()
