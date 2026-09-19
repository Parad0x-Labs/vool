"""Independent boundary probes. Synthetic providers/native runners; no owner app access."""
import http.client
import json
from types import SimpleNamespace

import pytest

from core.kas.contract import CalendarRefusedError
from core.kas.transport import build_transport
from core.operator import apple_notes, notes
from core.operator.models import OperatorActionIntent
from tests.pa_beta_gate.test_calendar_v2_independent_review import adapter, response
from tests.pa_beta_gate.test_v5_calendar_effect_phase import _stage, _stored
from tests.pa_beta_gate.test_v5_calendar_operation_ownership import _execute


def save(title, task):
    return notes.handle_save_note(OperatorActionIntent(kind='save_note', raw_text=f'save a note to Apple Notes titled "{title}" with: retain the delivery receipt'), task_id=task, session_id='independent-review', evaluate_local_action_fn=lambda *a,**k:SimpleNamespace(mode='execute'), audit_log_fn=lambda *a,**k:None)


@pytest.mark.parametrize('failure',['decode_after_create','terminated_after_create'])
def test_native_post_dispatch_failure_never_authorizes_another_create(tmp_path, monkeypatch, failure):
    monkeypatch.setenv('VOOL_WORKSPACE_ROOT',str(tmp_path))
    effects=[]
    def runner(command, **kwargs):
        effects.append('created-native-note')
        if len(effects)==1:
            if failure=='decode_after_create':
                raise UnicodeDecodeError('utf-8',b'\xff',0,1,'invalid completion bytes')
            return SimpleNamespace(returncode=-9, stdout='', stderr='')
        return SimpleNamespace(returncode=0,stdout='note id x-coredata://review/p2',stderr='')
    real=apple_notes.create_apple_note
    monkeypatch.setattr(apple_notes,'create_apple_note',lambda **kw:real(**kw,runner=runner))
    first=save('Pump inspection '+failure,'original')
    second=save('Pump inspection '+failure,'explicit-followup')
    assert len(effects)==1, {'first':first.response_text,'second':second.response_text,'effects':effects}
    assert not first.ok and first.details['delivery_state']=='unresolved'


@pytest.mark.parametrize('payload',[{'unexpected':'shape'},{'value':[{'id':'opaque-busy','start':{'dateTime':'not-a-date'},'end':{'dateTime':'also-invalid'}}]}])
def test_unreadable_calendar_is_not_empty_availability(payload):
    graph=adapter('graph',lambda req:response(payload))
    try:
        rows=graph.events_in_range('work',start_utc='2026-09-15T12:00:00+00:00',end_utc='2026-09-15T13:00:00+00:00')
        from datetime import datetime

        from core.operator.calendar_provider import compute_free_slots
        slots=compute_free_slots(rows,window_start=datetime.fromisoformat('2026-09-15T12:00:00+00:00'),window_end=datetime.fromisoformat('2026-09-15T13:00:00+00:00'),duration_minutes=30,tz_name='UTC')
    except (CalendarRefusedError,ValueError,TypeError) as exc:
        # A typed refusal is required, not a downstream parse accident.
        assert isinstance(exc,CalendarRefusedError), repr(exc)
        return
    pytest.fail(f'unreadable calendar became usable rows={rows!r}; slots={slots!r}')


@pytest.mark.parametrize('provider',['google','graph'])
def test_success_status_with_missing_event_payload_is_not_not_found(provider):
    instance=adapter(provider,lambda req:response({}))
    with pytest.raises(CalendarRefusedError) as captured:
        instance.get_event('work','review-event-id')
    assert captured.value.reason!='not_found', 'HTTP 200 with no event is unreadable, not proof of absence'


@pytest.mark.parametrize('failure',['timeout','incomplete'])
def test_transport_keeps_accepted_phase_before_reading_response_body(tmp_path,monkeypatch,failure):
    path,aid,config,scope=_stage(tmp_path,provider='graph',title='Transformer review '+failure)
    from core import remote_fetch_policy
    class Reply:
        status=201
        headers={}
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def read(self,*a):
            if failure=='timeout':raise TimeoutError('body stalled after headers')
            raise http.client.IncompleteRead(b'{',42)
    def opened(url,**kwargs):
        assert kwargs['method']=='POST'
        return Reply()
    monkeypatch.setattr(remote_fetch_policy,'open_remote_url',opened)
    transport=build_transport(provider_id='graph',allowed_hosts=('calendar.example.test',))
    def wire(req):
        if req.method=='GET': return response({'value':[]})
        return transport(req)
    result=_execute(path,aid,config,adapter('graph',wire))
    state,evidence=_stored(path,aid)
    assert state=='outcome_unproven',result.response_text
    assert evidence['operation']['phase']=='accepted', evidence


@pytest.mark.parametrize('missing',['note_reference','state_evidence'])
def test_semantically_damaged_confirmed_journal_cannot_claim_delivery(tmp_path,monkeypatch,missing):
    monkeypatch.setenv('VOOL_WORKSPACE_ROOT',str(tmp_path))
    title='Damaged receipt '+missing
    body='retain the delivery receipt'
    key=notes._apple_note_effect_key(title=title,body=body,account='',folder='')
    opid=notes._apple_note_operation_id(session_id='independent-review',task_id='original',content_key=key)
    (tmp_path/'notes').mkdir()
    journal={'schema':notes._JOURNAL_SCHEMA,'operations':{opid:{'op_id':opid,'state':'confirmed','content_key':key}}}
    if missing=='state_evidence':journal['operations'][opid]['note_reference']={'not':'a reference'}
    (tmp_path/'notes'/'.apple-notes-effects.json').write_text(json.dumps(journal))
    calls=[]
    monkeypatch.setattr(apple_notes,'create_apple_note',lambda **kw:calls.append(kw))
    result=save(title,'original')
    assert not result.ok, result.response_text
    assert calls==[]


@pytest.mark.parametrize('provider',['google','graph'])
def test_genuinely_empty_calendar_stays_usable(provider):
    instance=adapter(provider,lambda req:response({'items':[]} if provider=='google' else {'value':[]}))
    assert instance.events_in_range('work',start_utc='2026-09-15T12:00:00+00:00',end_utc='2026-09-15T13:00:00+00:00')==[]
