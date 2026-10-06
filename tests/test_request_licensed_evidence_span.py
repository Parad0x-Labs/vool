"""Under the user's own request for an approximate date, the evidence span of every record supports.

Measured on the paid replay (283 archived replies, 8 roots): after four rounds of clause-grammar and
binding patches the past-time guard still withdrew 23 replies that were right by gold, almost all on
questions that say "Use DATE of CONVERSATION to answer with an approximate date". The failures were
spread over clause grammar, possessive/copular actor resolution ("Calvin's place", "When was Deborah
in Bali") and window limits, and no withdrawal changed a right/wrong outcome for the better.

Owner contract (core.model_output_guard._licensed_evidence_span), request-licensed mode only: a
claimed calendar value is supported when it lies within the evidence span of the statement time S of
ANY record the reader saw ("(stated: ...)" labels and "Session date:" headers):

* a day-level claim within 31 days of some S (a yearless day read in the year nearest S);
* a month-level claim ("Around May 2023") within one calendar month of some S's month -- checked as
  its month, never as its year;
* a year-level claim equal to some S's year or the year before.

No actor binding, event-term binding or clause grammar is required. The receipt names the rule
(request_licensed_evidence_span), the nearest statement and the distance in days. A value outside
every record's span stays withdrawn; outside the license nothing changes; durations and clock times
keep their own handling. All sentences are synthetic.
"""

from __future__ import annotations

import pytest

from core.model_output_guard import replace_unsupported_past_time_claims, stated_past_time_claims


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
Q = "When did Halvard repaint the boathouse?"
# Halvard's own record names no time at all; the other records are about other people and events.
E = [
    line("2023-06-20", "Halvard", "The boathouse looks brand new now, the blue came out great."),
    line("2023-06-20", "Ines", "Love it! I spent the weekend in Bergen."),
    line("2023-03-02", "Ines", "My sister started a new job at the bakery."),
]


# --- day-level claims --------------------------------------------------------------------------------

@pytest.mark.parametrize("answer", [
    "Around 20 June 2023.",                 # S itself
    "Around 19 June 2023.",                 # the day before
    "Roughly mid-June: 12 June 2023.",
    "Around 21 July 2023.",                 # 31 days after S
    "Around 20 May 2023.",                  # 31 days before S
    "Probably 25 February 2023.",           # near another record's statement time
    "Halvard repainted it on June 18.",     # yearless, read in S's year
])
def test_a_day_within_a_month_of_any_record_is_supported(answer):
    assert guard(answer, Q + LICENSE, E) == answer


@pytest.mark.parametrize("answer", [
    "Around 22 July 2023.",                 # 32 days after the latest record
    "Around 19 May 2023.",                  # 32 days before S, 78 after the other
    "Around 20 September 2023.",            # three months from every record
    "Around 30 November 2022.",             # three months before the earliest record
    "Halvard repainted it on September 25.",
])
def test_a_day_outside_every_records_span_is_withdrawn(answer):
    assert stated_past_time_claims(answer, question=Q + LICENSE, evidence_texts=E), answer
    assert guard(answer, Q + LICENSE, E) != answer, answer


def test_a_yearless_day_across_the_new_year_is_read_in_the_nearest_year():
    evidence = [line("2024-01-03", "Halvard", "Finally done with the boathouse, what a winter project.")]
    assert guard("He finished around Dec 28.", Q + LICENSE, evidence) == "He finished around Dec 28."
    assert guard("He finished around Nov 20.", Q + LICENSE, evidence) != "He finished around Nov 20."


# --- month-level claims: checked as the month -----------------------------------------------------

@pytest.mark.parametrize("answer", ["Around June 2023.", "Around July 2023.", "Around May 2023.",
                                    "Around February 2023.", "In March of 2023."])
def test_a_month_within_one_calendar_month_of_a_record_is_supported(answer):
    assert guard(answer, Q + LICENSE, E) == answer
    entries = span_entries(answer, Q + LICENSE, E)
    assert [entry["granularity"] for entry in entries] == ["month"]


@pytest.mark.parametrize("answer", ["Around September 2023.", "Around August 2023.", "Around January 2023.",
                                    "Around December 2022.", "Around November 2023."])
def test_a_month_two_or_more_months_from_every_record_is_withdrawn(answer):
    assert guard(answer, Q + LICENSE, E) != answer, answer


def test_a_month_is_never_carried_by_its_year():
    # The leak of the previous round: a month-only claim checked as its year passed whenever the year
    # was the statement year or the year before.
    single = [line("2023-01-10", "Halvard", "Finally done with the boathouse!")]
    assert guard("Around December 2022.", Q + LICENSE, single) == "Around December 2022."
    for answer in ("Around November 2022.", "Around February 2022.", "Around March 2023."):
        assert guard(answer, Q + LICENSE, single) != answer, answer
    later = [line("2023-06-20", "Halvard", "Finally done with the boathouse!")]
    assert guard("Around January 2022.", Q + LICENSE, later) != "Around January 2022."


def test_a_bound_records_statement_year_does_not_carry_a_distant_month():
    # The record is the subject's own report of the asked event, so the licensed statement-time
    # rule supports S and S's year; a month three months away is still a month claim and withdraws.
    evidence = [line("2023-06-20", "Halvard", "I repainted the boathouse and it looks brand new.")]
    assert guard("Around 20 June 2023.", Q + LICENSE, evidence) == "Around 20 June 2023."
    assert guard("In 2023.", Q + LICENSE, evidence) == "In 2023."
    assert guard("Around September 2023.", Q + LICENSE, evidence) != "Around September 2023."


def test_a_month_and_its_day_are_each_checked():
    assert guard("Around June 2023, about 19 June 2023.", Q + LICENSE, E) == "Around June 2023, about 19 June 2023."
    assert guard("Around October 2023 (19 June 2023).", Q + LICENSE, E) != "Around October 2023 (19 June 2023)."


# --- year-level claims -----------------------------------------------------------------------------

def test_a_year_is_the_records_year_or_the_year_before():
    for answer in ("In 2023.", "Back in 2022."):
        assert guard(answer, Q + LICENSE, E) == answer, answer
    for answer in ("In 2020.", "In 2021.", "In 2024."):
        assert guard(answer, Q + LICENSE, E) != answer, answer


# --- no binding is required inside the span ----------------------------------------------------------

def test_no_actor_event_or_clause_binding_is_required_inside_the_span():
    for answer in ("Ines repainted the boathouse around 19 June 2023.",
                   "Around 18 June 2023 — that's when Halvard's boathouse chat with Ines happened.",
                   "Halvard visited the opera around 19 June 2023."):
        assert guard(answer, Q + LICENSE, E) == answer, answer
    for answer in ("Ines repainted the boathouse around 19 September 2023.",
                   "Halvard visited the opera around 19 September 2023."):
        assert guard(answer, Q + LICENSE, E) != answer, answer


def test_possessive_and_copular_questions_are_dated_by_the_span():
    # "Calvin's place" / "When was Deborah in Bali": the question's actor resolution is not needed.
    evidence = [line("2023-05-16", "Calvin", "Everyone came over last weekend, the house was packed."),
                line("2023-05-16", "Deborah", "Bali was magical, I'm still dreaming about the beaches.")]
    for question in ("When did the friends meet at Calvin's place?", "When was Deborah in Bali?"):
        assert guard("Around 9 May 2023.", question + LICENSE, evidence) == "Around 9 May 2023.", question
        assert guard("Around 9 August 2023.", question + LICENSE, evidence) != "Around 9 August 2023.", question


# --- statement times the reader saw ---------------------------------------------------------------

def test_session_headers_and_stated_suffixes_are_statement_times():
    header = ["Session date: 6:55 pm on 20 October, 2023\nHalvard: The boathouse is finished."]
    suffix = ["Halvard: The boathouse is finished. (stated: 12: 35 am on 14 August, 2023; stated: 2023-08-14)"]
    assert guard("Around 15 October 2023.", Q + LICENSE, header) == "Around 15 October 2023."
    assert guard("Around 10 August 2023.", Q + LICENSE, suffix) == "Around 10 August 2023."
    assert guard("Around 10 August 2023.", Q + LICENSE, header) != "Around 10 August 2023."


def test_evidence_without_any_statement_time_supports_nothing():
    evidence = ["Halvard: The boathouse is finished and the blue came out great."]
    assert guard("Around 19 June 2023.", Q + LICENSE, evidence) != "Around 19 June 2023."


def test_a_date_in_a_record_body_is_not_a_statement_time():
    evidence = [line("2023-06-20", "Ines", "My cousin's wedding was on 3 March 2021, what a party.")]
    assert guard("Around 3 April 2021.", Q + LICENSE, evidence) != "Around 3 April 2021."


# --- the receipt -------------------------------------------------------------------------------------

def test_the_receipt_names_the_rule_the_nearest_statement_and_the_distance():
    assert span_entries("Around 25 February 2023.", Q + LICENSE, E) == [{
        "rule": "request_licensed_evidence_span", "claimed_value": "m2:25:2023", "granularity": "day",
        "nearest_statement": "2023-03-02", "distance_days": 5, "values": ["m2:25:2023", "y2023"]}]
    assert span_entries("Around July 2023.", Q + LICENSE, E) == [{
        "rule": "request_licensed_evidence_span", "claimed_value": "y2023", "granularity": "month",
        "nearest_statement": "2023-06-20", "distance_days": 11, "values": ["y2023"]}]
    assert span_entries("Back in 2022.", Q + LICENSE, E) == [{
        "rule": "request_licensed_evidence_span", "claimed_value": "y2022", "granularity": "year",
        "nearest_statement": "2023-03-02", "distance_days": 61, "values": ["y2022"]}]


# --- license grammar, negation and the unlicensed guard ----------------------------------------------

@pytest.mark.parametrize("question", [
    Q + " Use DATE of CONVERSATION to answer with an approximate date.",
    "Roughly when did Halvard repaint the boathouse?",
    Q + " An approximate date is fine.",
    Q + " Answer based on when it was said.",
    Q + " use the date of the chat",
])
def test_each_license_wording_opens_the_span(question):
    assert guard("Around 19 June 2023.", question, E) == "Around 19 June 2023."
    assert guard("Around 19 September 2023.", question, E) != "Around 19 September 2023."


@pytest.mark.parametrize("question", [
    Q,
    Q + " Do not use the date of the conversation.",
    Q + " Don't use the date of the conversation, I want the exact day.",
    Q + " Give the exact day rather than an approximate date.",
])
def test_without_a_license_or_with_a_negated_one_the_strict_guard_stands(question):
    for answer in ("Around 19 June 2023.", "Around June 2023.", "Ines repainted the boathouse on 19 June 2023."):
        assert stated_past_time_claims(answer, question=question, evidence_texts=E), (question, answer)
        assert span_entries(answer, question, E) == []


# --- durations and clock times keep their handling -----------------------------------------------------

def test_durations_and_clock_times_are_not_span_values():
    assert guard("It took about 3 days.", Q + LICENSE, E) != "It took about 3 days."
    assert guard("At 4:07 pm on 19 June 2023.", Q + LICENSE, E) != "At 4:07 pm on 19 June 2023."


def test_one_value_outside_the_span_withdraws_the_sentence():
    answer = "Between 19 June 2023 and 2 October 2023."
    claims = stated_past_time_claims(answer, question=Q + LICENSE, evidence_texts=E)
    assert "m10:2:2023" in claims and "m6:19:2023" not in claims
    assert guard(answer, Q + LICENSE, E) != answer


# --- beyond the span the licensed clause grammar still governs the record's own relative window -----
#
# A record's relative expression read against its statement time ("two years ago") may name a window
# outside every evidence span. Under the license a bare dated clause takes the asked event from the
# subject's record (licensed binding, unchanged); a clause naming another event, or a record not
# naming the asked event, lends that window nothing.

FESTIVAL_Q = "When did Tomas visit the lantern festival?"
TWO_YEARS = [line("2024-03-03", "Tomas", "I visited the lantern festival two years ago and the river was full of light.")]


def test_a_bare_licensed_clause_reads_the_subjects_window_beyond_the_span():
    for answer in ("In 2022.", "In 2022 — that's when he said it."):
        assert guard(answer, FESTIVAL_Q + LICENSE, TWO_YEARS) == answer, answer


def test_a_licensed_clause_naming_another_event_gets_no_window_beyond_the_span():
    assert "2022" not in guard("The opera premiered in 2022.", FESTIVAL_Q + LICENSE, TWO_YEARS)
    assert "2022" not in guard("The opera premiered with Ulrich in 2022.", FESTIVAL_Q + LICENSE, TWO_YEARS)


def test_a_record_not_naming_the_asked_event_lends_no_window_beyond_the_span():
    # The turn's instruction shares "notes" with the record, so the record is admitted as the
    # subject's; the interrogative's event (a pottery class) is not in it.
    question = "Answer from my notes only.\nWhen did Ilse take a pottery class?" + LICENSE
    evidence = [line("2024-04-14", "Ilse", "My notes app crashed two years ago and I lost all my notes.")]
    assert "2022" not in guard("In 2022.", question, evidence)


def test_the_receipt_says_which_rule_supported_a_licensed_value():
    # A clause bound to the subject's record only through the request's verb is not the licensed
    # statement-time rule; the evidence span supports it. A clause naming the asked object is.
    evidence = [line("2024-03-03", "Tomas", "I visited the lantern festival in Riga and the river was full of light.")]
    rules = [entry["rule"] for entry in span_entries("Tomas visited the opera around 3 March 2024.", FESTIVAL_Q + LICENSE, evidence)]
    assert rules == ["request_licensed_evidence_span"]
    record: dict = {}
    answer = "Around 3 March 2024 — that's when Tomas visited the lantern festival with Ines."
    assert guard(answer, FESTIVAL_Q + LICENSE, evidence) == answer
    replace_unsupported_past_time_claims(answer, question=FESTIVAL_Q + LICENSE, evidence_texts=evidence, decision_receipt=record)
    assert [entry["rule"] for check in record["checks"] for entry in check["statement_time_derivation"]] == [
        "request_licensed_statement_time"]


def test_without_the_license_a_yearless_day_repeats_a_full_date_of_another_sentence():
    # Sunday 3 March 2024: "last Friday" names 1 March for the sentence that names the festival; the
    # bare repeat earns that full date only from the other sentence of the same answer.
    evidence = [line("2024-03-03", "Tomas", "I visited the lantern festival last Friday and the river was full of light.")]
    answer = "Tomas visited the lantern festival on 1 March 2024. In short: March 1."
    assert guard(answer, FESTIVAL_Q, evidence) == answer
    assert "February 23" not in guard("Tomas visited the lantern festival on 1 March 2024. In short: February 23.",
                                      FESTIVAL_Q, evidence)
