"""Live routing acceptance against the canonical local Ollama profile."""
from __future__ import annotations

import pytest

from core.runtime_execution_tools import RuntimeExecutionResult
from ops.routing_reliability_corpus import LIVE_ROUTING_CASES, NEGATIVE_ROUTING_CASES, RoutingCase
from tests.gauntlet._live import (
    LIVE_GATE,
    LIVE_MODEL,
    build_live_agent,
    require_live_provider,
    run_once_result,
)

pytestmark = [pytest.mark.gauntlet_live, LIVE_GATE]


def _case_id(case: RoutingCase) -> str:
    return case.case_id


@pytest.mark.parametrize(
    "case",
    tuple(case for case in NEGATIVE_ROUTING_CASES if case.live_safe),
    ids=_case_id,
)
def test_live_model_lane_controls_reach_real_ollama(make_agent, tmp_path, case: RoutingCase) -> None:
    require_live_provider()
    agent = build_live_agent(make_agent)

    result = run_once_result(agent, case.prompt, f"live-routing:{case.case_id}", workspace=tmp_path)

    assert result["model_execution"]["used_model"] is True, result["model_execution"]
    assert result["model_execution"]["provider_id"] == f"ollama-local:{LIVE_MODEL}", result
    assert str(result["model_execution"].get("model_call_id") or "").startswith("model-call-"), result
    assert str(result["model_execution"].get("response_id") or "").startswith("response-"), result
    assert str(result.get("response") or "").strip(), result
    assert result.get("route") != "arithmetic", result


@pytest.mark.parametrize(
    "case",
    tuple(case for case in LIVE_ROUTING_CASES if case.expected_route == "arithmetic"),
    ids=_case_id,
)
def test_live_agent_arithmetic_controls_bypass_model(make_agent, tmp_path, case: RoutingCase) -> None:
    require_live_provider()
    agent = build_live_agent(make_agent)

    result = run_once_result(agent, case.prompt, f"live-routing:{case.case_id}", workspace=tmp_path)

    assert result.get("route") == "arithmetic", result
    assert result["model_execution"]["used_model"] is False, result


@pytest.mark.parametrize(
    "case",
    tuple(case for case in LIVE_ROUTING_CASES if case.expected_intent == "machine.disk_usage"),
    ids=_case_id,
)
def test_live_agent_machine_controls_use_bounded_tool_result(
    make_agent,
    monkeypatch,
    tmp_path,
    case: RoutingCase,
) -> None:
    require_live_provider()
    agent = build_live_agent(make_agent)
    calls: list[str] = []

    def execute_stub(intent: str, arguments=None, *, source_context=None):
        del arguments, source_context
        calls.append(intent)
        return RuntimeExecutionResult(
            handled=True,
            ok=True,
            status="executed",
            response_text="Controlled disk result: 100 GB total, 60 GB free.",
            details={"observation": {"schema": "tool_observation_v1", "intent": intent, "ok": True}},
        )

    monkeypatch.setattr("core.agent_runtime.fast_paths_machine.execute_authorized_runtime_tool", execute_stub)
    result = run_once_result(agent, case.prompt, f"live-routing:{case.case_id}", workspace=tmp_path)

    assert calls == ["machine.disk_usage"], result
    assert result.get("mode") == "tool_executed", result
    assert result["model_execution"]["used_model"] is False, result
