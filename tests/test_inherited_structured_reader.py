import os
from pathlib import Path

import pytest

from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile


@pytest.mark.usefixtures('_hash_backend')
def test_structured_reader_after_failed_forget_stays_closed(tmp_path,monkeypatch):
    from core.memory.entries import add_memory_fact, list_memory_entries, resolve_memory_access_policy
    from core.memory.files import memory_entries_path
    from core.persistent_memory import maybe_handle_memory_command
    profile=_profile(tmp_path);chat='inherited-reader'
    _ingest(profile,chat,[(1760000000,'The notebook has a blue cover and paper pages.')])
    policy=resolve_memory_access_policy(chat_id=chat)
    assert add_memory_fact('My locker code is MINT-84.',session_id=chat,access_policy=policy)
    target=memory_entries_path();original=os.replace
    def denied(src,dst,*args,**kwargs):
        if Path(dst)==target:raise OSError('synthetic entries replacement denied')
        return original(src,dst,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(os,'replace',denied)
        try:
            command=maybe_handle_memory_command('forget MINT-84',session_id=chat,access_policy=policy,source_context={'chat_id':chat,'runtime_home':str(profile),'client_turn_id':'owner-turn'})
        except OSError as exc:
            command={'exception':type(exc).__name__}
    rows=list_memory_entries(access_policy=policy)
    print('BASELINE_READER',command,rows)
    assert 'MINT-84' not in str(rows)
