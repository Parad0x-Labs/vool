"""A one-word answer to an amount question survives any lead-in before the question.

The "answer too short" rule withdraws a one-word reply to an explanation question ("why ...",
"how does ..."). An amount question ("how many / much / long / old / often / far") is answered by
one word. The amount exemption used to be anchored to the very start of the user's message, so an
instruction line, a greeting or a "quick question" lead-in in front of the question made the rule
withdraw a correct one-word answer, and the turn ended as "I couldn't produce a normal chat
response". Measured 2026-10-06 on the memory benchmark: "Twice." to "How many times have I met up
with Alex from Germany?" after the reader instruction line.
"""
import pytest

from core.ordinary_chat_response_guard import inspect_ordinary_chat_output, ordinary_chat_output_policy

READER_INSTRUCTION = (
    "Answer using the imported prior conversations. You may derive only answers supported by those "
    "records. If the records do not support an answer, say you do not know. Give a concise final answer."
)


def _check(question: str, answer: str):
    policy = ordinary_chat_output_policy(prompt_profile="chat_minimal", output_mode="plain_text", user_text=question)
    return inspect_ordinary_chat_output(answer, policy, current_user_text=question)


AMOUNT_QUESTIONS_WITH_A_LEAD_IN = [
    # the reported wording
    (READER_INSTRUCTION + "\nHow many times have I met up with Alex from Germany?", "Twice."),
    # clean paraphrases: different lead-ins, quantities and domains
    ("Quick question about my garden. How many tomato plants did I put in this spring?", "Twelve."),
    ("Going by what I told you last week: how much did the ferry tickets to Hvar cost?", "€46."),
    ("Thanks for the help earlier. How long was the drive from Lyon to Annecy?", "Ninety-minutes."),
    ("Remind me, how old was Priya's terrier when she adopted him?", "Three."),
    ("Something I keep forgetting. How often does the Wednesday choir meet?", "Weekly."),
    ("I need this for a form. How far is the clinic from my flat?", "2km."),
    # sloppy, user-typed variants
    ("hey so how many cousins came to the reunion", "Eleven."),
    ("ok but how much was the rent in porto again??", "€900."),
    ("pls remind me how many km i ran on sunday", "8."),
    ("quick one how long did the visa take", "Weeks."),
    ("Remind me HOW MANY books i returned to the library", "Four."),
]


@pytest.mark.parametrize("question,answer", AMOUNT_QUESTIONS_WITH_A_LEAD_IN)
def test_a_one_word_amount_answer_survives_a_lead_in_before_the_question(question: str, answer: str) -> None:
    result = _check(question, answer)
    assert "ordinary_answer_too_short" not in result.reasons, (question, answer, result.reasons)


EXPLANATION_QUESTIONS = [
    # negative controls: a one-word reply to an explanation question is still withdrawn, lead-in or not
    ("Why did I move my dentist appointment?", "Work."),
    ("Quick question. How does a heat pump move warmth uphill?", "Physics."),
    (READER_INSTRUCTION + "\nWhy did Tomasz leave the band?", "Burnout."),
    # adversarial near-miss: "how come" asks why
    ("How come I stopped going to the Thursday swim?", "Injury."),
]


@pytest.mark.parametrize("question,answer", EXPLANATION_QUESTIONS)
def test_a_one_word_answer_to_an_explanation_question_is_still_too_short(question: str, answer: str) -> None:
    result = _check(question, answer)
    assert result.allowed is False
    assert result.reasons == ("ordinary_answer_too_short",)


def test_an_amount_question_that_also_asks_why_does_not_accept_one_word() -> None:
    # Adversarial near-miss: it opens like an amount question but also asks for the reasons.
    result = _check("How many reasons did I give for selling the van, and why?", "Three.")
    assert result.allowed is False


def test_a_why_or_how_inside_the_lead_in_does_not_make_the_amount_question_open() -> None:
    question = "I asked you how to prune them before. Anyway, how many apple trees did we plant?"
    assert "ordinary_answer_too_short" not in _check(question, "Seven.").reasons
