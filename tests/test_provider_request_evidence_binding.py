"""Final provider messages own support, including budget shedding and retries."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest, ModelResponse
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.memory_first_router import MemoryFirstRouter
from storage.model_provider_manifest import ModelProviderManifest

QUESTION = "Explain the crossing duration briefly."
CAPSULE = "<retrieved_context>\n- user said: The crossing took 11 days.\n</retrieved_context>"


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _record(messages, texts, *, question=QUESTION):
    capsule = "\n".join(text for text in texts if "<retrieved_context>" in text)
    return {"chat_id": "provider-bound", "turn_id": "provider-turn", "source": "canonical_runtime_transcript",
            "query_sha256": _digest(question), "request_sha256": _digest(messages),
            "text": capsule, "text_sha256": _digest(capsule),
            "evidence_texts": list(texts), "evidence_sha256": _digest(list(texts)),
            "evidence_message_indices": [index for index, message in enumerate(messages)
                                         if message["content"] in texts]}


def _adapter(*, context_window=0):
    return OpenAICompatibleAdapter(SimpleNamespace(
        provider_id="evidence-fixture", model_name="fixture-model",
        metadata={"runtime_family": "ollama"},
        runtime_config={"base_url": "http://127.0.0.1:11434", "context_window": context_window,
                        "timeout_seconds": 2.0},
    ))


def _payload(adapter, request, api):
    if api == "ollama":
        return adapter._build_ollama_payload(request, force_json=False, stream=False)
    return adapter._build_openai_payload(request, force_json=False, stream=False)


@pytest.mark.parametrize("api", ["ollama", "openai"])
def test_provider_budget_drops_only_omitted_support_and_rebinds_actual_wire(api):
    system = "Answer from supplied source evidence."
    huge = "<retrieved_context>" + "source detail " * 5000 + "</retrieved_context>"
    prior = "The crossing took 11 days."
    messages = [{"role": "system", "content": system}, {"role": "system", "content": huge},
                {"role": "user", "content": prior}, {"role": "user", "content": QUESTION}]
    original = _record(messages, [huge, prior])
    request = ModelRequest(task_kind="conversation", prompt=QUESTION, system_prompt=system,
                           messages=messages, max_output_tokens=64, reasoning_mode="disabled",
                           metadata={"admitted_capsule_evidence": deepcopy(original),
                                     "request_evidence_message_indices": [1, 2]})
    payload = _payload(_adapter(context_window=4096), request, api)
    assert not any(huge == message["content"] for message in payload["messages"])
    assert any(prior == message["content"] for message in payload["messages"])
    record = request.metadata["admitted_capsule_evidence"]
    assert record["evidence_texts"] == [prior]
    assert record["text"] == ""
    assert record["request_sha256"] == _digest(payload["messages"])
    assert record["query_sha256"] == _digest(QUESTION)
    assert original["evidence_texts"] == [huge, prior]
    assert all(not any(key.startswith("_vool_evidence") for key in message) for message in payload["messages"])


@pytest.mark.parametrize("api", ["ollama", "openai"])
def test_current_draft_copy_cannot_replace_dropped_source_carrier(api, monkeypatch):
    import adapters.openai_compatible_adapter as adapter_module

    messages = [{"role": "system", "content": "Answer faithfully."},
                {"role": "assistant", "content": CAPSULE},
                {"role": "user", "content": QUESTION},
                {"role": "assistant", "content": CAPSULE},
                {"role": "user", "content": "Rewrite the previous draft briefly."}]
    original = _record(messages[:3], [CAPSULE])
    request = ModelRequest(task_kind="conversation", prompt=messages[-1]["content"],
                           system_prompt=messages[0]["content"], messages=messages,
                           max_output_tokens=64, reasoning_mode="disabled",
                           metadata={"admitted_capsule_evidence": original,
                                     "request_evidence_message_indices": [1]})

    def shed_original(source, **kwargs):
        return SimpleNamespace(messages=[source[0], *source[3:]], telemetry={"status": "trimmed"})

    monkeypatch.setattr(adapter_module, "fit_messages_to_context_window", shed_original)
    payload = _payload(_adapter(context_window=4096), request, api)
    assert any(message["content"] == CAPSULE for message in payload["messages"])
    record = request.metadata["admitted_capsule_evidence"]
    assert record["evidence_texts"] == []
    assert record["text"] == ""
    assert record["request_sha256"] == _digest(payload["messages"])
    assert record["query_sha256"] == _digest(QUESTION)


def _manifest():
    return ModelProviderManifest(provider_name="evidence-fixture", model_name="fixture-model", source_type="http",
                                 adapter_type="local_qwen_provider", license_name="test", license_reference="test",
                                 weight_location="external", runtime_dependency="test", capabilities=["summarize"],
                                 runtime_config={"base_url": "http://127.0.0.1:11434"},
                                 metadata={"deployment_class": "local", "cost_class": "free_local"})


def test_style_retry_preserves_original_query_and_keeps_draft_out_of_support():
    router = MemoryFirstRouter(registry=mock.Mock())
    real_adapter = _adapter()
    adapter = mock.Mock()
    captured = []
    source_messages = [{"role": "system", "content": "Answer faithfully."},
                       {"role": "system", "content": CAPSULE}, {"role": "user", "content": QUESTION}]
    original = _record(source_messages, [CAPSULE])
    request = ModelRequest(task_kind="conversation", prompt=QUESTION, system_prompt="Answer faithfully.",
                           messages=[source_messages[0], source_messages[-1]], reasoning_mode="disabled",
                           metadata={"admitted_capsule_evidence": deepcopy(original),
                                     "request_evidence_message_indices": [],
                                     "ordinary_chat_output_policy": {"mode": "ordinary_chat", "prompt_profile": "chat_minimal"},
                                     "defer_stream_until_verified": True})

    def run(task_request):
        payload = real_adapter._build_openai_payload(task_request, force_json=False, stream=False)
        captured.append((task_request, deepcopy(payload["messages"])))
        text = (CAPSULE + "\nSubject: a library card. Camera: wide shot, 35mm lens. Lighting: cinematic blue hour."
                if len(captured) == 1 else "The crossing took eleven days.")
        return ModelResponse(output_text=text)

    adapter.run_text_task.side_effect = run
    router.registry.build_adapter.return_value = adapter
    context = {"chat_id": "provider-bound", "current_turn_id": "provider-turn", "admitted_capsule_evidence": original}
    with mock.patch("core.memory_first_router.should_probe_health", return_value=False), \
            mock.patch("core.memory_first_router.circuit_is_open", return_value=False):
        _, response, error = router._invoke_manifest(manifest=_manifest(), request=request, output_mode="plain_text",
                                                     task=SimpleNamespace(task_id="style-evidence"), source_context=context)
    assert error is None
    assert len(captured) == 2
    final_request, final_wire = captured[-1]
    assert final_request.prompt != QUESTION
    assert any(CAPSULE in str(message["content"]) for message in final_wire)
    record = getattr(response, "_admitted_capsule_evidence", None)
    assert isinstance(record, dict)
    assert record["evidence_texts"] == []
    assert record["query_sha256"] == _digest(QUESTION)
    assert record["request_sha256"] == _digest(final_wire)
    assert context["admitted_capsule_evidence"] == original
    assert request.metadata["admitted_capsule_evidence"] == original


def test_router_copies_request_carrier_and_excludes_current_user(monkeypatch):
    import core.memory_first_router as router_module

    messages = [{"role": "system", "content": "Answer faithfully."},
                {"role": "system", "content": CAPSULE}, {"role": "user", "content": QUESTION}]
    original = _record(messages, [CAPSULE])
    internal = SimpleNamespace(messages=[SimpleNamespace(**message) for message in messages], metadata={},
                               system_prompt=lambda: messages[0]["content"], user_prompt=lambda: QUESTION,
                               as_openai_messages=lambda: deepcopy(messages), context_summary={}, temperature=0.0,
                               max_output_tokens=64, trace_id="carrier-build", attachments=[])
    monkeypatch.setattr(router_module, "normalize_prompt", lambda **kwargs: internal)
    context = {"admitted_capsule_evidence": original}
    request = MemoryFirstRouter(registry=mock.Mock())._build_request(
        task=SimpleNamespace(task_id="carrier-build"), classification={},
        interpretation=SimpleNamespace(raw_text=QUESTION), context_result=SimpleNamespace(), persona=SimpleNamespace(),
        output_mode="plain_text", task_kind="conversation", surface="api", source_context=context)
    assert request.metadata["admitted_capsule_evidence"] == original
    assert request.metadata["admitted_capsule_evidence"] is not original
    assert 1 in request.metadata["request_evidence_message_indices"]
    assert 2 not in request.metadata["request_evidence_message_indices"]
    request.metadata["admitted_capsule_evidence"]["evidence_texts"].clear()
    assert original["evidence_texts"] == [CAPSULE]


@pytest.mark.parametrize("api", ["ollama", "openai"])
def test_wire_capture_matches_support_digest_without_journal_plaintext(api, monkeypatch):
    from dataclasses import asdict

    from core import provider_invocation_gateway as gateway

    messages = [{"role": "system", "content": "Answer faithfully."},
                {"role": "system", "content": CAPSULE}, {"role": "user", "content": QUESTION}]
    request = ModelRequest(task_kind="conversation", prompt=QUESTION, system_prompt=messages[0]["content"],
                           messages=messages, reasoning_mode="disabled", max_output_tokens=64,
                           metadata={"admitted_capsule_evidence": _record(messages, [CAPSULE]),
                                     "request_evidence_message_indices": [1]})
    adapter = _adapter()
    monkeypatch.setattr(adapter, "_gate_local_model_load", lambda: None)
    monkeypatch.setattr(adapter, "_record_benchmark_best_effort", lambda **kwargs: None)
    wires = []
    manifests = []
    store_manifest = gateway.store_provider_manifest

    def journal(manifest):
        manifests.append(asdict(manifest))
        return store_manifest(manifest)

    def post(url, *, json, **kwargs):
        wires.append(deepcopy(json["messages"]))
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = ({"message": {"content": "Eleven days."}, "done": True,
                                       "done_reason": "stop", "model": "fixture-model"}
                                      if api == "ollama" else
                                      {"choices": [{"message": {"content": "Eleven days."}, "finish_reason": "stop"}],
                                       "model": "fixture-model"})
        return response

    monkeypatch.setattr(gateway, "store_provider_manifest", journal)
    monkeypatch.setattr("adapters.openai_compatible_adapter.requests.post", post)
    response = (adapter._invoke_ollama_chat(request, force_json=False) if api == "ollama"
                else adapter._invoke_openai_compatible(request, force_json=False))
    assert response.output_text == "Eleven days."
    assert len(wires) == 1 and len(manifests) == 1
    record = request.metadata["admitted_capsule_evidence"]
    assert record["request_sha256"] == _digest(wires[0])
    assert record["evidence_texts"] == [CAPSULE]
    assert "The crossing took 11 days" not in json.dumps(manifests)
    assert "admitted_capsule_evidence" not in json.dumps(manifests)
    assert all(not any(key.startswith("_vool_evidence") for key in message) for message in wires[0])


def test_selected_lane_publishes_its_carrier_without_candidate_plaintext(monkeypatch):
    import core.memory_first_router as router_module
    from core import context_retrieval

    messages = [{"role": "system", "content": "Answer faithfully."}, {"role": "user", "content": QUESTION}]
    selected = _record(messages, [])
    losing = ModelResponse(output_text="Unselected answer.")
    losing._admitted_capsule_evidence = _record(messages, [CAPSULE])
    response = ModelResponse(output_text="Source evidence is unavailable.")
    response._admitted_capsule_evidence = selected
    context = {"runtime_session_id": "provider-bound", "current_turn_id": "provider-turn", "turn_id": "provider-turn",
               "admitted_capsule_evidence": losing._admitted_capsule_evidence}
    adapter = mock.Mock()
    adapter.get_license_metadata.return_value = {}
    candidate = mock.Mock(return_value="candidate-proof")
    monkeypatch.setattr(router_module, "record_candidate_output", candidate)
    monkeypatch.setattr(router_module, "_record_response_usage", lambda *args, **kwargs: None)
    router = MemoryFirstRouter(registry=mock.Mock())
    decision = router._decision_from_response(
        manifest=_manifest(), adapter=adapter, response=response, task_hash="selected-proof",
        task=SimpleNamespace(task_id="selected-proof"), classification={},
        context_result=SimpleNamespace(retrieval_confidence_score=0.0, report=SimpleNamespace(retrieval_confidence=0.0)),
        task_kind="conversation", output_mode="plain_text", provider_role="auto", ranked_manifests=[],
        attempted=[], failover_used=False, source="model", source_context=context)
    assert decision.output_text == response.output_text
    assert context["admitted_capsule_evidence"] == selected
    assert context["admitted_capsule_evidence"] is not selected
    assert context_retrieval.get_last_retrieval_telemetry()["last_admitted_capsule"] == selected
    candidate.assert_called_once()
    candidate_metadata = candidate.call_args.kwargs["metadata"]
    assert "admitted_capsule_evidence" not in json.dumps(candidate_metadata)
    assert "The crossing took 11 days" not in json.dumps(candidate_metadata)
    assert losing._admitted_capsule_evidence["evidence_texts"] == [CAPSULE]


def test_formatting_retry_drops_original_history_support():
    router = MemoryFirstRouter(registry=mock.Mock())
    real_adapter = _adapter()
    adapter = mock.Mock()
    captured = []
    question = "How many days did the crossing take? Answer in one word."
    messages = [{"role": "system", "content": "Answer faithfully."},
                {"role": "system", "content": CAPSULE}, {"role": "user", "content": question}]
    original = _record(messages, [CAPSULE], question=question)
    request = ModelRequest(task_kind="conversation", prompt=question, system_prompt=messages[0]["content"],
                           messages=messages, reasoning_mode="disabled",
                           metadata={"admitted_capsule_evidence": deepcopy(original),
                                     "request_evidence_message_indices": [1],
                                     "response_constraint": {"exact_words": 1, "max_words": 1},
                                     "defer_stream_until_verified": True})

    def run(task_request):
        payload = real_adapter._build_openai_payload(task_request, force_json=False, stream=False)
        captured.append((task_request, deepcopy(payload["messages"])))
        return ModelResponse(output_text="Eleven days." if len(captured) == 1 else "Eleven")

    adapter.run_text_task.side_effect = run
    router.registry.build_adapter.return_value = adapter
    with mock.patch("core.memory_first_router.should_probe_health", return_value=False), \
            mock.patch("core.memory_first_router.circuit_is_open", return_value=False):
        _, response, error = router._invoke_manifest(manifest=_manifest(), request=request, output_mode="plain_text",
                                                     task=SimpleNamespace(task_id="format-evidence"), source_context={})
    assert error is None
    assert len(captured) == 2
    first_request, first_wire = captured[0]
    final_request, final_wire = captured[-1]
    assert first_request.metadata["admitted_capsule_evidence"]["evidence_texts"] == [CAPSULE]
    assert any(message["content"] == CAPSULE for message in first_wire)
    assert not any(message["content"] == CAPSULE for message in final_wire)
    record = getattr(response, "_admitted_capsule_evidence", None)
    assert isinstance(record, dict)
    assert record["evidence_texts"] == []
    assert record["query_sha256"] == _digest(question)
    assert record["request_sha256"] == _digest(final_wire)
    assert final_request.metadata["request_evidence_message_indices"] == []


@pytest.mark.parametrize("origin", ["missing", "omitted", "merged-correction"])
def test_primary_system_matching_source_words_needs_actual_source_origin(monkeypatch, origin):
    import core.memory_first_router as router_module

    source_unit = "The crossing took 11 days."
    messages = [{"role": "system", "content": "Answer faithfully. " + source_unit},
                {"role": "user", "content": QUESTION}]
    record = _record(messages, [source_unit])
    if origin == "missing":
        record.pop("evidence_message_indices")
    else:
        record["evidence_message_indices"] = [0] if origin == "merged-correction" else []
    internal = SimpleNamespace(messages=[SimpleNamespace(**message) for message in messages], metadata={},
                               system_prompt=lambda: messages[0]["content"], user_prompt=lambda: QUESTION,
                               as_openai_messages=lambda: deepcopy(messages), context_summary={}, temperature=0.0,
                               max_output_tokens=64, trace_id="origin-build", attachments=[])
    monkeypatch.setattr(router_module, "normalize_prompt", lambda **kwargs: internal)
    request = MemoryFirstRouter(registry=mock.Mock())._build_request(
        task=SimpleNamespace(task_id="origin-build"), classification={},
        interpretation=SimpleNamespace(raw_text=QUESTION), context_result=SimpleNamespace(), persona=SimpleNamespace(),
        output_mode="plain_text", task_kind="conversation", surface="api",
        source_context={"admitted_capsule_evidence": record})
    payload = _adapter()._build_openai_payload(request, force_json=False, stream=False)
    support = request.metadata["admitted_capsule_evidence"]
    assert source_unit in payload["messages"][0]["content"]
    assert support["evidence_texts"] == ([source_unit] if origin == "merged-correction" else [])


@pytest.mark.parametrize("selected", [True, False])
def test_uninstrumented_cloud_selection_cannot_keep_internal_support(monkeypatch, selected):
    from core.cloud_broker import CloudBrokerResult
    from core.cloud_privacy_policy import CloudPrivacyGrant
    from core.cloud_provider_contract import CloudModelResponse, CloudTaskRequirements, PricingState, PrivacyClass

    messages = [{"role": "system", "content": CAPSULE}, {"role": "user", "content": QUESTION}]
    record = _record(messages, [CAPSULE])
    broker = mock.Mock()
    broker.execute.return_value = CloudBrokerResult(
        selected, CloudModelResponse("The crossing lasted eleven days.", {"cost": 0.0}) if selected else None,
        provider_id="fixture-cloud", model_id="fixture-model", model_call_id="fixture-call", attempts=1,
        pricing_state=PricingState.PROMOTIONAL.value)
    router = MemoryFirstRouter(cloud_broker=broker)
    monkeypatch.setattr("core.memory_first_router.cloud_escalation_policy.load_policy",
                        lambda: SimpleNamespace(free_cloud_enabled=True))
    context = {"surface": "cli", "session_id": "provider-bound", "turn_id": "provider-turn",
               "admitted_capsule_evidence": deepcopy(record),
               "cloud_task_requirements": CloudTaskRequirements(min_context_tokens=10, expected_output_tokens=64,
                                                                required_capabilities=("text",), privacy_class=PrivacyClass.PUBLIC),
               "cloud_privacy_grant": CloudPrivacyGrant()}
    request = ModelRequest(task_kind="conversation", prompt=QUESTION, messages=messages, max_output_tokens=64,
                           metadata={"admitted_capsule_evidence": record, "request_evidence_message_indices": [0]})
    decision = router._try_free_cloud_boost(request=request, task=SimpleNamespace(task_id="cloud-support"),
                                          task_hash="cloud-support", output_mode="plain_text", source_context=context)
    if selected:
        assert decision is not None and decision.used_model
        assert context["admitted_capsule_evidence"]["evidence_texts"] == []
        assert context["admitted_capsule_evidence"]["text"] == ""
        assert context["admitted_capsule_evidence"]["query_sha256"] == _digest(QUESTION)
    else:
        assert decision is None
        assert context["admitted_capsule_evidence"] == record
    assert request.metadata["admitted_capsule_evidence"] == record
