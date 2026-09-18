"""Independent semantic receipt probes; no native app, credentials or provider I/O."""
import json
from types import SimpleNamespace
import pytest
from core.operator import apple_notes, notes
from core.operator.models import OperatorActionIntent


def save(title, task='first'):
    return notes.handle_save_note(OperatorActionIntent(kind='save_note', raw_text=f'save a note to Apple Notes titled "{title}" with: retain the delivery receipt'), task_id=task, session_id='r6-independent', evaluate_local_action_fn=lambda *a,**k: SimpleNamespace(mode='execute'), audit_log_fn=lambda *a,**k:None)


def seed(root, title, *, reference='note id x-coredata://review/p1', legacy=False, wrong_content=False):
    key=notes._apple_note_effect_key(title=title,body='retain the delivery receipt',account='',folder='')
    opid=notes._apple_note_operation_id(session_id='r6-independent',task_id='first',content_key=key)
    record={'op_id':opid,'state':'confirmed','content_key':('a'*64 if wrong_content else key),'title':title,'session_id':'r6-independent','task_id':'first'}
    if legacy: record['legacy']=True
    if reference is not None: record['note_reference']=reference
    path=root/'notes'/'.apple-notes-effects.json';path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'schema':notes._JOURNAL_SCHEMA,'operations':{opid:record}}))
    return path, path.read_bytes()


@pytest.mark.parametrize('title,reference', [('Pump audit',None),('Harbour inspection','   ')])
def test_legacy_flag_cannot_exempt_a_current_operation_receipt(tmp_path,monkeypatch,title,reference):
    monkeypatch.setenv('VOOL_WORKSPACE_ROOT',str(tmp_path))
    path,raw=seed(tmp_path,title,reference=reference,legacy=True)
    calls=[]
    monkeypatch.setattr(apple_notes,'create_apple_note',lambda **kw:calls.append(kw))
    result=save(title)
    assert not result.ok, {'text':result.response_text,'details':result.details}
    assert calls==[]
    assert any(p.read_bytes()==raw for p in path.parent.glob('.apple-notes-effects.quarantine-*'))


@pytest.mark.parametrize('title', ['Boiler maintenance','Warehouse key register'])
def test_receipt_content_identity_must_match_the_requested_operation(tmp_path,monkeypatch,title):
    monkeypatch.setenv('VOOL_WORKSPACE_ROOT',str(tmp_path))
    path,raw=seed(tmp_path,title,wrong_content=True)
    calls=[]
    monkeypatch.setattr(apple_notes,'create_apple_note',lambda **kw:calls.append(kw))
    result=save(title)
    assert not result.ok, {'text':result.response_text,'details':result.details}
    assert calls==[]
    assert any(p.read_bytes()==raw for p in path.parent.glob('.apple-notes-effects.quarantine-*'))


def test_valid_current_receipt_replays_without_dispatch(tmp_path,monkeypatch):
    monkeypatch.setenv('VOOL_WORKSPACE_ROOT',str(tmp_path))
    seed(tmp_path,'Valid inspection')
    calls=[]
    monkeypatch.setattr(apple_notes,'create_apple_note',lambda **kw:calls.append(kw))
    result=save('Valid inspection')
    assert result.ok, result.response_text
    assert result.details['note_reference']=='note id x-coredata://review/p1'
    assert calls==[]
