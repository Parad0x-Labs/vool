"""End-to-end routing acceptance for the canonical issue #5 prompt corpus."""
from __future__ import annotations

from unittest import mock

import pytest

from core.agent_runtime.fast_paths_utility import looks_like_agentic_build_request
from core.curiosity_roamer import CuriosityResult
from core.execution.constants import (
    machine_diagnostics_intent,
    machine_folder_search_intent,
    machine_largest_intent,
)
from core.execution.planner import _extract_machine_specs_request
from core.media_analysis_pipeline import MediaAnalysisResult
from core.memory_first_router import ModelExecutionDecision
from core.runtime_execution_tools import RuntimeExecutionResult
from core.task_router import evaluate_direct_math_request
from ops.routing_reliability_corpus import (
    NEGATIVE_ROUTING_CASES,
    POSITIVE_ROUTING_CASES,
    ROUTING_RELIABILITY_CASES,
    RoutingCase,
)

TARGET_MACHINE_INTENTS = {
    "machine.disk_usage",
    "machine.inspect_specs",
    "machine.find_largest",
    "machine.find_folder",
}


def _case_id(case: RoutingCase) -> str:
    return case.case_id


def _detected_routes(prompt: str) -> set[str]:
    routes: set[str] = set()
    if evaluate_direct_math_request(prompt) is not None:
        routes.add("arithmetic")
    diagnostics = machine_diagnostics_intent(prompt)
    if diagnostics:
        routes.add(diagnostics)
    if _extract_machine_specs_request(prompt) is not None:
        routes.add("machine.inspect_specs")
    largest = machine_largest_intent(prompt)
    if largest:
        routes.add(largest)
    folder = machine_folder_search_intent(prompt)
    if folder:
        routes.add(folder[0])
    if looks_like_agentic_build_request(prompt):
        routes.add("model_build")
    return routes


def _model_decision(case: RoutingCase) -> ModelExecutionDecision:
    return ModelExecutionDecision(
        source="provider",
        task_hash=f"routing-{case.case_id}",
        provider_id="ollama:routing-acceptance",
        used_model=True,
        output_text=f"Conversational response for {case.case_id}.",
        confidence=0.84,
        trust_score=0.84,
        details={
            "model_call_id": f"model-call-{case.case_id}",
            "response_id": f"response-{case.case_id}",
        },
    )


def _configure_model_lane(agent, case: RoutingCase) -> None:
    agent.memory_router.resolve = mock.Mock(return_value=_model_decision(case))  # type: ignore[assignment]
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="routing_acceptance")
    )
    agent.media_pipeline.analyze = mock.Mock(  # type: ignore[assignment]
        return_value=MediaAnalysisResult(False, reason="no_external_media")
    )
def _source_context(case: RoutingCase, *, workspace: str = "") -> dict[str, object]:
    context: dict[str, object] = {
        "surface": "openclaw",
        "platform": "openclaw",
        "allow_remote_fetch": False,
        "operating_mode": case.source_mode,
    }
    if workspace:
        context.update({"workspace": workspace, "workspace_root": workspace})
    return context


def test_canonical_routing_corpus_is_complete_and_unique() -> None:
    assert len(POSITIVE_ROUTING_CASES) == 33
    assert len(NEGATIVE_ROUTING_CASES) == 16
    assert len(ROUTING_RELIABILITY_CASES) == 49
    assert len({case.case_id for case in ROUTING_RELIABILITY_CASES}) == 49
    assert all(case.prompt.strip() for case in ROUTING_RELIABILITY_CASES)
    assert all(case.category == "routing_reliability" for case in ROUTING_RELIABILITY_CASES)
    assert all(case.severity in {"critical", "high"} for case in ROUTING_RELIABILITY_CASES)
    assert all(case.prompt_sequence == (case.prompt,) for case in ROUTING_RELIABILITY_CASES)
    assert all(case.expected_behavior and case.forbidden_behavior for case in ROUTING_RELIABILITY_CASES)
    assert all(case.required_capability and case.expected_result_schema for case in ROUTING_RELIABILITY_CASES)
    assert all(case.verification_method and case.timeout_seconds > 0 for case in ROUTING_RELIABILITY_CASES)
    assert all(case.cleanup and case.evidence_location for case in ROUTING_RELIABILITY_CASES)


@pytest.mark.parametrize("case", POSITIVE_ROUTING_CASES, ids=_case_id)
def test_canonical_positive_detector_matrix(case: RoutingCase) -> None:
    routes = _detected_routes(case.prompt)
    expected = case.expected_intent or case.expected_route
    assert expected in routes, (case, routes)
    assert routes == {expected}, (case, routes)


@pytest.mark.parametrize("case", NEGATIVE_ROUTING_CASES, ids=_case_id)
def test_canonical_negative_detector_matrix(case: RoutingCase) -> None:
    assert _detected_routes(case.prompt) == set(), case


@pytest.mark.parametrize(
    "case",
    tuple(case for case in POSITIVE_ROUTING_CASES if case.expected_route != "builder"),
    ids=_case_id,
)
def test_full_agent_positive_routes_bypass_model(make_agent, monkeypatch, case: RoutingCase) -> None:
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        side_effect=AssertionError(f"{case.case_id} reached model resolution")
    )
    calls: list[tuple[str, dict[str, object]]] = []

    def execute_stub(intent: str, arguments=None, *, source_context=None):
        del source_context
        calls.append((intent, dict(arguments or {})))
        return RuntimeExecutionResult(
            handled=True,
            ok=True,
            status="executed",
            response_text=f"Measured result from {intent}.",
            details={"observation": {"schema": "tool_observation_v1", "intent": intent, "ok": True}},
        )

    monkeypatch.setattr("core.agent_runtime.fast_paths_machine.execute_authorized_runtime_tool", execute_stub)
    result = agent.run_once(
        case.prompt,
        session_id_override=f"routing-acceptance:{case.case_id}",
        source_context=_source_context(case),
    )

    assert result["model_execution"]["used_model"] is False
    if case.expected_route == "arithmetic":
        assert result.get("route") == "arithmetic"
        assert calls == []
        return
    receipts = list(result.get("tool_receipts") or [])
    assert result.get("mode") == "tool_executed"
    assert receipts and receipts[0]["tool_name"] == case.expected_intent
    assert calls and calls[0][0] == case.expected_intent


@pytest.mark.parametrize(
    "case",
    tuple(case for case in POSITIVE_ROUTING_CASES if case.expected_route == "builder"),
    ids=_case_id,
)
def test_full_agent_build_requests_select_model_build(make_agent, monkeypatch, tmp_path, case: RoutingCase) -> None:
    agent = make_agent()
    observed: list[dict[str, object]] = []

    def builder_probe(**kwargs):
        profile = agent._builder_controller_profile(
            effective_input=str(kwargs["effective_input"]),
            classification=dict(kwargs["classification"]),
            interpretation=kwargs["interpretation"],
            source_context=dict(kwargs["source_context"] or {}),
        )
        observed.append(profile)
        result = agent._fast_path_result(
            session_id=str(kwargs["session_id"]),
            user_input=str(kwargs["effective_input"]),
            response="Builder route selected for acceptance.",
            confidence=0.99,
            source_context=dict(kwargs["source_context"] or {}),
            reason="routing_reliability_builder_probe",
        )
        result["mode"] = "tool_executed"
        result["details"] = {"builder_controller": {"mode": profile.get("mode")}}
        return result

    monkeypatch.setattr(agent, "_maybe_run_builder_controller", builder_probe)
    result = agent.run_once(
        case.prompt,
        session_id_override=f"routing-acceptance:{case.case_id}",
        source_context=_source_context(case, workspace=str(tmp_path)),
    )

    assert observed and observed[0]["mode"] == case.expected_intent
    assert result["details"]["builder_controller"]["mode"] == "model_build"


@pytest.mark.parametrize("case", NEGATIVE_ROUTING_CASES, ids=_case_id)
def test_full_agent_negative_controls_avoid_target_routes(make_agent, tmp_path, case: RoutingCase) -> None:
    agent = make_agent()
    _configure_model_lane(agent, case)

    with (
        mock.patch("core.agent_runtime.agent.ingest_media_evidence", return_value=[]),
        mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None),
        mock.patch("core.agent_runtime.agent.request_relevant_holders", return_value=[]),
        mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None),
    ):
        result = agent.run_once(
            case.prompt,
            session_id_override=f"routing-acceptance:{case.case_id}",
            source_context=_source_context(case, workspace=str(tmp_path)),
        )

    assert result.get("route") != "arithmetic"
    builder_controller = dict(dict(result.get("details") or {}).get("builder_controller") or {})
    assert builder_controller.get("mode") != "model_build"
    receipts = list(result.get("tool_receipts") or [])
    assert not TARGET_MACHINE_INTENTS.intersection(
        str(receipt.get("tool_name") or "") for receipt in receipts
    )
    if case.expected_route == "calendar":
        # Schedule wording belongs to the operator's calendar lane: a real answer when a
        # provider is configured, an honest unavailability refusal when none is. Either way
        # the operator owns the turn -- no machine intent fired above, and the wording did
        # not fall through to a model substitution.
        assert str(result.get("route") or "").startswith("action:operator"), result.get("route")
        assert result["model_execution"]["used_model"] is False
        assert str(result.get("response") or "").strip()
    elif case.live_safe:
        assert result["model_execution"]["used_model"] is True
        assert result["model_execution"]["model_call_id"] == f"model-call-{case.case_id}"
        assert result["model_execution"]["response_id"] == f"response-{case.case_id}"


def test_nounless_storage_fail_closed_gate_prevents_model(make_agent) -> None:
    agent = make_agent()
    with (
        mock.patch("core.agent_runtime.fast_paths_machine.machine_diagnostics_intent", return_value=None),
        mock.patch.object(agent, "_maybe_execute_model_tool_intent", return_value=None),
        mock.patch.object(
            agent,
            "_model_routing_profile",
            side_effect=AssertionError("local machine fact reached model routing"),
        ),
    ):
        result = agent.run_once(
            "how much storage is left",
            session_id_override="routing-acceptance:fail-closed",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result.get("mode") == "tool_failed"
    assert "won't guess" in str(result.get("response") or "")
    assert result["model_execution"]["used_model"] is False
