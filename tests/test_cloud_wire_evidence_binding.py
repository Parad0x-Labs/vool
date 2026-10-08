"""Built-in cloud lanes retain only support carried on their selected wire."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from adapters.cloud_provider_common import CloudProviderRequestError
from adapters.cloudflare_workers_ai_provider import CloudflareWorkersAIProvider
from adapters.generic_openai_cloud_provider import GenericOpenAICloudProvider
from adapters.openrouter_cloud_provider import OpenRouterCloudProvider
from core import provider_invocation_gateway as gateway
from core.cloud_broker import CloudModelBroker
from core.cloud_privacy_policy import CloudPrivacyGrant
from core.cloud_provider_contract import CloudModelRequest
from core.cloud_routing import CloudRouteMode
from tests.test_cloud_broker import REQ, _model, _Provider, _Registry
from tests.test_provider_request_evidence_binding import CAPSULE, QUESTION, _digest, _record


class _WireTransport:
    def __init__(self):
        self.wires = []

    def request_json(self, **kwargs):
        self.wires.append(deepcopy(kwargs["body"]["messages"]))
        answer = "The crossing took 11 days."
        return 200, {}, {"choices": [{"message": {"content": answer}}],
                         "result": {"response": answer}, "usage": {"cost": 0.0}}


def _cloud_request(*, capsule=CAPSULE, question=QUESTION):
    original = [{"role": "system", "content": capsule}, {"role": "user", "content": question}]
    messages = tuple({**message, "_vool_evidence_support": index == 0} for index, message in enumerate(original))
    return CloudModelRequest(task_id="wire-test", turn_id="provider-turn", subtask_id="", model_call_id="wire-call",
                             model_id="fixture-model", messages=messages, max_output_tokens=64,
                             metadata={"session_id": "provider-bound", "admitted_capsule_evidence": _record(original, [capsule], question=question)})


@pytest.mark.parametrize("provider", ["generic", "openrouter", "cloudflare"])
def test_built_in_cloud_wire_is_bound_without_plaintext_journal(provider, monkeypatch):
    adapter = ({"generic": GenericOpenAICloudProvider(provider_id="fixture-generic", base_url="https://fixture.invalid/v1", credential_name="fixture"),
                "openrouter": OpenRouterCloudProvider(),
                "cloudflare": CloudflareWorkersAIProvider(account_id="fixture-account")}[provider])
    request = _cloud_request()
    original = deepcopy(request.metadata["admitted_capsule_evidence"])
    transport = _WireTransport()
    manifests = []
    store = gateway.store_provider_manifest

    def journal(manifest):
        manifests.append(asdict(manifest))
        return store(manifest)

    monkeypatch.setattr(gateway, "store_provider_manifest", journal)
    response = adapter.send_request(transport, request)
    assert "11 days" in response.output_text
    wire = transport.wires[-1]
    record = request.metadata["admitted_capsule_evidence"]
    assert request.metadata["request_evidence_finalized"]
    assert record["evidence_texts"] == [CAPSULE]
    assert record["request_sha256"] == _digest(wire)
    assert record["query_sha256"] == original["query_sha256"]
    assert all("_vool_evidence_support" not in message for message in wire)
    import json

    assert "The crossing took 11 days" not in json.dumps(manifests)
    assert "admitted_capsule_evidence" not in json.dumps(manifests)


def test_generic_cloud_projection_drops_support_for_omitted_private_carrier():
    from core.egress_gate import EgressSegment

    request = _cloud_request()
    private = EgressSegment(segment_id="source", authority_class="memory", principal_scope="owner_local", exposure_class="LOCAL_ONLY")
    request = replace(request, messages=({**request.messages[0], "_egress_segment": private}, request.messages[1]))
    transport = _WireTransport()
    provider = GenericOpenAICloudProvider(provider_id="fixture-generic", base_url="https://fixture.invalid/v1", credential_name="fixture")
    provider.send_request(transport, request)
    wire = transport.wires[-1]
    assert not any(message["content"] == CAPSULE for message in wire)
    record = request.metadata["admitted_capsule_evidence"]
    assert record["evidence_texts"] == [] and record["text"] == ""
    assert record["request_sha256"] == _digest(wire)


class _CatalogLane(_Provider):
    def __init__(self, provider_id, *, drop=False):
        super().__init__([(_model(model_id=provider_id, provider_id=provider_id),)], provider_id=provider_id)
        self.drop = drop
        self.attempts = []
        self.metadata_objects = []

    def send_request(self, transport, request):
        self.metadata_objects.append(request.metadata)
        if self.drop:
            payload = {"model": request.model_id, "messages": [dict(request.messages[-1])], "max_tokens": request.max_output_tokens}
            permit = gateway.seal_provider_invocation(request=request, provider_id=self.provider_id, model_id=request.model_id,
                                                     operation="chat", payload=payload)
            permit.consume()
            self.attempts.append(deepcopy(request.metadata["admitted_capsule_evidence"]))
            raise CloudProviderRequestError(503, "fixture failed after its source was omitted")
        provider = GenericOpenAICloudProvider(provider_id=self.provider_id, base_url="https://fixture.invalid/v1", credential_name="fixture")
        response = provider.send_request(transport, request)
        self.attempts.append(deepcopy(request.metadata["admitted_capsule_evidence"]))
        return response


def _broker(lanes, transports):
    return CloudModelBroker(registry=_Registry(lanes), transports=transports, sleeper=lambda _seconds: None)


def test_cloud_failover_keeps_lane_records_isolated_and_returns_selected_support(monkeypatch, tmp_path):
    import json

    from core.cloud_route_receipt import list_cloud_route_receipts

    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path / "receipts")
    failed = _CatalogLane("a-failed", drop=True)
    selected = _CatalogLane("b-selected")
    transport = _WireTransport()
    request = replace(_cloud_request(), model_id="")
    original = deepcopy(request.metadata)
    result = _broker([failed, selected], {failed.provider_id: object(), selected.provider_id: transport}).execute(
        request, requirements=REQ, mode=CloudRouteMode.LOCAL_FREE,
        privacy_grant=CloudPrivacyGrant(allow_provider_fallback=True), max_attempts=3)
    assert result.used_cloud and result.provider_id == selected.provider_id
    record = result.response.admitted_request_evidence
    assert record["evidence_texts"] == [CAPSULE]
    assert record["request_sha256"] == _digest(transport.wires[-1])
    assert failed.attempts and all(record["evidence_texts"] == [] for record in failed.attempts)
    metadata = failed.metadata_objects + selected.metadata_objects
    assert len({id(value) for value in metadata}) == len(metadata)
    assert request.metadata == original
    assert "The crossing took 11 days" not in json.dumps(list_cloud_route_receipts("provider-bound"))


def test_selected_builtin_cloud_support_preserves_the_actual_past_time_answer(monkeypatch, tmp_path):
    from adapters.base_adapter import ModelRequest
    from core.agent_runtime.turn_reasoning import _past_time_guard_evidence
    from core.memory_first_router import MemoryFirstRouter
    from core.model_output_guard import replace_unsupported_past_time_claims
    from core.temporal_question_scope import question_time_scope

    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path / "receipts")
    monkeypatch.setattr("core.memory_first_router.cloud_escalation_policy.load_policy",
                        lambda: SimpleNamespace(free_cloud_enabled=True))
    lane = _CatalogLane("selected")
    transport = _WireTransport()
    broker = _broker([lane], {lane.provider_id: transport})
    question = "How many days did the crossing take?"
    assert question_time_scope(question).asks_past
    messages = [{"role": "system", "content": CAPSULE}, {"role": "user", "content": question}]
    record = _record(messages, [CAPSULE], question=question)
    request = ModelRequest(task_kind="conversation", prompt=question, messages=messages, max_output_tokens=64,
                           metadata={"admitted_capsule_evidence": record, "request_evidence_message_indices": [0]})
    context = {"surface": "cli", "session_id": "provider-bound", "turn_id": "provider-turn", "current_turn_id": "provider-turn",
               "admitted_capsule_evidence": deepcopy(record), "cloud_task_requirements": REQ,
               "cloud_privacy_grant": CloudPrivacyGrant()}
    decision = MemoryFirstRouter(cloud_broker=broker)._try_free_cloud_boost(
        request=request, task=SimpleNamespace(task_id="past-cloud"), task_hash="past-cloud",
        output_mode="plain_text", source_context=context)
    assert decision is not None
    assert context["admitted_capsule_evidence"]["request_sha256"] == _digest(transport.wires[-1])
    support = _past_time_guard_evidence(context_result=None, source_context=context, web_notes=[],
                                       session_id="provider-bound", question=question)
    assert CAPSULE in support
    answer = replace_unsupported_past_time_claims(decision.output_text, question=question, evidence_texts=support)
    assert answer == decision.output_text
    assert "11 days" in answer


def test_wire_support_binding_preserves_declared_seal_boundary() -> None:
    from core.provider_execution_boundary import is_provider_execution_boundary
    from core.provider_invocation_gateway import (
        _finalize_request_evidence_payload,
        seal_provider_invocation,
    )

    assert is_provider_execution_boundary(seal_provider_invocation)
    assert not is_provider_execution_boundary(_finalize_request_evidence_payload)


@pytest.mark.parametrize("stale_finalized", [False, True])
def test_unsealed_custom_response_cannot_publish_an_adapter_supplied_donor(
    monkeypatch, tmp_path, stale_finalized
):
    from core.cloud_provider_contract import CloudModelResponse

    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path / "receipts")
    request = replace(_cloud_request(), model_id="")
    request.metadata["request_evidence_finalized"] = stale_finalized
    supplied = deepcopy(request.metadata["admitted_capsule_evidence"])
    lane = _Provider(
        [(_model(model_id="custom", provider_id="custom"),)],
        responses=[CloudModelResponse("The crossing took 11 days.", {"cost": 0.0},
                                      admitted_request_evidence=supplied)],
        provider_id="custom",
    )
    result = _broker([lane], {lane.provider_id: object()}).execute(
        request, requirements=REQ, mode=CloudRouteMode.LOCAL_FREE,
        privacy_grant=CloudPrivacyGrant(), max_attempts=1
    )
    assert result.used_cloud
    assert result.response.admitted_request_evidence is None
    assert request.metadata["admitted_capsule_evidence"] == supplied
