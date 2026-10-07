"""Untuned second acceptance, frozen after the repaired product code."""
import datetime as dt
import json
from pathlib import Path
import pytest
from core.agent_runtime.response import _validate_final_chat_output
from core.unsourced_current_claim import inspect_unsourced_current_claim
from tests.test_final_repair_fresh_acceptance import isolate_telemetry, context_for, pottery_home

CASES=json.loads(Path(__file__).with_name('final_repair_fresh2_cases.json').read_text())

@pytest.mark.parametrize('case',CASES,ids=[c['id'] for c in CASES])
def test_fresh2_final_delivery(case):
    delivered=_validate_final_chat_output(case['reply'],source_context=context_for(case))
    if case['keep']: assert delivered==case['reply'],delivered
    else: assert case['value'] not in delivered,delivered

@pytest.mark.parametrize('case',CASES,ids=[c['id'] for c in CASES])
def test_fresh2_earlier_owner(case):
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

PHOTO_TOPIC={'evening','twilight','photography','walks','camera','tripod','neck','strap','lens','cloth','pack','take','bring','bag'}
PHOTO_USER='For my evening photography walks I always pack my compact tripod and the neck strap, plus a lens cloth in my camera bag.'
PHOTO_QUOTE='The magazine says: "For my photography walks I always pack a flash bracket."'
PHOTO_Q='What should I take for my twilight photography walks?'

@pytest.fixture
def photo_home(monkeypatch,request):
    from tests import test_final_repair_fresh_acceptance as first
    monkeypatch.setattr(first,'POTTERY_TOPIC',PHOTO_TOPIC)
    return request.getfixturevalue('pottery_home')

def prepare_photo(home,foreign=False):
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy
    from core.context_retrieval import store_turn
    for name in ['photo-walks','photo-neighbour']:ensure_chat_namespace(name,grant_current_receipts=False)
    chat='photo-neighbour' if foreign else 'photo-walks'
    turns=[(PHOTO_USER,'Noted — compact tripod and neck strap.'),
           ('The camera repair took five hours last week.','Repair recorded.'),
           ('My heaviest camera case weighs 12 kilograms.','That case is heavy.'),
           (PHOTO_QUOTE,'That is the magazine writer’s choice.')]
    for index,(user,assistant) in enumerate(turns):
        receipt=store_turn(chat,user,assistant,access_policy=resolve_memory_access_policy(chat_id=chat),
            source_context={'chat_id':chat,'runtime_home':str(home),
                            'statement_at':dt.datetime(2026,7,12+index,tzinfo=dt.timezone.utc).timestamp()})
        assert receipt['status'] in {'stored','retained'},receipt

def ask_photo(home,question):
    from core.context_retrieval import inject_retrieved
    from core.memory.entries import resolve_memory_access_policy
    rows=inject_retrieved('photo-walks',question,[{'role':'user','content':question}],
        access_policy=resolve_memory_access_policy(chat_id='photo-walks'),
        source_context={'chat_id':'photo-walks','runtime_home':str(home),'surface':'channel','platform':'api'})
    return '\n'.join(m.get('content','') for m in rows if '<retrieved_context>' in m.get('content',''))

def test_fresh2_personal_advice_keeps_owned_preference(photo_home):
    prepare_photo(photo_home)
    text=ask_photo(photo_home,PHOTO_Q)
    assert 'neck strap' in text,text
    assert any(line.startswith('- user said') and 'compact tripod' in line for line in text.splitlines()),text
    assert 'flash bracket' not in text,text

def test_fresh2_explicit_source_advice_keeps_source_history(photo_home):
    prepare_photo(photo_home)
    text=ask_photo(photo_home,'Any suggestions about the magazine quote I mentioned about photography walks?')
    assert 'flash bracket' in text,text
    assert 'magazine says' in text,text

def test_fresh2_ungranted_photo_history_excluded(photo_home):
    prepare_photo(photo_home,foreign=True)
    text=ask_photo(photo_home,PHOTO_Q)
    assert 'neck strap' not in text,text
    assert 'compact tripod' not in text,text
