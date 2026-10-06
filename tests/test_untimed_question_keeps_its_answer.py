"""A question that asks for no time never loses its answer to a time the answer adds.

"Why did Rosa get a snake?" answered "Rosa got a snake because reptiles fascinate her — she adopted it
two years earlier when she felt lonely, and caring for it calms her." with no record of "two years":
the reason is the answer and the duration is incidental. Replacing the whole reply with the notice for
the duration removed a right answer; keeping the duration would ship an invented time.

Contract (core.model_output_guard.replace_unsupported_past_time_claims): when the request asks for no
time (no when / what date / what year / how long / how long ago / how many weeks / which month ...
question and no approximate-date license), every unsupported time expression the guard extracts --
date, month, year, duration ("two years earlier", "for three months", "about six weeks ago"), clock
time -- is removed at the smallest grain that holds: its adverbial frame, then the dash, comma or
bracket clause that carries it, then its sentence; when every sentence carries one, the first sentence
ships without its time phrases. Each edit is rechecked, so an unsupported time never ships and a
supported one stays. A question that asks for a time keeps the existing withdrawal exactly.
"""
import pytest

from core.model_output_guard import replace_unsupported_past_time_claims, stated_past_time_claims

NOTICE = "I don't have that time in anything I can see from our conversation, so I am not going to state one."

ROSA = ("- user said: Session date: 14 May 2024\n"
        "Rosa: I adopted my corn snake two years ago because reptiles fascinate me, and looking after her "
        "calms me. (stated: 2024-05-14)")
WHY = "Why did Rosa adopt a corn snake?"


def guard(answer, question=WHY, evidence=(ROSA,)):
    return replace_unsupported_past_time_claims(answer, question=question, evidence_texts=list(evidence))


def claims(text, question=WHY, evidence=(ROSA,)):
    return stated_past_time_claims(text, question=question, evidence_texts=list(evidence))


def test_a_duration_inside_a_dash_explanation_is_removed_and_the_reason_ships():
    answer = ("Rosa adopted a corn snake because reptiles fascinate her — she got it three years earlier "
              "when she felt lonely, and caring for it calms her.")
    assert claims(answer) == ("d3year",)
    assert guard(answer) == ("Rosa adopted a corn snake because reptiles fascinate her — she got it "
                             "when she felt lonely, and caring for it calms her.")


@pytest.mark.parametrize("answer, expected", [
    ("Rosa adopted a corn snake because reptiles fascinate her; she has kept it for three months.",
     "Rosa adopted a corn snake because reptiles fascinate her; she has kept it."),
    ("Rosa adopted a corn snake because reptiles fascinate her. She bought a tank about six weeks ago.",
     "Rosa adopted a corn snake because reptiles fascinate her. She bought a tank."),
    ("Five years ago, Rosa adopted a corn snake because reptiles fascinate her.",
     "Rosa adopted a corn snake because reptiles fascinate her."),
    ("Five years ago she adopted a corn snake because reptiles fascinate her.",
     "She adopted a corn snake because reptiles fascinate her."),
    ("Rosa adopted a corn snake because reptiles fascinate her, a fascination she has had over the past nine years.",
     "Rosa adopted a corn snake because reptiles fascinate her, a fascination she has had."),
    ("Rosa adopted a corn snake 5 years ago because reptiles fascinate her.",
     "Rosa adopted a corn snake because reptiles fascinate her."),
    ("Rosa adopted a corn snake because reptiles fascinate her, and she fed it at 9 am.",
     "Rosa adopted a corn snake because reptiles fascinate her, and she fed it."),
])
def test_an_unsupported_duration_or_clock_adjunct_is_removed(answer, expected):
    assert claims(answer)
    assert guard(answer) == expected


def test_a_relative_clause_carrying_an_argument_duration_goes_with_both_commas():
    # "took three years" is no adjunct: the relative clause that carries it is removed as a unit.
    answer = "The snake, which took three years to find, calms Rosa because reptiles fascinate her."
    assert guard(answer) == "The snake calms Rosa because reptiles fascinate her."


def test_a_comma_clause_carrying_an_argument_duration_is_removed_as_a_unit():
    answer = "Rosa adopted a corn snake because reptiles fascinate her, which cost her three months of savings."
    assert guard(answer) == "Rosa adopted a corn snake because reptiles fascinate her."


def test_a_sentence_carrying_an_argument_duration_is_dropped_and_the_answer_ships():
    answer = "Rosa adopted a corn snake because reptiles fascinate her. The search took seven months."
    assert guard(answer) == "Rosa adopted a corn snake because reptiles fascinate her."


def test_when_every_sentence_carries_one_the_first_ships_without_its_time():
    question = "Where did Rosa travel?"
    answer = "Rosa went to Lisbon for the 2019 reptile fair."
    assert guard(answer, question) == "Rosa went to Lisbon for the reptile fair."


def test_nothing_meaningful_left_keeps_the_notice():
    for answer in ("In 2019.", "Seven months.", "It took seven months."):
        assert guard(answer) == NOTICE, answer


@pytest.mark.parametrize("question", [
    "Who adopted a corn snake?", "Why did Rosa adopt a corn snake?", "why did rosa adopt the snake",
    "Which animal did Rosa adopt?",
])
def test_a_supported_duration_stays_while_an_unsupported_one_goes(question):
    # "two years ago" is Rosa's own words about the adoption the question names; "six months" is in no record.
    answer = "Rosa adopted a corn snake two years ago, after searching for six months."
    result = guard(answer, question)
    assert "two years ago" in result and "six months" not in result and "corn snake" in result, result


def test_a_supported_date_stays_in_its_own_sentence():
    pets = ("- user said: Session date: 14 May 2024\nDana: We adopted a beagle today, and Mira already owns "
            "two kayaks. (stated: 2024-05-14)")
    question = "What dog did Dana adopt?"
    answer = "Dana adopted a beagle on 14 May 2024. Lena adopted a cat four years ago."
    assert guard(answer, question, [pets]) == "Dana adopted a beagle on 14 May 2024. Lena adopted a cat."


TIME_QUESTIONS = (
    "When did Rosa adopt the corn snake?", "How long has Rosa had the corn snake?",
    "How long ago did Rosa adopt the corn snake?", "What year did Rosa adopt the corn snake?",
    "How many weeks did Rosa search for the snake?", "What month did Rosa adopt the corn snake?",
    "Why did Rosa adopt a corn snake? Give an approximate date from the conversation.",
)


@pytest.mark.parametrize("question", TIME_QUESTIONS)
def test_time_questions_keep_the_withdrawal(question):
    assert guard("Rosa adopted the corn snake five years ago.", question) == NOTICE
    assert guard("Rosa searched for six months.", question) == NOTICE


def test_a_time_question_keeps_the_notice_after_a_withdrawn_sentence():
    question = "How long ago did Rosa adopt the corn snake?"
    answer = "Rosa adopted a corn snake. That was five years ago."
    assert guard(answer, question) == "Rosa adopted a corn snake. " + NOTICE


UNTIMED_FAMILY = [
    "Rosa adopted a corn snake because reptiles fascinate her — she got it three years earlier when lonely.",
    "Rosa adopted a corn snake because reptiles fascinate her; she has kept it for three months.",
    "Rosa adopted a corn snake, which took her eleven months to find, because reptiles fascinate her.",
    "Eleven months ago Rosa adopted a corn snake because reptiles fascinate her.",
    "Rosa adopted a corn snake because reptiles fascinate her (she bought it 3 weeks ago).",
    "Rosa adopted a corn snake on 2 March 2021 because reptiles fascinate her, after a search of eleven months.",
    "Rosa adopted a corn snake because reptiles fascinate her. The search took eleven months. She fed it at 7 pm.",
    "Back in 2019, Rosa adopted a corn snake because reptiles fascinate her, roughly four years before the move.",
    "Rosa has a three-year-old corn snake because reptiles fascinate her.",
]
UNSUPPORTED_TEXT = ("three years", "three months", "eleven months", "3 weeks", "2 March", "2021", "7 pm",
                    "2019", "four years", "three-year")


@pytest.mark.parametrize("question", ["Why did Rosa adopt a corn snake?", "What pet did Rosa adopt?",
                                      "Who did Rosa adopt?", "why did rosa get the snake"])
def test_the_untimed_family_keeps_the_answer_and_never_ships_an_unsupported_time(question):
    for answer in UNTIMED_FAMILY:
        result = guard(answer, question)
        assert result != NOTICE and "corn snake" in result, (question, answer, result)
        assert not claims(result, question), (answer, result)
        assert not any(text in result for text in UNSUPPORTED_TEXT), (answer, result)
        assert not result.lstrip().startswith((",", "—", ";")) and result[:1].isupper(), (answer, result)


@pytest.mark.parametrize("question", TIME_QUESTIONS)
def test_the_untimed_family_under_a_time_question_never_ships_its_time(question):
    for answer in UNTIMED_FAMILY:
        result = guard(answer, question)
        assert not any(text in result for text in UNSUPPORTED_TEXT), (question, answer, result)


def test_a_determined_duration_goes_with_its_preposition():
    # "for the eight months prior" is one adjunct; cutting only "eight months prior" left "for the,".
    question = "Which pet did Rosa adopt most recently?"
    answer = ("The newest is a corn snake. Her two cats were already living with her for the eight months prior, "
              "so the snake is the latest.")
    assert guard(answer, question) == ("The newest is a corn snake. Her two cats were already living with her, "
                                       "so the snake is the latest.")


def test_a_determined_duration_without_a_preposition_is_not_cut_out_of_its_noun_phrase():
    question = "Which pet did Rosa adopt most recently?"
    answer = "The newest is a corn snake. She spent the eight months prior looking for one."
    result = guard(answer, question)
    assert result == "The newest is a corn snake.", result


def test_a_conditional_example_is_not_cut_down_to_its_consequence():
    # Cutting "if you rode it in 50 minutes" and "that's 6 minutes faster" left "e.g., about a 10% gain":
    # the example sentence goes whole instead.
    question = "How much faster was my second bike loop than the first?"
    answer = ("I only have your first loop time of 56 minutes. Tell me the second and I'll compare — e.g., "
              "if you rode it in 50 minutes, that's 6 minutes faster, about a 10% gain.")
    evidence = ["- user said: Session date: 3 June 2024\nuser: My first bike loop took 56 minutes. (stated: 2024-06-03)"]
    result = guard(answer, question, evidence)
    assert result == "I only have your first loop time of 56 minutes.", result


@pytest.mark.parametrize("pronoun", ["her", "him", "them", "it"])
def test_an_object_pronoun_before_a_duration_is_no_determiner(pronoun):
    # "adopted her five years earlier": "her" is the object, so only the duration and its "earlier" go.
    answer = (f"Rosa adopted a corn snake because reptiles fascinate her — she adopted {pronoun} five years earlier "
              "when she felt lonely, and caring for it calms her.")
    assert guard(answer) == (f"Rosa adopted a corn snake because reptiles fascinate her — she adopted {pronoun} "
                             "when she felt lonely, and caring for it calms her.")


def test_a_complementizer_before_a_duration_is_no_determiner():
    answer = "Rosa adopted a corn snake because reptiles fascinate her; she said that five years ago she wanted one."
    assert guard(answer) == "Rosa adopted a corn snake because reptiles fascinate her; she said that she wanted one."


def test_a_reporting_verb_is_no_condition():
    # "don't say" states no condition: the dash explanation carrying the year is detachable.
    question = "Which country did Rosa visit?"
    answer = "The records don't say — nothing in them places Rosa in a country during spring 2021."
    assert guard(answer, question) == "The records don't say."


OMAR = ("- user said: Session date: 9 Feb 2024\nOmar: I took up pottery because my therapist suggested a hands-on "
        "hobby; the wheel relaxes me. (stated: 2024-02-09)")
OMAR_QUESTIONS = ("Why did Omar take up pottery?", "why omar start pottery", "whats the reason omar does pottery",
                  "What hobby did Omar pick up?", "who got omar into pottery")


@pytest.mark.parametrize("question", OMAR_QUESTIONS)
@pytest.mark.parametrize("answer, expected", [
    ("omar took up pottery cuz his therapist suggested a hands-on hobby, he started like 3 yrs ago",
     "omar took up pottery cuz his therapist suggested a hands-on hobby, he started"),
    ("Because his therapist suggested a hands-on hobby - he began **two years ago** and the wheel relaxes him.",
     "Because his therapist suggested a hands-on hobby - he began and the wheel relaxes him."),
    ("His therapist suggested it.\n- started: 14 months ago\n- the wheel relaxes him",
     "His therapist suggested it.\n- the wheel relaxes him"),
    ("His therapist suggested a hands-on hobby; Omar has thrown pots for a solid twenty months, three evenings a week.",
     "His therapist suggested a hands-on hobby."),
    ("Since 2019 — his therapist suggested it.", "His therapist suggested it."),
    ("His therapist suggested a hands-on hobby; Omar has been throwing pots since March 2021, three evenings a week.",
     "His therapist suggested a hands-on hobby; Omar has been throwing pots, three evenings a week."),
])
def test_sloppy_and_formatted_answers_lose_only_the_time(question, answer, expected):
    assert guard(answer, question, [OMAR]) == expected


def test_a_year_that_is_its_sentence_subject_goes_with_its_sentence():
    # "2019 was a hard year" carries no frame: cutting the year alone shipped "was a hard year for her".
    question = "What dog did Dana adopt?"
    pets = ("- user said: Session date: 14 May 2024\nDana: We adopted a beagle today, and Mira already owns "
            "two kayaks. (stated: 2024-05-14)")
    assert guard("Dana adopted a beagle. 2019 was a hard year for her.", question, [pets]) == "Dana adopted a beagle."


def test_a_fronted_date_inside_the_answer_takes_its_comma_and_keeps_a_capital():
    # Only the answer's very first character is tidied afterwards; a fronted adverbial in a later
    # sentence must take its own comma ("... corner. , she ..." otherwise).
    question = "What pet did Rosa adopt?"
    answer = "Rosa adopted a corn snake. On 3 April 2022, she bought it a heated tank."
    assert guard(answer, question) == "Rosa adopted a corn snake. She bought it a heated tank."
