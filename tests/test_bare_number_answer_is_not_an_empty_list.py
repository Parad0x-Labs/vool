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


@pytest.mark.parametrize("text", ["1.", "10.", "(3)"])
@pytest.mark.parametrize("question", [None, "", "Why did I give up the allotment plot by the canal?", "What should I cook tonight?", "Summarise my week."])
def test_a_lone_number_with_no_count_question_is_still_a_cut_off_list(text, question):
    assert inspect_answer_completeness(text, question=question).incomplete


def test_the_final_backstop_passes_the_users_question_through():
    from core.agent_runtime.response import _validate_final_chat_output

    ctx = {"conversation_history": [{"role": "user", "content": "How many items were in the list you gave me?"}]}
    assert _validate_final_chat_output("10.", source_context=ctx).strip() == "10."
    ctx2 = {"conversation_history": [{"role": "user", "content": "Why did I give up the allotment plot?"}]}
    assert "cut off" in _validate_final_chat_output("1.", source_context=ctx2).lower()


@pytest.mark.parametrize("text", ["1.\n2.\n3.", "- \n- ", "1. Red\n2.", "Here are the items:\n1.\n2."])
def test_real_empty_or_unfinished_enumerations_are_still_caught(text):
    assert inspect_answer_completeness(text).incomplete
