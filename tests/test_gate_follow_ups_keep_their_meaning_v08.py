"""Follow-ups from the v0.8 real-app gate (iMac, qwen3:8b, Nov 24), read at their root and asked in fresh words.

* A sum asked as a supposition ("and if i divide it by 2") works on the previous RESULT, and a one-word operation on
  it ("half of it", "double that") is that result, never "the same task for a new object".
* "the question right before this one" is the person's own latest message, read from the chat, never the message
  being written.
* A recap of THIS chat ("list all the things we covered here") is read from the chat's own messages.
* A clause that only says HOW the ask is to be done ("use the calculator") is not a second ask, and the runtime's
  own "Could not be answered" rows are never inspected as if they were a cut-off answer.

Every wording below is new: none is the gate's. Contributor: sls_0x.
"""
from __future__ import annotations

import pytest

import core.agent_runtime.previous_reply_count as result_lane
import core.agent_runtime.same_chat_recall as recall
from core.agent_runtime.filler_words import is_method_clause, without_method_clauses
from core.agent_runtime.task_continuation import compose_continuation
from core.context_history_authority import asks_for_chat_recap, is_same_chat_history_recall
from core.finalization import RSS_UNAVAILABLE_HEADER, without_closure_section
from core.incomplete_answer import inspect_answer_completeness
from core.task_router import evaluate_direct_math_request

# --- arithmetic on the previous result -----------------------------------------------------------------------------

@pytest.mark.parametrize(
    "reply,question,answer",
    [
        ("6031 * 17 = 102527.", "um so if we take away 527 from it?", "102527 − 527 = 102000"),
        ("418 + 902 = 1320.", "what would it be if i multiply that by 4", "1320 × 4 = 5280"),
        ("75 * 12 = 900.", "suppose you divide the result by 6 thx", "900 ÷ 6 = 150"),
        ("1250 - 50 = 1200.", "ok and halve that please", "1200 ÷ 2 = 600"),
        ("33 * 3 = 99.", "and twice it?", "99 × 2 = 198"),
        ("19 + 23 = 42.", "what happens if i triple the answer", "42 × 3 = 126"),
    ],
)
def test_a_supposed_operation_works_on_the_previous_result(monkeypatch, reply, question, answer):
    monkeypatch.setattr(result_lane, "_previous_reply", lambda _sid: reply)
    assert result_lane.previous_result_arithmetic(question, session_id="s") == answer


@pytest.mark.parametrize(
    "question",
    [
        "if i divide it by 2 will it fit in the van",      # a second subject: not only the sum
        "is that more than 50 times 3",                     # a comparison, not an operation
        "double it and send it to my sister",               # a second ask beside the sum
        "what if it was not divided by 4",                  # negation changes the sum
    ],
)
def test_a_message_that_asks_more_than_the_sum_is_left_alone(monkeypatch, question):
    monkeypatch.setattr(result_lane, "_previous_reply", lambda _sid: "75 * 12 = 900.")
    assert result_lane.previous_result_arithmetic(question, session_id="s") is None


@pytest.mark.parametrize("follow_up", ["and a third of it?", "double that", "ok half the result", "then those two"])
def test_an_object_that_points_back_is_not_a_new_object_for_the_last_task(follow_up):
    assert compose_continuation(follow_up, "work out 64 times 11 for me") == ""


def test_a_new_object_still_carries_the_last_task():
    carried = compose_continuation("and lantern?", "how do you say window in polish")
    assert carried.startswith("Same as my previous request") and carried.endswith("lantern")


# --- the question before this one, and a recap of this chat ------------------------------------------------------

@pytest.mark.parametrize(
    "ask",
    [
        "remind me, which question came right before this one?",
        "sorry what was the message just before this",
        "what did i ask immediately before this one",
        "ok so what was the previous question",
    ],
)
def test_the_question_before_this_one_is_the_last_one_asked(monkeypatch, ask):
    rows = [("whats 7 squared", "7 squared = 49."), ("find a pizza place in Riga", "Here are three...")]
    monkeypatch.setattr(recall, "_persisted_chat_rows", lambda _sid: (list(rows), True))
    result = recall._question_order_recall(
        ask, ordering_kind=recall._ordering_recall_kind(ask), session_id="openclaw:" + "a" * 20, source_context={}
    )
    assert result is not None and result.found
    assert "find a pizza place in Riga" in result.response
    assert ask not in result.response


@pytest.mark.parametrize(
    "ask",
    [
        "could you go over what we talked about so far?",
        "pls give me an overview of this chat",
        "list everything we did here today",
        "hey sum up this conversation for me",
    ],
)
def test_a_recap_of_this_chat_is_a_question_about_the_chat(ask):
    assert asks_for_chat_recap(ask)
    assert is_same_chat_history_recall(ask)


@pytest.mark.parametrize(
    "ask",
    [
        "summarize the notes we discussed at the meeting",
        "list all pdfs in my documents folder",
        "give me an overview of the french revolution",
        "recap what we covered with the plumber",
    ],
)
def test_other_recaps_are_not_this_chat(ask):
    assert not asks_for_chat_recap(ask)


def test_a_recap_lists_every_earlier_ask_and_its_answer(monkeypatch):
    rows = [
        ("any new mail from the bank?", "No mailbox is connected yet, so I can't read your email."),
        ("ok what's 14 + 9", "14 + 9 = 23."),
        ("tell me a long story about a lighthouse keeper " * 1, "Once upon a time " + "the sea rose and fell " * 20),
    ]
    ask = "can you sum up what we went over here?"
    monkeypatch.setattr(recall, "_persisted_chat_rows", lambda _sid: ([*rows, (ask, "")], True))
    result = recall._chat_recap(ask, session_id="openclaw:" + "b" * 20, source_context={})
    assert result is not None and result.found and result.route_reason == "authoritative_chat_recap"
    for asked, _ in rows:
        assert asked.strip() in result.response
    assert "14 + 9 = 23." in result.response
    assert ask not in result.response
    # A long answer is cut at a word with a marker, so the recap never ends mid-clause.
    assert not inspect_answer_completeness(result.response).incomplete


def test_a_recap_of_an_empty_chat_says_so(monkeypatch):
    monkeypatch.setattr(recall, "_persisted_chat_rows", lambda _sid: ([], True))
    result = recall._chat_recap("recap this chat", session_id="openclaw:" + "c" * 20, source_context={})
    assert result is not None and not result.found and "nothing to recap" in result.response


# --- how an ask is done is not a second ask ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "clause", ["using the calculator app", "with your calc pls", "via web search", "use my notes thanks", "su skaičiuotuvu"]
)
def test_a_method_clause_is_no_ask_of_its_own(clause):
    assert is_method_clause(clause)


@pytest.mark.parametrize(
    "clause", ["use 4 decimal places", "using the calculator to check my bill", "what's 9 times 8", "with"]
)
def test_a_clause_with_its_own_object_or_purpose_is_still_an_ask(clause):
    assert not is_method_clause(clause)


@pytest.mark.parametrize(
    "ask,answer",
    [
        ("how much is 3907 times 41, use the calculator thx", "3907 * 41 = 160187."),
        ("whats 88 + 1290 using ur calculator", "88 + 1290 = 1378."),
    ],
)
def test_a_sum_with_its_method_is_still_just_the_sum(ask, answer):
    assert evaluate_direct_math_request(ask) == answer


def test_without_method_clauses_keeps_the_ask():
    assert without_method_clauses("work out 5 x 6, with the calculator please").strip(" ,") == "work out 5 x 6"


def test_the_runtimes_own_rows_are_not_read_as_a_cut_off_answer():
    body = "The total is 91."
    served = f"{body}\n\n{RSS_UNAVAILABLE_HEADER}\n* the other part — nothing in it was something this step could work on"
    assert without_closure_section(served) == body
    assert inspect_answer_completeness(without_closure_section(served)).incomplete is False
    # The rows themselves still look unfinished to the detector; only the answer is its business.
    assert inspect_answer_completeness(served).incomplete is True
