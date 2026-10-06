"""A record's statement time, and the relative time it states against that time, support a date.

Measured on the paid fresh probe (GLM reader): the past-time guard withdrew correct date answers that
were read straight off a record and its statement time -- "around 15 September" from a record stated
on a Sunday saying "last Friday", "16 November" from a record saying "yesterday", "around July" from
a record saying "booked a trip for next month", and even "booked the trip on <the record's own
statement date>". Three general defects:

1. The capsule binds a fragment to its record's speaker label in the envelope
   ('- user said [reported source prefix "Name:"] (stated ...): ...'). The annotation kept the
   envelope from being stripped, so the label was never read and the record fell to the generic user.
2. The record suffix "(stated: 2: 31 pm on 9 June, 2023; ...)" carries a clock time before the date,
   which only the session header parser accepted.
3. The support law never read a record's own statement time, nor resolved the record's relative
   time expressions (yesterday, last <weekday>, last/next week/month/year, N days ago, ...) against it.

The law: a claimed date or year is supported when a source unit spoken by the asked subject, naming
the asked event (a term shared by the question, the claim clause and the unit), carries statement
time S and either (a) the value equals S at the claim's granularity and the unit anchors its own act
at S -- a completed arrangement against a forward window ("I booked a trip for next month") -- or (b)
one of the unit's relative expressions, read against S, names a window containing the value at the
claim's granularity. A day claim needs a window of at most a week; a month or year window supports
only the year. A bare past-tense report ("I had the guitar serviced", stated S) still dates nothing
at S: the existing historical-time contract keeps withdrawing that.

Fabricated values, another speaker's derivation, a different event of the same speaker, an uncertain
unit and a day picked out of a month window all stay withdrawn. All sentences are synthetic.
"""

from __future__ import annotations

from core.model_output_guard import replace_unsupported_past_time_claims, stated_past_time_claims


def _suffix_line(speaker_text: str, clock: str, day: str, iso: str) -> str:
    # Session header + label + the record suffix with a clock time before the date.
    return (f"- user said: Session date: {clock} on {day}\n{speaker_text} "
            f"(stated: {clock.replace(':', ': ', 1)} on {day}; stated: {iso})")


def _annotated_line(label: str, text: str, clock: str, day: str, iso: str, *, repeat_label: bool = True) -> str:
    # The capsule's speaker binding in the envelope; the fragment may or may not repeat the label.
    body = f"{label}: {text}" if repeat_label else text
    return (f'- user said [reported source prefix "{label}:"] (stated {iso}): '
            f"Session date: {clock} on {day}\n{body}")


def guard(answer, question, evidence):
    return replace_unsupported_past_time_claims(answer, question=question, evidence_texts=evidence)


# --- yesterday, through the speaker-bound envelope --------------------------------------------

MARATHON_Q = "When did Marek finish the harbour half marathon?"
MARATHON_E = [
    _annotated_line("Marek", "Morning! I finished the harbour half marathon yesterday and my legs are done.",
                    "9:12 am", "18 April, 2024", "2024-04-18"),
    _annotated_line("Dalia", "Congrats! I'm still training for mine.", "9:14 am", "18 April, 2024", "2024-04-18"),
]


def test_yesterday_in_a_speaker_bound_record_dates_the_day_before_its_statement():
    answer = "Marek finished the harbour half marathon on 17 April 2024 -- he said so the next morning."
    assert guard(answer, MARATHON_Q, MARATHON_E) == answer


def test_speaker_binding_holds_when_the_fragment_omits_the_label():
    evidence = [_annotated_line("Marek", "I finished the harbour half marathon yesterday.", "9:12 am",
                                "18 April, 2024", "2024-04-18", repeat_label=False)]
    answer = "Marek finished the harbour half marathon on 17 April 2024."
    assert guard(answer, MARATHON_Q, evidence) == answer


def test_a_past_dated_record_does_not_lend_its_statement_date_to_the_event():
    # "yesterday" places the race before the statement: the statement date itself is not the event's.
    assert "18 April" not in guard("Marek finished the harbour half marathon on 18 April 2024.", MARATHON_Q, MARATHON_E)


def test_another_speakers_derivation_is_withdrawn():
    evidence = [
        _annotated_line("Dalia", "I finished the harbour half marathon yesterday!", "9:12 am", "18 April, 2024", "2024-04-18"),
        _annotated_line("Marek", "Well done, Dalia.", "9:14 am", "18 April, 2024", "2024-04-18"),
    ]
    assert "17 April" not in guard("Marek finished the harbour half marathon on 17 April 2024.", MARATHON_Q, evidence)


# --- last <weekday>, through the clock-time record suffix ------------------------------------

POTTERY_Q = "When did Ilse take a pottery class?"
# 14 April 2024 is a Sunday; the Friday before it is 12 April 2024.
POTTERY_E = [_suffix_line("Ilse: We took a pottery class last Friday and made two lopsided bowls.",
                          "4:40 pm", "14 April, 2024", "2024-04-14")]


def test_last_weekday_resolves_against_the_records_statement_time():
    answer = "Ilse mentioned on 14 April 2024 that she took a pottery class last Friday, so around 12 April 2024."
    receipt = {}
    assert stated_past_time_claims(answer, question=POTTERY_Q, evidence_texts=POTTERY_E, decision_receipt=receipt) == ()
    derivations = [entry for check in receipt["checks"] for entry in check["statement_time_derivation"]]
    assert any(entry["rule"] == "relative_window" and entry["expression"] == "last friday"
               and entry["window"] == ["2024-04-12", "2024-04-12"] for entry in derivations), derivations
    assert guard(answer, POTTERY_Q, POTTERY_E) == answer


def test_a_date_no_same_subject_record_derives_is_withdrawn():
    assert "5 April" not in guard("Ilse took a pottery class on 5 April 2024.", POTTERY_Q, POTTERY_E)


def test_a_wrong_actor_derivation_is_withdrawn():
    evidence = [
        _suffix_line("Tomas: We took a pottery class last Friday!", "4:40 pm", "14 April, 2024", "2024-04-14"),
        _suffix_line("Ilse: That sounds fun, Tomas.", "4:42 pm", "14 April, 2024", "2024-04-14"),
    ]
    assert "12 April" not in guard("Ilse took a pottery class on 12 April 2024.", POTTERY_Q, evidence)


def test_without_a_question_actor_the_clauses_actor_must_have_spoken_the_record():
    # "did Ilse and her partner ..." names no single grammatical actor; the claim clause's own
    # subject (Ilse) is the asked subject, and another speaker's "last Friday" lends her nothing.
    question = "When did Ilse and her partner take a pottery class?"
    evidence = [_suffix_line("Tomas: We took a pottery class last Friday!", "4:40 pm", "14 April, 2024", "2024-04-14")]
    assert "12 April" not in guard("Ilse and her partner took a pottery class around 12 April 2024.", question, evidence)
    own = [_suffix_line("Ilse: We took a pottery class last Friday!", "4:40 pm", "14 April, 2024", "2024-04-14")]
    kept = "Ilse and her partner took a pottery class around 12 April 2024."
    assert guard(kept, question, own) == kept


def test_a_record_sharing_only_instruction_words_names_no_asked_event():
    # The turn carries an instruction before its question; a record matching only the instruction's
    # words ("notes") is not about the asked event, though it is the same speaker's.
    question = "Answer from my notes only.\nWhen did Ilse take a pottery class?"
    evidence = [_suffix_line("Ilse: My notes app crashed last Friday and I lost a week of notes.", "4:40 pm", "14 April, 2024", "2024-04-14")]
    assert "12 April" not in guard("Ilse took a pottery class around 12 April 2024, per her notes.", question, evidence)


def test_the_same_speakers_different_event_supports_nothing():
    evidence = [_suffix_line("Ilse: I reorganised my whole bookshelf last Friday.", "4:40 pm", "14 April, 2024", "2024-04-14")]
    assert "12 April" not in guard("Ilse took a pottery class on 12 April 2024.", POTTERY_Q, evidence)


def test_an_uncertain_record_supports_nothing():
    evidence = [_suffix_line("Ilse: Maybe we took a pottery class last Friday, I forget.", "4:40 pm", "14 April, 2024", "2024-04-14")]
    assert "12 April" not in guard("Ilse took a pottery class on 12 April 2024.", POTTERY_Q, evidence)


# --- next month and the record's own statement time ------------------------------------------

BOOKING_Q = "When did Ilse book the cabin by the fjord?"
BOOKING_E = [_suffix_line("Ilse: I booked a cabin by the fjord for next month! Can't wait.", "11:05 am", "3 May, 2024", "2024-05-03")]
VISIT_Q = "When did Ilse visit the fjord?"
VISIT_E = [_suffix_line("Ilse: I'm visiting the fjord next month with my sister!", "11:05 am", "3 May, 2024", "2024-05-03")]


def test_the_records_own_statement_time_supports_the_act_it_reports():
    answer = "Ilse booked the cabin by the fjord on 3 May 2024, for the following month."
    assert guard(answer, BOOKING_Q, BOOKING_E) == answer


def test_next_month_supports_the_month_level_answer():
    answer = "Ilse visited the fjord around June 2024."
    assert guard(answer, VISIT_Q, VISIT_E) == answer


def test_a_bare_past_report_does_not_take_the_statement_date():
    evidence = [_suffix_line("Ilse: I booked a cabin by the fjord.", "11:05 am", "3 May, 2024", "2024-05-03")]
    assert "3 May" not in guard("Ilse booked the cabin by the fjord on 3 May 2024.", BOOKING_Q, evidence)


def test_a_need_without_a_completed_act_does_not_take_the_statement_date():
    evidence = [_suffix_line("Ilse: I still need to book a cabin by the fjord for next month.", "11:05 am", "3 May, 2024", "2024-05-03")]
    assert "3 May" not in guard("Ilse booked the cabin by the fjord on 3 May 2024.", BOOKING_Q, evidence)


def test_a_planned_event_does_not_take_the_statement_date():
    # "I'm visiting ... next month" reports a plan; the visit is not dated at the statement.
    assert "3 May" not in guard("Ilse visited the fjord on 3 May 2024.", VISIT_Q, VISIT_E)


def test_a_day_is_not_picked_out_of_a_month_window():
    assert "14 June" not in guard("Ilse visited the fjord on 14 June 2024.", VISIT_Q, VISIT_E)


def test_a_year_outside_every_window_is_withdrawn():
    assert "2025" not in guard("Ilse visited the fjord around June 2025.", VISIT_Q, VISIT_E)


# --- last week -------------------------------------------------------------------------------

SHED_Q = "When did Tomas repaint the garden shed?"
# 22 May 2024 is a Wednesday; the previous Monday-to-Sunday week is 13-19 May 2024.
SHED_E = [_suffix_line("Tomas: Last week I repainted the garden shed a deep green.", "7:30 pm", "22 May, 2024", "2024-05-22")]


def test_last_week_supports_a_day_inside_the_previous_week():
    answer = "Tomas repainted the garden shed around 15 May 2024."
    assert guard(answer, SHED_Q, SHED_E) == answer


def test_last_week_does_not_support_a_day_outside_that_week():
    assert "21 May" not in guard("Tomas repainted the garden shed on 21 May 2024.", SHED_Q, SHED_E)


# --- the same law over other relative forms, answer renderings and statement-time spellings ----

import pytest  # noqa: E402

# Each row: (record text, statement clock, statement day as rendered, ISO, question, kept answer, withdrawn answer).
# 6 June 2024 is a Thursday.
_FAMILY = [
    ("Noor: I pruned the lemon tree two days ago.", "8:02 pm", "6 June, 2024", "2024-06-06",
     "When did Noor prune the lemon tree?", "Noor pruned the lemon tree on June 4th, 2024.",
     "Noor pruned the lemon tree on June 5th, 2024."),
    ("Noor: This past Monday I pruned the lemon tree.", "8:02 pm", "6 June 2024", "2024-06-06",
     "When did Noor prune the lemon tree?", "Noor pruned the lemon tree on 3 Jun 2024.",
     "Noor pruned the lemon tree on 27 May 2024."),
    ("Noor: I pruned the lemon tree earlier this week, finally.", "8:02 pm", "6 June, 2024", "2024-06-06",
     "when did noor prune the lemon tree", "Noor pruned the lemon tree around 4 June 2024.",
     "Noor pruned the lemon tree around 29 May 2024."),
    ("Noor: I pruned the lemon tree a week ago and it already looks happier.", "8:02 pm", "6 June 2024", "2024-06-06",
     "When did Noor prune the lemon tree?", "Noor pruned the lemon tree roughly 30 May 2024.",
     "Noor pruned the lemon tree on 20 May 2024."),
    ("Noor: Last year I pruned the lemon tree right back to the trunk.", "8:02 pm", "6 June, 2024", "2024-06-06",
     "When did Noor prune the lemon tree?", "Noor pruned the lemon tree in 2023.",
     "Noor pruned the lemon tree in 2021."),
    ("Noor: Can't believe it, I pruned the lemon tree today.", "8:02 pm", "6 June 2024", "2024-06-06",
     "When did Noor prune the lemon tree?", "Noor pruned the lemon tree on 06 June 2024.",
     "Noor pruned the lemon tree on 7 June 2024."),
]


@pytest.mark.parametrize("record,clock,day,iso,question,kept,withdrawn", _FAMILY)
def test_relative_forms_resolve_against_the_statement_time(record, clock, day, iso, question, kept, withdrawn):
    evidence = [_suffix_line(record, clock, day, iso)]
    assert guard(kept, question, evidence) == kept
    assert guard(withdrawn, question, evidence) != withdrawn


def test_the_clock_suffix_alone_carries_the_statement_time():
    # No ISO companion: the "(stated: <clock> on <date>)" suffix is the only statement time.
    evidence = ["- user said: Noor: I pruned the lemon tree yesterday. (stated: 8: 02 pm on 6 June 2024)"]
    answer = "Noor pruned the lemon tree on 5 June 2024."
    assert guard(answer, "When did Noor prune the lemon tree?", evidence) == answer
