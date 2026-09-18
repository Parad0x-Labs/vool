"""Independent v3 review: real approval SQL/state/handlers, strict synthetic transports.

Native Notes calls are injected runner doubles. No owner app or live account is used.
"""
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import unquote
import json
import sqlite3
import subprocess

import pytest

from core.kas.contract import CalendarRefusedError, TransportUnknownError
from core.operator import calendar_provider as cp
from core.operator import approvals
from core.operator.models import OperatorActionIntent
from tests.pa_beta_gate.test_calendar_v2_independent_review import staged, adapter, response, event, execute


@pytest.mark.parametrize('title',['Packaging handover','Novel laboratory briefing'])
def test_google_lost_create_reply_reconciles_provider_id_without_second_post(title):
    config,row=staged('google',title)
    stored={};posts=[];receipts=[]
    def wire(req):
        if req.method=='POST':
            body=json.loads(req.body);posts.append(body['id'])
            if body['id'] in stored:return response({'error':{'code':409,'message':'The requested identifier already exists.'}},409)
            stored[body['id']]=dict(body,etag='"v1"')
            raise TransportUnknownError('reply_lost_after_acceptance')
        if '/events?' in req.url:return response({'items':[]})
        uid=unquote(req.url.rsplit('/',1)[-1])
        return response(stored[uid]) if uid in stored else response({},404)
    a=adapter('google',wire)
    def unknown(*args,**kw):row['status']='outcome_unproven'
    kwargs=dict(task_id='review-task',session_id='review-session',adapter=a,config=config,
        load_pending_action_fn=lambda **kw:row,pending_row=row,
        mark_action_executed_fn=lambda *args,**kw:receipts.append(kw),
        mark_outcome_unproven_fn=unknown,audit_log_fn=lambda *a,**k:None,claim_action_fn=lambda _:True)
    intent=OperatorActionIntent(kind='approve_calendar_event',action_id=row['action_id'])
    first=cp.execute_proposed_event(intent,**kwargs)
    assert first.status=='outcome_unproven' and len(stored)==1
    second=cp.execute_proposed_event(intent,**kwargs)
    assert second.ok and len(posts)==1,(second.status,second.response_text,posts)
    assert len(receipts)==1


@pytest.mark.parametrize('status_code',[429,503])
def test_reconcile_read_refusal_does_not_leave_live_claim_stuck_executing(tmp_path,status_code):
    config,row=staged('caldav');row['status']='outcome_unproven'
    path=tmp_path/'approvals.sqlite'
    def connection():
        conn=sqlite3.connect(path);conn.row_factory=sqlite3.Row;return conn
    conn=connection()
    conn.execute('CREATE TABLE operator_action_requests (action_id TEXT PRIMARY KEY, session_id TEXT, task_id TEXT, action_kind TEXT, scope_json TEXT, result_json TEXT, status TEXT, created_at TEXT, updated_at TEXT, executed_at TEXT)')
    conn.close()
    now=lambda:'2026-09-13T12:00:00+00:00'
    aid=approvals.create_pending_action(session_id='review-session',task_id='review-task',
        action_kind='provider_calendar_event',scope=json.loads(row['scope_json']),now_fn=now,get_connection_fn=connection)
    approvals.mark_action_outcome_unproven(aid,result={},now_fn=now,get_connection_fn=connection)
    def load():return cp.load_action_any_state(session_id='review-session',action_kind='provider_calendar_event',action_id=aid,get_connection_fn=connection)
    def unavailable(*a,**k):raise CalendarRefusedError(status_code,reason='rate_limited' if status_code==429 else 'http_503')
    result=cp.execute_proposed_event(OperatorActionIntent(kind='approve_calendar_event',action_id=aid),
        task_id='review-task',session_id='review-session',adapter=SimpleNamespace(provider_id='caldav',get_event=unavailable),
        config=config,pending_row=load(),load_pending_action_fn=lambda **k:load(),
        claim_action_fn=lambda key:approvals.claim_pending_action(key,now_fn=now,get_connection_fn=connection),
        mark_action_executed_fn=lambda *a,**k:None,
        mark_outcome_unproven_fn=lambda key,**kw:approvals.mark_action_outcome_unproven(key,now_fn=now,get_connection_fn=connection,**kw),
        audit_log_fn=lambda *a,**k:None)
    assert not result.ok
    assert load()['status']=='outcome_unproven',(result.status,load()['status'])


@pytest.mark.parametrize('wrong_end',['2026-09-15T13:30:00+00:00','2026-09-16T12:30:00+00:00'])
def test_reconciliation_does_not_certify_changed_duration(wrong_end):
    config,row=staged('caldav');row['status']='outcome_unproven';scope=json.loads(row['scope_json'])
    existing=replace(event(scope['intent_uid'],'caldav',scope['title']),end_utc=wrong_end)
    result=execute(row,config,SimpleNamespace(provider_id='caldav',get_event=lambda *a,**k:existing),claim=lambda _:True)
    assert not result.ok,(result.status,result.response_text,existing.end_utc,scope['end_utc'])


@pytest.mark.parametrize('title',['Supplier call','Novel equipment review'])
def test_uncertain_refire_rechecks_current_calendar_conflicts(title):
    config,row=staged('caldav',title);row['status']='outcome_unproven';scope=json.loads(row['scope_json'])
    created=[];reads=[]
    def get(cal,uid):
        if created:return created[0]
        raise CalendarRefusedError(404,reason='not_found')
    def conflicts(*a,**k):reads.append(k);return [event('newly-busy','caldav','Another meeting')]
    a=SimpleNamespace(provider_id='caldav',get_event=get,events_in_range=conflicts,
        create_event=lambda cal,e:(created.append(e) or e))
    result=execute(row,config,a,claim=lambda _:True)
    assert not created,(result.status,'Reconciliation created the event without checking the newly occupied slot')
    assert reads and result.status=='conflict'


@pytest.mark.parametrize('failure',['timeout-after-create','empty-reference-after-create'])
def test_notes_ambiguous_creation_is_not_retried_or_called_permission_denied(tmp_path,monkeypatch,failure):
    from core.operator import apple_notes,notes
    monkeypatch.setenv('VOOL_WORKSPACE_ROOT',str(tmp_path/'workspace'))
    accepted=[]
    real_create=apple_notes.create_apple_note
    def native_double(command,**kwargs):
        accepted.append(command)
        if failure=='timeout-after-create':raise subprocess.TimeoutExpired(command,kwargs['timeout'])
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setattr(apple_notes,'create_apple_note',lambda **kw:real_create(**kw,runner=native_double))
    intent=OperatorActionIntent(kind='save_note',raw_text='save a note to Apple Notes titled "Review" with: preserve this text')
    kwargs=dict(task_id='same-effect-task',session_id='same-session',
        evaluate_local_action_fn=lambda *a,**k:SimpleNamespace(mode='execute'),audit_log_fn=lambda *a,**k:None)
    first=notes.handle_save_note(intent,**kwargs)
    second=notes.handle_save_note(intent,**kwargs)
    assert len(accepted)==1,(len(accepted),first.status,first.response_text,second.response_text)
    assert not first.ok and first.status in {'outcome_unproven','delivery_unknown','unverified'}
    assert 'No note was created' not in first.response_text and 'not granted' not in first.response_text


def test_real_notes_denial_remains_distinct_from_ambiguity():
    from core.operator.apple_notes import create_apple_note
    denied=create_apple_note(title='control',body='safe',runner=lambda *a,**k:SimpleNamespace(returncode=1,stderr='Not authorized (-1743)',stdout=''))
    assert not denied['ok'] and denied['reason']=='os_permission_denied'


@pytest.mark.parametrize('selected',['work-calendar','personal-calendar'])
def test_eventkit_calendar_selection_uses_identifier_selector(selected):
    from datetime import datetime,timezone
    from core.kas.adapters.eventkit_mac import EventKitStore
    calendars=[SimpleNamespace(calendarIdentifier=lambda:'work-calendar'),
               SimpleNamespace(calendarIdentifier=lambda:'personal-calendar')]
    captured=[]
    def predicate(start,end,chosen):captured.append(chosen);return chosen
    store=EventKitStore.__new__(EventKitStore)
    store._ek=SimpleNamespace(EKEntityTypeEvent=0,EKEventStore=SimpleNamespace(authorizationStatusForEntityType_=lambda _:3))
    store._foundation=SimpleNamespace(NSDate=SimpleNamespace(dateWithTimeIntervalSince1970_=lambda value:value))
    store._store=SimpleNamespace(calendarsForEntityType_=lambda _:calendars,
        predicateForEventsWithStartDate_endDate_calendars_=predicate,
        eventsMatchingPredicate_=lambda chosen:list(chosen if chosen is not None else calendars))
    result=store.events_in_range(datetime(2026,9,15,tzinfo=timezone.utc),datetime(2026,9,16,tzinfo=timezone.utc),[selected])
    assert captured and captured[0] is not None,'Selected calendar became an unrestricted nil calendar predicate'
    assert len(result)==1 and result[0].calendarIdentifier()==selected
