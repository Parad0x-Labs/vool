"""A question that asks for no time keeps its answer when the answer adds an unsupported date.

"What picture did Dana share?" answered "On 3 April 2024, Dana shared a photo of her study corner"
with no record of 3 April: the picture is the answer and the date is incidental. Withdrawing the whole
reply for the date removed a correct answer; keeping the date would ship an invented time.

Contract (core.model_output_guard.replace_unsupported_past_time_claims): when the request asks for no
time (no when / what date / what year / how long / which month ... question, and no approximate-date
license), the unsupported time phrase alone is removed in its adverbial frame -- a fronted
"On <date>,", a trailing ", on <date>", "(<date>)", "in <month year>", "in <year>", "as of <date>",
"Dana, on <date>, ..." -- and the rest of the sentence ships. The unsupported time itself never ships:
a time that is no adjunct ("the 2019 trip") is removed with its clause or sentence, never with the whole
answer (tests/test_untimed_question_keeps_its_answer.py). A question that asks for a time withdraws as before.
"""
from core.model_output_guard import replace_unsupported_past_time_claims

NOTICE = "I don't have that time in anything I can see from our conversation, so I am not going to state one."

DANA = ("- user said: Session date: 6:05 pm on 14 May, 2024\n"
        "Dana: Finals week has me swamped. This is my study corner right now. "
        "(stated: 6: 05 pm on 14 May, 2024; stated: 2024-05-14)")
PETS = ("- user said: Session date: 14 May 2024\nDana: We adopted a beagle today, and Mira already "
        "owns two kayaks. (stated: 2024-05-14)")
WHAT = "What picture did Dana share about finals week?"


def guard(answer, question, evidence):
    return replace_unsupported_past_time_claims(answer, question=question, evidence_texts=evidence)


def test_a_fronted_unsupported_date_is_removed_and_the_answer_ships():
    answer = "On 3 April 2024, Dana shared a photo of her study corner."
    assert guard(answer, WHAT, [DANA]) == "Dana shared a photo of her study corner."


def test_a_fronted_date_before_a_pronoun_subject_keeps_a_capital():
    question = "What picture did she share about finals week?"
    answer = "On 3 April 2024, she shared a photo of her study corner."
    assert guard(answer, question, [DANA]) == "She shared a photo of her study corner."


def test_a_trailing_comma_date_is_removed():
    answer = "Dana shared a photo of her study corner, on 3 April 2024."
    assert guard(answer, WHAT, [DANA]) == "Dana shared a photo of her study corner."


def test_a_parenthetical_comma_date_takes_both_commas():
    answer = "Dana, on 3 April 2024, shared a photo of her study corner."
    assert guard(answer, WHAT, [DANA]) == "Dana shared a photo of her study corner."


def test_an_in_month_year_adjunct_is_removed():
    question = "What dog did Dana adopt?"
    answer = "Dana adopted a beagle in April 2023."
    assert guard(answer, question, [PETS]) == "Dana adopted a beagle."


def test_an_in_year_adjunct_is_removed():
    question = "What dog did Dana adopt?"
    answer = "Dana adopted a beagle back in 2019, and she loves it."
    assert guard(answer, question, [PETS]) == "Dana adopted a beagle, and she loves it."


def test_an_as_of_adjunct_is_removed():
    question = "How many kayaks did Mira own?"
    answer = "As of 3 April 2024, Mira owned two kayaks."
    assert guard(answer, question, [PETS]) == "Mira owned two kayaks."


def test_a_date_inside_a_bracket_is_removed_without_a_stray_space():
    question = "What dog did Dana adopt?"
    answer = "Dana adopted a beagle (the shelter closed on 3 April 2024)."
    assert guard(answer, question, [PETS]) == "Dana adopted a beagle (the shelter closed)."


def test_a_time_that_is_no_adjunct_is_never_kept():
    question = "Where did Dana travel?"
    answer = "Dana went to Lisbon for the 2019 festival."
    result = guard(answer, question, [PETS])
    assert "2019" not in result
    # Round 7: the answer to a question that asks no time is never replaced whole; the first sentence
    # ships without the time (tests/test_untimed_question_keeps_its_answer.py).
    assert result == "Dana went to Lisbon for the festival."


def test_a_date_supported_in_its_own_sentence_stays():
    # The same date is Dana's (her record says "today") but not another person's in the next sentence.
    question = "What dog did Dana adopt?"
    answer = "Dana adopted a beagle on 14 May 2024. Lena adopted a cat on 14 May 2024."
    result = guard(answer, question, [PETS])
    assert result == "Dana adopted a beagle on 14 May 2024. Lena adopted a cat."


def test_a_when_question_with_an_unsupported_date_is_still_withdrawn():
    question = "When did Dana adopt the beagle?"
    answer = "Dana adopted the beagle on 3 April 2023."
    assert guard(answer, question, [PETS]) == NOTICE


def test_a_calendar_unit_question_with_an_unsupported_date_is_still_withdrawn():
    for question in ("What month did Dana adopt the beagle?", "Which week did Dana adopt the beagle?",
                     "How recently did Dana adopt the beagle?"):
        answer = "Dana adopted the beagle on 3 April 2023."
        assert guard(answer, question, [PETS]) == NOTICE, question


def test_a_licensed_question_is_not_incidental():
    # The request asks for an approximate date: the date is the answer, not an adjunct.
    question = "What dog did Dana adopt? Give an approximate date from the conversation."
    answer = "Dana adopted a beagle on 3 January 2020."
    assert guard(answer, question, [PETS]) == NOTICE


def test_a_time_question_keeps_the_notice_after_a_withdrawn_sentence():
    question = "What month did Dana adopt the beagle?"
    answer = "Dana adopted a beagle. That was in April 2023."
    assert guard(answer, question, [PETS]) == "Dana adopted a beagle. " + NOTICE


INCIDENTAL_FAMILY = [
    "On 3 April 2024, Dana shared a photo of her study corner.",
    "on april 3 2024 dana shared a photo of her study corner",
    "Dana shared a pic of her study corner on Apr 3, 2024.",
    "Dana posted a photo of her study corner (3 April 2024).",
    "Back on April 3rd 2024, Dana shared a photo of her study corner.",
    "The photo Dana shared on 3 April 2024 showed her study corner.",
    "Dana shared a photo of her study corner — on 3 April 2024.",
    "Dana shared a photo of her study corner, dated 3 April 2024.",
    "In April 2024 Dana shared a photo of her study corner",
    "dana sent a photo of her study corner in 2023",
]


def test_the_incidental_family_keeps_the_answer_and_drops_every_unsupported_time():
    for question in (WHAT, "what pic did dana share about finals", "Which photo did Dana post about finals week?"):
        for answer in INCIDENTAL_FAMILY:
            result = guard(answer, question, [DANA])
            assert result != NOTICE and "study corner" in result, (question, answer, result)
            assert not any(token in result for token in ("2023", "2024", "April", "april", "Apr")), (answer, result)
            assert not result.lstrip().startswith((",", "Back", "back")), (answer, result)


def test_time_questions_over_the_same_family_still_withdraw():
    for question in ("When did Dana share a photo of her study corner?", "What date did Dana share the photo?",
                     "What year did Dana share the photo?", "How long ago did Dana share the photo?",
                     "What picture did Dana share, and when?"):
        for answer in INCIDENTAL_FAMILY[:3]:
            assert guard(answer, question, [DANA]) == NOTICE, (question, answer)


def test_an_appositive_date_leaves_one_comma():
    question = "Who gave Dana the beagle?"
    answer = "I don't know — Dana mentions the beagle arriving on Saturday, 3 April 2024, but nothing about who gave it."
    assert guard(answer, question, [PETS]) == (
        "I don't know — Dana mentions the beagle arriving on Saturday, but nothing about who gave it.")


def test_a_fronted_date_after_a_colon_leaves_no_stray_comma():
    question = "What picture did Dana share about finals week?"
    answer = "One picture: on 3 April 2024, Dana shared a photo of her study corner."
    assert guard(answer, question, [DANA]) == "One picture: Dana shared a photo of her study corner."
