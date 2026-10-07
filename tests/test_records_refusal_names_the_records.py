"""A refused answer on a turn supported only by this chat's records says it is not in the records.

Measured on the port's paid LoCoMo run (2026-10-07): "How much does James pay per dance class?" is a trap (James
stated $10 for a cooking class; no dance-class price exists). The reader declined correctly, the current-claim guard
withdrew the draft over its "$10", and the publication gate then shipped "it needed current information", a notice
about a live lookup that never ran. When the turn's only support rows are the chat's own memory records, the gate's
refusal now says the answer is not in those records; a turn with a live lookup keeps the current-information notice.
"""
from __future__ import annotations

import pytest

from core.grounding_lifecycle import GroundingLifecycle, TurnIdentity
from core.grounding_publication import publication_verdict
from core.memory_grounding import memory_record_rows

JAMES = (
    "<retrieved_context>\n"
    "James: Yes, two days ago I signed up for a cooking class. I never liked cooking, but I wanted to learn something new. (stated: 2022-05-01)\n"
    "James: At only $10 per class, it's very cheap! Also, I made meringue there. (stated: 2022-05-01)\n"
    "</retrieved_context>"
)
RECORDS_LEAD = "That is not mentioned in the records I have from our conversations"
LIVE_LEAD = "I can't publish an answer to this: it needed current information"


def _verdict(answer, *, records=JAMES, notes=()):
    lifecycle = GroundingLifecycle(
        lifecycle_id="records-refusal", identity=TurnIdentity(),
        request_text="How much does James pay per dance class?", model_authored=True,
        memory_records=tuple(memory_record_rows(records)), retrieved_notes=tuple(notes),
    )
    return publication_verdict(lifecycle, answer)


@pytest.mark.parametrize("answer", [
    "I could not obtain a current reading for this on this turn, so I am not going to state one.",
    "James pays $25 per dance class.",
], ids=["guard-notice", "invented-price"])
def test_a_refusal_over_records_alone_says_it_is_not_in_the_records(answer):
    verdict = _verdict(answer)
    assert verdict.state in {"refused", "failed"}, verdict
    assert verdict.content.startswith(RECORDS_LEAD), verdict.content
    assert "$25" not in verdict.content and "current information" not in verdict.content


def test_a_turn_with_a_lookup_keeps_the_current_information_notice():
    note = {"summary": "Dance studio price list: beginner classes are $18 per session.", "source": "web"}
    verdict = _verdict("James pays $25 per dance class.", notes=(note,))
    assert verdict.state in {"refused", "failed"}, verdict
    assert verdict.content.startswith(LIVE_LEAD), verdict.content


def test_a_live_ask_in_a_chat_with_records_keeps_the_current_information_notice():
    lifecycle = GroundingLifecycle(
        lifecycle_id="records-refusal-live", identity=TurnIdentity(),
        request_text="When does the next train to Vilnius leave?", model_authored=True,
        memory_records=tuple(memory_record_rows(JAMES)),
    )
    verdict = publication_verdict(lifecycle, "The next train to Vilnius leaves at 14:05 from platform 3.")
    assert verdict.state in {"refused", "failed"}, verdict
    assert verdict.content.startswith(LIVE_LEAD), verdict.content


def test_the_guard_names_the_records_only_for_a_question_about_someone_they_name():
    from core.memory_grounding import question_names_someone_in_the_records

    assert question_names_someone_in_the_records("How much does James pay per dance class?", JAMES)
    assert not question_names_someone_in_the_records("When does the next train to Vilnius leave?", JAMES)
    assert not question_names_someone_in_the_records("How much does Maria pay per dance class?", JAMES)
    assert not question_names_someone_in_the_records(
        "How much does James pay?", "- [2022-05-02] assistant said: James pays $10.\n"
    )
