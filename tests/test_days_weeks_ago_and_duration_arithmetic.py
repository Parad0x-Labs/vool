"""A days/weeks-ago count and a duration computed from cited durations ship when the arithmetic holds.

Measured 2026-10-06 on the memory benchmark (archived reader replies, no provider call here): the
past-time guard withdrew "17 days ago (on 12 February 2023)" to "How many days ago did I watch the
Super Bowl?" because only months and years ago were ever derived from the bound clock, and withdrew
"Four weeks (six weeks of lessons as of May 25, amp bought two weeks prior)" and "About 5 months
ago: booked 3 months in advance for a trip 2 months ago" because a value computed from durations
the answer cites was never derived. Every operand must still pass the unchanged request-support
law; a wrong count, a missing clock, an ambiguous, negated, future or other person's event, an
unsupported operand and a bare computed value with no operands are all still withdrawn. Names,
places and dates are authored for this contract and appear in no benchmark.
"""
from datetime import date

import pytest

from core.model_output_guard import ReferenceClock, replace_unsupported_past_time_claims, stated_past_time_claims

READER_PREFIX = (
    "Answer using the imported prior conversations. You may derive only answers supported by those records. "
    "If the records do not support an answer, say you do not know. Give a concise final answer.\n"
)
MOTOR = ["- user said (stated 2024-05-03): I serviced the outboard motor on the dinghy today."]


def clock(day: date) -> ReferenceClock:
    return ReferenceClock(day, "request-hash", "clock-hash", "turn")


def kept(answer, question, evidence, reference=None) -> bool:
    return replace_unsupported_past_time_claims(answer, question=question, evidence_texts=evidence,
                                                reference_clock=reference) == answer


# ---------------------------------------------------------------- days and weeks ago from the clock

DAYS_AGO_QUESTIONS = [
    "How many days ago did I service the outboard motor?",
    READER_PREFIX + "How many days ago did I service the outboard motor?",
    "So, how many days ago did I service the outboard motor on the dinghy?",
    "how many days ago did i service the outboard motor",
    "Quick check: how many days ago did I service the outboard motor?",
]


@pytest.mark.parametrize("question", DAYS_AGO_QUESTIONS)
def test_a_days_ago_count_from_the_bound_clock_ships(question):
    reference = clock(date(2024, 5, 20))
    assert kept("17 days ago (you serviced it on May 3, 2024).", question, MOTOR, reference)
    assert kept("17 days ago.", question, MOTOR, reference)


@pytest.mark.parametrize("answer", ["3 weeks ago.", "2 weeks ago."])
def test_a_weeks_ago_count_rounds_down_or_to_the_nearest_week(answer):
    # 20 days: "2 weeks" (whole weeks) and "3 weeks" (nearest) are both how the count is spoken.
    assert kept(answer, "How many weeks ago did I service the outboard motor?", MOTOR, clock(date(2024, 5, 23)))


def test_an_exact_weeks_ago_count_ships():
    assert kept("3 weeks ago.", "How many weeks ago did I service the outboard motor?", MOTOR, clock(date(2024, 5, 24)))


@pytest.mark.parametrize("answer,question", [
    ("16 days ago.", "How many days ago did I service the outboard motor?"),
    ("18 days ago.", "How many days ago did I service the outboard motor?"),
    ("5 weeks ago.", "How many weeks ago did I service the outboard motor?"),
])
def test_a_wrong_days_or_weeks_ago_count_is_withdrawn(answer, question):
    assert stated_past_time_claims(answer, question=question, evidence_texts=MOTOR, reference_clock=clock(date(2024, 5, 20)))


def test_without_a_bound_clock_a_days_ago_count_is_withdrawn():
    assert stated_past_time_claims("17 days ago.", question="How many days ago did I service the outboard motor?",
                                   evidence_texts=MOTOR)


@pytest.mark.parametrize("evidence", [
    # two candidate days for the event
    ["- user said (stated 2024-05-03): I serviced the outboard motor on the dinghy today.",
     "- user said (stated 2024-05-08): I serviced the outboard motor on the dinghy today."],
    # another person's event
    ["- user said (stated 2024-05-03): Nadia serviced the outboard motor on her dinghy today."],
    # negated
    ["- user said (stated 2024-05-03): I did not service the outboard motor on the dinghy today."],
    # after the clock
    ["- user said (stated 2024-06-03): I serviced the outboard motor on the dinghy today."],
])
def test_an_ambiguous_other_negated_or_future_event_authorizes_no_days_ago_count(evidence):
    assert stated_past_time_claims("17 days ago.", question="How many days ago did I service the outboard motor?",
                                   evidence_texts=evidence, reference_clock=clock(date(2024, 5, 20)))


# ---------------------------------------------------------- a duration computed from cited durations

LESSONS = [
    "- user said (stated 2025-05-25): I've been taking cello lessons for six weeks now.",
    "- user said (stated 2025-05-25): I bought the new cello bow two weeks ago, right after my fourth lesson.",
]
LESSONS_Q = "How long had I been taking cello lessons when I bought the new cello bow?"


def test_the_difference_of_two_cited_supported_durations_ships():
    answer = "Four weeks (six weeks of cello lessons as of May 25, the bow bought two weeks before that)."
    assert kept(answer, LESSONS_Q, LESSONS)
    assert kept(answer, READER_PREFIX + LESSONS_Q, LESSONS)


def test_a_wrong_difference_is_withdrawn():
    answer = "Three weeks (six weeks of cello lessons as of May 25, the bow bought two weeks before that)."
    assert "Three weeks" not in replace_unsupported_past_time_claims(answer, question=LESSONS_Q, evidence_texts=LESSONS)


def test_an_operand_no_record_states_licenses_nothing():
    evidence = ["- user said (stated 2025-05-25): I've been taking cello lessons for five weeks now.", LESSONS[1]]
    answer = "Four weeks (six weeks of cello lessons as of May 25, the bow bought two weeks before that)."
    assert "Four weeks" not in replace_unsupported_past_time_claims(answer, question=LESSONS_Q, evidence_texts=evidence)


def test_another_persons_duration_is_no_operand():
    evidence = ["- user said (stated 2025-05-25): My brother Lars has been taking cello lessons for six weeks now.",
                LESSONS[1]]
    answer = "Four weeks (six weeks of cello lessons as of May 25, the bow bought two weeks before that)."
    assert "Four weeks" not in replace_unsupported_past_time_claims(answer, question=LESSONS_Q, evidence_texts=evidence)


def test_a_bare_computed_value_without_its_operands_is_still_withdrawn():
    assert "Four weeks" not in replace_unsupported_past_time_claims("Four weeks.", question=LESSONS_Q,
                                                                    evidence_texts=LESSONS)


DRIVES = [
    "- user said (stated 2025-06-02): The drive to Savannah took 6 hours on the first day of the road trip.",
    "- user said (stated 2025-06-05): The drive to Asheville took 4 hours, much shorter than Savannah.",
    "- user said (stated 2025-06-09): The drive to Nashville took 5 hours with one stop for lunch.",
]
DRIVES_Q = "How many hours in total did I spend driving to Savannah, Asheville and Nashville on the road trip?"


def test_the_total_of_three_cited_supported_durations_ships():
    answer = "15 hours: the drive to Savannah took 6 hours, Asheville 4 hours and Nashville 5 hours."
    assert kept(answer, DRIVES_Q, DRIVES)


def test_a_wrong_total_is_withdrawn():
    answer = "16 hours: the drive to Savannah took 6 hours, Asheville 4 hours and Nashville 5 hours."
    assert "16 hours" not in replace_unsupported_past_time_claims(answer, question=DRIVES_Q, evidence_texts=DRIVES)


JOG = ["- user said (stated 2025-05-20): I went jogging for 30 minutes along the canal this morning."]


def test_a_duration_in_a_neighbouring_unit_ships():
    answer = "About 0.5 hours: you went jogging for 30 minutes along the canal."
    assert kept(answer, "How many hours did I spend jogging last week?", JOG)


def test_a_wrong_unit_conversion_is_withdrawn():
    answer = "About 1.5 hours: you went jogging for 30 minutes along the canal."
    assert "1.5 hours" not in replace_unsupported_past_time_claims(answer, question="How many hours did I spend jogging last week?",
                                                                   evidence_texts=JOG)


TRIP = [
    "- user said (stated 2025-07-02): We spent 5 days at Lake Bled before driving on.",
    "- user said (stated 2025-07-09): Then 3 days in the Julian Alps, mostly hiking.",
]
TRIP_Q = "How many days did I spend at Lake Bled and in the Julian Alps in total?"


@pytest.mark.parametrize("answer", [
    "8 days: 5 at Lake Bled and 3 in the Julian Alps.",
    "8 days (5 at Lake Bled, 3 in the Julian Alps).",
    "8 days - 5 at the lake and 3 in the Alps.",
])
def test_a_total_with_bare_number_addends_ships(answer):
    assert kept(answer, TRIP_Q, TRIP)


@pytest.mark.parametrize("answer", [
    "9 days: 5 at Lake Bled and 3 in the Julian Alps.",
    "8 days: 6 at Lake Bled and 2 in the Julian Alps.",
])
def test_a_wrong_total_or_an_unsupported_bare_addend_is_withdrawn(answer):
    assert stated_past_time_claims(answer, question=TRIP_Q, evidence_texts=TRIP)
