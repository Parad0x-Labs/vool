"""Independent provider boundary checks; synthetic protocol/OS doubles, no live accounts.

HTTP doubles enforce documented wire rules rather than echoing the candidate's assumptions.
The EventKit objects expose methods as PyObjC does; they are NOT native runtime proof.
"""
import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from core.kas.adapters._json_calendars import cal_to_json, json_event_to_cal
from core.kas.adapters.google_calendar import GoogleCalendarAdapter
from core.kas.adapters.graph_calendar import GraphCalendarAdapter
from core.kas.contract import AdapterConfig, CalCalendar, CalendarRefusedError, CalEvent, KasResponse
from core.operator.calendar_provider import CalendarProviderConfig, execute_proposed_event, propose_event
from core.operator.models import OperatorActionIntent


def response(body, status=200):
    return KasResponse(status=status, body=json.dumps(body).encode())


def event(uid='abc123def456', provider='google', title='Packaging review'):
    return CalEvent(provider_id=provider, calendar_id='work', uid=uid, summary=title,
                    start_utc='2026-09-15T12:00:00+00:00', end_utc='2026-09-15T12:30:00+00:00')


def payload(uid='abc123def456', provider='google', etag='"v1"'):
    body=cal_to_json(event(uid,provider), uid=uid, provider=provider)
    body['etag' if provider=='google' else '@odata.etag']=etag
    return body


def adapter(provider, transport):
    cls=GoogleCalendarAdapter if provider=='google' else GraphCalendarAdapter
    return cls(transport=transport, config=AdapterConfig(provider_id=provider, base_url='https://calendar.example.test/api'))


def staged(provider='google', title='Packaging review'):
    config=CalendarProviderConfig(provider,'https://calendar.example.test/api','account-A','work','10')
    captured={}
    def save(**kw):
        captured.update(kw)
        return 'proposal-1'
    no_conflicts=SimpleNamespace(list_calendars=lambda:[CalCalendar(provider,'work','Work')], events_in_range=lambda *a,**k:[])
    when=SimpleNamespace(ok=True,due_at_utc='2026-09-15T12:00:00+00:00',tz_name='UTC',due_wall='2026-09-15 12:00')
    result=propose_event(OperatorActionIntent(kind='propose_calendar_event',raw_text=f'propose "{title}" Tuesday at 12:00 for 30 minutes'),task_id='synthetic-task',session_id='synthetic-session',config=config,adapter=no_conflicts,parse_when_fn=lambda x:when,now_fn=None,create_pending_action_fn=save,evaluate_local_action_fn=None,audit_log_fn=lambda *a,**k:None)
    assert result.status=='approval_required',result.response_text
    row={'status':'pending_approval','action_id':'proposal-1','scope_json':json.dumps(captured['scope'])}
    return config,row


def execute(row,config,provider_adapter,claim=None):
    return execute_proposed_event(OperatorActionIntent(kind='approve_calendar_event',action_id='proposal-1'),
        task_id='synthetic-task',session_id='synthetic-session',adapter=provider_adapter,
        load_pending_action_fn=lambda **k:row,pending_row=row,config=config,
        mark_action_executed_fn=lambda *a,**k:None,audit_log_fn=lambda *a,**k:None,
        claim_action_fn=claim)


@pytest.mark.parametrize('title',['Packaging review','Supplier handover'])
def test_google_real_proposal_id_is_accepted_by_strict_wire(title):
    config,row=staged(title=title)
    stored={}
    def wire(req):
        if req.method=='POST':
            body=json.loads(req.body)
            if not re.fullmatch('[a-v0-9]{5,1024}',body.get('id','')):
                return response({'error':{'message':'Invalid resource id value'}},400)
            stored[body['id']]=dict(body,etag='"v1"')
            return response(stored[body['id']],201)
        if '/events?' in req.url:return response({'items':[]})
        return response(next(iter(stored.values()))) if stored else response({},404)
    result=execute(row,config,adapter('google',wire))
    assert result.status=='executed',result.response_text
    assert len(stored)==1


@pytest.mark.parametrize('provider,token',[('google','"opaque-17"'),('graph','W/"opaque-23"')])
def test_update_preserves_exact_provider_etag(provider,token):
    body=payload(provider=provider,etag=token)
    writes=[]
    def wire(req):
        if req.method=='PATCH':
            writes.append(req.headers.get('If-Match'))
            if req.headers.get('If-Match')!=token:return response({},412)
        return response(body)
    a=adapter(provider,wire)
    current=a.get_event('work',body['id'])
    updated=a.update_event('work',replace(current,summary='Changed title'))
    assert writes==[token]
    assert updated.uid==body['id']


@pytest.mark.parametrize('title',['Planning','Novel supplier call'])
def test_graph_replayed_create_uses_documented_transaction_identity(title):
    stored={}; transactions={}
    def wire(req):
        if req.method=='POST':
            body=json.loads(req.body)
            tx=body.get('transactionId')
            if tx and tx in transactions:return response(stored[transactions[tx]],201)
            uid=f'provider-assigned-{len(stored)+1}'
            stored[uid]=dict(body,id=uid)
            if tx:transactions[tx]=uid
            return response(stored[uid],201)
        uid=req.url.rsplit('/',1)[-1]
        return response(stored[uid]) if uid in stored else response({},404)
    a=adapter('graph',wire)
    intent=event(uid='durable-intent@vool.local',provider='graph',title=title)
    first=a.create_event('work',intent)
    second=a.create_event('work',intent)
    assert len(stored)==1,f'Replayed one durable intent created {len(stored)} events'
    assert first.uid==second.uid


@pytest.mark.parametrize('provider',['google','graph'])
def test_approval_refuses_account_switch_at_same_api_url(provider):
    config,row=staged(provider=provider)
    changed=replace(config,auth_binding='account-B')
    writes=[]
    fake=SimpleNamespace(provider_id=provider,events_in_range=lambda *a,**k:[],
        create_event=lambda cal,e:(writes.append(e) or e),get_event=lambda cal,uid:event(uid,provider))
    result=execute(row,changed,fake)
    assert result.status=='provider_changed',f'Account B received approval staged for account A: {result.status}'
    assert not writes


@pytest.mark.parametrize('changed_provider',['google','graph'])
def test_uncertain_resume_rechecks_provider_binding_before_any_effect(changed_provider):
    config,row=staged(provider='caldav')
    row['status']='outcome_unproven'
    changed=replace(config,provider=changed_provider,base_url='https://different.example.test/api')
    writes=[]
    def missing(*a,**k):raise CalendarRefusedError(404,reason='not_found')
    def create(cal,e):
        writes.append(e)
        return e
    def get(cal,uid):
        if writes:return writes[0]
        return missing()
    fake=SimpleNamespace(provider_id=changed_provider,get_event=get,create_event=create)
    result=execute(row,changed,fake)
    assert result.status=='provider_changed',result.response_text
    assert not writes


def test_uncertain_resume_must_obtain_atomic_claim():
    config,row=staged(provider='caldav');row['status']='outcome_unproven'
    calls=[];writes=[]
    def claim(action_id):calls.append(action_id);return False
    def get(cal,uid):
        if writes:return writes[0]
        raise CalendarRefusedError(404,reason='not_found')
    fake=SimpleNamespace(provider_id='caldav',get_event=get,create_event=lambda cal,e:(writes.append(e) or e))
    result=execute(row,config,fake,claim=claim)
    assert not writes,f'Resume wrote despite a losing claim: {result.status}'
    assert calls==['proposal-1']


@pytest.mark.parametrize('provider',['google','graph'])
def test_pagination_cannot_silently_report_partial_availability(provider):
    pages=[]
    def wire(req):
        pages.append(req.url);n=len(pages)
        item=payload(uid=f'event{n:05}',provider=provider)
        if provider=='google':
            data={'items':[item]}
            if n<11:data['nextPageToken']=str(n+1)
        else:
            data={'value':[item]}
            if n<11:data['@odata.nextLink']=f'https://calendar.example.test/api/page/{n+1}'
        return response(data)
    try: found=adapter(provider,wire).events_in_range('work',start_utc=event().start_utc,end_utc=event().end_utc)
    except CalendarRefusedError:
        return  # Explicit incomplete-read refusal is safe; partial success is not.
    assert len(found)==11,f'{len(found)} of 11 events returned as a complete successful read'


@pytest.mark.parametrize('date,end',[('2026-09-15','2026-09-16'),('2026-10-08','2026-10-11')])
def test_graph_all_day_uses_graph_wire_shape(date,end):
    e=CalEvent(provider_id='graph',calendar_id='work',uid='day1',all_day=True,start_date=date,end_date=end)
    body=cal_to_json(e,uid='',provider='graph')
    assert body.get('isAllDay') is True,body
    assert body['start']['dateTime'].startswith(date+'T00:00:00')
    assert body['end']['dateTime'].startswith(end+'T00:00:00')
    assert 'date' not in body['start']


@pytest.mark.parametrize('date,end',[('2026-09-15','2026-09-16'),('2026-10-08','2026-10-11')])
def test_graph_all_day_read_preserves_date_semantics(date,end):
    data={'id':'day1','subject':'Away','isAllDay':True,
        'start':{'dateTime':date+'T00:00:00','timeZone':'UTC'},
        'end':{'dateTime':end+'T00:00:00','timeZone':'UTC'}}
    e=json_event_to_cal(data,provider_id='graph',calendar_id='work',etag='"v1"',href='')
    assert e.all_day and e.start_date==date and e.end_date==end,e


def test_eventkit_selector_projection_keeps_timed_event_and_real_id():
    from core.kas.adapters.eventkit_mac import _ek_to_cal
    class Date:
        def timeIntervalSince1970(self):return datetime(2026,9,15,12,tzinfo=timezone.utc).timestamp()
    class Calendar:
        def calendarIdentifier(self):return 'work'
    class NativeEvent:
        def eventIdentifier(self):return 'actual-event-id'
        def isAllDay(self):return False
        def lastModifiedDate(self):return 'actual-version'
        def startDate(self):return Date()
        def endDate(self):return Date()
        def calendar(self):return Calendar()
        def title(self):return 'Timed appointment'
        def notes(self):return ''
    e=_ek_to_cal(NativeEvent(),provider_id='eventkit',foundation=None)
    assert (e.uid,e.all_day,e.etag)==('actual-event-id',False,'actual-version'),e


def test_eventkit_calendar_source_is_a_selector():
    from core.kas.adapters.eventkit_mac import EventKitCalendarAdapter
    class Source:
        def title(self):return 'iCloud'
    class Calendar:
        def source(self):return Source()
        def calendarIdentifier(self):return 'work'
        def title(self):return 'Work'
    a=EventKitCalendarAdapter(transport=lambda req:None,config=AdapterConfig(provider_id='eventkit',base_url='eventkit://local'),store=SimpleNamespace(calendars=lambda:[Calendar()]))
    rows=a.list_calendars()
    assert rows[0].display_name=='Work [iCloud]'


def test_controls_valid_google_id_timed_graph_and_same_account_binding():
    from core.operator.calendar_provider import _binding_mismatch
    config,row=staged()
    assert _binding_mismatch(json.loads(row['scope_json']),config) is None
    e=json_event_to_cal(payload(provider='graph'),provider_id='graph',calendar_id='work',etag='',href='')
    assert e and not e.all_day and e.start_utc==event().start_utc
    assert re.fullmatch('[a-v0-9]{5,1024}',event().uid)


@pytest.mark.parametrize('prompt',[
    'check Tuesday afternoon for a free 30-minute slot and explain how a hash table works',
    'list calendars and explain why tides happen',
])
def test_operator_binder_cannot_swallow_an_independent_question(prompt):
    from core.agent_runtime.demand_ownership import lane_may_claim_whole_turn
    assert not lane_may_claim_whole_turn(prompt,'operator_action_dispatch'),prompt
