"""A dated record by the asked subject supports its own date when its event is named by pronoun.

Measured on the original comparison (exposed LoCoMo id q0c7234): the reader answered a "when did
<person> ... after <earlier event>" question with exactly the date the person's own record carries
("we just did it yesterday", stated the next day, in a turn that names the earlier event), and the
past-time guard withdrew it. Two defects, both general:

1. The capsule renders a speaker turn as "Session date: <clock time> on <date>" on its own line,
   then "<Name>: ...". The statement-marker parser did not accept the clock-time prefix, so the
   header stayed in front of the speaker label, the label was never read, and every first-person
   clause of that turn was attributed to the generic user instead of the named speaker.
2. A clause whose object is a pronoun ("we did it yesterday") takes its event from the discourse,
   so it can never share the question's event words. The guard withdrew such a date even when the
   asked subject spoke it and the same turn names the asked event.

The narrowed law: a date is withdrawn unless the existing support contract holds OR an admitted
dated record exists about the subject -- a record clause spoken (by explicit label) by the asked,
named subject, whose object is a pronoun, that resolves to exactly that date, in a turn that names
a term of the asked event (a closed compound matches its spaced spelling). A fabricated date, a
date carried by another speaker, a non-anaphoric clause about a different action, and a turn that
does not name the asked event are all still withdrawn.

All sentences are synthetic.
"""

from __future__ import annotations

from core.model_output_guard import replace_unsupported_past_time_claims, stated_past_time_claims


def _line(speaker_text: str, clock: str, day: str, iso: str) -> str:
    # The capsule's rendering of one stored speaker turn (header line, label, statement suffix).
    return f"- user said: Session date: {clock} on {day}\n{speaker_text} (stated: {clock} on {day}; stated: {iso})"


QUESTION = "When did Priya go kayaking after the fieldtrip?"
EVIDENCE = [
    _line("Tomas: Love the photo of the two of you on the lake! Is that recent?", "4:10 pm", "12 March, 2024", "2024-03-12"),
    _line(
        "Priya: Thanks, Tomas! Yes, we finally did it yesterday! It was a calm way to unwind after the field trip.",
        "4:12 pm", "12 March, 2024", "2024-03-12",
    ),
]
ANSWER = "Priya went kayaking on 11 March 2024 -- she mentioned on 12 March 2024 that they had done it the day before."


def guard(answer=ANSWER, question=QUESTION, evidence=EVIDENCE):
    return replace_unsupported_past_time_claims(answer, question=question, evidence_texts=evidence)


def test_anaphoric_record_by_the_asked_subject_supports_its_own_date():
    receipt = {}
    assert stated_past_time_claims(ANSWER, question=QUESTION, evidence_texts=EVIDENCE, decision_receipt=receipt) == ()
    assert guard() == ANSWER


def test_clock_time_session_header_does_not_hide_the_speaker_label():
    question = "When did Priya visit the aquarium?"
    evidence = [_line("Priya: What a morning! I visited the aquarium today with my niece.", "9:05 am", "2 May, 2024", "2024-05-02")]
    answer = "Priya visited the aquarium on 2 May 2024."
    assert guard(answer, question, evidence) == answer


def test_clock_time_session_header_never_lends_the_speaker_to_another_name():
    question = "When did Tomas visit the aquarium?"
    evidence = [_line("Priya: What a morning! I visited the aquarium today with my niece.", "9:05 am", "2 May, 2024", "2024-05-02")]
    assert "2 May" not in guard("Tomas visited the aquarium on 2 May 2024.", question, evidence)


def test_fabricated_date_for_the_same_subject_is_still_withdrawn():
    assert "9 March" not in guard("Priya went kayaking on 9 March 2024.")


def test_date_carried_by_another_speakers_record_is_still_withdrawn():
    evidence = [
        _line("Tomas: We finally did it yesterday! A calm way to unwind after the field trip.", "4:10 pm", "12 March, 2024", "2024-03-12"),
        _line("Priya: That sounds lovely, Tomas.", "4:12 pm", "12 March, 2024", "2024-03-12"),
    ]
    assert "11 March" not in guard("Priya went kayaking on 11 March 2024.", evidence=evidence)


def test_anaphoric_record_in_a_turn_that_does_not_name_the_asked_event_is_still_withdrawn():
    evidence = [_line("Priya: Thanks, Tomas! Yes, we finally did it yesterday! The kids loved it.", "4:12 pm", "12 March, 2024", "2024-03-12")]
    assert "11 March" not in guard("Priya went kayaking on 11 March 2024.", evidence=evidence)


def test_non_anaphoric_clause_about_a_different_action_is_still_withdrawn():
    question = "When did Priya finish the kayak repair?"
    evidence = [
        _line(
            "Priya: Good news! I finished the kayak repair yesterday. I started the kayak repair on 2 March 2024.",
            "4:12 pm", "12 March, 2024", "2024-03-12",
        )
    ]
    assert "2 March" not in guard("Priya finished the kayak repair on 2 March 2024.", question, evidence)
    assert guard("Priya finished the kayak repair on 11 March 2024.", question, evidence) == (
        "Priya finished the kayak repair on 11 March 2024."
    )


def test_statement_date_is_not_the_event_date_unless_presented_as_when_she_said_it():
    out = guard("Priya went kayaking on 12 March 2024.")
    assert "12 March" not in out


def test_no_record_at_all_still_withdraws():
    assert "11 March" not in guard("Priya went kayaking on 11 March 2024.", evidence=[])


# --- semantic family (CLAUDE.md 6b.2) ------------------------------------------------------------

import pytest  # noqa: E402

FAMILY = [
    # (question, speaker turn, answer, supported date)
    (QUESTION, "Priya: Thanks, Tomas! Yes, we finally did it yesterday! It was a calm way to unwind after the field trip.",
     "Priya went kayaking on 11 March 2024.", "11 March"),
    ("When did Priya go kayaking after the field trip?",
     "Priya: Thanks! We did it yesterday, a calm way to unwind after the fieldtrip.",
     "She went kayaking on 11 March 2024.", "11 March"),
    ("When did Okon visit the planetarium after the boatshow?",
     "Okon: Ha, yes! I finally did it today. Needed something quiet after the boat show.",
     "Okon visited the planetarium on 12 March 2024.", "12 March"),
    ("when did okon visit the planetarium after the boat show",
     "Okon: yep we did it yesterday lol, so quiet after the boat show",
     "Okon visited the planetarium on 11 March 2024", "11 March"),
    ("When did Mirela try the climbing gym after her workshop?",
     "Mirela: Oh, I tried that yesterday! The workshop left me restless, so it was perfect.",
     "Mirela tried the climbing gym on 11 March 2024.", "11 March"),
    ("When did Mirela try the climbing gym after her workshop?",
     "Mirela: Good question. Well, we did it yesterday - after the workshop wrapped up.",
     "Mirela went on 11 March 2024 -- she said on 12 March 2024 that she had done it the day before.", "11 March"),
]


@pytest.mark.parametrize("question,turn,answer,day", FAMILY)
def test_family_anaphoric_record_by_the_asked_subject(question, turn, answer, day):
    evidence = [
        _line("Tomas: That photo looks great, is it recent?", "4:10 pm", "12 March, 2024", "2024-03-12"),
        _line(turn, "4:12 pm", "12 March, 2024", "2024-03-12"),
    ]
    assert guard(answer, question, evidence) == answer


@pytest.mark.parametrize("question,turn,answer,day", FAMILY)
def test_family_negative_other_speaker_says_it(question, turn, answer, day):
    # The same turn spoken by somebody else is not a record about the asked subject.
    other = "Tomas:" + turn.split(":", 1)[1]
    evidence = [_line(other, "4:12 pm", "12 March, 2024", "2024-03-12")]
    assert day not in guard(answer, question, evidence)


@pytest.mark.parametrize("question,turn,answer,day", FAMILY)
def test_family_negative_shifted_date_is_withdrawn(question, turn, answer, day):
    evidence = [_line(turn, "4:12 pm", "12 March, 2024", "2024-03-12")]
    shifted = answer.replace(day, "8 " + day.split()[1])
    assert "8 " + day.split()[1] not in guard(shifted, question, evidence)


def test_near_miss_turn_names_only_an_unasked_event():
    # Adversarial near-miss: pronoun record by the subject, but the turn names a different event.
    evidence = [_line("Priya: We did it yesterday! A calm way to unwind after the dentist.", "4:12 pm", "12 March, 2024", "2024-03-12")]
    assert "11 March" not in guard("Priya went kayaking on 11 March 2024.", evidence=evidence)
