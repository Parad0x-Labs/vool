"""MemoryFirstRouter <-> cloud-broker SEAM test — uses a mock _Broker, makes NO live cloud call.

This exercises the router's decision seam (privacy gating, escalation off-by-default, validated
CloudModelResponse handling) against an in-process mock broker. It is deliberately NOT a live
integration test: there is no network, no provider, no OPENROUTER_API_KEY. Real-cloud validation
lives in the online acceptance run (ops/run_local_acceptance.py), not here.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from adapters.base_adapter import ModelRequest
from core.cloud_broker import CloudBrokerResult
from core.cloud_privacy_policy import CloudPrivacyGrant
from core.cloud_provider_contract import CloudModelResponse, CloudTaskRequirements, PricingState, PrivacyClass
from core.memory_first_router import MemoryFirstRouter


class _Broker:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, request, **kwargs):
        self.calls.append((request, kwargs))
        return CloudBrokerResult(
            True,
            CloudModelResponse("cloud answer", {"cost": 0.0}),
            provider_id="provider",
            model_id="model",
            model_call_id="call",
            attempts=1,
            pricing_state=PricingState.PROMOTIONAL.value,
        )


def _request() -> ModelRequest:
    return ModelRequest(
        task_kind="reasoning",
        prompt="hello",
        messages=[{"role": "user", "content": "hello"}],
        output_mode="plain_text",
        max_output_tokens=100,
    )


def test_cloud_router_seam_is_default_off_and_requires_typed_privacy_context(monkeypatch) -> None:
    broker = _Broker()
    router = MemoryFirstRouter(cloud_broker=broker)
    task = SimpleNamespace(task_id="task")
    monkeypatch.setattr(
        "core.memory_first_router.cloud_escalation_policy.load_policy",
        lambda: SimpleNamespace(free_cloud_enabled=False),
    )
    assert router._try_free_cloud_boost(
        request=_request(), task=task, task_hash="hash", output_mode="plain_text", source_context={"surface": "cli"}
    ) is None
    assert broker.calls == []

    monkeypatch.setattr(
        "core.memory_first_router.cloud_escalation_policy.load_policy",
        lambda: SimpleNamespace(free_cloud_enabled=True),
    )
    assert router._try_free_cloud_boost(
        request=_request(), task=task, task_hash="hash", output_mode="plain_text", source_context={"surface": "cli"}
    ) is None
    assert broker.calls == []


def test_cloud_router_seam_returns_validated_cloud_decision(monkeypatch) -> None:
    broker = _Broker()
    router = MemoryFirstRouter(cloud_broker=broker)
    monkeypatch.setattr(
        "core.memory_first_router.cloud_escalation_policy.load_policy",
        lambda: SimpleNamespace(free_cloud_enabled=True),
    )
    context = {
        "surface": "cli",
        "session_id": "session",
        "turn_id": "turn",
        "cloud_task_requirements": CloudTaskRequirements(
            min_context_tokens=10,
            expected_output_tokens=100,
            required_capabilities=("text",),
            privacy_class=PrivacyClass.PUBLIC,
        ),
        "cloud_privacy_grant": CloudPrivacyGrant(),
    }
    decision = router._try_free_cloud_boost(
        request=_request(),
        task=SimpleNamespace(task_id="task"),
        task_hash="hash",
        output_mode="plain_text",
        source_context=context,
    )
    assert decision is not None and decision.used_model
    assert decision.source == "free_cloud_boost"
    assert decision.output_text == "cloud answer"
    assert decision.details["pricing_state"] == "promotional"
    assert broker.calls[0][1]["requirements"] is context["cloud_task_requirements"]


def test_tool_intent_cloud_route_uses_native_runtime_tool_contract(monkeypatch) -> None:
    broker = _Broker()
    broker.execute = mock.Mock(
        return_value=CloudBrokerResult(
            True,
            CloudModelResponse(
                '{"_native_tool_call_id":"native-1","intent":"sandbox.run_command",'
                '"arguments":{"command":"pwd","cwd":null}}',
                {"cost": 0.0},
            ),
            provider_id="openrouter",
            model_id="native-tools-model",
            model_call_id="call",
            attempts=1,
            pricing_state=PricingState.FREE.value,
        )
    )
    router = MemoryFirstRouter(cloud_broker=broker)
    monkeypatch.setattr(
        "core.memory_first_router.cloud_escalation_policy.load_policy",
        lambda: SimpleNamespace(free_cloud_enabled=True),
    )
    context = {
        "surface": "cli",
        "session_id": "session",
        "turn_id": "turn",
        "cloud_task_requirements": CloudTaskRequirements(
            min_context_tokens=10,
            expected_output_tokens=100,
            required_capabilities=("text",),
            privacy_class=PrivacyClass.PUBLIC,
        ),
        "cloud_privacy_grant": CloudPrivacyGrant(),
    }
    decision = router._try_free_cloud_boost(
        request=_request(),
        task=SimpleNamespace(task_id="task"),
        task_hash="hash",
        output_mode="tool_intent",
        source_context=context,
    )
    assert decision is not None
    assert decision.structured_output["intent"] == "sandbox.run_command"
    cloud_request = broker.execute.call_args.args[0]
    cloud_requirements = broker.execute.call_args.kwargs["requirements"]
    assert cloud_request.tool_choice == "required"
    assert any(tool.intent == "sandbox.run_command" for tool in cloud_request.tools)
    assert all("." not in tool.name for tool in cloud_request.tools)
    assert "tool_calling" in cloud_requirements.required_capabilities


def test_no_ranked_local_route_uses_enabled_cloud_boost(monkeypatch) -> None:
    broker = _Broker()
    router = MemoryFirstRouter(cloud_broker=broker)
    monkeypatch.setattr(
        "core.memory_first_router.cloud_escalation_policy.load_policy",
        lambda: SimpleNamespace(free_cloud_enabled=True),
    )
    task = SimpleNamespace(task_id="task", task_summary="answer a normal question")
    interpretation = SimpleNamespace(reconstructed_text="answer a normal question")
    report = SimpleNamespace(
        retrieval_confidence=0.2,
        to_dict=lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []},
    )
    context_result = SimpleNamespace(
        retrieval_confidence_score=0.2,
        report=report,
        assembled_context=lambda: "",
        context_snippets=lambda: [],
    )
    context = {
        "surface": "cli",
        "session_id": "session",
        "turn_id": "turn",
        "cloud_task_requirements": CloudTaskRequirements(
            min_context_tokens=10,
            expected_output_tokens=100,
            required_capabilities=("text",),
            privacy_class=PrivacyClass.PUBLIC,
        ),
    }
    with mock.patch("core.memory_first_router._cached_free_vram_gb", return_value=None), mock.patch(
        "core.memory_first_router.rank_provider_candidates", return_value=[]
    ):
        decision = router._execute_provider_task(
            task=task,
            classification={"task_class": "chat_conversation"},
            interpretation=interpretation,
            context_result=context_result,
            persona=SimpleNamespace(tone="neutral"),
            task_hash="hash",
            task_kind="normalization_assist",
            output_mode="plain_text",
            allow_paid_fallback=False,
            provider_role="queen",
            surface="cli",
            source_context=context,
        )
    assert decision.source == "free_cloud_boost"
    assert decision.details["route_reason"] == "free_cloud_boost_no_local_route"
    assert len(broker.calls) == 1
