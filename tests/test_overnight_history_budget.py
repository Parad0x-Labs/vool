"""Past assistant-answer packing amid unrelated request-register facts."""
import pytest
from core.context_retrieval import _query_overlap_terms
from tests.test_p300_pref_retrieval_repair import pref_env, _ingest, _capsule

@pytest.mark.parametrize('question,subject,noise',[
    ('I was thinking about our previous conversation about the Tide Exhibition, and I was wondering if you could remind me what sculpture you said showed the artist evolving?',{'tide','exhibition','sculpture','artist','evolving'}, {'thinking','wondering','remind','conversation'}),
    ('I was going through our previous conversation and I was wondering if you could remind me of the two studios you mentioned that prioritize apprenticeships like Northfield?',{'studios','apprenticeships','northfield'}, {'going','through','wondering','remind','conversation'}),
])
def test_history_request_register_does_not_count_as_subject(question,subject,noise):
    terms=_query_overlap_terms(question)
    assert subject <= terms
    assert not terms & noise,terms


def test_exact_quoted_request_words_remain_content():
    question='In our previous conversation, which phrase contained "I was wondering if you could remind me"?'
    terms=_query_overlap_terms(question)
    assert {'wondering','remind'} <= terms


def test_assistant_prose_answer_survives_unrelated_valued_request_filler(pref_env,monkeypatch):
    import core.embedding_service as es
    monkeypatch.setattr(es,'_best_embed_model',lambda:None)
    home,_=pref_env
    answer='In the Tide Exhibition, the sculpture "Harbour Spiral" best demonstrates how the artist is evolving through layered stonework.'
    sessions=[('2025/05/10 (Sat) 10:00','What sculpture in the Tide Exhibition demonstrates the artist evolving?',answer)]
    for day in range(11,21):
        sessions.append((f'2025/05/{day} (Sun) 10:00',
            "I was thinking about arranging a trip and I was wondering if you could suggest better plans. "
            "My rail ticket costs 140 euros and the lodge is 36 kilometres from the station. "
            "The outing is unrelated to the sculpture exhibition.",'I can suggest a travel itinerary.'))
    _ingest(home,'historical-prose',sessions)
    question='I was thinking about our previous conversation about the Tide Exhibition, and I was wondering if you could remind me what sculpture you said best demonstrates the artist evolving?'
    context,telemetry=_capsule(home,'historical-prose',question)
    assert answer in context,(context,telemetry)
