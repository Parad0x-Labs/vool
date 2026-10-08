import json
from pathlib import Path

import pytest

from tests.test_question_date_time_leg_20261002 import _capsule, _hash_backend, _ingest, _profile

pytestmark = pytest.mark.usefixtures('_hash_backend')

def owned(profile, chat, text):
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import maybe_handle_memory_command
    context={'chat_id':chat,'runtime_home':str(profile),'client_turn_id':'owner-turn'}
    handled,reply=maybe_handle_memory_command(text,session_id=chat,access_policy=resolve_memory_access_policy(chat_id=chat),source_context=context)
    assert handled
    return reply,context


def test_failed_open_still_withholds_after_owner_recovers(tmp_path,monkeypatch):
    import core.context_retrieval as cr
    profile=_profile(tmp_path);chat='failed-open'
    _ingest(profile,chat,[(1760000000,'My locker code is MINT-84.')])
    with monkeypatch.context() as patch:
        patch.setattr(cr,'_open_memory_for_runtime',lambda home:None)
        reply,context=owned(profile,chat,'forget MINT-84')
    capsule=_capsule(profile,chat,'What is my locker code?')[0]
    print('OPEN_FAILURE',str(reply),capsule)
    assert 'MINT-84' not in capsule

def test_visible_transcript_cannot_recall_forgotten_value(tmp_path):
    from core.agent_runtime.same_chat_recall import same_chat_transcript_recall_fast_path
    profile=_profile(tmp_path);chat='visible-transcript'
    _ingest(profile,chat,[(1760000000,'The title is MINT-84.')])
    reply,context=owned(profile,chat,'forget MINT-84')
    context['conversation_history']=[{'role':'user','content':'Give me a title.'},{'role':'assistant','content':'Title: MINT-84'}]
    recalled=same_chat_transcript_recall_fast_path('What title did you give me earlier in this chat?',source_surface='api',source_context=context)
    print('VISIBLE',str(reply),recalled)
    assert recalled is None or 'MINT-84' not in recalled.response

def test_retained_structured_entry_is_not_served_while_incomplete(tmp_path,monkeypatch):
    import os

    from core.memory.entries import add_memory_fact, list_memory_entries, resolve_memory_access_policy
    from core.memory.files import memory_entries_path
    from core.persistent_memory import maybe_handle_memory_command
    profile=_profile(tmp_path);chat='retained-entry'
    _ingest(profile,chat,[(1760000000,'The notebook has a blue cover and paper pages.')])
    policy=resolve_memory_access_policy(chat_id=chat)
    assert add_memory_fact('My locker code is MINT-84.',session_id=chat,access_policy=policy)
    target=memory_entries_path();original=os.replace
    def denied(src,dst,*args,**kwargs):
        if Path(dst)==target:raise OSError('synthetic entries replacement denied')
        return original(src,dst,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(os,'replace',denied)
        reply,context=owned(profile,chat,'forget MINT-84')
    rows=list_memory_entries(access_policy=policy)
    handled,recall=maybe_handle_memory_command('What facts do you remember from this chat?',session_id=chat,access_policy=policy,source_context=context)
    print('ENTRY',str(reply),rows,handled,recall)
    assert 'MINT-84' not in str(rows) and 'MINT-84' not in str(recall)

def test_conversation_artifact_metadata_is_erased(tmp_path):
    from core.memory.files import append_jsonl, conversation_log_path, load_jsonl
    profile=_profile(tmp_path);chat='artifact-log'
    _ingest(profile,chat,[(1760000000,'My notebook is blue and has a paper cover.')])
    path=conversation_log_path()
    from core.persistent_memory import append_assistant_artifact_event
    assert append_assistant_artifact_event(session_id=chat,text='The notebook is blue.',artifact_kind='review',artifact={'stored_fact':'MINT-84'})
    reply,context=owned(profile,chat,'forget MINT-84')
    rows=load_jsonl(path)
    print('ARTIFACT',str(reply),rows)
    assert 'MINT-84' not in str(rows)

def test_concurrent_log_append_after_verified_scrub_is_not_complete(tmp_path,monkeypatch):
    import core.context_retrieval as cr
    from core.memory.files import append_jsonl, conversation_log_path, load_jsonl
    profile=_profile(tmp_path);chat='concurrent-log'
    _ingest(profile,chat,[(1760000000,'My locker code is MINT-84.')])
    original=cr.forget_session_memory
    def racing(*args,**kwargs):
        result=original(*args,**kwargs)
        from core.persistent_memory import append_assistant_artifact_event
        assert append_assistant_artifact_event(session_id=chat,text='The locker code is MINT-84.',artifact_kind='review')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(cr,'forget_session_memory',racing)
        reply,context=owned(profile,chat,'forget MINT-84')
    rows=load_jsonl(conversation_log_path())
    print('CONCURRENT',str(reply),rows)
    assert 'MINT-84' not in str(rows) or not str(reply).startswith('Forget applied')


