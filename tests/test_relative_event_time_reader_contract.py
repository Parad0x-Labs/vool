"""A record's relative time is answered anchored to the record's date, not as that date.

Measured on archived dev answers (no provider call in this file): a record stated on one date that
says the event happened "last weekend" or "last week" was answered with the record's own date, so
the event was dated to the day it was talked about. Two records that bound an event (planned on one
date, under way on a later one) were answered with the later date alone.

The reader request of a memory question that asks when something happened now states the rule in
its answer-format block and on the one rewrite: keep the relative time anchored to the record's
stated date ("the weekend before <date>"), never give that date as the event date unless the record
says the event happened that day, and give a range when two records bound the event. The rule is
absent from every other turn.

The past-time guard ships the anchored form: under the user's approximate-date request, and, for a
clause that names the asked event, when the anchored phrase read against the record's date meets
the record's own relative window. A fabricated anchor date is still withdrawn.

Every sentence here is synthetic.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

import core.memory_first_router as mfr
import core.ordinary_chat_response_guard as guard
from core.memory_first_router import MemoryFirstRouter
from core.model_output_guard import replace_unsupported_past_time_claims
from core.temporal_question_scope import asks_event_time

READER_PREFIX = (
    "Answer from the harbor logbook entries I shared earlier. Use only what those entries "
    "support. If they do not support an answer, say you do not know. Give a concise final "
    "answer.\n"
)
LICENSE = " Use the date of the conversation to answer with an approximate date."

RULE_MARKERS = (
    "This question asks when something happened.",
    "anchored to the date the record was stated",
    "Never give the record's stated date as the event date unless the record says the event "
    "happened that day.",
    "give the range between their dates",
)

EVENT_TIME_ASKS = [
    "When did Mira repaint her kayak?",
    "When was Oren's first regatta?",
    "When did I renew the mooring permit?",
    "What date did Lena move to the harbor flat?",
    "In which month did the trawler leave for the north banks?",
    "Which year did Ilse buy the crane?",
    "How long ago did Tomasz fix the winch?",
    "How many weeks ago did we paint the hull?",
    "When were the new sails delivered?",
    "When did Mira repaint her kayak?" + LICENSE,
]

NOT_EVENT_TIME_ASKS = [
    "What did Mira repaint?",
    "What did I buy when I visited the coast?",
    "Why did Oren pick the north pier?",
    "When does the harbor office open?",
    "What day is it today?",
    "What time is it in Lisbon?",
    "Which colour did Mira pick: (a) red (b) blue",
    "Write a short poem about a weekend at the harbor.",
    "Where was the regatta held?",
    "When will the ferry leave tomorrow?",
]


def _policy(text: str, *, records: bool = True, output_mode: str = "plain_text") -> dict:
    return guard.ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode=output_mode,
        user_text=text,
        memory_records_supplied=records,
    )


def _reader_request(user_text: str, *, capsule: bool = True):
    """The provider request for one turn; `normalize_prompt` is replaced by a fixed base."""
    wire = [
        {"role": "system", "content": "BASE SYSTEM"},
        *(
            [{"role": "system", "content": "<retrieved_context>harbor log</retrieved_context>"}]
            if capsule
            else []
        ),
        {"role": "user", "content": user_text},
    ]
    internal = SimpleNamespace(
        metadata={},
        temperature=0.2,
        max_output_tokens=256,
        context_summary="",
        trace_id="relative-time-trace",
        attachments=(),
        messages=[SimpleNamespace(**message) for message in wire],
        system_prompt=lambda: "BASE SYSTEM",
        user_prompt=lambda: user_text,
        as_openai_messages=lambda: [dict(message) for message in wire],
    )
    interpretation = SimpleNamespace(
        raw_text=user_text, normalized_text=user_text, user_text="", understanding_confidence=0.9
    )
    router = MemoryFirstRouter.__new__(MemoryFirstRouter)
    with mock.patch.object(mfr, "normalize_prompt", return_value=internal):
        return MemoryFirstRouter._build_request(
            router,
            task=None,
            classification={"task_class": "chat"},
            interpretation=interpretation,
            context_result=None,
            persona=None,
            output_mode="plain_text",
            task_kind="conversation",
            surface="openclaw",
            source_context={},
        )


def _primary_system(request) -> str:
    return next(str(m["content"]) for m in request.messages if m.get("role") == "system")


# The answer-format block rides the per-turn system message that follows the history, and the leading
# system message stays byte-stable across turns for provider prompt caching: main 00ba5bd5
# (2026-10-05, "keep the leading provider system message stable across turns"), ported in dc937f7
# (core/memory_first_router.py, "Ported from 00ba5bd").
_TURN_PREFIX = "Context for this turn:\n"


def _turn_system(request) -> str:
    return next((str(m["content"]) for m in request.messages if m.get("role") == "system"
                 and str(m["content"]).startswith(_TURN_PREFIX)), "")


def _all_system(request) -> str:
    return "\n".join(str(m["content"]) for m in request.messages if m.get("role") == "system")


# --- recognition -------------------------------------------------------------------------------


@pytest.mark.parametrize("text", EVENT_TIME_ASKS)
def test_a_question_about_when_a_past_event_happened_is_an_event_time_ask(text: str) -> None:
    assert asks_event_time(READER_PREFIX + text) is True, text


@pytest.mark.parametrize("text", NOT_EVENT_TIME_ASKS)
def test_other_questions_are_not_event_time_asks(text: str) -> None:
    assert asks_event_time(READER_PREFIX + text) is False, text


def test_empty_and_malformed_text_is_not_an_event_time_ask() -> None:
    assert asks_event_time("") is False
    assert asks_event_time(None) is False  # type: ignore[arg-type]
    assert asks_event_time("when when when ???") is False


# --- policy and instruction --------------------------------------------------------------------


@pytest.mark.parametrize("text", EVENT_TIME_ASKS)
def test_an_event_time_memory_question_sets_the_relative_time_rule(text: str) -> None:
    policy = _policy(READER_PREFIX + text)
    assert policy["relative_event_time"] is True, text
    instruction = guard.relative_event_time_instruction(policy)
    for marker in RULE_MARKERS:
        assert marker in instruction, (marker, text)


@pytest.mark.parametrize("text", NOT_EVENT_TIME_ASKS)
def test_a_question_that_asks_no_event_time_carries_no_rule(text: str) -> None:
    policy = _policy(READER_PREFIX + text)
    assert policy["relative_event_time"] is False, text
    assert guard.relative_event_time_instruction(policy) == ""


def test_without_memory_records_the_question_is_not_a_memory_question() -> None:
    policy = _policy(READER_PREFIX + EVENT_TIME_ASKS[0], records=False)
    assert policy["relative_event_time"] is False
    assert guard.relative_event_time_instruction(policy) == ""
    # The default keeps every existing caller unchanged.
    default = guard.ordinary_chat_output_policy(
        prompt_profile="chat_minimal", output_mode="plain_text", user_text=EVENT_TIME_ASKS[0]
    )
    assert default["relative_event_time"] is False


def test_a_non_chat_output_mode_takes_no_rule() -> None:
    policy = _policy(READER_PREFIX + EVENT_TIME_ASKS[0], output_mode="json_object")
    assert policy["relative_event_time"] is False


def test_the_rule_is_general_wording_with_synthetic_examples() -> None:
    instruction = guard.relative_event_time_instruction({"relative_event_time": True})
    for relative in ("yesterday", "last night", "last weekend", "last week", "two weeks ago",
                     "a few days ago", "next month", "recently"):
        assert relative in instruction, relative
    for anchored in ("the weekend before", "the day before", "the week before"):
        assert anchored in instruction, anchored
    assert len(instruction) < 800


# --- the reader request ------------------------------------------------------------------------


def test_the_reader_request_states_the_rule_in_the_answer_format_block() -> None:
    turn = READER_PREFIX + "When did Mira repaint her kayak?" + LICENSE
    request = _reader_request(turn)
    assert _primary_system(request) == "BASE SYSTEM"  # stable across turns
    turn_block = _turn_system(request)
    assert turn_block.startswith(_TURN_PREFIX + "Answer format for this request:")
    for marker in RULE_MARKERS:
        assert marker in turn_block, marker
    # The short-answer contract the same turn asks for is still there, and still leads.
    assert turn_block.index("give the answer first") < turn_block.index(RULE_MARKERS[0])
    assert request.metadata["ordinary_chat_output_policy"]["relative_event_time"] is True
    # Evidence and the user's turn are untouched.
    assert request.messages[-1]["content"] == turn


def test_a_time_ask_without_the_short_contract_still_gets_the_block() -> None:
    request = _reader_request("When did Mira repaint her kayak?")
    assert _primary_system(request) == "BASE SYSTEM"  # stable across turns
    turn_block = _turn_system(request)
    assert turn_block.startswith(_TURN_PREFIX + "Answer format for this request:")
    assert RULE_MARKERS[0] in turn_block
    assert "give the answer first" not in _all_system(request)


@pytest.mark.parametrize("text", [READER_PREFIX + "What did Mira repaint?", "Why did Oren pick the north pier?"])
def test_a_reader_turn_that_asks_no_event_time_carries_no_rule(text: str) -> None:
    request = _reader_request(text)
    assert RULE_MARKERS[0] not in _all_system(request)
    assert request.metadata["ordinary_chat_output_policy"]["relative_event_time"] is False


def test_a_time_ask_with_no_retrieved_records_carries_no_rule() -> None:
    request = _reader_request("When did Mira repaint her kayak?", capsule=False)
    assert RULE_MARKERS[0] not in _all_system(request)
    assert request.metadata["ordinary_chat_output_policy"]["relative_event_time"] is False


def test_the_one_rewrite_repeats_the_rule() -> None:
    asked = guard.ordinary_chat_retry_instruction(_policy(READER_PREFIX + "When was Oren's first regatta?"))
    plain = guard.ordinary_chat_retry_instruction(_policy(READER_PREFIX + "Where was Oren's first regatta?"))
    assert RULE_MARKERS[0] in asked
    assert RULE_MARKERS[0] not in plain


# --- the past-time guard ships the anchored form -----------------------------------------------


EVIDENCE = [
    "<retrieved_context>\n"
    "- user said (stated 2021-06-13): Session date: 4:45 pm on 13 June, 2021\n"
    "Orin: Last weekend I went to see the Lanterns play live at the arena. Their show was incredible.\n"
    "- user said (stated 2021-09-12): Session date: 9:10 am on 12 September, 2021\n"
    "Pella: I bought a second sailboat last week!\n"
    "- user said (stated 2021-05-04): Session date: 8:00 pm on 4 May, 2021\n"
    "Ilse: We had dinner and drinks with the crew yesterday.\n"
    "- user said (stated 2021-03-14): Session date: 1:00 pm on 14 March, 2021\n"
    "Pella: I am planning a trip to Lisbon soon.\n"
    "- user said (stated 2021-04-18): Session date: 1:00 pm on 18 April, 2021\n"
    "Pella: Exploring Lisbon now, the food is amazing.\n"
    "</retrieved_context>"
]
ORIN = "When did Orin see the Lanterns play live?"
PELLA_BOAT = "When did Pella buy her second sailboat?"
ILSE = "When did Ilse have dinner and drinks with the crew?"
PELLA_TRIP = "When did Pella first travel to Lisbon?"


def _guard(answer: str, question: str) -> tuple[str, dict]:
    receipt: dict = {}
    out = replace_unsupported_past_time_claims(
        answer, question=question, evidence_texts=EVIDENCE, decision_receipt=receipt
    )
    return out, receipt


@pytest.mark.parametrize(
    ("question", "answer"),
    [
        (ORIN, "The weekend before 13 June 2021."),
        (ORIN, "The weekend before June 13, 2021."),
        (PELLA_BOAT, "The week before 12 September 2021."),
        (ILSE, "The day before 4 May 2021."),
        (ILSE, "The night before 4 May 2021."),
        (PELLA_TRIP, "Between 14 March and 18 April 2021."),
    ],
)
def test_an_anchored_answer_ships_under_the_approximate_date_request(question: str, answer: str) -> None:
    out, receipt = _guard(answer, question + LICENSE)
    assert out == answer, receipt.get("unsupported_values")
    assert receipt.get("unsupported_values") == []


@pytest.mark.parametrize(
    "answer",
    [
        "The weekend before 3 January 2015.",
        "The weekend before 20 November 2021.",
        "The day before 9 February 2019.",
    ],
)
def test_a_fabricated_anchor_far_from_every_record_is_withdrawn_under_the_request(answer: str) -> None:
    out, receipt = _guard(answer, ORIN + LICENSE)
    assert out != answer
    assert receipt["unsupported_values"]
    assert "2015" not in out and "2019" not in out and "November" not in out


@pytest.mark.parametrize(
    ("question", "answer"),
    [
        (ORIN, "Orin saw the Lanterns live the weekend before 13 June 2021."),
        (ORIN, "Orin saw the Lanterns live the week before 13 June 2021."),
        (PELLA_BOAT, "Pella bought her second sailboat the week before 12 September 2021."),
        (ILSE, "Ilse had dinner and drinks with the crew the day before 4 May 2021."),
    ],
)
def test_an_anchored_clause_that_names_the_event_ships_without_the_request(question: str, answer: str) -> None:
    out, receipt = _guard(answer, question)
    assert out == answer, receipt.get("unsupported_values")
    rules = {
        entry.get("rule")
        for check in receipt["checks"]
        for entry in check["statement_time_derivation"]
    }
    assert "relative_anchor" in rules


@pytest.mark.parametrize(
    ("question", "answer"),
    [
        # The record's date given as the event date: the record says the event was last weekend.
        (ORIN, "Orin saw the Lanterns live on 13 June 2021."),
        # A fabricated anchor date.
        (ORIN, "Orin saw the Lanterns live the weekend before 3 January 2015."),
        # The anchor is one day off the record's date.
        (ORIN, "Orin saw the Lanterns live the weekend before 14 June 2021."),
        # The wrong direction: the record's relative time is in the past.
        (ORIN, "Orin saw the Lanterns live the weekend after 13 June 2021."),
        # A window that misses the record's own: "last week" is not the day before.
        (PELLA_BOAT, "Pella bought her second sailboat the day before 12 September 2021."),
        (ORIN, "Orin saw the Lanterns live two months before 13 June 2021."),
        # Another subject's record date cannot anchor this event.
        (PELLA_BOAT, "Pella bought her second sailboat the weekend before 13 June 2021."),
    ],
)
def test_an_anchor_the_records_do_not_bear_out_is_withdrawn_without_the_request(question: str, answer: str) -> None:
    out, receipt = _guard(answer, question)
    assert out != answer
    assert receipt["unsupported_values"]
