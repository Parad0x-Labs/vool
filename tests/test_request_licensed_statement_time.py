"""The user's request to date an event by when it was said licenses the record's statement time.

Measured on the paid v4a run: two correct date answers were withdrawn as invented past times.

* "When did <Name> and her partner try <lessons>? Use DATE of CONVERSATION to answer with an
  approximate date." answered "Last Friday before <S> -- roughly <S - 2 days>." The compound subject
  resolved to no actor, and the bare dated answer never repeated the asked event, so the subject's
  own record ("We tried a <lesson> last Friday", stated S) could not support it.
* "<date> (the day before his <Mon D> chat, when he said he went "yesterday")": the yearless month
  day of the record's statement time stayed unsupported although the record is the subject's own.

Three general rules at the owning support law (core.model_output_guard):

1. Request-licensed approximation. When the user's own request asks for the date of the
   conversation/record or an approximate date ("use the date of the conversation", "approximate
   date", "roughly when", "based on when it was said"), a claimed value equal to a same-subject,
   same-event record's statement time S, or derived from S by the existing relative-time rules, is
   supported (receipt rule request_licensed_statement_time). Under that request the claim clause may
   be a bare dated answer that names nothing beyond the asked event, the record's own words and
   time/report grammar. Without the request the narrower law stands: a bare past report does not
   date its event at S.
2. A yearless month-day repeats a full date the answer is entitled to: a supported full date stated
   in the same answer, or the statement time of a same-subject record when the request licenses it
   or the answer presents it as when the subject spoke ("his Nov 17 chat").
3. A compound subject ("<Name> and her partner", "<Name> and <Name>") binds the actor to the named
   person(s): another speaker's record no longer supports the answer.

Controls: no license keeps withdrawing. Under the license the owner contract of
tests/test_request_licensed_evidence_span.py now applies on top of these rules: any record's statement
time supports a value within its evidence span without actor, event or clause binding, so the licensed
controls below place the fabricated value outside every record's span (more than 31 days from it).
All sentences are synthetic.
"""

from __future__ import annotations

from core.model_output_guard import replace_unsupported_past_time_claims, stated_past_time_claims


def guard(answer, question, evidence):
    return replace_unsupported_past_time_claims(answer, question=question, evidence_texts=evidence)


def receipt(answer, question, evidence):
    record: dict = {}
    replace_unsupported_past_time_claims(answer, question=question, evidence_texts=evidence, decision_receipt=record)
    return record


def line(iso: str, speaker: str, text: str) -> str:
    # The retrieval capsule's record line: statement time in the envelope, speaker label in the body.
    return f"- user said (stated {iso}): {speaker}: {text}"


LICENSE = " Use the date of the conversation to answer with an approximate date."

# --- 1. request-licensed approximation ------------------------------------------------------------

KAYAK_Q = "When did Marisol and her brother try kayaking lessons?"
KAYAK_E = [
    line("2024-05-12", "Marisol", "We tried a kayaking lesson last Friday and loved every minute!"),
    line("2024-05-12", "Ines", "I've wanted to try kayaking for ages but never found the time."),
]


def test_licensed_bare_answer_reads_last_weekday_against_the_subjects_record():
    answer = "Last Friday before 12 May 2024 — roughly 10 May 2024."
    assert guard(answer, KAYAK_Q + LICENSE, KAYAK_E) == answer
    rules = {entry["rule"] for check in receipt(answer, KAYAK_Q + LICENSE, KAYAK_E)["checks"]
             for entry in check["statement_time_derivation"]}
    assert {"request_licensed_statement_time", "relative_window"} <= rules


def test_licensed_bare_answer_with_a_fabricated_day_is_withdrawn():
    answer = "Roughly 3 March 2024."
    assert "3 March" not in guard(answer, KAYAK_Q + LICENSE, KAYAK_E)


FESTIVAL_Q = "When did Tomas visit the lantern festival?"
FESTIVAL_E = [
    line("2024-03-03", "Tomas", "I visited the lantern festival in Riga and the river was full of light."),
    line("2024-03-03", "Ines", "That sounds lovely, I was stuck at work all week."),
]


def test_licensed_request_dates_a_bare_past_report_at_its_statement_time():
    answer = "Around 3 March 2024."
    assert guard(answer, FESTIVAL_Q + LICENSE, FESTIVAL_E) == answer
    check = receipt(answer, FESTIVAL_Q + LICENSE, FESTIVAL_E)["checks"][0]
    assert [entry["rule"] for entry in check["statement_time_derivation"]] == ["request_licensed_statement_time"]


def test_without_the_request_a_bare_past_report_still_dates_nothing():
    answer = "Tomas visited the lantern festival on 3 March 2024."
    assert stated_past_time_claims(answer, question=FESTIVAL_Q, evidence_texts=FESTIVAL_E)
    assert "3 March" not in guard(answer, FESTIVAL_Q, FESTIVAL_E)


def test_other_license_grammar_is_read():
    answer = "Around 3 March 2024."
    for question in ("Roughly when did Tomas visit the lantern festival?",
                     "When did Tomas visit the lantern festival? Answer based on when it was said.",
                     "When did Tomas visit the lantern festival? An approximate date is fine."):
        assert guard(answer, question, FESTIVAL_E) == answer, question


def test_a_negated_license_licenses_nothing():
    question = "When did Tomas visit the lantern festival? Do not use the date of the conversation."
    assert "3 March" not in guard("Around 3 March 2024.", question, FESTIVAL_E)


def test_licensed_fabricated_date_without_a_matching_record_is_withdrawn():
    assert "19 December" not in guard("Around 19 December 2023.", FESTIVAL_Q + LICENSE, FESTIVAL_E)


def test_licensed_wrong_actor_record_supports_only_its_evidence_span():
    evidence = [line("2024-03-03", "Ines", "I visited the lantern festival in Riga and the river was full of light.")]
    assert guard("Around 3 March 2024.", FESTIVAL_Q + LICENSE, evidence) == "Around 3 March 2024."
    assert "3 June" not in guard("Around 3 June 2024.", FESTIVAL_Q + LICENSE, evidence)


def test_licensed_record_of_another_event_supports_only_its_evidence_span():
    evidence = [line("2024-03-03", "Tomas", "I visited the dentist and my jaw still hurts.")]
    assert guard("Around 3 March 2024.", FESTIVAL_Q + LICENSE, evidence) == "Around 3 March 2024."
    assert "3 June" not in guard("Around 3 June 2024.", FESTIVAL_Q + LICENSE, evidence)


def test_licensed_clause_naming_another_event_is_withdrawn_outside_the_span():
    assert guard("Tomas visited the opera around 3 March 2024.", FESTIVAL_Q + LICENSE, FESTIVAL_E) == (
        "Tomas visited the opera around 3 March 2024.")
    assert "3 June" not in guard("Tomas visited the opera around 3 June 2024.", FESTIVAL_Q + LICENSE, FESTIVAL_E)


def test_licensed_uncertain_or_negated_record_supports_only_its_evidence_span():
    for text in ("I might visit the lantern festival in Riga.", "I didn't visit the lantern festival in Riga."):
        evidence = [line("2024-03-03", "Tomas", text)]
        assert guard("Around 3 March 2024.", FESTIVAL_Q + LICENSE, evidence) == "Around 3 March 2024.", text
        assert "3 June" not in guard("Around 3 June 2024.", FESTIVAL_Q + LICENSE, evidence), text


# --- 2. yearless month-day ------------------------------------------------------------------------

GALA_Q = "When did Oskar attend the harbour gala?"
GALA_E = [
    line("2023-11-17", "Oskar", "I attended the harbour gala yesterday and met some interesting people."),
    line("2023-11-17", "Dalia", "Congratulations on the gala and the upcoming show."),
]


def test_yearless_statement_time_presented_as_when_he_spoke_is_supported():
    answer = 'November 16, 2023 (the day before his Nov 17 chat, when he said he went "yesterday").'
    assert guard(answer, GALA_Q, GALA_E) == answer
    assert guard(answer, GALA_Q + LICENSE, GALA_E) == answer


def test_yearless_statement_time_as_the_event_date_is_withdrawn_without_license():
    assert "Nov 17" not in guard("Oskar attended the harbour gala on Nov 17.", GALA_Q, GALA_E)


def test_yearless_value_matching_no_record_is_withdrawn():
    answer = "November 16, 2023 (he brought it up again in his Nov 19 chat)."
    assert "Nov 19" not in guard(answer, GALA_Q, GALA_E)
    # Under the license Nov 19 lies two days from the record's statement time; Jan 19 lies outside it.
    assert guard(answer, GALA_Q + LICENSE, GALA_E) == answer
    outside = "November 16, 2023 (he brought it up again in his Jan 19 chat)."
    assert "Jan 19" not in guard(outside, GALA_Q + LICENSE, GALA_E)


def test_yearless_month_day_repeats_a_supported_full_date_of_the_same_answer():
    answer = "Last Friday before 12 May 2024, so roughly 10 May 2024. In short: May 10."
    assert guard(answer, KAYAK_Q + LICENSE, KAYAK_E) == answer


def test_yearless_month_day_repeating_an_unsupported_full_date_is_withdrawn():
    answer = "Roughly 3 March 2024. In short: March 3."
    assert "March 3" not in guard(answer, KAYAK_Q + LICENSE, KAYAK_E)


# --- 3. compound subjects -------------------------------------------------------------------------

MUSEUM_Q = "When did Marisol and her brother visit the glass museum?"


def test_compound_subject_binds_the_named_person():
    evidence = [line("2024-06-05", "Marisol", "We visited the glass museum on 4 June 2024 and bought a vase.")]
    answer = "They visited the glass museum on 4 June 2024."
    assert guard(answer, MUSEUM_Q, evidence) == answer
    assert receipt(answer, MUSEUM_Q, evidence)["checks"][0]["question_actor"] == "marisol"


def test_compound_subject_rejects_another_speakers_record():
    evidence = [line("2024-06-05", "Ines", "I visited the glass museum on 4 June 2024 and bought a vase.")]
    assert "4 June" not in guard("They visited the glass museum on 4 June 2024.", MUSEUM_Q, evidence)


def test_both_named_conjuncts_are_asked_subjects():
    question = "When did Marisol and Petra visit the glass museum?"
    petra = [line("2024-06-05", "Petra", "I visited the glass museum on 4 June 2024 with Marisol.")]
    ines = [line("2024-06-05", "Ines", "I visited the glass museum on 4 June 2024 with a friend.")]
    answer = "They visited the glass museum on 4 June 2024."
    assert guard(answer, question, petra) == answer
    assert "4 June" not in guard(answer, question, ines)


def test_unnamed_conjunct_is_not_the_asked_event():
    # "her brother" names who acted; a record of the subject that only mentions her brother is not
    # about the asked event, under the license or not.
    # Under the license the record's evidence span still supports a nearby day; a day outside it
    # withdraws.
    evidence = [line("2024-06-05", "Marisol", "My brother called me last Friday about his new job.")]
    assert "31 March" not in guard("Roughly 31 March 2024.", MUSEUM_Q + LICENSE, evidence)


def test_unnamed_conjunct_does_not_admit_its_own_dated_record():
    # The subject's record about her brother's own event names no asked-event term.
    evidence = [line("2024-06-05", "Marisol", "My brother started his new job on 31 May 2024.")]
    assert "31 May" not in guard("They visited the glass museum on 31 May 2024.", MUSEUM_Q, evidence)


def test_license_without_an_asked_event_supports_only_the_evidence_span():
    # "do that" names no event: the license dates nothing beyond the evidence span of the records.
    question = "When did Tomas do that?" + LICENSE
    assert guard("Around 3 March 2024.", question, FESTIVAL_E) == "Around 3 March 2024."
    assert "3 June" not in guard("Around 3 June 2024.", question, FESTIVAL_E)


def test_with_no_asked_subject_the_claim_clauses_actor_must_have_spoken_the_record():
    # No person in the question: the claim clause's own subject binds the record's speaker, so
    # another speaker's "last Friday" lends that subject nothing.
    question = "When was the harbour pottery class?"
    others = [line("2024-04-14", "Tomas", "We took the harbour pottery class last Friday!")]
    own = [line("2024-04-14", "Ilse", "We took the harbour pottery class last Friday!")]
    answer = "Ilse took the harbour pottery class around 12 April 2024."
    assert "12 April" not in guard(answer, question, others)
    assert guard(answer, question, own) == answer


def test_license_does_not_date_a_record_sharing_only_instruction_words_beyond_its_span():
    # The turn's instruction shares a word with the subject's record ("notes"); the asked event is
    # the interrogative's. Under the license a record not naming it lends only its evidence span.
    question = ("Answer from my notes only.\nWhen did Ilse take a pottery class?"
                " Use the date of the conversation to answer with an approximate date.")
    evidence = [line("2024-04-14", "Ilse", "My notes app crashed and I lost a week of notes.")]
    assert "14 July" not in guard("Around 14 July 2024.", question, evidence)
    own = [line("2024-04-14", "Ilse", "I took a pottery class and made a lopsided bowl.")]
    assert guard("Around 14 April 2024.", question, own) == "Around 14 April 2024."
