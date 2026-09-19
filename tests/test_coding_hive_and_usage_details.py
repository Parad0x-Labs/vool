from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.agent_runtime import hive_followups
from core.response_provenance import format_provenance_footer
from core.response_usage_details import response_usage_details, usage_display_segments
from core.turn_model_call_ledger import (
    begin_turn,
    record_provider_call,
    record_provider_call_outcome,
    turn_call_accounting,
)

CODING_REQUEST = '''Review and fix this Python function. Work only with the pasted code—do not access files, run tools, or mention local paths.
Bug: updating a task sometimes changes the original input too.
Requirements:
- Never modify the input or share mutable nested data with it.
- Reject unknown task IDs with KeyError.

def update_tasks(tasks, updates):
    result = tasks.copy()
    for task_id, changes in updates.items():
        result[task_id].update(changes)
    return result
'''


@pytest.mark.parametrize('text', [CODING_REQUEST, 'Reject unknown task IDs with KeyError.',
    'Do not approve post post-abcdef12', 'Explain "approve post post-abcdef12"',
    '```\napprove post post-abcdef12\n```', 'approve unknown',
    'Review this example: reject topic topic-abcdef12',
    'approve post post-abcdef12 only if the tests pass'])
def test_non_commands_do_not_claim_hive(text):
    agent = Mock()
    agent._looks_like_hive_review_queue_command = hive_followups.looks_like_hive_review_queue_command
    agent._parse_hive_review_action = hive_followups.parse_hive_review_action
    agent._looks_like_hive_cleanup_command = hive_followups.looks_like_hive_cleanup_command
    assert hive_followups.maybe_handle_hive_review_command(agent, text, session_id='code', source_context={}) is None
    agent._handle_hive_review_action.assert_not_called()


@pytest.mark.parametrize('text', ['approve post post-1', 'Please approve post post-abcdef12.',
                                  'Hive reject topic topic-abcdef12', 'quarantine post-abcdef12'])
def test_explicit_moderation_remains_available(text):
    assert hive_followups.parse_hive_review_action(text) is not None


def response(balance='2999043', operation='op-one'):
    return SimpleNamespace(usage={'prompt_tokens': 1000, 'completion_tokens': 200}, provider_metadata={
        'vool_call_seconds': 5,
        'receipt': {'schema': 'vool.usepod.receipt.v1', 'operation_id': operation,
                    'pricing_unit': 'usdc_microunit',
                    'balance_remaining': {'raw': balance, 'decimal': balance, 'unit': 'USDC'},
                    'cost': {'usage_upper_bound_atomic': 1600, 'exact_atomic': None, 'unit': 'usdc_microunit'},
                    'usage': {'input_tokens': 1000, 'output_tokens': 200},
                    'price_ceiling_microunits_per_million': {'input': 800000, 'output': 4000000}}
    })


def test_per_response_bound_and_speed_never_read_balance():
    a = response_usage_details(response())
    b = response_usage_details(response('0.000001'))
    assert a == b
    assert a['amount'] == 0.0016
    assert float(a['input_bound']) == float(a['output_bound']) == 0.0008
    rendered = ' | '.join(usage_display_segments([a]))
    assert '40.0 output tok/s (request time)' in rendered
    assert '≤0.0016 USDC (upper bound)' in rendered
    assert 'charged' not in rendered
    assert 'input ≤0.0008 / output ≤0.0008 USDC' in rendered


def test_concurrent_agents_and_duplicate_terminal_events_are_isolated():
    contexts = [{'request_id': f'usage-demo-{i}'} for i in range(5)]
    def run(item):
        i, ctx = item
        begin_turn(ctx)
        call = record_provider_call(ctx, provider_id='usepod', model_id='astra', cost_class='paid_cloud')
        details = response_usage_details(response(operation=f'op-{i}'))
        assert record_provider_call_outcome(ctx, call, outcome='completed', usage_details=details)
        assert not record_provider_call_outcome(ctx, call, outcome='completed', usage_details=details)
        return turn_call_accounting(ctx)
    with ThreadPoolExecutor(max_workers=5) as pool:
        accounts = list(pool.map(run, enumerate(contexts)))
    for i, accounting in enumerate(accounts):
        assert len(accounting['usage_details']) == 1
        assert accounting['usage_details'][0]['operation_id'] == f'op-{i}'
        footer = format_provenance_footer({'model_execution': {'used_model': True}}, {
            'prompt_tokens': 1000, 'output_tokens': 200, 'model_id': 'astra', 'cost_class': 'paid_cloud'
        }, accounting)
        assert '≤0.0016 USDC' in footer
        assert '0.008 USDC' not in footer


def test_two_calls_same_answer_sum_and_unknown_retry_is_not_free():
    a = response_usage_details(response())
    rendered = ' | '.join(usage_display_segments([a, deepcopy(a)]))
    assert '≤0.0032 USDC' in rendered
    rendered = ' | '.join(usage_display_segments([a, {'cost_state': 'unreported'}]))
    assert 'reported calls ≤0.0016 USDC' in rendered
    assert 'some call costs unreported' in rendered
    assert 'tok/s' not in rendered


def test_exact_charge_is_distinct_from_bound_and_missing_timing():
    r = response()
    r.provider_metadata['receipt']['cost']['exact_atomic'] = 1200
    r.provider_metadata.pop('vool_call_seconds')
    rendered = ' | '.join(usage_display_segments([response_usage_details(r)]))
    assert '0.0012 USDC charged' in rendered
    assert 'tok/s' not in rendered


def test_openrouter_actual_cost_and_local_missing_cost():
    r = SimpleNamespace(usage={'cost': 0.001, 'completion_tokens': 10}, provider_metadata={})
    assert '0.001 USD charged' in usage_display_segments([response_usage_details(r)])
    r.usage = {'eval_count': 10}
    assert response_usage_details(r, cost_class='free_local')['cost_state'] == 'free'


def test_activity_projection_counts_input_and_deduplicates_response_events():
    from core.proof_projection import _projection_of_events
    event = {"event_type": "model_usage", "response_id": "unique-reply", "prompt_tokens": 1000, "output_tokens": 200,
             "turn_usage_details": [response_usage_details(response())]}
    projection = _projection_of_events([event, deepcopy(event)])
    assert projection["tokens"] == 1200
    assert len(projection["usage_details"]) == 1
    assert "≤0.0016 USDC" in " | ".join(usage_display_segments(projection["usage_details"]))


def test_malformed_observability_never_discards_an_answer():
    r = SimpleNamespace(usage={"cost": float("nan")}, provider_metadata={"receipt": "invalid"})
    assert response_usage_details(r) == {"cost_state": "unreported"}


def test_unfenced_and_normalized_paste_is_its_own_scope():
    from core.inline_payload import turn_supplies_its_own_content
    for text in (CODING_REQUEST, " ".join(CODING_REQUEST.split())):
        assert turn_supplies_its_own_content(text)
    assert not turn_supplies_its_own_content("Review the pasted code")
    assert not turn_supplies_its_own_content("Review project code in tasks.py")


def test_pasted_task_update_code_is_not_a_live_hive_task_update():
    from core.agent_runtime.hive_topic_mutation_detection import (
        looks_like_hive_topic_delete_request,
        looks_like_hive_topic_update_request,
    )
    agent = Mock()
    agent._looks_like_hive_topic_create_request.return_value = False
    assert not looks_like_hive_topic_update_request(agent, CODING_REQUEST)
    assert not looks_like_hive_topic_delete_request(agent, CODING_REQUEST.replace("Reject", "Delete"))
    assert looks_like_hive_topic_update_request(agent, "update hive task task-123 title to demo")
    assert looks_like_hive_topic_delete_request(agent, "delete hive task task-123")
