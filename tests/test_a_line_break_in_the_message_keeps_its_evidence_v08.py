"""A message with a line break is the same question at the request seal as on the turn that reads the seal.

Measured on the real app, cloud route: "Answer using the imported prior conversations. ...\\nWhen did Caroline go to the
support group?" with the answering record in the turn's retrieved context. The model answered with the recorded day and
the reply delivered was "I don't have that time in anything I can see from our conversation, so I am not going to state
one." The seal is written from the message as typed; the past-time check reads it back with the turn's working text, in
which the line break is a space, so the two digests of the one question differed and the check was handed no evidence.
The same message on one line was answered.

What the seal is for is pinned beside it: another question, the same words in another order, another chat, another
turn and a seal written before this field existed still hand over nothing, and a time no record states is still
withdrawn.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.turn_reasoning import _past_time_guard_evidence
from core.bootstrap_context import (
    admitted_capsule_evidence_text,
    admitted_request_evidence_texts,
    admitted_request_reference_clock,
    finalize_request_evidence,
    seal_request_evidence,
)
from core.model_output_guard import replace_unsupported_past_time_claims, unverified_past_time_notice

CHAT = "openclaw:0a1b2c3d4e5f6a7b8c9d"
TYPED = "Answer from what I told you.\nWhen was the boiler serviced?"
WORKING = "Answer from what I told you. When was the boiler serviced?"
CAPSULE = "<retrieved_context>\n- user said: The boiler was serviced on 3 March 2025 by Mr Tolland.\n</retrieved_context>"
NO_TIME = "<retrieved_context>\n- user said: The fence was painted by Mr Orrin.\n</retrieved_context>"
CLOCK = "The current date and time is Thursday 19 November 2026, 13:00 (UTC, UTC offset +0000)."


def _seal(question=TYPED, capsule=CAPSULE):
    context = {"chat_id": CHAT, "_canonical_user_turn_id": "dialogue-1", "cancel_turn_id": "client-1",
               "turn_id": "client-1", "request_id": "api-1"}
    messages = [{"role": "system", "content": CLOCK}, {"role": "system", "content": capsule},
                {"role": "user", "content": question}]
    seal_request_evidence(context, session_id=CHAT, question=question, turn_id="dialogue-1", messages=messages,
                          evidence_texts=[capsule], capsule_text=capsule, evidence_message_indices=[0, 1],
                          reference_clock={"source": "runtime_clock", "date": "2026-11-19", "text": CLOCK})
    return context


# ------------------------------------------------------------ the same question, typed with a line break


@pytest.mark.parametrize("typed", [
    TYPED,
    "Answer from what I told you.\n\nWhen was the boiler serviced?",
    "Answer from what I told you.\r\nWhen was the boiler serviced?",
    "Answer from what I told you.\tWhen was  the boiler serviced?\n",
])
def test_the_working_text_of_a_message_with_a_line_break_reads_its_own_seal(typed):
    context = _seal(typed)
    assert admitted_request_evidence_texts(context, CHAT, question=WORKING) == (CAPSULE,)
    assert admitted_capsule_evidence_text(context, CHAT, question=WORKING) == CAPSULE
    clock = admitted_request_reference_clock(context, CHAT, question=WORKING)
    assert clock is not None and clock.day.isoformat() == "2026-11-19"


def test_the_message_as_typed_reads_its_seal_as_before():
    context = _seal()
    assert admitted_request_evidence_texts(context, CHAT, question=TYPED) == (CAPSULE,)
    assert context["admitted_capsule_evidence"]["query_sha256"] != context["admitted_capsule_evidence"]["query_spaced_sha256"]


def test_a_one_line_message_is_sealed_and_read_as_before():
    context = _seal(WORKING)
    record = context["admitted_capsule_evidence"]
    assert record["query_sha256"] == record["query_spaced_sha256"]
    assert admitted_request_evidence_texts(context, CHAT, question=WORKING) == (CAPSULE,)


def test_the_seal_survives_the_provider_finalizer_with_its_question():
    context = _seal()
    messages = [{"role": "system", "content": CLOCK}, {"role": "system", "content": CAPSULE}, {"role": "user", "content": TYPED}]
    context["admitted_capsule_evidence"] = finalize_request_evidence(
        context["admitted_capsule_evidence"], messages, evidence_messages=messages[:2])
    assert admitted_request_evidence_texts(context, CHAT, question=WORKING) == (CAPSULE,)


# ------------------------------------------------------------ what the seal must still refuse


@pytest.mark.parametrize("question", [
    "Answer from what I told you. When was the gutter cleared?",
    "When was the boiler serviced? Answer from what I told you.",
    "Answer from what I told you. When was the boiler serviced",
    "answer from what i told you. when was the boiler serviced?",
    "Answer from what I told you.When was the boiler serviced?",
    "",
])
def test_another_question_reads_nothing(question):
    context = _seal()
    assert admitted_request_evidence_texts(context, CHAT, question=question) == ()
    assert admitted_capsule_evidence_text(context, CHAT, question=question) == ""
    assert admitted_request_reference_clock(context, CHAT, question=question) is None


def test_another_chat_reads_nothing():
    context = _seal()
    assert admitted_request_evidence_texts(context, "openclaw:ffffffffffffffffffff", question=WORKING) == ()


def test_another_turn_reads_nothing():
    context = _seal()
    context["_canonical_user_turn_id"] = "dialogue-2"
    assert admitted_request_evidence_texts(context, CHAT, question=WORKING) == ()


def test_a_seal_written_without_the_spaced_digest_keeps_the_old_rule():
    context = _seal()
    del context["admitted_capsule_evidence"]["query_spaced_sha256"]
    assert admitted_request_evidence_texts(context, CHAT, question=TYPED) == (CAPSULE,)
    assert admitted_request_evidence_texts(context, CHAT, question=WORKING) == ()


def test_an_empty_spaced_digest_matches_nothing():
    context = _seal()
    context["admitted_capsule_evidence"]["query_spaced_sha256"] = ""
    assert admitted_request_evidence_texts(context, CHAT, question=WORKING) == ()


def test_changed_evidence_under_a_matching_question_is_refused():
    context = _seal()
    context["admitted_capsule_evidence"]["evidence_texts"] = [CAPSULE.replace("3 March", "4 March")]
    assert admitted_request_evidence_texts(context, CHAT, question=WORKING) == ()


# ------------------------------------------------------------ at the past-time check itself


def _checked(answer, context, question=WORKING):
    evidence = _past_time_guard_evidence(context_result=None, source_context=context, web_notes=[], session_id=CHAT,
                                         question=question)
    receipt: dict = {}
    return replace_unsupported_past_time_claims(answer, question=question, evidence_texts=evidence,
                                                reference_clock=None, decision_receipt=receipt), evidence


def test_the_recorded_day_reaches_the_owner_when_the_message_had_a_line_break():
    answer = "The boiler was serviced on 3 March 2025."
    kept, evidence = _checked(answer, _seal())
    assert evidence == [CAPSULE]
    assert kept == answer


def test_a_day_the_record_does_not_state_is_still_withdrawn():
    kept, evidence = _checked("The boiler was serviced on 4 April 2024.", _seal())
    assert evidence == [CAPSULE]
    assert kept == unverified_past_time_notice()


def test_a_day_is_still_withdrawn_when_no_record_states_a_time():
    typed = "Answer from what I told you.\nWhen was the fence painted?"
    working = "Answer from what I told you. When was the fence painted?"
    kept, evidence = _checked("The fence was painted on 12 June 2025.", _seal(typed, NO_TIME), question=working)
    assert evidence == [NO_TIME]
    assert kept == unverified_past_time_notice()


def test_the_check_of_another_question_is_handed_nothing():
    kept, evidence = _checked("The boiler was serviced on 3 March 2025.", _seal(),
                              question="Answer from what I told you. When was the gutter cleared?")
    assert evidence == []
    assert kept == unverified_past_time_notice()
