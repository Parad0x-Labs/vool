import pytest

from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile

pytestmark = pytest.mark.usefixtures('_hash_backend')


@pytest.fixture(autouse=True)
def _private_default_home(tmp_path, monkeypatch):
    """A forget here binds its chat's default runtime home into the erasure, and a failed
    verification fences that authority for the rest of the process, by design. Under pytest the
    default home is one directory for the whole session, so without this the fence outlived the
    test: the next file in the shard to write memory there failed with "forget authority lease
    was abandoned"."""
    from core import runtime_paths
    from core.runtime_paths import configure_runtime_home
    previous = runtime_paths._VOOL_HOME_OVERRIDE
    default = tmp_path / 'default-home'
    default.mkdir()
    monkeypatch.setenv('VOOL_HOME', str(default))
    monkeypatch.setenv('VOOL_WORKSPACE_ROOT', str(default / 'workspace'))
    configure_runtime_home(default)
    try:
        yield default
    finally:
        configure_runtime_home(previous)

def forget(home, chat, text):
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import maybe_handle_memory_command
    handled, reply = maybe_handle_memory_command(text, session_id=chat,
        access_policy=resolve_memory_access_policy(chat_id=chat),
        source_context={'chat_id':chat,'runtime_home':str(home),'client_turn_id':'owned-independent'})
    assert handled
    return reply

@pytest.mark.parametrize('reader', ['scope', 'single', 'strict_scope', 'rebuild', 'packet'])
def test_story_s4_receipts_cannot_serve_owned_subject_withdrawal(tmp_path, reader):
    from core.evidence_compiler import compile_packet, render
    from core.memory_receipts import rebuild_missing_receipts, receipt_for_occurrence, receipts_for_scope, write_receipt
    from core.vool_memory import VoolMemory
    home=_profile(tmp_path);chat='story-s4'
    body='My kiln locker code is 7428.'
    _ingest(home,chat,[(1760000000,body)])
    mem=VoolMemory(runtime_home=home)
    try:
        occ=mem.occurrence_search('kiln locker code',chat_scope=chat)[0][0]
        assert write_receipt(mem,occ,chat_scope=chat).status in {'written','already_exists'}
        assert '7428' in str(receipts_for_scope(mem,chat))
        if reader == 'rebuild':
            mem._conn.execute('DELETE FROM memory_receipts WHERE occurrence_id=?',(occ.occurrence_id,));mem._conn.commit()
        reply=forget(home,chat,'Forget the kiln locker code, it is wrong.')
        assert reply.startswith('Nothing was removed'), reply
        if reader == 'scope':result=receipts_for_scope(mem,chat)
        elif reader == 'single':result=receipt_for_occurrence(mem,occ.occurrence_id)
        elif reader == 'strict_scope':result=receipts_for_scope(mem,chat,strict=True)
        elif reader == 'packet':result=render(compile_packet(mem,chat,'What is my kiln locker code?'))
        else:
            rebuilt=rebuild_missing_receipts(mem,chat)
            result=receipt_for_occurrence(mem,occ.occurrence_id)
            print('REBUILT',rebuilt)
        print('STORY_S4',reader,str(reply),result)
        assert '7428' not in str(result)
    finally:mem.close()

def test_prompt_block_names_do_not_serve_during_pending_erasure(tmp_path, monkeypatch):
    from core.vool_memory import VoolMemory
    home=_profile(tmp_path);chat='block-name'
    _ingest(home,chat,[(1760000000,'The orchard key is silver.')])
    mem=VoolMemory(runtime_home=home)
    try:
        mem.block_write('mint84','The orchard key is silver.')
        assert 'mint84' in mem.all_block_names()
        from core.memory import carriers
        def unavailable(*args, **kwargs):
            raise OSError('final erasure verification unavailable')
        with monkeypatch.context() as patch:
            patch.setattr(carriers, 'verify_forget_stores', unavailable)
            reply=forget(home,chat,'forget MINT84')
        assert 'could not be verified' in reply,reply
        result=mem.all_block_names()
        print('BLOCK_NAMES',str(reply),result)
        assert 'mint84' not in result
    finally:mem.close()

@pytest.mark.parametrize('reader', ['single', 'strict_scope'])
def test_receipt_readers_withhold_during_exact_incomplete_erasure(tmp_path, monkeypatch, reader):
    import sqlite3

    import core.context_retrieval as cr
    from core.memory_receipts import receipt_for_occurrence, receipts_for_scope, write_receipt
    from core.vool_memory import VoolMemory
    home=_profile(tmp_path);chat='exact-receipt-pending'
    _ingest(home,chat,[(1760000000,'My locker code is MINT-84.')])
    mem=VoolMemory(runtime_home=home)
    try:
        occ=mem.occurrence_search('locker code',chat_scope=chat)[0][0]
        assert write_receipt(mem,occ,chat_scope=chat).status in {'written','already_exists'}
        def deny_read(action, first, *rest):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and first == 'memory_receipts' else sqlite3.SQLITE_OK
        mem._conn.set_authorizer(deny_read)
        with monkeypatch.context() as patch:
            patch.setattr(cr,'_open_memory_for_runtime',lambda home:mem)
            patch.setattr(mem,'close',lambda:None)
            reply=forget(home,chat,'forget MINT-84')
        mem._conn.set_authorizer(None)
        assert 'could not be verified' in reply
        assert receipts_for_scope(mem,chat)==[]
        result=receipt_for_occurrence(mem,occ.occurrence_id) if reader == 'single' else receipts_for_scope(mem,chat,strict=True)
        print('EXACT_RECEIPT',reader,str(reply),result)
        assert 'MINT-84' not in str(result)
    finally:
        mem._conn.set_authorizer(None)
        mem.close()

def test_partial_barrier_publication_withholds_the_other_selected_owner(tmp_path, monkeypatch):
    import os
    from pathlib import Path

    from core import runtime_paths
    from core.runtime_paths import configure_runtime_home
    from core.vool_memory import VoolMemory
    previous=runtime_paths._VOOL_HOME_OVERRIDE
    active=tmp_path/'a-active';selected=tmp_path/'z-selected'
    active.mkdir();selected.mkdir()
    monkeypatch.setenv('VOOL_HOME',str(active));monkeypatch.setenv('VOOL_WORKSPACE_ROOT',str(active/'workspace'))
    configure_runtime_home(active)
    mem=default=None
    try:
        default=VoolMemory(runtime_home=active)
        mem=VoolMemory(runtime_home=selected)
        chat='two-selected-owners'
        from core.context_namespace import ensure_chat_namespace
        ensure_chat_namespace(chat,grant_current_receipts=False)
        occ=mem.occurrence_store(chat_scope=chat,role='user',authority='observed-user-statement',
            body='My locker code is MINT84.',statement_at=1760000000)
        assert mem.occurrence_get(occ.occurrence_id).body=='My locker code is MINT84.'
        original=os.replace
        def deny_one_authority(src,dst,*args,**kwargs):
            if Path(dst)==selected/'.forget-authority/ledger.json':raise OSError('selected authority publication denied')
            return original(src,dst,*args,**kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(os,'replace',deny_one_authority)
            reply=forget(selected,chat,'forget MINT84')
        import json
        ledger=json.loads((active/'.forget-authority/ledger.json').read_text())
        assert any(row['status']=='pending' for row in ledger['operations'].values())
        assert 'could not be verified' in reply,reply
        retained=mem.occurrence_get(occ.occurrence_id)
        import subprocess
        import sys
        script='from core.vool_memory import VoolMemory; import sys,json; m=VoolMemory(runtime_home=sys.argv[1]); r=m.occurrence_get(sys.argv[2]); print(json.dumps(r.body if r else None)); m.close()'
        child=subprocess.run([sys.executable,'-c',script,str(selected),occ.occurrence_id],capture_output=True,text=True,timeout=20)
        assert child.returncode==0,child.stderr
        restarted=json.loads(child.stdout)
        print('PARTIAL_AUTHORITY',str(reply),retained,'RESTARTED',restarted)
        assert (retained is None or 'MINT84' not in retained.body) and (restarted is None or 'MINT84' not in restarted)
    finally:
        if mem:mem.close()
        if default:default.close()
        configure_runtime_home(previous)
