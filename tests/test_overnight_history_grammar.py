"""Development grammar regressions: noun-qualified past interrogatives."""
import pytest

from core.temporal_question_scope import question_time_scope
from tests.test_overnight_source_structure import _recall, _store, source_env


@pytest.mark.parametrize("question,subject", [
    ("What binding material did I report using for the attic atlas restoration?", "user"),
    ("Which storage case did I say held the spindle bearings in that earlier workshop note?", "user"),
    ("What temperature did I record for the indigo dye vat on 2025-04-09, before the later adjustment?", "user"),
    ("What target height had we agreed for the archway?", "user"),
    ("Which spare parts did you recommend for the old recorder?", "assistant"),
    ("What opening time was the island ferry using?", "world"),
    ("How many copper tokens did they find?", "user"),
])
def test_past_interrogative_accepts_the_asked_noun_phrase(question,subject):
    scope=question_time_scope(question)
    assert scope.asks_past and scope.past_subject==subject, scope

@pytest.mark.parametrize("question", [
    "What binding material should I use for the new atlas?",
    "Which storage case will I buy for the new bearings?",
    "What temperature do I need for dyeing tomorrow?",
    "What time was it in Tallinn two hours ago?",
])
def test_noun_phrase_does_not_turn_future_advice_or_clock_arithmetic_into_user_history(question):
    assert not question_time_scope(question).asks_past

@pytest.mark.parametrize("user,question,required", [
    ("Remember: I restored the attic atlas using rice starch paste.",
     "What binding material did I report using for the attic atlas restoration?",
     "I restored the attic atlas using rice starch paste."),
    ("Session date: 2025-07-22. Remember: I packed the spindle bearings in the felt-lined cedar case.",
     "Which storage case did I say held the spindle bearings in that earlier workshop note?",
     "I packed the spindle bearings in the felt-lined cedar case."),
])
def test_real_source_history_not_rejected_as_never_asserted_facet(source_env,user,question,required):
    _store(source_env,"history-grammar",user,"",1753171200)
    _store(source_env,"history-grammar","Correction: the ceramic kiln permit label is now cyan.","",1755259200)
    result=_recall(source_env,"history-grammar",question)
    assert required in result, result
