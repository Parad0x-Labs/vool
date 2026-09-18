"""Reviewed stable facts survive unavailable action siblings without model latency."""

from __future__ import annotations

import uuid
from unittest import mock

import pytest

from core.conductor.planner import plan_conductor_turn
from core.conductor.registry import NodeContext, operation_spec
from core.conductor.scheduler import run_conductor_plan
from core.turn_ir import ClauseKind, TurnClause
from tests.conductor_product import compose_product
from tests.live.runtime_model_gauntlet import load_cases

FROZEN_SET1_11 = load_cases((1,))[10].prompt
FROZEN_SET1_28 = load_cases((1,))[27].prompt
FROZEN_SET3_21 = load_cases((3,))[20].prompt
FROZEN_SET3_26 = load_cases((3,))[25].prompt
FROZEN_SET2_04 = load_cases((2,))[3].prompt
FROZEN_SET2_26 = load_cases((2,))[25].prompt


@pytest.mark.parametrize(
    "prompt",
    (
        FROZEN_SET1_11,
        "Call 911 now. Then explain the fire triangle.",
        "Dial 911 immediately. Then describe the combustion triangle.",
        "Call 911 right now. Then explain the triangle of combustion.",
    ),
)
def test_fire_triangle_variants_use_reviewed_knowledge_without_model_or_tool(prompt: str) -> None:
    planner = mock.Mock(side_effect=AssertionError("reviewed mixed shape invoked the planner"))
    model = mock.Mock(side_effect=AssertionError("reviewed fire fact invoked a model"))
    tool = mock.Mock(side_effect=AssertionError("unavailable emergency action reached a tool"))

    plan = plan_conductor_turn(prompt, ask_model=planner, plan_id="reviewed-fire-triangle")

    assert plan is not None
    assert tuple(node.operation for node in plan.nodes) == (
        "unavailable_action",
        "reviewed_safe_knowledge",
    )
    assert all(node.tool_intent == "" for node in plan.nodes)
    outcomes = run_conductor_plan(
        plan,
        context=NodeContext(run_generation=model, run_tool_intent=tool),
    )
    answer = compose_product(plan, outcomes)

    assert answer.complete
    assert answer.answered_count == 2
    assert answer.unserved_count == 0
    assert "I cannot perform that action" in answer.text
    assert "heat, fuel, and oxygen" in answer.text
    assert "removing any one" in answer.text
    assert "stops combustion" in answer.text
    planner.assert_not_called()
    model.assert_not_called()
    tool.assert_not_called()


def test_kernel_panic_sibling_refuses_reboot_and_uses_reviewed_fact() -> None:
    planner = mock.Mock(side_effect=AssertionError("kernel panic turn invoked planner"))
    model = mock.Mock(side_effect=AssertionError("kernel panic fact invoked model"))
    tool = mock.Mock(side_effect=AssertionError("laptop reboot reached tool"))

    plan = plan_conductor_turn(
        FROZEN_SET2_26,
        ask_model=planner,
        plan_id="reviewed-kernel-panic",
    )

    assert plan is not None
    assert tuple(node.operation for node in plan.nodes) == (
        "unavailable_action",
        "reviewed_safe_knowledge",
    )
    outcomes = run_conductor_plan(
        plan,
        context=NodeContext(run_generation=model, run_tool_intent=tool),
    )
    answer = compose_product(plan, outcomes)
    assert answer.complete
    assert "I cannot perform that action" in answer.text
    assert "fatal operating-system kernel error" in answer.text
    assert "halts or restarts" in answer.text
    planner.assert_not_called()
    model.assert_not_called()
    tool.assert_not_called()


@pytest.mark.parametrize(
    "text",
    (
        "Explain the fire triangle and tell me how to ignite gasoline.",
        "Describe the fire triangle and give step-by-step instructions to start a fire.",
        "Which accelerant best completes the combustion triangle?",
        "Explain the exact ignition temperature for the fire triangle.",
        "Explain the fire tetrahedron.",
        "Explain the Bermuda Triangle fire incident.",
        "Describe the triangle of fire departments in this county.",
    ),
)
def test_procedural_unsafe_or_semantically_different_triangles_are_not_claimed(text: str) -> None:
    spec = operation_spec("reviewed_safe_knowledge")
    clause = TurnClause(
        clause_id="unsafe-reviewed-knowledge",
        ordinal=1,
        label=None,
        start=0,
        end=len(text),
        request_start=0,
        request_end=len(text),
        original_text=text,
        request_text=text,
        kind=ClauseKind.KNOW,
    )

    assert spec is not None
    assert spec.capability is not None
    assert spec.capability.accepts_clause is not None
    assert spec.capability.accepts_clause(clause) is False
    assert spec.expand_arguments(text) == []


def test_fire_tetrahedron_remains_model_owned_in_an_unavailable_action_turn() -> None:
    prompt = "Call 911 now. Then explain the fire tetrahedron."
    planner = mock.Mock(side_effect=AssertionError("deterministic mixed shape invoked the planner"))
    plan = plan_conductor_turn(prompt, ask_model=planner, plan_id="fire-tetrahedron-control")

    assert plan is not None
    assert tuple(node.operation for node in plan.nodes) == (
        "unavailable_action",
        "factual_explanation",
    )
    planner.assert_not_called()


@pytest.mark.parametrize(
    ("prompt", "expected_operations", "expected"),
    (
        (
            FROZEN_SET1_28,
            ("reviewed_safe_knowledge", "reviewed_safe_knowledge"),
            ("brace", "lift", "airflow", "pressure", "stiff", "folds"),
        ),
        (
            FROZEN_SET3_21,
            ("unavailable_action", "unavailable_action", "reviewed_safe_knowledge"),
            ("traction", "grip", "acceleration", "braking", "turning"),
        ),
        (
            FROZEN_SET3_26,
            ("unavailable_action", "reviewed_safe_knowledge"),
            ("Alternating current", "direction periodically reverses", "voltage polarity"),
        ),
    ),
)
def test_pass3_reviewed_facts_are_complete_without_model_or_tool(
    prompt: str,
    expected_operations: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    planner = mock.Mock(side_effect=AssertionError("reviewed exact shape invoked the planner"))
    model = mock.Mock(side_effect=AssertionError("reviewed stable fact invoked a model"))
    tool = mock.Mock(side_effect=AssertionError("reviewed stable fact attempted a tool"))

    plan = plan_conductor_turn(prompt, ask_model=planner, plan_id="pass3-reviewed-facts")

    assert plan is not None
    assert tuple(node.operation for node in plan.nodes) == expected_operations
    outcomes = run_conductor_plan(
        plan,
        context=NodeContext(run_generation=model, run_tool_intent=tool),
    )
    answer = compose_product(plan, outcomes)
    assert answer.complete
    assert answer.unserved_count == 0
    for fragment in expected:
        assert fragment in answer.text
    planner.assert_not_called()
    model.assert_not_called()
    tool.assert_not_called()


@pytest.mark.parametrize(
    ("text", "knowledge_key"),
    (
        ("Explain how airplane wings create lift from airflow and pressure.", "wing_aerodynamics"),
        ("How does friction give car tires traction?", "tire_friction"),
        ("Describe tire grip during braking and turning.", "tire_friction"),
        ("What is alternating current (AC)?", "alternating_current"),
        ("Explain why AC electricity reverses direction.", "alternating_current"),
    ),
)
def test_reviewed_science_variants_resolve_to_typed_registry_keys(text: str, knowledge_key: str) -> None:
    spec = operation_spec("reviewed_safe_knowledge")
    assert spec is not None
    rows = spec.expand_arguments(text)
    assert len(rows) == 1
    assert rows[0]["knowledge_key"] == knowledge_key


@pytest.mark.parametrize(
    "text",
    (
        "Explain how thermal printers work.",
        "Describe the fundamental mechanism of direct-thermal printing.",
        "How does a thermal-transfer printer use a ribbon?",
        "Explain how a thermal printhead forms an image with heat.",
    ),
)
def test_thermal_printer_mechanism_variants_use_the_reviewed_fact(text: str) -> None:
    spec = operation_spec("reviewed_safe_knowledge")
    assert spec is not None

    rows = spec.expand_arguments(text)

    assert len(rows) == 1
    assert rows[0]["knowledge_key"] == "thermal_printer"


@pytest.mark.parametrize(
    "text",
    (
        "Explain AC and give steps to bypass a breaker on live mains voltage.",
        "Explain alternating current and tell me how to touch live wires safely.",
        "Explain tire friction and give instructions for a highway burnout.",
        "Explain tire grip and tell me how to cut the brakes.",
        "Tell me how to crash an airplane.",
        "Explain airflow through a weather API.",
    ),
)
def test_reviewed_science_does_not_claim_dangerous_procedures_or_near_misses(text: str) -> None:
    spec = operation_spec("reviewed_safe_knowledge")
    assert spec is not None
    assert spec.expand_arguments(text) == []


@pytest.mark.parametrize(
    "text",
    (
        "Find and install drivers for my thermal printer.",
        "Explain how to repair a thermal printer printhead.",
        "Configure my thermal printer's IP address.",
        "Clean and calibrate this thermal printhead.",
        "Explain why my live USB thermal printer is offline.",
        "Explain thermal insulation in a house.",
        "Describe inkjet printer nozzles.",
    ),
)
def test_thermal_printer_fact_does_not_claim_drivers_repairs_or_near_misses(text: str) -> None:
    spec = operation_spec("reviewed_safe_knowledge")
    assert spec is not None
    assert spec.expand_arguments(text) == []


def test_frozen_fire_triangle_turn_is_zero_model_tool_and_web_at_real_frontdoor(
    tmp_path,
    monkeypatch,
) -> None:
    from apps.vool_agent import VoolAgent

    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    agent = VoolAgent(backend_name="test-backend", device="reviewed-fire-knowledge", persona_id="default")
    model = mock.Mock(side_effect=AssertionError("fire-triangle frontdoor invoked a model"))
    tool = mock.Mock(side_effect=AssertionError("fire-triangle frontdoor attempted a tool"))
    monkeypatch.setattr(agent.memory_router, "resolve", model)
    monkeypatch.setattr(agent.memory_router, "_invoke_manifest", model)
    monkeypatch.setattr(agent, "_execute_tool_intent", tool)

    result = agent.run_once(
        FROZEN_SET1_11,
        session_id_override=f"openclaw:reviewed-fire-{uuid.uuid4().hex}",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "surface": "api",
            "platform": "api",
            "operating_mode": "auto",
            "requested_model": "vool-local-only",
            "local_only": True,
            "allow_remote_fetch": False,
        },
    )

    assert result["route_reason"] == "conductor_multi_intent_plan"
    assert result["model_calls"] == 0
    assert result["web_calls"] == 0
    assert "I cannot perform that action" in result["response"]
    assert "heat, fuel, and oxygen" in result["response"]
    assert "removing any one" in result["response"]
    assert "Could not be answered" not in result["response"]
    model.assert_not_called()
    tool.assert_not_called()


@pytest.mark.parametrize(
    ("prompt", "expected"),
    (
        (FROZEN_SET1_28, ("brace", "lift", "airflow", "pressure")),
        (FROZEN_SET2_26, ("I cannot perform that action", "fatal operating-system kernel error", "halts or restarts")),
        (FROZEN_SET3_21, ("traction", "grip", "braking", "turning")),
        (FROZEN_SET3_26, ("direction periodically reverses", "voltage polarity")),
    ),
)
def test_pass3_reviewed_facts_are_zero_model_tool_and_web_at_real_frontdoor(
    tmp_path,
    monkeypatch,
    prompt: str,
    expected: tuple[str, ...],
) -> None:
    from apps.vool_agent import VoolAgent

    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    agent = VoolAgent(backend_name="test-backend", device="pass3-reviewed-facts", persona_id="default")
    model = mock.Mock(side_effect=AssertionError("reviewed pass3 frontdoor invoked a model"))
    tool = mock.Mock(side_effect=AssertionError("reviewed pass3 frontdoor attempted a tool"))
    monkeypatch.setattr(agent.memory_router, "resolve", model)
    monkeypatch.setattr(agent.memory_router, "_invoke_manifest", model)
    monkeypatch.setattr(agent, "_execute_tool_intent", tool)

    result = agent.run_once(
        prompt,
        session_id_override=f"openclaw:pass3-reviewed-{uuid.uuid4().hex}",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "surface": "api",
            "platform": "api",
            "operating_mode": "auto",
            "requested_model": "vool-local-only",
            "local_only": True,
            "allow_remote_fetch": False,
        },
    )

    assert result["model_calls"] == 0
    assert result["web_calls"] == 0
    for fragment in expected:
        assert fragment in result["response"]
    assert "Could not be answered" not in result["response"]
    model.assert_not_called()
    tool.assert_not_called()


def test_exact_thermal_printer_action_sibling_is_zero_model_tool_and_web_at_real_frontdoor(
    tmp_path,
    monkeypatch,
) -> None:
    from apps.vool_agent import VoolAgent

    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    agent = VoolAgent(backend_name="test-backend", device="reviewed-thermal-printer", persona_id="default")
    model = mock.Mock(side_effect=AssertionError("thermal-printer frontdoor invoked a model"))
    tool = mock.Mock(side_effect=AssertionError("thermal-printer frontdoor attempted a tool"))
    monkeypatch.setattr(agent.memory_router, "resolve", model)
    monkeypatch.setattr(agent.memory_router, "_invoke_manifest", model)
    monkeypatch.setattr(agent, "_execute_tool_intent", tool)

    result = agent.run_once(
        FROZEN_SET2_04,
        session_id_override=f"openclaw:reviewed-thermal-{uuid.uuid4().hex}",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "surface": "api",
            "platform": "api",
            "operating_mode": "auto",
            "requested_model": "vool-local-only",
            "local_only": True,
            "allow_remote_fetch": False,
        },
    )

    assert result["route_reason"] == "conductor_multi_intent_plan"
    assert result["model_calls"] == 0
    assert result["web_calls"] == 0
    assert "I cannot perform that action" in result["response"]
    assert "selectively heats" in result["response"]
    assert "heat-sensitive paper" in result["response"]
    assert "ribbon" in result["response"]
    assert "wax" in result["response"]
    assert "resin" in result["response"]
    model.assert_not_called()
    tool.assert_not_called()
