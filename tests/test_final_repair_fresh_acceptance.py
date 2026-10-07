"""Post-code-freeze acceptance: new worlds, assertions and advice questions.

These are local mechanism and workflow checks, not live model accuracy scores.
The first executed attempt is preserved even if any case needs later repair.
"""
import datetime as dt
import json
from pathlib import Path
import pytest
from core.agent_runtime.response import _validate_final_chat_output
from core.unsourced_current_claim import inspect_unsourced_current_claim

CASES=json.loads(Path(__file__).with_name('final_repair_fresh_cases.json').read_text())

@pytest.fixture(autouse=True)
def isolate_telemetry():
    from core.context_retrieval import reset_retrieval_telemetry
    reset_retrieval_telemetry()
    yield
    reset_retrieval_telemetry()

def context_for(case):
    context={'surface':'openclaw','platform':'openclaw','chat_id':'fresh-atelier',
             'conversation_history':[{'role':'user','content':case['question']}]}
    if case['lines']:
        context['admitted_capsule_evidence']={'text':'<retrieved_context>\n'+'\n'.join(case['lines'])+'\n</retrieved_context>',
            'chat_id':'foreign-atelier' if case.get('foreign') else 'fresh-atelier',
            'source':'canonical_runtime_transcript'}
    return context

@pytest.mark.parametrize('case',CASES,ids=[c['id'] for c in CASES])
def test_fresh_guard_delivery(case):
    delivered=_validate_final_chat_output(case['reply'],source_context=context_for(case))
    if case['keep']:
        assert delivered==case['reply'], delivered
    else:
        assert case['value'] not in delivered, delivered

@pytest.mark.parametrize('case',[c for c in CASES if c['lines']],ids=[c['id'] for c in CASES if c['lines']])
def test_fresh_earlier_record_guard(case):
    verdict=inspect_unsourced_current_claim(answer=case['reply'],requires_current=True,
        user_turn_text=case['question'],source_context=context_for(case))
    if case['keep']:
        assert verdict.unsupported is False and not verdict.qualify_only, verdict.as_dict()
    else:
        # v14.6 item 2 (c9e5e50e): an unrecorded measured value is no longer withdrawn whole at the verdict, it is
        # qualified; what the user sees is guarded on the delivery path: the value never reaches them as a plain fact
        assert verdict.unsupported or verdict.qualify_only, verdict.as_dict()
        delivered = _validate_final_chat_output(case['reply'], source_context=context_for(case))
        assert case['value'] not in delivered, delivered

POTTERY_TOPIC={'sunset','evening','pottery','sessions','canvas','apron','wire','cutter','sponge','tote','pack','carry','class','clay','glaze'}
POTTERY_USER='For my sunset pottery sessions I always pack my canvas apron and the wire cutter, plus a sponge in my tote.'
POTTERY_Q='Any suggestions for what to carry before my evening pottery sessions?'
DISTRACTORS=[
    ('The pottery kiln was serviced in three hours last week.','Fast service.'),
    ('My heaviest clay delivery weighed 29 kilograms.','That is quite heavy.'),
    ('The magazine says: "For my pottery sessions I always pack a glaze sprayer."','That is the magazine writer’s choice.'),
]

@pytest.fixture
def pottery_home(tmp_path,monkeypatch):
    import core.context_retrieval as cr
    import core.embedding_service as es
    import core.runtime_paths as rp
    from storage.migrations import run_migrations
    from tests.test_p300_fresh_acceptance_20260930 import _make_stub_embed_stamped
    prior=rp._VOOL_HOME_OVERRIDE
    home=tmp_path/'pottery-home'; home.mkdir()
    monkeypatch.setenv('VOOL_HOME',str(home)); monkeypatch.setenv('VOOL_HOME',str(home))
    monkeypatch.setenv('VOOL_CONTEXT_CAPSULE_V2','1')
    rp.configure_runtime_home(home)
    monkeypatch.setattr(es,'_best_embed_model',lambda:'acceptance-stub-model')
    monkeypatch.setattr(cr,'embed_stamped',_make_stub_embed_stamped(POTTERY_TOPIC))
    try:
        run_migrations()
        yield home
    finally:
        rp.configure_runtime_home(prior)

def prepare_pottery(home, *, only_foreign=False):
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy
    from core.context_retrieval import store_turn
    ensure_chat_namespace('pottery-evenings',grant_current_receipts=False)
    ensure_chat_namespace('pottery-neighbour',grant_current_receipts=False)
    source='pottery-neighbour' if only_foreign else 'pottery-evenings'
    turns=[(POTTERY_USER,'Noted — apron, wire cutter and sponge for pottery.'),*DISTRACTORS]
    for index,(user,assistant) in enumerate(turns):
        receipt=store_turn(source,user,assistant,access_policy=resolve_memory_access_policy(chat_id=source),
            source_context={'chat_id':source,'runtime_home':str(home),
                            'statement_at':dt.datetime(2026,8,10+index,tzinfo=dt.timezone.utc).timestamp()})
        assert receipt['status'] in {'stored','retained'},receipt

def ask_pottery(home):
    from core.memory.entries import resolve_memory_access_policy
    from core.context_retrieval import inject_retrieved, get_last_retrieval_telemetry
    out=inject_retrieved('pottery-evenings',POTTERY_Q,[{'role':'user','content':POTTERY_Q}],
        access_policy=resolve_memory_access_policy(chat_id='pottery-evenings'),
        source_context={'chat_id':'pottery-evenings','runtime_home':str(home),'surface':'channel','platform':'api'})
    text='\n'.join(str(m.get('content','')) for m in out if '<retrieved_context>' in str(m.get('content','')))
    return text,get_last_retrieval_telemetry()

def test_fresh_advice_preference_with_distractors(pottery_home):
    prepare_pottery(pottery_home)
    text,telemetry=ask_pottery(pottery_home)
    assert 'wire cutter' in text,text
    assert any(line.startswith('- user said') and 'canvas apron' in line for line in text.splitlines()),text
    assert 'glaze sprayer' not in text,text
    assert any(ref.get('delivered') and ref.get('role')=='user' for ref in telemetry.get('evidence_refs',[])),telemetry

def test_fresh_advice_foreign_preference_excluded(pottery_home):
    prepare_pottery(pottery_home,only_foreign=True)
    text,_=ask_pottery(pottery_home)
    assert 'wire cutter' not in text,text
