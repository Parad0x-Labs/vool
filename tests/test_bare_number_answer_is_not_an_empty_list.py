"""A reply that is one number with a period ("10.") answers a count question; the truncation guard read it as an
enumeration with nothing under its marker and replaced it."""
import pytest

from core.incomplete_answer import inspect_answer_completeness

COUNT_ASKS = ["How many items were in the list of beginner woodworking projects you gave me?", "how many pepper plants do i have now",
              "What number did I draw in the raffle?", "How old is my cousin Greta?", "Which position did I finish in the 10K?"]


@pytest.mark.parametrize("text", ["10.", "(3)", "7)", " 12. ", "1."])
@pytest.mark.parametrize("question", COUNT_ASKS)
def test_a_lone_number_answering_a_count_or_ordinal_ask_is_complete(text, question):
    assert not inspect_answer_completeness(text, question=question).incomplete


@pytest.mark.parametrize("question", [None, "", "Why did I give up the allotment plot by the canal?", "What should I cook tonight?", "Summarise my week.",
                                      "¿Qué debería cocinar esta noche?", "Fass meine Woche zusammen."])
def test_a_lone_one_with_no_count_question_is_still_a_cut_off_list(question):
    assert inspect_answer_completeness("1.", question=question).incomplete


# Mac gate on 91e898675: "wie viel ist 9 mal 14?" -> "126." was replaced by "the answer came back cut off". A list a
# model started opens on 1, so any other lone number is an answer, in any language; and a question holding a number
# is answered by one, "1." included.
@pytest.mark.parametrize("seed", range(8))
def test_a_lone_number_answers_in_any_language(seed):
    import random

    rng = random.Random(f"lone-number-{seed}")
    a, b = rng.randint(2, 30), rng.randint(2, 30)
    question = rng.choice((f"wie viel ist {a} mal {b}?", f"¿cuánto es {a} por {b}?", f"kiek bus {a} kart {b}?",
                           f"combien font {a} fois {b} ?", f"what's {a} times {b}"))
    answer = rng.choice((f"{a * b}.", f"{a * b}", f"({a * b})")) if a * b < 1000 else f"{a * b}."
    assert not inspect_answer_completeness(answer, question=question).incomplete
    assert not inspect_answer_completeness(f"{rng.randint(2, 999)}.", question=rng.choice(("¿cuántas patas tiene una araña?", None))).incomplete
    assert not inspect_answer_completeness("1.", question=f"was ist {a} minus {a - 1}?").incomplete


def test_the_final_backstop_passes_the_users_question_through():
    from core.agent_runtime.response import _validate_final_chat_output

    ctx = {"conversation_history": [{"role": "user", "content": "How many items were in the list you gave me?"}]}
    assert _validate_final_chat_output("10.", source_context=ctx).strip() == "10."
    ctx2 = {"conversation_history": [{"role": "user", "content": "Why did I give up the allotment plot?"}]}
    assert "cut off" in _validate_final_chat_output("1.", source_context=ctx2).lower()


@pytest.mark.parametrize("text", ["1.\n2.\n3.", "- \n- ", "1. Red\n2.", "Here are the items:\n1.\n2."])
def test_real_empty_or_unfinished_enumerations_are_still_caught(text):
    assert inspect_answer_completeness(text).incomplete
