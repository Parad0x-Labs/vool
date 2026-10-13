"""Synthetic record questions retain their private, dated grounding scope."""
from __future__ import annotations

import pytest

from core.bootstrap_context import seal_request_evidence
from core.execution_requirements import begin_synthesis_freeze, escalate_current_requirement, requirements_for
from core.grounding_lifecycle import record_model_authorship, record_retrieved, reset_for_tests
from core.grounding_publication import _REFUSAL_LEAD, gate_publishable_content

CLOCK_TEXT = "The current date and time is Saturday 12 July 2025, 12:00 (UTC)."
STOCK_Q = "How many tomato plants do I have now?"
STOCK = "- user said (stated 2025-06-18): I have 7 tomato plants now, down from the 10 I planted."
SPEND_Q = "How much did I spend on garden gear this year?"
PURCHASES = [
    "- user said (stated 2025-02-03): I bought a rake for $31 as garden gear.",
    "- user said (stated 2025-04-11): I bought a hose for $46 as garden gear.",
    "- user said (stated 2025-06-20): I bought a spade for $19 as garden gear.",
]


def deliver(question, records, answer, *, early=False, frozen=False):
    reset_for_tests()
    context = {"chat_id": "synthetic-record-session", "current_turn_id": "synthetic-record-turn", "turn_id": "synthetic-record-turn"}
    if early or frozen:
        requirements_for(question, source_context=context)
    if frozen:
        begin_synthesis_freeze(context, question)
    evidence = "\n".join(records)
    messages = [{"role": "system", "content": CLOCK_TEXT},
                {"role": "system", "content": evidence}, {"role": "user", "content": question}]
    seal_request_evidence(context, session_id=context["chat_id"], question=question,
                          turn_id=context["current_turn_id"], messages=messages,
                          evidence_texts=records, capsule_text=evidence,
                          reference_clock={"source": "runtime_clock", "date": "2025-07-12", "text": CLOCK_TEXT})
    requirements = requirements_for(question, source_context=context)
    widened = escalate_current_requirement(context, text=question, source="temporal_lane",
                                          reason_code="temporal_markers")
    begin_synthesis_freeze(context, question)
    record_retrieved(context, outcome="no_sources", source_count=0)
    record_model_authorship(context)
    content, receipt = gate_publishable_content(answer, turn_id=context["current_turn_id"])
    return content, receipt, requirements, widened


def test_dated_present_stock_answer_survives_publication():
    answer = "You have 7 tomato plants."
    content, _, requirement, widened = deliver(STOCK_Q, [STOCK], answer)
    assert content == answer
    assert "7" in content and _REFUSAL_LEAD not in content
    assert not requirement.current_information_required
    assert not widened


def test_dated_purchases_sum_survives_publication():
    answer = "You spent $96 on garden gear this year."
    content, _, _, widened = deliver(SPEND_Q, PURCHASES, answer)
    assert content == answer
    assert _REFUSAL_LEAD not in content
    assert not widened


@pytest.mark.parametrize("question,records,answer", [
    ("What is the price of a tomato plant today?", [], "A tomato plant costs $12 today."),
    (STOCK_Q, [STOCK.replace("2025-06-18", "2025-07-13")], "You have 7 tomato plants."),
    (STOCK_Q, [STOCK.replace("tomato", "pepper")], "You have 7 tomato plants."),
    (STOCK_Q, [STOCK.replace("user said", "assistant said")], "You have 7 tomato plants."),
    (STOCK_Q, ["- user said: I have 7 tomato plants now."], "You have 7 tomato plants."),
    ("How many tomato plants do I have now, and what is their price today?", [STOCK],
     "You have 7 tomato plants and they cost $12 today."),
], ids=["live-price", "record-after-clock", "wrong-subject", "assistant-record", "undated-record", "mixed-live"])
def test_missing_or_ineligible_records_keep_the_current_guard(question, records, answer):
    content, receipt, _, widened = deliver(question, records, answer)
    assert widened
    assert content != answer
    assert receipt["publication"]["state"] in {"refused", "failed"}


def test_records_admitted_after_initial_classification_refine_before_synthesis():
    answer = "You have 7 tomato plants."
    content, _, requirement, widened = deliver(STOCK_Q, [STOCK], answer, early=True)
    assert content == answer
    assert not requirement.current_information_required
    assert not widened


def test_records_arriving_after_synthesis_do_not_change_the_frozen_contract():
    answer = "You have 7 tomato plants."
    content, receipt, requirement, widened = deliver(STOCK_Q, [STOCK], answer, frozen=True)
    assert requirement.current_information_required and widened
    assert content != answer
    assert receipt["publication"]["state"] in {"refused", "failed"}


def test_a_category_noun_is_not_a_subject_a_record_must_repeat():
    # "garden gear" names the collection; the records name each thing in it and never say "gear".
    records = [
        "- user said (stated 2025-02-03): I bought a garden rake for $31.",
        "- user said (stated 2025-04-11): I bought a garden hose for $46.",
    ]
    answer = "You spent $77 on garden gear this year."
    content, _, _, widened = deliver(SPEND_Q, records, answer)
    assert content == answer and not widened


def test_an_ask_that_is_only_a_category_still_widens():
    # Nothing but the category is left to match ("stuff"), so no record can be said to answer it.
    records = ["- user said (stated 2025-02-03): I bought a garden rake for $31."]
    _content, _, _, widened = deliver("How much have I spent on stuff this year?", records, "You spent $31.")
    assert widened
