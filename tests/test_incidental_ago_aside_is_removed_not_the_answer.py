"""A "which is N days ago" aside to a question that asks no time is removed, not the whole answer.

Measured 2026-10-06 on the memory benchmark (archived reply, no provider call here): "A smoker - you
said you got it on March 15, which is 10 days ago." to "What kitchen appliance did I buy 10 days
ago?" was replaced whole because removing the unsupported "10 days ago" left "which is." behind, the
edit read as a fragment and was refused. The question asks for an object, so the time is incidental:
the aside goes and the answer stays. A time that is the answer is still withdrawn. Names and dates
are authored for this contract.
"""
import pytest

from core.model_output_guard import replace_unsupported_past_time_claims

DRILL = ["- user said (stated 2024-03-15): I bought a cordless drill at the hardware store on Elm Street today."]
QUESTION = "What tool did I buy 10 days ago?"


@pytest.mark.parametrize("answer", [
    "A cordless drill, which you bought at the hardware store 10 days ago.",
    "A cordless drill — you bought it at the hardware store, which is 10 days ago.",
    "A cordless drill — you bought it at the hardware store, which was 10 days ago.",
    "A cordless drill (you bought it at the hardware store — that's 10 days ago).",
    "a cordless drill, bought at the hardware store on elm street, which is 10 days ago",
])
def test_the_ago_aside_goes_and_the_object_answer_stays(answer):
    out = replace_unsupported_past_time_claims(answer, question=QUESTION, evidence_texts=DRILL)
    assert "cordless drill" in out.lower()
    assert "10 days" not in out
    assert "which is." not in out and "which was." not in out and "that's)" not in out


def test_an_unsupported_time_that_is_the_answer_is_still_withdrawn():
    out = replace_unsupported_past_time_claims("12 days ago.", question="How many days ago did I buy the cordless drill?",
                                               evidence_texts=DRILL)
    assert "12 days" not in out


def test_an_answer_with_no_time_is_untouched():
    answer = "A cordless drill, which is the one you said you needed for the shelves."
    assert replace_unsupported_past_time_claims(answer, question=QUESTION, evidence_texts=DRILL) == answer
