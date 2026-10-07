"""Only direct review commands authorize moderation. Contributor: sls_0x."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.agent_runtime import hive_followups as hive

ORIGINAL = 'What is my March 10 approved record for Harbor Lantern? Return only the JSON object exactly as I saved it.'

@pytest.mark.parametrize('text', [
    ORIGINAL,
    'What was my rejected proposal for the April meeting?',
    'Describe the approved motion from the earlier discussion.',
    'Recall the rejected record from March 10.',
    'Quote "approve post post-abcdef12" exactly.',
    '`approve post post-abcdef12`',
    '{"role":"user","record":"approve post post-abcdef12"}',
    'Do not approve post post-abcdef12.',
    'We approved post post-abcdef12 yesterday.',
    'What does approve post post-abcdef12 mean?',
    'Please recall the approved record.',
    'Can you tell me which approved record I saved?',
    'Approve post post-abcdef12 and tell me the saved record.',
])
def test_history_quotes_data_prohibitions_and_mixed_requests_are_not_moderation_commands(text):
    assert hive.parse_hive_review_action(text) is None


@pytest.mark.parametrize('text,decision,kind,target', [
    ('approve post post-abcdef12', 'approve', 'post', 'post-abcdef12'),
    ('reject topic topic-abcdef12', 'void', 'topic', 'topic-abcdef12'),
    ('needs more evidence post post-abcdef12', 'review_required', 'post', 'post-abcdef12'),
    ('send back post post-abcdef12', 'review_required', 'post', 'post-abcdef12'),
    ('quarantine post post-abcdef12', 'quarantine', 'post', 'post-abcdef12'),
    ('void topic topic-abcdef12', 'void', 'topic', 'topic-abcdef12'),
    ('approved post post-abcdef12', 'approve', 'post', 'post-abcdef12'),
    ('Could you please approve post post-abcdef12?', 'approve', 'post', 'post-abcdef12'),
])
def test_existing_real_operator_commands_keep_exact_decision_and_target(text,decision,kind,target):
    parsed = hive.parse_hive_review_action(text)
    assert parsed is not None
    assert (parsed['decision'],parsed['object_type'],parsed['object_id']) == (decision,kind,target)


def fake_agent(enabled=True,write_enabled=True):
    bridge=SimpleNamespace(enabled=Mock(return_value=enabled),write_enabled=Mock(return_value=write_enabled),
        submit_public_moderation_review=Mock(return_value={'ok':True,'current_state':'approved','quorum_reached':False}))
    agent=SimpleNamespace(public_hive_bridge=bridge,_fast_path_result=lambda **kw:kw,
        _looks_like_hive_review_queue_command=hive.looks_like_hive_review_queue_command,
        _parse_hive_review_action=hive.parse_hive_review_action,
        _looks_like_hive_cleanup_command=hive.looks_like_hive_cleanup_command)
    agent._handle_hive_review_action=lambda text,**kw:hive.handle_hive_review_action(agent,text,**kw)
    return agent


@pytest.mark.parametrize('scope',['ordinary-owner-chat','foreign-chat-with-no-hive-authority'])
def test_actual_frontdoor_owner_cannot_submit_or_claim_historical_recall(scope):
    agent=fake_agent()
    result=hive.maybe_handle_hive_review_command(agent,ORIGINAL,session_id=scope,
        source_context={'chat_id':scope,'cross_chat_imports':[]})
    assert result is None
    agent.public_hive_bridge.enabled.assert_not_called()
    agent.public_hive_bridge.write_enabled.assert_not_called()
    agent.public_hive_bridge.submit_public_moderation_review.assert_not_called()


@pytest.mark.parametrize('enabled,writable,reason,calls',[
    (False,False,'hive_review_action_disabled',0),
    (True,False,'hive_review_action_write_disabled',0),
    (True,True,'hive_review_action',1),
])
def test_actual_action_handler_retains_bridge_enablement_and_write_authority(enabled,writable,reason,calls):
    agent=fake_agent(enabled,writable)
    result=hive.maybe_handle_hive_review_command(agent,'approve post post-abcdef12',session_id='operator-hive',
        source_context={'chat_id':'operator-hive'})
    assert result['reason']==reason
    assert agent.public_hive_bridge.submit_public_moderation_review.call_count==calls
    if calls:
        payload=agent.public_hive_bridge.submit_public_moderation_review.call_args.kwargs
        assert (payload['object_type'],payload['object_id'],payload['decision'])==('post','post-abcdef12','approve')
