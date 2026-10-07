"""Support follows the final request after history budget and memory suppression."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from core import bootstrap_context as bc
from core import prompt_normalizer as pn
from core.internal_message_schema import InternalMessage

CAPSULE = ("<retrieved_context>\nDistilled local facts. Answer from these exact facts only.\n"
           "- user said: The crossing took 11 days.\n</retrieved_context>")
QUESTION = "How long did the crossing take?"


def _request(monkeypatch, context, *, drop_budget=False, with_capsule=True):
    def history(source_context, **kwargs):
        if not with_capsule:
            return [], "dialogue_memory"
        bc._record_admitted_capsule_evidence(source_context, session_id="bound-chat", capsule_text=CAPSULE)
        return [InternalMessage(role="system", content=CAPSULE)], "dialogue_memory"
    monkeypatch.setattr(pn, "_history_messages_for_chat", history)
    if drop_budget:
        monkeypatch.setattr(pn, "_apply_final_history_budget",
                            lambda correction, history, capsule, ctx, **kwargs: (correction, history, [], ctx))
    return pn._build_conversational_request(
        user_text=QUESTION, persona=SimpleNamespace(display_name="VOOL", tone="calm"),
        classification={"task_class":"research"},
        context_result=SimpleNamespace(report=SimpleNamespace(to_dict=lambda: {}),
                                       assembled_context=lambda **kwargs: ""),
        task_kind="summarization", output_mode="plain_text", trace_id="binding-test", ambiguity=0.9,
        source_context=context, current_turn_id="turn-a",
    )


@pytest.mark.parametrize("off,drop_budget", [(True,False), (False,True)])
def test_omitted_capsule_cannot_authorize_an_answer(monkeypatch, off, drop_budget):
    context = {"chat_id":"bound-chat", "surface":"api", "platform":"api", "memory_prompt_enabled":not off}
    request = _request(monkeypatch, context, drop_budget=drop_budget)
    assert not any("<retrieved_context>" in message.content for message in request.messages)
    assert not bc.admitted_capsule_evidence_text(context, "bound-chat")
    from core.agent_runtime.turn_reasoning import _past_time_guard_evidence

    assert _past_time_guard_evidence(
        context_result=SimpleNamespace(assembled_context=lambda: CAPSULE, local_candidates=[{"content": CAPSULE}]),
        source_context=context, web_notes=[CAPSULE], session_id="bound-chat", question=QUESTION,
    ) == []


def test_evidence_is_sealed_to_final_messages_query_and_turn(monkeypatch):
    context = {"chat_id":"bound-chat", "surface":"api", "platform":"api", "current_turn_id":"turn-a"}
    request = _request(monkeypatch, context)
    record = context["admitted_capsule_evidence"]
    serialized = json.dumps(request.as_openai_messages(), ensure_ascii=False, separators=(",",":"))
    assert record["request_sha256"] == hashlib.sha256(serialized.encode()).hexdigest()
    assert record["turn_id"] == "turn-a"
    # Carrier indices include the separate runtime clock, which since ae264ad6 (2026-10-06) is the turn-directives
    # system message after the stable leading system prompt (index 1), followed by the capsule (index 2). It must
    # survive provider serialization without becoming event/claim evidence.
    assert record["evidence_message_indices"] == [1, 2]
    assert record["evidence_texts"] == [CAPSULE]
    clock = bc.admitted_request_reference_clock(context, "bound-chat", question=QUESTION)
    assert clock is not None
    assert record["reference_clock"]["text"] in "\n".join(str(m.content or "") for m in request.messages if getattr(m, "role", None) == "system")
    assert "\n".join(str(m.content or "") for m in request.messages if getattr(m, "role", None) == "system") not in record["evidence_texts"]
    assert bc.admitted_request_evidence_texts(context, "bound-chat", question=QUESTION) == (CAPSULE,)
    assert bc.admitted_capsule_evidence_text(context, "bound-chat", question=QUESTION) == CAPSULE
    assert not bc.admitted_capsule_evidence_text(context, "bound-chat", question="When did the ferry leave?")
    context["current_turn_id"] = "turn-b"
    assert not bc.admitted_capsule_evidence_text(context, "bound-chat", question=QUESTION)


def test_mutated_evidence_text_is_rejected(monkeypatch):
    context = {"chat_id":"bound-chat", "surface":"api", "platform":"api"}
    _request(monkeypatch, context)
    context["admitted_capsule_evidence"]["text"] += " It took 99 days."
    assert not bc.admitted_capsule_evidence_text(context, "bound-chat", question=QUESTION)


def test_cleared_carrier_cannot_borrow_same_chat_telemetry(monkeypatch):
    context = {"chat_id": "bound-chat", "surface": "api", "platform": "api"}
    _request(monkeypatch, context)
    context["admitted_capsule_evidence"] = None
    assert bc.admitted_request_evidence_texts(context, "bound-chat", question=QUESTION) == ()
    assert not bc.admitted_capsule_evidence_text(context, "bound-chat", question=QUESTION)


def test_summary_survives_the_combined_history_and_capsule_budget(monkeypatch):
    from core.context_history_authority import HISTORY_MAX_CHARS

    summary = "<context_summary>The launch port is 8096.</context_summary>"
    transcript = [
        {"role": "assistant", "content": summary, "_history_retention_priority": 1},
        *({"role": "user" if i % 2 == 0 else "assistant", "content": f"Recent turn {i}"} for i in range(8)),
        {"role": "system", "content": CAPSULE},
    ]
    monkeypatch.setattr(pn, "canonical_runtime_transcript", lambda **kwargs: (transcript, "structured_dialogue_memory"))
    history, _ = pn._history_messages_for_chat(
        {}, runtime_session_id="summary-chat", current_user_text="What launch port did we choose?",
        prompt_profile="chat_minimal",
    )
    context_message = InternalMessage(role="user", content="Relevant historical context.")
    context_result = SimpleNamespace(bootstrap_items=[SimpleNamespace(source_type="runtime_memory")])
    _, retained_history, retained_capsules, _ = pn._apply_final_history_budget(
        [], history[:-1], history[-1:], context_message, context_result=context_result,
        max_messages=10, max_chars=HISTORY_MAX_CHARS,
    )
    assert summary in [message.content for message in retained_history]
    assert "Recent turn 7" in [message.content for message in retained_history]
    assert CAPSULE in [message.content for message in retained_capsules]
    assert len(retained_history) + len(retained_capsules) + 1 <= 10


def test_clock_only_provider_carrier_cannot_authorize_a_past_event(monkeypatch):
    from copy import deepcopy
    from datetime import datetime, timezone

    from adapters.base_adapter import ModelRequest
    from core.model_output_guard import stated_past_time_claims
    from tests.test_provider_request_evidence_binding import _adapter

    class FixtureDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2024, 2, 1, 12, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(pn, "datetime", FixtureDateTime)
    context = {"chat_id":"bound-chat", "surface":"api", "platform":"api", "current_turn_id":"turn-a"}
    internal = _request(monkeypatch, context, with_capsule=False)
    record = deepcopy(context["admitted_capsule_evidence"])
    assert record["evidence_message_indices"] == [1]   # the clock carrier after the stable leading prompt (ae264ad6)
    assert record["evidence_texts"] == []
    request = ModelRequest(task_kind="summarization", prompt=QUESTION,
        system_prompt=internal.system_prompt(), messages=internal.as_openai_messages(),
        max_output_tokens=256, metadata={"admitted_capsule_evidence":record,
            "request_evidence_message_indices":record["evidence_message_indices"]})
    monkeypatch.setattr("adapters.openai_compatible_adapter.build_memory_prefix_for_request",
        lambda _: "Synthetic wrapper; no private profile.")
    payload = _adapter(context_window=32768)._build_openai_payload(request, force_json=False, stream=False)
    context["admitted_capsule_evidence"] = request.metadata["admitted_capsule_evidence"]
    clock = bc.admitted_request_reference_clock(context, "bound-chat", question=QUESTION)
    assert clock is not None and clock.day.isoformat() == "2024-02-01"
    assert clock.request_sha256 == bc._evidence_digest(payload["messages"])
    assert any(record["reference_clock"]["text"] in str(m.get("content") or "") for m in payload["messages"] if m.get("role") == "system")  # the clock carrier (ae264ad6)
    evidence = bc.admitted_request_evidence_texts(context, "bound-chat", question=QUESTION)
    assert evidence == ()
    assert stated_past_time_claims("The crossing lasted 11 days.", question=QUESTION,
        evidence_texts=evidence, reference_clock=clock)
    assert stated_past_time_claims("The crossing ended on February 1, 2024.", question=QUESTION,
        evidence_texts=evidence, reference_clock=clock)
