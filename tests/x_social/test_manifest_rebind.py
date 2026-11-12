"""Review r4 L1: rebinding tool evidence replaces the grounding manifest instead of stacking it."""
from __future__ import annotations

import json

from tests._credential_intelligence_support import isolated_home, vault_home


def _step(n):
    return {'mode': 'tool_executed', 'ok': True, 'tool_name': 'x.search', 'summary': 'Searched X.',
            'response_text': f'Found post {n} about checkpoint recovery.', 'observation': {'post': str(n)}}


def test_three_binds_leave_one_current_manifest(vault_home):
    from core.agent_runtime.research_tool_loop_facade import _bind_tool_step_evidence

    ctx = {'runtime_session_id': 'l1-chat', 'conversation_history': [
        {'role': 'user', 'content': 'Find checkpoint posts.'},
        {'role': 'user', 'content': 'Grounding observations for this turn:\n- one post'}]}
    steps = [_step(1)]
    for round_no in range(3):
        steps.append(_step(round_no + 2))
        _bind_tool_step_evidence(ctx, list(steps), query='checkpoint', task_id='l1-task', stage='tool_round')
    history = ctx['conversation_history']
    text = json.dumps(history)
    assert text.count('Evidence identity for this turn') == 1, history
    current = ctx['evidence_synthesis_binding']
    assert str(current.get('evidence_set_id')) in text
    assert history[0]['content'] == 'Find checkpoint posts.'
    assert history[-1]['content'].startswith('Grounding observations for this turn')


def test_the_same_bind_twice_does_not_add_a_second_manifest(vault_home):
    from core.agent_runtime.research_tool_loop_facade import _bind_tool_step_evidence

    ctx = {'runtime_session_id': 'l1-same', 'conversation_history': [{'role': 'user', 'content': 'Find posts.'}]}
    for _ in range(3):
        _bind_tool_step_evidence(ctx, [_step(1)], query='checkpoint', task_id='l1-same', stage='tool_round')
    history = ctx['conversation_history']
    assert json.dumps(history).count('Evidence identity for this turn') == 1, history
    assert len(history) == 2
