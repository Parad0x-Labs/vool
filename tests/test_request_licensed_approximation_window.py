"""Under the user's own request for an approximate date, a record's statement time S approximates.

Measured on the paid round-1 replay: on the "When did ...? Use the date of the conversation to answer
with an approximate date." questions the reader's reply was right and the guard withdrew it whenever
the record dated its act with wording outside the relative-time vocabulary ("last night" -> the day
before S; "reported <S>" -> two days before S) or explained the date with words beyond the clause
grammar ("he announced opening the shop", "that's when ...", "that day's conversation with <the
other speaker>"). Four rounds of vocabulary patches had not converged: every unlisted phrase failed,
and one unsupported value withdrew the whole answer.

Rules at the owning support law (core.model_output_guard._statement_time_values), in request-licensed
mode only:

1. Approximation, now the evidence span (owner contract, tests/test_request_licensed_evidence_span.py).
   The round-4 window (a day within a week of a licensed-bound record's statement time S, a year
   equal to S's year or the year before, receipt request_licensed_approximation_window) is replaced:
   a claimed value is supported within the evidence span of ANY record's statement time -- a day
   within 31 days, a month within one calendar month (checked as the month), a year equal to S's
   year or the year before -- with no binding. Receipt rule request_licensed_evidence_span with
   nearest_statement and distance_days. The exact and relative derivations stay as they were.
2. The licensed clause is read at its head words: a contraction ("that's") or possessive ("day's")
   is the word it is built on; the conversation's own speaker labels are people, not another event;
   a clause that names the asked event's object itself may describe it further ("that's when she
   first mentioned her new kayak"), while a clause bound only through the request's verb still may
   not name another object ("visited the opera").

Controls: a value outside every record's evidence span, a negated or absent license and an
unlicensed bare past report stay withdrawn. Another speaker's record, a record of another event and
a clause bound only by a shared verb support a value only inside the span. All sentences are
synthetic.
"""

from __future__ import annotations

import pytest

import core.model_output_guard as guard_module
from core.model_output_guard import replace_unsupported_past_time_claims, stated_past_time_claims

# Read leniently so the file collects against a tree without the span (the falsification run).
SPAN_DAYS = getattr(guard_module, "_LICENSED_EVIDENCE_SPAN_DAYS", 31)
SPAN_RULE = "request_licensed_evidence_span"


def guard(answer, question, evidence):
    return replace_unsupported_past_time_claims(answer, question=question, evidence_texts=evidence)


def receipt_rules(answer, question, evidence):
    record: dict = {}
    replace_unsupported_past_time_claims(answer, question=question, evidence_texts=evidence, decision_receipt=record)
    return [entry for check in record["checks"] for entry in check["statement_time_derivation"]]


def line(iso: str, speaker: str, text: str) -> str:
    return f"- user said (stated {iso}): {speaker}: {text}"


LICENSE = " Use the date of the conversation to answer with an approximate date."

# --- 1. the approximation window -------------------------------------------------------------------

Q_MURAL = "When did Priya finish the courtyard mural?"
E_MURAL = [
    line("2024-06-20", "Priya", "I finished the courtyard mural last night and my arms are still aching."),
    line("2024-06-20", "Ravi", "I would love to see it, send a photo!"),
]


@pytest.mark.parametrize("wording, answer", [
    ("last night", "19 June 2024 (she said \"last night\" on 20 June 2024)."),
    ("the other day", "Around 17 June 2024."),
    ("last weekend", "Around 18 June 2024 — reported on 20 June 2024."),
    ("a week before", "Roughly 13 June 2024."),
    ("recently", "Around 16 June 2024, give or take a few days."),
    ("over the weekend", "About 15 June 2024."),
])
def test_an_unlisted_relative_phrase_approximates_to_the_statement_time(wording, answer):
    evidence = [line("2024-06-20", "Priya", f"I finished the courtyard mural {wording} and my arms are still aching."),
                E_MURAL[1]]
    assert guard(answer, Q_MURAL + LICENSE, evidence) == answer, wording
    rules = receipt_rules(answer, Q_MURAL + LICENSE, evidence)
    window = [entry for entry in rules if entry["rule"] == SPAN_RULE]
    assert window and all(0 < entry["distance_days"] <= SPAN_DAYS for entry in window)
    assert all(entry["granularity"] == "day" for entry in window)


def test_a_day_after_the_statement_is_inside_the_window_too():
    # Thursday 20 June 2024; "by Saturday" is no listed relative expression.
    evidence = [line("2024-06-20", "Priya", "I will finish the courtyard mural by Saturday, promise!")]
    answer = "Around 22 June 2024."
    assert guard(answer, Q_MURAL + LICENSE, evidence) == answer


def test_a_yearless_day_is_read_in_the_year_that_places_it_near_the_statement():
    evidence = [line("2024-01-02", "Priya", "I finished the courtyard mural the other day, just before the fireworks.")]
    answer = "Around 30 December 2023 — she mentioned it on Jan 2."
    assert guard(answer, Q_MURAL + LICENSE, evidence) == answer


def test_month_granularity_the_month_before_the_statement_is_checked_as_its_month():
    # A month-only mention is a month claim: the calendar month before S is inside the span.
    evidence = [line("2024-01-05", "Priya", "I finished the courtyard mural a few days before the new year.")]
    answer = "Around December 2023."
    assert guard(answer, Q_MURAL + LICENSE, evidence) == answer
    window = [entry for entry in receipt_rules(answer, Q_MURAL + LICENSE, evidence)
              if entry["rule"] == SPAN_RULE]
    assert [entry["granularity"] for entry in window] == ["month"] and window[0]["claimed_value"] == "y2023"
    assert window[0]["distance_days"] == 5
    assert "November 2023" not in guard("Around November 2023.", Q_MURAL + LICENSE, evidence)


def test_year_granularity_the_year_before_the_statement_is_supported():
    evidence = [line("2024-03-03", "Priya", "I finished the courtyard mural a while back, before the winter set in.")]
    assert guard("In 2023.", Q_MURAL + LICENSE, evidence) == "In 2023."
    assert guard("In 2024.", Q_MURAL + LICENSE, evidence) == "In 2024."


def test_two_years_before_the_statement_is_withdrawn():
    evidence = [line("2024-03-03", "Priya", "I finished the courtyard mural a while back, before the winter set in.")]
    assert "2022" not in guard("In 2022.", Q_MURAL + LICENSE, evidence)
    assert "2025" not in guard("In 2025.", Q_MURAL + LICENSE, evidence)


def test_the_receipt_names_the_rule_and_the_distance():
    answer = "19 June 2024 (she said \"last night\" on 20 June 2024)."
    window = [entry for entry in receipt_rules(answer, Q_MURAL + LICENSE, E_MURAL)
              if entry["rule"] == SPAN_RULE]
    assert window == [{"rule": SPAN_RULE, "claimed_value": "m6:19:2024", "granularity": "day",
                       "nearest_statement": "2024-06-20", "distance_days": 1, "values": ["m6:19:2024", "y2024"]}]


def test_the_statement_time_itself_is_the_exact_rule_not_the_window():
    answer = "Around 20 June 2024."
    rules = [entry["rule"] for entry in receipt_rules(answer, Q_MURAL + LICENSE, E_MURAL)]
    assert "request_licensed_statement_time" in rules and SPAN_RULE not in rules


# --- controls: what the window must not do ----------------------------------------------------------

def test_a_day_more_than_the_span_from_every_record_is_withdrawn():
    for answer in ("Around 22 July 2024.", "Around 19 May 2024.", "Around 21 April 2024."):
        assert stated_past_time_claims(answer, question=Q_MURAL + LICENSE, evidence_texts=E_MURAL), answer
        assert "2024" not in guard(answer, Q_MURAL + LICENSE, E_MURAL), answer


def test_a_day_sixty_days_from_every_matching_record_is_withdrawn():
    evidence = [*E_MURAL, line("2024-07-30", "Priya", "The courtyard mural still looks great in the evening light.")]
    assert "19 April" not in guard("Around 19 April 2024.", Q_MURAL + LICENSE, evidence)


def test_a_day_in_the_statements_year_but_outside_the_span_is_not_the_year_rule():
    # Granularity: a dated day is checked as a day; its year alone does not carry it.
    assert "3 March" not in guard("Around 3 March 2024.", Q_MURAL + LICENSE, E_MURAL)


def test_another_speakers_record_supports_only_inside_its_span():
    evidence = [line("2024-06-20", "Ravi", "I finished the courtyard mural last night and my arms are still aching.")]
    assert guard("Around 19 June 2024.", Q_MURAL + LICENSE, evidence) == "Around 19 June 2024."
    assert "19 March" not in guard("Around 19 March 2024.", Q_MURAL + LICENSE, evidence)


def test_a_record_of_another_event_supports_only_inside_its_span():
    evidence = [line("2024-06-20", "Priya", "I finished the tax return last night and my head is still aching.")]
    assert guard("Around 19 June 2024.", Q_MURAL + LICENSE, evidence) == "Around 19 June 2024."
    assert "19 March" not in guard("Around 19 March 2024.", Q_MURAL + LICENSE, evidence)


def test_without_the_license_the_window_does_not_exist():
    for answer in ("Priya finished the courtyard mural on 19 June 2024.", "Around 19 June 2024.", "In 2023."):
        assert stated_past_time_claims(answer, question=Q_MURAL, evidence_texts=E_MURAL), answer
        assert guard(answer, Q_MURAL, E_MURAL) != answer, answer


def test_an_unlicensed_bare_past_report_stays_withdrawn_as_before():
    evidence = [line("2024-06-20", "Priya", "I finished the courtyard mural and my arms are still aching.")]
    assert "20 June" not in guard("Priya finished the courtyard mural on 20 June 2024.", Q_MURAL, evidence)
    assert "19 June" not in guard("Priya finished the courtyard mural on 19 June 2024.", Q_MURAL, evidence)


def test_a_negated_license_licenses_no_window():
    question = Q_MURAL + " Do not use the date of the conversation."
    assert "19 June" not in guard("Around 19 June 2024.", question, E_MURAL)


def test_an_uncertain_or_negated_record_supports_only_inside_its_span():
    for text in ("I might finish the courtyard mural tomorrow.", "I didn't finish the courtyard mural last night."):
        evidence = [line("2024-06-20", "Priya", text)]
        assert guard("Around 19 June 2024.", Q_MURAL + LICENSE, evidence) == "Around 19 June 2024.", text
        assert "19 March" not in guard("Around 19 March 2024.", Q_MURAL + LICENSE, evidence), text


def test_no_record_statement_time_withdraws_under_the_license():
    evidence = [line("2024-06-20", "Ravi", "I would love to see the mural, send a photo!")]
    assert "19 March" not in guard("Around 19 March 2024.", Q_MURAL + LICENSE, evidence)
    unlabelled = ["Ravi: I would love to see the mural, send a photo!"]
    assert "19 June" not in guard("Around 19 June 2024.", Q_MURAL + LICENSE, unlabelled)


# --- 2. the licensed clause read at its head words -------------------------------------------------

Q_KAYAK = "When did Marisol buy her second kayak?"
E_KAYAK = [
    line("2024-05-12", "Marisol", "I bought my second kayak at the shop by the harbour, what a day!"),
    line("2024-05-12", "Ines", "Congratulations on the new kayak, that is incredible!"),
]


def test_a_contraction_and_a_possessive_are_their_head_words():
    answer = "Around 12 May 2024 — that's when Marisol mentioned it in that day's chat."
    assert guard(answer, Q_KAYAK + LICENSE, E_KAYAK) == answer


def test_the_other_speakers_name_is_a_person_not_another_event():
    answer = "12 May 2024 — Marisol mentioned it in that day's conversation with Ines."
    assert guard(answer, Q_KAYAK + LICENSE, E_KAYAK) == answer


def test_a_name_that_is_nobody_in_the_conversation_withdraws_outside_the_span():
    answer = "12 May 2024 — Marisol mentioned it in that day's conversation with Ulrich."
    assert guard(answer, Q_KAYAK + LICENSE, E_KAYAK) == answer
    outside = "12 August 2024 — Marisol mentioned it in that day's conversation with Ulrich."
    assert "12 August" not in guard(outside, Q_KAYAK + LICENSE, E_KAYAK)


def test_a_capitalised_subject_in_the_evidence_supports_only_inside_the_span():
    # "Opera" heads a sentence of the subject's own record; it labels no speaker. Under the license
    # the clause's other object needs no binding inside the span and withdraws outside it.
    question = "When did Marisol visit the lantern festival?"
    for aside in ("Opera tickets are so expensive these days.", "The Opera House was lit up too."):
        evidence = [line("2024-03-03", "Marisol", "I visited the lantern festival in Riga."),
                    line("2024-03-03", "Marisol", aside), line("2024-03-03", "Ines", "Tell me more!")]
        assert guard("Marisol visited the opera around 3 March 2024.", question + LICENSE, evidence) == (
            "Marisol visited the opera around 3 March 2024."), aside
        assert "3 June" not in guard("Marisol visited the opera around 3 June 2024.", question + LICENSE, evidence), aside


def test_a_clause_naming_the_asked_object_may_describe_it_further():
    answer = "Around 12 May 2024 — that's when Marisol first announced her new (second) kayak; her first was older."
    assert guard(answer, Q_KAYAK + LICENSE, E_KAYAK) == answer


def test_a_clause_bound_only_by_the_requests_verb_names_another_object_only_inside_the_span():
    question = "When did Marisol visit the lantern festival?"
    evidence = [line("2024-03-03", "Marisol", "I visited the lantern festival in Riga and the river was full of light.")]
    for answer in ("Marisol visited the opera around 3 March 2024.", "Marisol visited the opera around 1 March 2024."):
        assert guard(answer, question + LICENSE, evidence) == answer
    assert "1 June" not in guard("Marisol visited the opera around 1 June 2024.", question + LICENSE, evidence)


def test_a_described_clause_still_needs_the_record_within_the_span():
    answer = "Around 30 July 2024 — that's when Marisol first announced her new (second) kayak."
    assert "30 July" not in guard(answer, Q_KAYAK + LICENSE, E_KAYAK)


def test_head_word_reading_applies_only_under_the_license():
    answer = "Marisol bought her second kayak on 12 May 2024 — that's when she mentioned it."
    assert "12 May" not in guard(answer, Q_KAYAK, E_KAYAK)
