"""A "today is <date>" statement later in a sentence is the bound clock, not an event date.

Measured 2026-10-06 on the memory benchmark (archived reply, no provider call here): "About 4 weeks
ago - you attended it on 1 April 2023, and today is 1 May 2023." was withdrawn because the trailing
clock statement was read as a second event date. A leading "Today is ..." was already the clock. The
date must equal the bound clock day; a different date, or no clock, is still an unsupported claim.
Names and dates are authored for this contract.
"""
from datetime import date

import pytest

from core.model_output_guard import ReferenceClock, replace_unsupported_past_time_claims, stated_past_time_claims

WORKSHOP = ["- user said (stated 2024-04-01): I attended the bird ringing workshop at the Oxbow reserve today."]
QUESTION = "How many weeks ago did I attend the bird ringing workshop at the Oxbow reserve?"
CLOCK = ReferenceClock(date(2024, 4, 29), "request-hash", "clock-hash", "turn")


@pytest.mark.parametrize("answer", [
    "About 4 weeks ago, on 1 April 2024, and today is 29 April 2024.",
    "About 4 weeks ago (on 1 April 2024; today is 29 April 2024).",
    "4 weeks ago, you attended it on 1 April 2024 (today is 29 April 2024).",
    "4 weeks ago - you attended it on April 1, 2024, and today's date is April 29, 2024.",
    "about 4 weeks ago, 1 april 2024, so today is 29 april 2024",
])
def test_a_trailing_clock_statement_on_the_clock_day_ships(answer):
    assert replace_unsupported_past_time_claims(answer, question=QUESTION, evidence_texts=WORKSHOP,
                                                reference_clock=CLOCK) == answer


def test_a_trailing_clock_statement_on_another_day_is_withdrawn():
    answer = "About 4 weeks ago, on 1 April 2024, and today is 30 April 2024."
    assert stated_past_time_claims(answer, question=QUESTION, evidence_texts=WORKSHOP, reference_clock=CLOCK)


def test_without_a_bound_clock_the_trailing_date_is_an_unsupported_claim():
    answer = "About 4 weeks ago, on 1 April 2024, and today is 29 April 2024."
    assert stated_past_time_claims(answer, question=QUESTION, evidence_texts=WORKSHOP)


def test_an_event_dated_to_the_clock_day_is_still_withdrawn():
    answer = "You attended the bird ringing workshop on 29 April 2024."
    assert stated_past_time_claims(answer, question="When did I attend the bird ringing workshop?",
                                   evidence_texts=WORKSHOP, reference_clock=CLOCK)


def test_a_yearless_statement_of_the_clock_day_is_the_clock():
    evidence = ["- user said (stated 2024-04-26): I got back from the bird ringing course at the Oxbow reserve today."]
    question = "How many days ago did I get back from the bird ringing course?"
    answer = "3 days ago (you got back on 26 April 2024; today is 29 April)."
    assert replace_unsupported_past_time_claims(answer, question=question, evidence_texts=evidence,
                                                reference_clock=CLOCK) == answer
    wrong = "3 days ago (you got back on 26 April 2024; today is 28 April)."
    assert stated_past_time_claims(wrong, question=question, evidence_texts=evidence, reference_clock=CLOCK)
