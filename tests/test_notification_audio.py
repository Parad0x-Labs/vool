"""Synthetic runtime events through real persistence and served owner routes."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from tests.pa_beta_gate._pc_calendar_rig import api_get, api_post, prepare_home

@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('VOOL_MODEL_RADAR_POLLER', '0')
    return prepare_home(tmp_path, monkeypatch)

def event(turn='export', kind='task_completed', chat='chat-a'):
    from core.runtime_task_events import emit_runtime_event
    from core.mode_permission_policy import register_external_approval
    token = register_external_approval({'session_id': chat, 'task_id': turn}) if kind == 'task_pending_approval' else ''
    emit_runtime_event({'session_id':chat, 'cancel_turn_id':turn}, event_type=kind,
                       message='Synthetic bounded work', details={'approval_request':{'approval_id':token}} if kind=='task_pending_approval' else {})

def audio(after=0):
    status, result = api_post('/api/notifications/audio', {'after':after})
    assert status==200, result
    return result['notification_ids']

def test_completion_and_other_chat_approval_claim_once(home):
    event()
    event()
    event('permission', 'task_pending_approval', 'chat-b')
    items=api_get('/api/notifications')[1]['items']
    assert {(i['session_id'],i['payload']['turn_id']) for i in items}=={('chat-a','export'),('chat-b','permission')}
    with ThreadPoolExecutor(2) as pool:
        deliveries=list(pool.map(lambda _:audio(), range(2)))
    assert sum(map(len,deliveries))==2
    assert audio()==[]
    event('distinct')
    assert len(audio())==1

def test_mute_restart_unmute_no_history_burst(home):
    from core.operator import notification_center as c
    assert c.load_preferences()['sound'] is True
    assert api_post('/api/notifications/preferences', {'preferences':{'sound':False}})[1]['ok']
    event('muted')
    assert c.load_preferences()['sound'] is False
    assert audio()==[]
    assert api_post('/api/notifications/preferences', {'preferences':{'sound':True}})[1]['ok']
    assert audio()==[]
    event('new')
    assert len(audio())==1
    assert api_post('/api/notifications/preferences', {'preferences':{'sound':'false'}})[0]==400

def test_closed_action_and_failure_never_sound(home):
    event('action','task_pending_approval')
    event('action','task_cancelled')
    event('failed','task_failed')
    event('partial','tool_executed')
    assert audio()==[]
    items=api_get('/api/notifications')[1]['items']
    assert not any(i['payload']['event_type']=='task_pending_approval' for i in items)

def test_native_owns_even_denied_and_quiet_hours(home):
    api_post('/api/notifications/preferences', {'preferences':{'native_notifications':True}})
    event('native')
    assert audio()==[]
    from core.operator import notification_center as c
    api_post('/api/notifications/preferences', {'preferences':{'native_notifications':False,'quiet_hours':{'enabled':True,'start':'00:00','end':'23:59'}}})
    event('quiet')
    assert audio()==[]

def test_history_before_cursor_and_dispatch_mute(home):
    event('old')
    cursor=api_get('/api/notifications')[1]['cursor']
    assert audio(cursor)==[]
    event('pending')
    api_post('/api/notifications/preferences',{'preferences':{'sound':False}})
    assert audio(cursor)==[]
    api_post('/api/notifications/preferences',{'preferences':{'sound':True}})
    assert audio(cursor)==[]
    assert api_post('/api/notifications/audio',{'after':True})[0]==400
    assert api_post('/api/notifications/audio',{'after':0},host='remote.test')[0]==403


def test_actual_permission_authority_revokes_pending_sound(home):
    from core.mode_permission_policy import resolve_approval
    event('revoke', 'task_pending_approval')
    item=api_get('/api/notifications')[1]['items'][0]
    assert resolve_approval(item['payload']['approval_id'],decision='deny')['status']=='denied'
    assert audio()==[]
    assert api_get('/api/notifications')[1]['items']==[]


def test_native_task_request_uses_chime_once_and_rehand_is_silent(home,monkeypatch):
    from tests.pa_beta_gate._pc_calendar_rig import Clock,T0
    clock=Clock(T0,monkeypatch)
    api_post('/api/notifications/preferences',{'preferences':{'native_notifications':True}})
    api_post('/api/notifications/native/report',{'bridge_id':'sound-native','events':[{'event':'settings','authorization':'authorized','sound':'enabled'}]})
    api_post('/api/notifications/native/outbox',{'bridge_id':'sound-native'})
    clock.advance(seconds=1)
    event('native-synthetic')
    requests=api_post('/api/notifications/native/outbox',{'bridge_id':'sound-native'})[1]['requests']
    assert len(requests)==1 and requests[0]['sound'] is True and requests[0]['sound_name']=='vool-pop.wav'
    assert audio()==[]
    assert api_post('/api/notifications/native/outbox',{'bridge_id':'sound-native'})[1]['requests']==[]
    clock.advance(seconds=61)
    rehand=api_post('/api/notifications/native/outbox',{'bridge_id':'sound-native'})[1]['requests']
    assert len(rehand)==1 and rehand[0]['sound'] is False


def test_waveform_matches_approved_soft_water_drop():
    import hashlib,io,wave,struct
    from core.notification_audio import sound_wav
    data=sound_wav()
    assert hashlib.sha256(data).hexdigest() == "8865db59e130762acb39ed75e5dc5dc7627788afe09662017dbb5c090fbd76a2"
    assert len(data)<7000
    with wave.open(io.BytesIO(data)) as wav:
        assert wav.getnchannels()==1 and wav.getsampwidth()==2
        assert wav.getframerate() == 16000 and wav.getnframes() == 3200
        values=struct.unpack('<'+'h'*wav.getnframes(),wav.readframes(wav.getnframes()))
        assert 500 < max(map(abs,values)) < 2100
        assert values[0] == 0 and abs(values[-1]) <= 1
        assert max(map(abs,values[2400:])) < 20
        assert sum(v*v for v in values[:960]) > 0.95 * sum(v*v for v in values)
        assert sound_wav() == data


def test_mute_refreshes_os_owned_future_timer_without_new_identifier(home,monkeypatch):
    from tests.pa_beta_gate._pc_calendar_rig import Clock,T0
    from core.operator import reminders
    clock=Clock(T0,monkeypatch)
    api_post('/api/notifications/preferences',{'preferences':{'native_notifications':True}})
    api_post('/api/notifications/native/report',{'bridge_id':'future','events':[{'event':'settings','authorization':'authorized'}]})
    from storage.db import get_connection
    reminders.schedule_reminder(tz_name='UTC',get_connection_fn=get_connection,session_id='future-chat',task_id='future-task',note='Synthetic reminder',due_at_utc=(T0.replace(hour=8)).isoformat())
    first=api_post('/api/notifications/native/outbox',{'bridge_id':'future'})[1]['requests'][0]
    api_post('/api/notifications/native/report',{'bridge_id':'future','events':[{'event':'submitted','identifier':first['identifier']}]})
    api_post('/api/notifications/preferences',{'preferences':{'sound':False}})
    muted=api_post('/api/notifications/native/outbox',{'bridge_id':'future'})[1]['requests']
    assert len(muted)==1 and muted[0]['identifier']==first['identifier'] and muted[0]['sound'] is False
    assert muted[0]['deliver_at_utc']==first['deliver_at_utc']


def test_concurrent_settings_patches_keep_both_preferences(home):
    from core.operator.notification_center import save_preferences,load_preferences
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(save_preferences,[{'sound':False},{'lock_screen':'hidden'}]))
    assert all(x['ok'] for x in results)
    prefs=load_preferences()
    assert prefs['sound'] is False and prefs['lock_screen']=='hidden'


def test_canonical_control_and_greeting_receipts_never_become_work_notifications(home):
    from core.runtime_task_events import emit_runtime_event
    for index,status in enumerate(('heartbeat_poll_fast_path','ui_command_fast_path','smalltalk_fast_path','hive_cleanup_noop')):
        emit_runtime_event({'session_id':'control-chat','cancel_turn_id':str(index)},event_type='task_completed',message='Terminal transport receipt',details={'status':status})
    assert api_get('/api/notifications')[1]['items']==[] and audio()==[]
