"""Canonical dialogue, client cancellation and API request IDs do not mix."""
from copy import deepcopy
from datetime import date

import pytest

from core.bootstrap_context import (
    admitted_request_evidence_texts,
    admitted_request_reference_clock,
    finalize_request_evidence,
    seal_request_evidence,
)

QUESTION = "How many months ago did I attend the photography workshop?"
CAPSULE = "<retrieved_context>\n- user said: I attended the photography workshop on November 1, 2023.\n</retrieved_context>"
CLOCK = "The current date and time is Thursday 01 February 2024, 12:00 (EET, UTC offset +0200)."
ANSWER = "You attended the photography workshop three months ago. Today is February 1, 2024."


def seal(*, identities=True):
    context = {"chat_id": "identity-clock-session"}
    if identities:
        context.update(_canonical_user_turn_id="dialogue-server-1", cancel_turn_id="client-1", turn_id="client-1", request_id="api-request-1")
    else:
        context["current_turn_id"] = "dialogue-server-1"
    messages = [{"role":"system", "content":CLOCK}, {"role":"system", "content":CAPSULE}, {"role":"user", "content":QUESTION}]
    seal_request_evidence(context, session_id=context["chat_id"], question=QUESTION,
        turn_id="dialogue-server-1", messages=messages, evidence_texts=[CAPSULE],
        capsule_text=CAPSULE, evidence_message_indices=[0,1],
        reference_clock={"source":"runtime_clock", "date":"2024-02-01", "text":CLOCK})
    return context, messages


def assert_refused(context, *, session="identity-clock-session", question=QUESTION):
    assert admitted_request_evidence_texts(context, session, question=question) == ()
    assert admitted_request_reference_clock(context, session, question=question) is None


def test_canonical_client_and_request_ids_remain_separate():
    context, _ = seal()
    record = context["admitted_capsule_evidence"]
    assert record["turn_id"] == "dialogue-server-1"
    assert admitted_request_evidence_texts(context, context["chat_id"], question=QUESTION) == (CAPSULE,)
    assert admitted_request_reference_clock(context, context["chat_id"], question=QUESTION).turn_id == "dialogue-server-1"
    binding = record["identity_bindings"]
    assert binding["canonical_turn_id"] == "dialogue-server-1"
    assert binding["context_ids"]["cancel_turn_id"] == "client-1"
    assert binding["context_ids"]["request_id"] == "api-request-1"
    assert binding["context_ids"]["turn_id"] == "client-1"


@pytest.mark.parametrize("key", ["_canonical_user_turn_id", "cancel_turn_id", "turn_id", "request_id"])
def test_changed_identity_in_its_own_space_withholds_source_and_clock(key):
    context, _ = seal()
    context[key] += "-later"
    assert_refused(context)


@pytest.mark.parametrize("key", ["_canonical_user_turn_id", "cancel_turn_id", "turn_id", "request_id"])
def test_removed_identity_cannot_reuse_a_sealed_request(key):
    context, _ = seal()
    del context[key]
    assert_refused(context)


@pytest.mark.parametrize("key", ["cancel_turn_id", "request_id", "turn_id"])
def test_new_client_or_request_identity_cannot_borrow_an_unbound_receipt(key):
    context, _ = seal(identities=False)
    context[key] = "new-client-or-request"
    assert_refused(context)


def test_missing_canonical_identity_does_not_use_matching_client_as_substitute():
    context, _ = seal()
    context["cancel_turn_id"] = "dialogue-server-1"
    del context["_canonical_user_turn_id"]
    assert_refused(context)


def test_legacy_explicit_current_turn_id_still_binds_in_its_own_space():
    context, _ = seal(identities=False)
    assert admitted_request_evidence_texts(context, context["chat_id"], question=QUESTION) == (CAPSULE,)
    context["current_turn_id"] = "later-canonical-turn"
    assert_refused(context)


@pytest.mark.parametrize("target", ["question", "session", "cleared", "text", "evidence", "clock", "identity"])
def test_stale_cleared_or_corrupt_receipt_cannot_authorize_recall(target, monkeypatch):
    context, _ = seal()
    original = deepcopy(context["admitted_capsule_evidence"])
    if target == "question":
        assert_refused(context, question="When did I attend the workshop?")
    elif target == "session":
        assert_refused(context, session="foreign-session")
    elif target == "cleared":
        monkeypatch.setattr("core.context_retrieval.get_last_retrieval_telemetry", lambda: {"last_admitted_capsule": original})
        context["admitted_capsule_evidence"] = None
        assert_refused(context)
    else:
        record = context["admitted_capsule_evidence"]
        if target == "text": record["text"] += "changed"
        if target == "evidence": record["evidence_texts"].append("Borrowed source.")
        if target == "clock": record["reference_clock"]["date"] = "2024-02-02"
        if target == "identity": record["identity_bindings"]["context_ids"]["request_id"] = "other-request"
        assert_refused(context)


def test_finalizer_preserves_identity_seal_while_binding_actual_surviving_wire():
    context, messages = seal()
    original = deepcopy(context["admitted_capsule_evidence"])
    context["admitted_capsule_evidence"] = finalize_request_evidence(original, messages, evidence_messages=messages[:2])
    finalized = context["admitted_capsule_evidence"]
    assert finalized["identity_bindings"] == original["identity_bindings"]
    assert finalized["identity_sha256"] == original["identity_sha256"]
    assert admitted_request_evidence_texts(context, context["chat_id"], question=QUESTION) == (CAPSULE,)
    assert admitted_request_reference_clock(context, context["chat_id"], question=QUESTION).day == date(2024,2,1)


def test_canonical_only_legacy_receipt_cannot_donate_to_changed_canonical_turn():
    context, _ = seal()
    record = context["admitted_capsule_evidence"]
    record.pop("identity_bindings", None)
    record.pop("identity_sha256", None)
    for key in ("cancel_turn_id", "request_id", "turn_id"):
        context.pop(key)
    assert admitted_request_evidence_texts(context, context["chat_id"], question=QUESTION) == (CAPSULE,)
    context["_canonical_user_turn_id"] = "another-canonical-turn"
    assert_refused(context)


def test_legacy_receipt_cannot_gain_client_request_authority():
    context, _ = seal(identities=False)
    record = context["admitted_capsule_evidence"]
    record.pop("identity_bindings", None)
    record.pop("identity_sha256", None)
    context["request_id"] = "later-api-request"
    assert_refused(context)


def test_actual_adapter_and_temporal_guard_share_final_request_and_reference_clock(monkeypatch):
    from adapters.base_adapter import ModelRequest
    from core.agent_runtime.turn_reasoning import _past_time_guard_evidence, _past_time_guard_reference_clock
    from core.bootstrap_context import _evidence_digest
    from core.model_output_guard import replace_unsupported_past_time_claims
    from tests.test_provider_request_evidence_binding import _adapter
    context, messages = seal()
    record = deepcopy(context["admitted_capsule_evidence"])
    request = ModelRequest(task_kind="conversation", prompt=QUESTION, system_prompt=CLOCK,
        messages=messages, max_output_tokens=320,
        metadata={"admitted_capsule_evidence":record, "request_evidence_message_indices":[0,1]})
    monkeypatch.setattr("adapters.openai_compatible_adapter.build_memory_prefix_for_request", lambda _: "Authorized synthetic wrapper.")
    payload = _adapter(context_window=32768)._build_openai_payload(request, force_json=False, stream=False)
    context["admitted_capsule_evidence"] = request.metadata["admitted_capsule_evidence"]
    clock = _past_time_guard_reference_clock(context, context["chat_id"], question=QUESTION)
    assert clock is not None
    assert clock.turn_id == context["_canonical_user_turn_id"]
    assert clock.request_sha256 == _evidence_digest(payload["messages"])
    evidence = _past_time_guard_evidence(context_result=None, source_context=context,
        web_notes=[], session_id=context["chat_id"], question=QUESTION)
    assert evidence == [CAPSULE]
    assert replace_unsupported_past_time_claims(ANSWER, question=QUESTION, evidence_texts=evidence, reference_clock=clock) == ANSWER


def test_legacy_generic_turn_alias_requires_matching_explicit_canonical_identity():
    context, _ = seal(identities=False)
    context["admitted_capsule_evidence"].pop("identity_bindings")
    context["admitted_capsule_evidence"].pop("identity_sha256")
    context["turn_id"] = context["current_turn_id"]
    assert admitted_request_evidence_texts(context, context["chat_id"], question=QUESTION) == (CAPSULE,)
    assert admitted_request_reference_clock(context, context["chat_id"], question=QUESTION).turn_id == "dialogue-server-1"
    context["turn_id"] = "another-client-turn"
    assert_refused(context)


def test_legacy_generic_turn_cannot_replace_absent_canonical_identity():
    context, _ = seal(identities=False)
    context["admitted_capsule_evidence"].pop("identity_bindings")
    context["admitted_capsule_evidence"].pop("identity_sha256")
    context["turn_id"] = context.pop("current_turn_id")
    assert_refused(context)
