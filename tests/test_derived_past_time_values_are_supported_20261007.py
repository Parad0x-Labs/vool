"""Synthetic arithmetic retains operand provenance and rejects invented times."""
from datetime import date

import pytest

from core.model_output_guard import ReferenceClock, replace_unsupported_past_time_claims, unverified_past_time_notice

WEEKS_Q = "How many weeks ago did I get back from Lisbon?"
WEEKS_RECORDS = ["- user said (stated 2025-05-04): I got back from Lisbon on 3 May 2025."]
CLOCK = ReferenceClock(date(2025, 7, 12), "synthetic-request", "synthetic-clock", "synthetic-turn")
GOAL_Q = "By how many minutes did I beat my goal?"
GOAL_RECORDS = ["- user said (stated 2025-06-06): My goal was 47:00.",
                "- user said (stated 2025-06-06): I finished in 41:00."]


def guarded(answer, question, records, clock=CLOCK):
    receipt = {}
    content = replace_unsupported_past_time_claims(answer, question=question, evidence_texts=records,
                                                 reference_clock=clock, decision_receipt=receipt)
    return content, receipt


def test_dated_return_supports_ten_weeks_ago():
    answer = "10 weeks ago."
    content, receipt = guarded(answer, WEEKS_Q, WEEKS_RECORDS)
    assert content == answer
    check = receipt["checks"][0]
    assert check["supporting_source_units"]
    assert check["calendar_derivation"]["reason"] == "derived"
    assert check["calendar_derivation"]["rule"] == "interval_from_dated_record_to_reference_clock"


def test_recorded_goal_and_finish_support_their_difference():
    answer = "6 minutes (47:00 goal → 41:00 finish)."
    content, receipt = guarded(answer, GOAL_Q, GOAL_RECORDS)
    assert content == answer
    assert receipt["checks"][0]["supporting_source_units"]
    assert receipt["checks"][0]["record_time_derivation"]["rule"] == "difference_of_recorded_durations"


@pytest.mark.parametrize("answer,question,records,clock", [
    ("8 weeks ago.", WEEKS_Q, WEEKS_RECORDS, CLOCK),
    ("You got back from Lisbon on 5 May 2025.", WEEKS_Q, WEEKS_RECORDS, CLOCK),
    ("10 weeks ago.", WEEKS_Q, WEEKS_RECORDS, None),
    ("10 weeks ago.", WEEKS_Q, [WEEKS_RECORDS[0].replace("Lisbon", "Oslo")], CLOCK),
    ("10 weeks ago.", WEEKS_Q, [WEEKS_RECORDS[0].replace("I got", "Nora got")], CLOCK),
    ("10 weeks ago.", WEEKS_Q, [WEEKS_RECORDS[0].replace("3 May", "13 July")], CLOCK),
    ("7 minutes (47:00 goal → 41:00 finish).", GOAL_Q, GOAL_RECORDS, CLOCK),
    ("6 minutes.", GOAL_Q, GOAL_RECORDS[:1], CLOCK),
    ("6 minutes.", GOAL_Q, [GOAL_RECORDS[0], GOAL_RECORDS[1].replace("I finished", "Nora finished")], CLOCK),
], ids=["wrong-weeks", "invented-date", "missing-clock", "wrong-city", "wrong-person", "future-event",
        "wrong-difference", "missing-finish", "other-person-finish"])
def test_wrong_or_unbound_arithmetic_is_withdrawn(answer, question, records, clock):
    content, receipt = guarded(answer, question, records, clock)
    assert content == unverified_past_time_notice(question)
    assert receipt["unsupported_values"]


def test_bare_difference_uses_both_recorded_operands():
    assert guarded("6 minutes.", GOAL_Q, GOAL_RECORDS)[0] == "6 minutes."


def test_minute_second_notation_does_not_license_an_hour_reading():
    assert guarded("2820 minutes.", GOAL_Q, GOAL_RECORDS)[0] == unverified_past_time_notice(GOAL_Q)


def test_duration_derivation_preserves_the_recorded_goal_date():
    records = [GOAL_RECORDS[0].replace("47:00.", "47:00 on 5 June 2025."), GOAL_RECORDS[1]]
    answer = "6 minutes (47:00 goal → 41:00 finish; goal set on 5 June 2025)."
    assert guarded(answer, GOAL_Q, records)[0] == answer


@pytest.mark.parametrize("answer", [
    "47 minutes.", "41 minutes.",
    "47 minutes (47:00 goal → 41:00 finish).",
    "41 minutes (47:00 goal → 41:00 finish).",
])
def test_an_operand_is_not_the_requested_difference(answer):
    assert guarded(answer, GOAL_Q, GOAL_RECORDS)[0] == unverified_past_time_notice(GOAL_Q)


def test_explicit_uncertainty_is_not_a_fabricated_difference():
    answer = "I don't know whether it was 47 minutes."
    assert guarded(answer, GOAL_Q, GOAL_RECORDS)[0] == answer


def test_labelled_operands_can_be_stated_in_minutes_beside_the_difference():
    answer = "6 minutes (goal: 47 minutes; finish: 41 minutes)."
    assert guarded(answer, GOAL_Q, GOAL_RECORDS)[0] == answer
