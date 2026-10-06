"""Under the request license, a month-year claim is checked as its month in every written form.

Measured on HEAD 058cddbf: the evidence-span rule checked "Around September 2023" as a month (one
calendar month from a record's statement time) but read other spellings of the same claim as a
bare year, so a month far outside every record's span shipped whenever its year matched a record:

* an abbreviated month with a period ("Dec. 2023", "Sept. 2023", "Jan. 2023"): the answer
  splitter ended the sentence at the abbreviation's period and "2023" was checked on its own;
* a numeric month-year ("2023-12", "2023/09", "12/2023", "9/2023", "09/2023"): never read as a
  month claim at all.

Contract (core.model_output_guard._calendar_claims and _answer_sentences): each of these forms is a
month claim at month granularity, so the evidence-span month rule applies to it; a period after an
abbreviated month followed by a day or year does not end the claim unit. Names, events and dates
are synthetic.
"""

from __future__ import annotations

import pytest

from core.model_output_guard import (
    _answer_sentences,
    _calendar_claims,
    replace_unsupported_past_time_claims,
    stated_past_time_claims,
)


def guard(answer, question, evidence):
    return replace_unsupported_past_time_claims(answer, question=question, evidence_texts=evidence)


def span_entries(answer, question, evidence):
    record: dict = {}
    replace_unsupported_past_time_claims(answer, question=question, evidence_texts=evidence, decision_receipt=record)
    return [entry for check in record["checks"] for entry in check["statement_time_derivation"]
            if entry["rule"] == "request_licensed_evidence_span"]


def line(iso: str, speaker: str, text: str) -> str:
    return f"- user said (stated {iso}): {speaker}: {text}"


LICENSE = " Use the date of the conversation to answer with an approximate date."
Q = "When did Odile take the ferry to Vessel Island?" + LICENSE
# Statement times 2024-02-11 and 2024-05-03; no record names a time for the ferry trip.
E = [
    line("2024-02-11", "Odile", "The crossing was rough but the island was worth it."),
    line("2024-05-03", "Bram", "I finally fixed the porch light."),
]


# --- out of every record's span: withdrawn ----------------------------------------------------------

@pytest.mark.parametrize("answer", [
    "Around Sept. 2024.",          # abbreviation with a period, four months after the latest record
    "Around Sep. 2024.",
    "Around Dec. 2024.",
    "Around Oct. 2023.",           # four months before the earliest record
    "Late Aug. 2024.",
    "around nov. 2024",            # lower case, no final period
    "Odile took it in Jul. 2024.",  # two months from the latest record
    "Around 2024-09.",             # year-first numerals, dash
    "Around 2024/11.",             # year-first numerals, slash
    "Around 9/2024.",              # month-first, one digit
    "Around 09/2024.",             # month-first, two digits
    "Around 12-2023.",             # month-first, dash
    "In 2024-08 or so.",
    "Probably Dec.2024.",          # glued after the period
    "Probably Oct-2024.",
])
def test_a_month_outside_every_records_span_is_withdrawn_in_any_written_form(answer):
    assert stated_past_time_claims(answer, question=Q, evidence_texts=E), answer
    assert guard(answer, Q, E) != answer, answer


# --- in a record's span: shipped, checked as the month ----------------------------------------------

@pytest.mark.parametrize("answer, month_of", [
    ("Around Feb. 2024.", "2024-02-11"),
    ("Around Mar. 2024.", "2024-02-11"),
    ("Late Jan. 2024.", "2024-02-11"),
    ("Around Jun. 2024.", "2024-05-03"),
    ("Around 2024-02.", "2024-02-11"),
    ("Around 2024/05.", "2024-05-03"),
    ("Around 02/2024.", "2024-02-11"),
    ("Around 5/2024.", "2024-05-03"),
    ("Around 06-2024.", "2024-05-03"),
])
def test_a_month_in_a_records_span_ships_and_is_checked_as_a_month(answer, month_of):
    assert guard(answer, Q, E) == answer
    entries = span_entries(answer, Q, E)
    assert [entry["granularity"] for entry in entries] == ["month"], entries
    assert entries[0]["nearest_statement"] == month_of


def test_a_month_claim_in_numerals_is_read_at_month_granularity():
    for text, month, year in [("2023-12", 12, 2023), ("2023/09", 9, 2023), ("12/2023", 12, 2023),
                              ("9/2023", 9, 2023), ("09/2023", 9, 2023), ("Dec. 2023", 12, 2023),
                              ("Sept. 2023", 9, 2023), ("Jan.2023", 1, 2023)]:
        claims = _calendar_claims(f"Around {text}.")
        assert [(c["granularity"], c["month"], c["year"]) for c in claims] == [("month", month, year)], text


def test_a_fuller_numeric_date_is_not_read_as_a_month_claim():
    # A full ISO day stays a day claim; a slash day or an impossible month is not a month claim.
    assert [c["granularity"] for c in _calendar_claims("On 2023-12-05.")] == ["day"]
    assert [c["granularity"] for c in _calendar_claims("On 12/05/2023.")] == ["year"]
    assert [c["granularity"] for c in _calendar_claims("On 2023/09/15.")] == ["year"]
    assert [c["granularity"] for c in _calendar_claims("The 2022-23 season.")] == ["year"]
    assert [c["granularity"] for c in _calendar_claims("Version 3.12/2023 notes")] == ["year"]


def test_a_numeric_month_far_from_the_record_withdraws_even_in_the_records_year():
    # The record's own year supports the year; the month is three months off and must not ride on it.
    evidence = [line("2023-06-20", "Ines", "My sister started a new job.")]
    question = "When did Halvard visit the clock museum?" + LICENSE
    for answer in ("Around 2023-09.", "Around 9/2023.", "Around Sept. 2023.", "Around 2023/12.", "Around Dec. 2023."):
        assert guard(answer, question, evidence) != answer, answer
    for answer in ("Around 2023-06.", "Around 7/2023.", "Around Jun. 2023.", "Around Jul. 2023."):
        assert guard(answer, question, evidence) == answer, answer


# --- the claim unit -----------------------------------------------------------------------------------

def test_a_month_abbreviation_period_before_a_number_does_not_end_the_claim_unit():
    assert [unit for unit, _ in _answer_sentences("Around Dec. 2023. Bram said so.")] == ["Around Dec. 2023.", "Bram said so."]
    assert [unit for unit, _ in _answer_sentences("On Sept. 14 the ferry ran late.")] == ["On Sept. 14 the ferry ran late."]


def test_a_month_abbreviation_period_before_a_word_still_ends_the_sentence():
    assert [unit for unit, _ in _answer_sentences("It happened in Jan. Bram said so.")] == ["It happened in Jan.", "Bram said so."]
    assert [unit for unit, _ in _answer_sentences("Mid-Feb. 2024 is my guess. 19 people came.")] == [
        "Mid-Feb. 2024 is my guess.", "19 people came."]


# --- outside the license nothing about these forms changes ------------------------------------------

def test_without_the_license_an_abbreviated_month_is_read_against_its_supported_year_as_before():
    evidence = [line("2023-12-20", "Odile", "I took the ferry to Vessel Island in December 2023.")]
    question = "When did Odile take the ferry to Vessel Island?"
    assert guard("Odile took it in Dec. 2023.", question, evidence) == "Odile took it in Dec. 2023."
    assert guard("Odile took it in Dec. 2021.", question, evidence) != "Odile took it in Dec. 2021."


def test_without_the_license_an_in_span_numeric_month_bound_to_nothing_is_withdrawn():
    question = "When did Odile take the ferry to Vessel Island?"
    assert guard("Around 2024-02.", question, E) != "Around 2024-02."
    assert guard("Around Feb. 2024.", question, E) != "Around Feb. 2024."
