"""A delayed runtime observation cannot resurrect a closed action in the inbox."""
import time
import pytest
from tests.pa_beta_gate._pc_calendar_rig import api_get, api_post, prepare_home

@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('VOOL_MODEL_RADAR_POLLER','0')
    return prepare_home(tmp_path,monkeypatch)

@pytest.mark.parametrize('closure',['denied','expired'])
def test_closed_before_observation_never_becomes_outstanding(home,closure):
    from core.mode_permission_policy import register_external_approval,resolve_approval
    from core.runtime_task_events import emit_runtime_event
    token=register_external_approval({'session_id':'chat-late','task_id':'delayed-permission',
        **({'expires_at':time.time()-1} if closure=='expired' else {})})
    if closure=='denied':
        assert resolve_approval(token,decision='deny')['status']=='denied'
    context={'session_id':'chat-late','cancel_turn_id':'delayed-permission'}
    emit_runtime_event(context,event_type='task_pending_approval',message='Delayed permission observation',
        details={'approval_request':{'approval_id':token}})
    assert api_post('/api/notifications/audio',{'after':0})[1]['notification_ids']==[]
    assert api_get('/api/notifications')[1]['items']==[]
    # A distinct later action still works; closing one request is not global mute.
    next_token=register_external_approval({'session_id':'chat-other','task_id':'active-permission'})
    emit_runtime_event({'session_id':'chat-other','cancel_turn_id':'active-permission'},event_type='task_pending_approval',
        message='An actually outstanding permission',details={'approval_request':{'approval_id':next_token}})
    pending=api_get('/api/notifications')[1]['items']
    assert len(pending)==1 and pending[0]['session_id']=='chat-other'
    assert pending[0]['payload']['approval_id']==next_token
    assert len(api_post('/api/notifications/audio',{'after':0})[1]['notification_ids'])==1
    assert api_post('/api/notifications/audio',{'after':0})[1]['notification_ids']==[]

@pytest.mark.parametrize('channel',['browser','native'])
def test_resolution_after_sync_before_claim_is_silent(home,monkeypatch,channel):
    from core.mode_permission_policy import register_external_approval,resolve_approval
    from core.runtime_task_events import emit_runtime_event
    from core.operator import notification_hub
    from tests.pa_beta_gate._pc_calendar_rig import Clock,T0
    if channel=='native':
        clock=Clock(T0,monkeypatch)
        api_post('/api/notifications/preferences',{'preferences':{'native_notifications':True}})
        api_post('/api/notifications/native/report',{'bridge_id':'review-native','events':[{'event':'settings','authorization':'authorized','sound':'enabled'}]})
        api_post('/api/notifications/native/outbox',{'bridge_id':'review-native'})
        clock.advance(seconds=1)
    token=register_external_approval({'session_id':'chat-race','task_id':'resolve-during-observation'})
    emit_runtime_event({'session_id':'chat-race','cancel_turn_id':'resolve-during-observation'},event_type='task_pending_approval',
        message='Permission resolved after inbox sync but before audio admission',details={'approval_request':{'approval_id':token}})
    original=notification_hub.sync_pending_actions
    def resolve_after_sync():
        original()
        assert resolve_approval(token,decision='deny')['status']=='denied'
    with monkeypatch.context() as patch:
        patch.setattr(notification_hub,'sync_pending_actions',resolve_after_sync)
        route='/api/notifications/audio' if channel=='browser' else '/api/notifications/native/outbox'
        status,result=api_post(route,{'after':0} if channel=='browser' else {'bridge_id':'review-native'})
        assert status==200
    if channel=='browser':
        assert result['notification_ids']==[]
    else:
        assert not any(request.get('sound') for request in result['requests'])
    assert api_get('/api/notifications')[1]['items']==[]
