"""Retrieved evidence has its own allowance and reaches the actual provider payload."""
from __future__ import annotations
from types import SimpleNamespace
from copy import deepcopy
import json
import pytest
from core import bootstrap_context as bc
from core import prompt_normalizer as pn
from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter

QUESTION = "What sequence did you recommend for preparing the exhibit?"
TARGET = "Stage 27: leave a 3 mm hinge gap before binding the panels."
CAPSULE = "<retrieved_context>\n- assistant said: " + ("Earlier exhibit material, retained in its original source unit. " * 140) + TARGET + "\n</retrieved_context>"



@pytest.fixture(autouse=True)
def preserve_retrieval_telemetry():
    from core.context_retrieval import get_last_retrieval_telemetry, _set_retrieval_telemetry
    before = deepcopy(get_last_retrieval_telemetry())
    try:
        yield
    finally:
        _set_retrieval_telemetry(before)


def _assembly(monkeypatch, *, admitted=True, memory=True, capsule=CAPSULE, extra_capsule=""):
    context = {"chat_id": "delivery-bound", "runtime_session_id": "delivery-bound", "surface": "api", "platform": "api", "memory_prompt_enabled": memory}
    def transcript(**kwargs):
        bc._record_admitted_capsule_evidence(kwargs["source_context"], session_id="delivery-bound", capsule_text=capsule if admitted else "")
        return [{"role": "system", "content": text} for text in (extra_capsule, capsule) if text], "structured_dialogue_memory"
    monkeypatch.setattr(pn, "canonical_runtime_transcript", transcript)
    result = SimpleNamespace(report=SimpleNamespace(to_dict=lambda: {}), bootstrap_items=[SimpleNamespace(source_type="runtime_memory")], assembled_context=lambda **kwargs: "Relevant historical context: prior exhibit notes.")
    request = pn._build_conversational_request(user_text=QUESTION, persona=SimpleNamespace(display_name="VOOL", tone="calm"), classification={"task_class": "research"}, context_result=result, task_kind="conversation", output_mode="plain_text", trace_id="delivery-fixture", ambiguity=0.9, source_context=context, current_turn_id="read-turn")
    return request, context


def _payload(request, context, *, window=32768):
    adapter = OpenAICompatibleAdapter(SimpleNamespace(provider_id="delivery-fixture", model_name="fixture", metadata={"runtime_family": "ollama"}, runtime_config={"base_url": "http://127.0.0.1:11434", "context_window": window, "timeout_seconds": 2.0}))
    model_request = ModelRequest(task_kind="conversation", prompt=QUESTION, system_prompt=request.messages[0].content, messages=request.as_openai_messages(), max_output_tokens=2048, reasoning_mode="disabled", metadata={**request.metadata, "admitted_capsule_evidence": deepcopy(context["admitted_capsule_evidence"]), "request_evidence_message_indices": context["admitted_capsule_evidence"]["evidence_message_indices"]})
    return adapter._build_openai_payload(model_request, force_json=False, stream=False), model_request


def test_budgeted_capsule_survives_request_assembly_and_actual_serialization(monkeypatch):
    request, context = _assembly(monkeypatch)
    assert len(CAPSULE) > 5000
    assert any(message.content == CAPSULE for message in request.messages)
    payload, model_request = _payload(request, context)
    assert any(message["content"] == CAPSULE for message in payload["messages"])
    assert TARGET in json.dumps(payload, ensure_ascii=False)
    assert model_request.metadata["admitted_capsule_evidence"]["text"] == CAPSULE


def test_different_complete_manifest_survives_the_same_handoff(monkeypatch):
    rows = "\n".join(f"- Cart {i}: dispatch door {i}, {i + 11} indigo folders, checker Person{i}; retain the complete manifest entry." for i in range(1, 65))
    capsule = "<retrieved_context>\n" + rows + "\n</retrieved_context>"
    request, context = _assembly(monkeypatch, capsule=capsule)
    payload, _ = _payload(request, context)
    assert any(message["content"] == capsule for message in payload["messages"])
    assert "Cart 64:" in json.dumps(payload)


def test_unadmitted_history_marker_cannot_bypass_history_envelope(monkeypatch):
    request, context = _assembly(monkeypatch, admitted=False)
    assert not any(message.content == CAPSULE for message in request.messages)
    assert not bc.admitted_capsule_evidence_text(context, "delivery-bound", question=QUESTION)


def test_memory_off_still_removes_large_admitted_capsule(monkeypatch):
    request, context = _assembly(monkeypatch, memory=False)
    assert not any(message.content == CAPSULE for message in request.messages)
    assert not bc.admitted_capsule_evidence_text(context, "delivery-bound", question=QUESTION)


def test_real_provider_capacity_still_sheds_oversized_unit_and_support(monkeypatch):
    request, context = _assembly(monkeypatch)
    payload, model_request = _payload(request, context, window=4096)
    assert not any(message["content"] == CAPSULE for message in payload["messages"])
    assert model_request.metadata["admitted_capsule_evidence"]["text"] == ""


def test_mixed_capsules_exempt_only_this_assemblys_fresh_admission(monkeypatch):
    forged = "<retrieved_context>\n" + "Unadmitted old marker text. " * 230 + "\n</retrieved_context>"
    request, context = _assembly(monkeypatch, extra_capsule=forged)
    payload, _ = _payload(request, context)
    contents = [item["content"] for item in payload["messages"]]
    assert CAPSULE in contents
    assert forged not in contents
    assert context["admitted_capsule_evidence"]["text"] == CAPSULE
