from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.test_x_actions_approval import _settings, fake_x


def _context(surface='desktop', local=True, request_id='request-card', session='cards-chat'):
    from core.turn_contract import TurnRequest

    ctx={'surface':surface,'_owner_local':local,'runtime_session_id':session}
    ctx['turn_request']=TurnRequest.from_ingress(user_text='Quote this post on X.',source_context=ctx,request_id=request_id,turn_id='turn-card',session_id=session)
    return ctx


def _propose(ctx):
    from core.runtime_execution_tools import execute_runtime_tool

    return execute_runtime_tool('x.action.propose',{'kind':'quote','post':'555','text':'Checkpoint resume makes interrupted work recoverable.'}, source_context=ctx)


def _cards(session='cards-chat', request_id='request-card', client_host='127.0.0.1', headers=None):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    res=dispatch_post(path='/api/x/cards',body={'session_id':session,'request_id':request_id},
                      headers={'Content-Type':'application/json',**(headers or {})},runtime=RuntimeServices(display_name='test'),model_name='fixture',client_host=client_host,workspace_root_provider=lambda:'.')
    return res.status,json.loads(res.body)


def test_card_hash_is_owner_local_and_never_in_the_model_result(fake_x):
    from core import x_actions

    out=_propose(_context())
    assert out.ok and 'hash' not in json.dumps(out.details)
    code,cards=_cards()
    assert code==200
    [card]=cards['cards']
    assert card['hash'] and card['account']['username']=='fixture_owner'
    assert card['weighted_length']==len(card['text'])
    assert _cards(request_id='another-turn')[1]['cards']==[]
    assert _cards(session='another-chat')[1]['cards']==[]
    assert _cards(client_host='198.51.100.1')[0]==403
    assert _cards(headers={'Origin':'http://other.example'})[0]==403
    assert _cards(headers={'Sec-Fetch-Site':'same-site'})[0]==403
    assert fake_x.writes()==[]


@pytest.mark.parametrize('surface,local',[('channel',False),('openclaw',True),('api',True),('desktop',False)])
def test_remote_and_relayed_proposals_never_receive_a_chat_card(fake_x,surface,local):
    # Real proposal persistence with remote-vs-local trust; the read permission remains
    # the runtime's own decision. Direct module calls do not bypass the final card filter.
    out=_propose(_context(surface,local))
    assert fake_x.writes()==[]
    assert _cards()[1]['cards']==[]


def test_settings_and_chat_approval_race_sends_exactly_once(fake_x):
    out=_propose(_context())
    [card]=_cards()[1]['cards']
    barrier=threading.Barrier(2)
    def approve():
        barrier.wait()
        return _settings({'action':'approve','action_id':card['id'],'hash':card['hash']})
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(approve); second=pool.submit(approve)
        results=[first.result(),second.result()]
    assert sum(code==200 for code,_ in results)==1
    assert len(fake_x.writes())==1
    assert all('hash' not in json.dumps(body) for _,body in results)
    assert out.ok


def test_each_proposed_reply_still_needs_its_own_approval(fake_x):
    from core.runtime_execution_tools import execute_runtime_tool

    one=execute_runtime_tool('x.action.propose',{'kind':'reply','post':'555','text':'First considered answer.'},source_context=_context())
    two=execute_runtime_tool('x.action.propose',{'kind':'reply','post':'555','text':'Second considered answer.'},source_context=_context())
    [first,second]=_cards()[1]['cards']
    code,sent=_settings({'action':'approve','action_id':first['id'],'hash':first['hash']})
    assert code==200 and sent['ok']
    assert len(fake_x.writes())==1
    assert next(c for c in _cards()[1]['cards'] if c['id']==second['id'])['state']=='pending'
    assert one.ok and two.ok


def test_notifications_hold_only_ids_and_close_when_the_action_is_declined(fake_x):
    from core import x_actions
    from core.operator import notification_center, notification_hub

    _propose(_context())
    conn=notification_center._default_connection()
    try:
        row=conn.execute("SELECT * FROM notification_items WHERE dedupe_key LIKE 'x-action:%'").fetchone()
        assert row is not None
        payload=json.loads(row['payload_json'])
        assert 'hash' not in payload and 'text' not in payload
        assert notification_hub.supersede_closed_action(conn,row,now_iso=notification_center._utcnow()) is False
        x_actions.decline(payload['x_action_id'])
        assert notification_hub.supersede_closed_action(conn,row,now_iso=notification_center._utcnow()) is True
        conn.commit()
    finally:
        conn.close()
