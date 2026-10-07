"""Exposed independent advice failures are regressions, never fresh proof."""
import math
import re

import pytest

from core.context_retrieval import _advice_ask_frame_terms, _advice_topic_clause
from tests.test_p300_pref_retrieval_repair import _capsule, _ingest, pref_env

CASES = [{'id': 'F2-05', 'q': 'I am deciding how to schedule this ceramics firing. Any suggestions that respect my earlier kiln preference?', 'owned': 'At the glazing bench, my preferred kiln program is a slow bisque cycle at 910 degrees; I do not use rapid firing.', 'topic_words': ['glazing', 'bench', 'kiln', 'program', 'slow', 'bisque', 'cycle', 'degrees', 'rapid', 'firing', 'ceramics', 'schedule', 'preference'], 'noise': ['I am wondering which option I should choose for the upcoming task. The unrelated docket label is Cedar-1.', 'Lately I am trying to improve my routine and looking for suggestions. The unrelated docket label is Cedar-2.', 'Any advice about what I should consider when thinking through this choice? The unrelated docket label is Cedar-3.', 'I am unsure what to pick and would appreciate tips on a consistent routine. The unrelated docket label is Cedar-4.', 'Which option do you recommend while I am considering my next choice? The unrelated docket label is Cedar-5.', 'I am thinking about an upcoming decision and looking for any suggestions. The unrelated docket label is Cedar-6.', 'What should I do when I am not sure which option to choose? The unrelated docket label is Cedar-7.', 'I am trying to improve consistency and wondering what advice to consider. The unrelated docket label is Cedar-8.', 'I am looking for tips because my upcoming routine needs a decision. The unrelated docket label is Cedar-9.']}, {'id': 'F2-06', 'q': 'I am considering what to pack for the field day; what lunch would you recommend given my dietary constraint?', 'owned': 'For field-day lunches I avoid sesame entirely and pack rye wraps filled with beans.', 'topic_words': ['field', 'day', 'lunches', 'lunch', 'sesame', 'rye', 'wraps', 'filled', 'beans', 'pack', 'dietary', 'constraint'], 'noise': ['I am wondering which option I should choose for the upcoming task. The unrelated docket label is Brass-1.', 'Lately I am trying to improve my routine and looking for suggestions. The unrelated docket label is Brass-2.', 'Any advice about what I should consider when thinking through this choice? The unrelated docket label is Brass-3.', 'I am unsure what to pick and would appreciate tips on a consistent routine. The unrelated docket label is Brass-4.', 'Which option do you recommend while I am considering my next choice? The unrelated docket label is Brass-5.', 'I am thinking about an upcoming decision and looking for any suggestions. The unrelated docket label is Brass-6.', 'What should I do when I am not sure which option to choose? The unrelated docket label is Brass-7.', 'I am trying to improve consistency and wondering what advice to consider. The unrelated docket label is Brass-8.', 'I am looking for tips because my upcoming routine needs a decision. The unrelated docket label is Brass-9.']}, {'id': 'F2-07', 'q': 'Which finish should I settle on for the repair project if I follow my own material restriction?', 'owned': 'For finishing my repair projects I never use oil-based sealants; I choose water-based acrylic.', 'topic_words': ['finishing', 'finish', 'repair', 'projects', 'project', 'oil-based', 'sealants', 'water-based', 'acrylic', 'material', 'restriction'], 'noise': ['I am wondering which option I should choose for the upcoming task. The unrelated docket label is Flint-1.', 'Lately I am trying to improve my routine and looking for suggestions. The unrelated docket label is Flint-2.', 'Any advice about what I should consider when thinking through this choice? The unrelated docket label is Flint-3.', 'I am unsure what to pick and would appreciate tips on a consistent routine. The unrelated docket label is Flint-4.', 'Which option do you recommend while I am considering my next choice? The unrelated docket label is Flint-5.', 'I am thinking about an upcoming decision and looking for any suggestions. The unrelated docket label is Flint-6.', 'What should I do when I am not sure which option to choose? The unrelated docket label is Flint-7.', 'I am trying to improve consistency and wondering what advice to consider. The unrelated docket label is Flint-8.', 'I am looking for tips because my upcoming routine needs a decision. The unrelated docket label is Flint-9.']}]
REGISTER = {'lately','trying','improve','tips','advice','any','wondering','consistent','routine','recommend','suggestions','what','should','do','looking','consider','thinking','upcoming','sure','which','choose','choice','option','pick','unsure'}

def axis(words):
    topic=set(words)
    def embed(text,*a,**kw):
        vec=[0.0]*8
        for w in re.findall(r"[a-z][a-z'-]*",str(text).lower()):
            if w in REGISTER: vec[0]+=1
            elif w in topic: vec[1+sum(map(ord,w))%3]+=1
        length=math.sqrt(sum(x*x for x in vec))
        if not length: vec[-1]=length=1.0
        return [x/length for x in vec], 'ollama:pref-stub-ax'
    return embed

@pytest.mark.parametrize('case',CASES,ids=lambda c:c['id'])
def test_independent_owned_targets_reach_capsule(pref_env,monkeypatch,case):
    import tests.test_p300_pref_retrieval_repair as geo
    monkeypatch.setattr(geo,'_make_stub_embed_stamped',axis)
    home,install=pref_env
    install(case['topic_words'])
    sessions=[('2025/07/02 (Wed) 08:00',case['owned'],'')]
    sessions += [(f'2025/09/{i:02d} (Mon) 08:00',body,'') for i,body in enumerate(case['noise'],1)]
    _ingest(home,'independent-advice-regression',sessions)
    context,telemetry=_capsule(home,'independent-advice-regression',case['q'])
    assert case['owned'] in context,(context,telemetry)

@pytest.mark.parametrize('question,required',[
    ("I am deciding how to schedule this ceramics firing. Any suggestions that respect my earlier kiln preference?",('ceramics','kiln','preference')),
    ("I am considering what to pack for the field day; what lunch would you recommend given my dietary constraint?",('field day','lunch','dietary constraint')),
    ("Which finish should I settle on for the repair project if I follow my own material restriction?",('finish','repair project','material restriction')),
    ("I am excited to visit the instrument shop tomorrow. Any tips on what to look for in a new cello?",('instrument shop','new cello')),
])
def test_advice_keeps_subjects_before_and_after_request(question,required):
    topic=_advice_topic_clause(question)
    assert topic is not None
    assert all(word in topic for word in required),topic

@pytest.mark.parametrize('question',[
    'Which finish had I selected for the old kayak?',
    'What lunch did you recommend in our previous chat?',
    "Which finish should I have used on last year's panel?",
])
def test_historical_and_counterfactual_questions_are_not_advice(question):
    assert _advice_topic_clause(question) is None


def test_named_recommendation_preserves_negative_qualifications():
    question='Which meal would you suggest without sesame, given that my guest must avoid nuts?'
    topic=_advice_topic_clause(question)
    assert topic is not None and 'meal' in topic
    assert 'without sesame' in topic and 'must avoid nuts' in topic
    assert 'meal' not in _advice_ask_frame_terms(question)
    assert 'sesame' not in _advice_ask_frame_terms(question)
