from __future__ import annotations

import sys
from unittest import mock

import pytest

from core.agent_runtime import agent, action_honesty_validator
from core import remote_fetch_policy
from core.semantic.semantic_result_seam import current_admission, reset_admission
from core.web.api import response_control


def _seal_with_observed_real_guards(question: str, response: str, *, fetches: int = 0,
                                   deterministic: bool = False):
    """Observe real transforms and admission without provider calls or network I/O."""
    reset_admission()
    events = []
    real_control = response_control.apply_exact_response_control
    real_ground = action_honesty_validator.enforce_url_grounding
    real_honesty = agent.enforce_final_action_honesty
    real_admit = agent.admit_semantic_result
    real_receipt = agent.emit_turn_honesty_receipt
    real_counter = remote_fetch_policy.remote_fetch_attempt_count
    counter_callers = []

    def counter():
        frame = sys._getframe(1)
        while frame is not None and frame.f_code.co_filename == mock.__file__:
            frame = frame.f_back
        counter_callers.append(frame.f_code.co_name if frame is not None else 'not_captured')
        return real_counter()

    def control(result, user_input):
        events.append(('response_control', str(result.get('response') or '')))
        return real_control(result, user_input)

    def ground(result, **kwargs):
        events.append(('url_grounding', str(result.get('response') or ''), kwargs['fetch_attempts']))
        return real_ground(result, **kwargs)

    def honesty(result, **kwargs):
        events.append(('action_honesty', str(result.get('response') or '')))
        return real_honesty(result, **kwargs)

    def admit(result, **kwargs):
        events.append(('admission', str(result.get('response') or '')))
        return real_admit(result, **kwargs)

    def receipt(result, **kwargs):
        events.append(('honesty_receipt', str(result.get('response') or '')))
        return real_receipt(result, **kwargs)

    with remote_fetch_policy.remote_fetch_policy_scope({'allow_remote_fetch': False}):
        # Report into the real turn-local ledger only; no remote transport is executed.
        for _ in range(fetches):
            remote_fetch_policy.note_remote_fetch_attempt('https://example.test/fixture')
        with mock.patch.object(response_control, 'apply_exact_response_control', side_effect=control), \
             mock.patch.object(action_honesty_validator, 'enforce_url_grounding', side_effect=ground) as grounding, \
             mock.patch.object(remote_fetch_policy, 'remote_fetch_attempt_count', side_effect=counter), \
             mock.patch.object(agent, 'enforce_final_action_honesty', side_effect=honesty), \
             mock.patch.object(agent, 'admit_semantic_result', side_effect=admit), \
             mock.patch.object(agent, 'emit_turn_honesty_receipt', side_effect=receipt):
            result = agent._seal_semantic_result(
                {'response': response, 'model_calls': 1, 'route_reason': 'model_lane_guard_control',
                 **({'deterministic': True} if deterministic else {})},
                session_id='synthetic-url-seal', user_input=question, source_context={'surface': 'cli'},
            )
            return result, events, grounding.call_count, counter_callers


def _assert_real_order(result, events, grounding_calls, counter_calls):
    assert grounding_calls == 1, 'actual pre-admission URL grounding was skipped'
    assert counter_calls.count('_seal_semantic_result') == 1, 'the sealing path did not consume its owning turn-local fetch counter exactly once'
    assert [x[0] for x in events] == [
        'response_control', 'url_grounding', 'action_honesty', 'admission', 'honesty_receipt',
    ]
    record = current_admission()
    assert record is not None and record.accepted
    assert record.content == result['response'] == events[-2][1] == events[-1][1]


def test_unfetched_url_claim_is_rewritten_before_real_admission():
    raw = 'The repository has three services and deserves a score of 9/10.'
    result, events, grounding_calls, counter_calls = _seal_with_observed_real_guards(
        'Review https://example.test/fixture and score this repository.', raw,
    )
    assert result['response'] == action_honesty_validator._UNFETCHED_URL_RESPONSE
    assert result['url_grounding_validator']['reason'] == 'url_named_but_not_fetched'
    _assert_real_order(result, events, grounding_calls, counter_calls)
    assert events[1][1] == raw and events[1][2] == 0
    assert events[2][1] != raw


def test_actual_turn_fetch_count_preserves_response_and_reaches_guard():
    raw = 'The fixture repository describes three independently tested services.'
    result, events, grounding_calls, counter_calls = _seal_with_observed_real_guards(
        'Review https://example.test/fixture.', raw, fetches=2,
    )
    _assert_real_order(result, events, grounding_calls, counter_calls)
    assert result['response'] == raw
    assert events[1][2] == 2
    assert 'url_grounding_validator' not in result


@pytest.mark.parametrize('question', [
    'Explain how to center a div.',
    'My project URL is https://example.test/fixture. Explain how to center a div.',
])
def test_non_review_requests_remain_unchanged_but_still_cross_guard(question):
    raw = 'Use flexbox with justify-content and align-items set to center.'
    result, events, grounding_calls, counter_calls = _seal_with_observed_real_guards(question, raw)
    _assert_real_order(result, events, grounding_calls, counter_calls)
    assert result['response'] == raw


def test_deterministic_url_result_preserves_existing_grounded_exemption():
    raw = 'A deterministic fixture response.'
    result, events, grounding_calls, counter_calls = _seal_with_observed_real_guards(
        'Review https://example.test/fixture.', raw, deterministic=True,
    )
    _assert_real_order(result, events, grounding_calls, counter_calls)
    assert result['response'] == raw


def test_explicit_literal_url_text_remains_literal_before_admission():
    raw = 'Review https://example.test/fixture.'
    question = 'Reply with exactly "Review https://example.test/fixture."'
    assert response_control.exact_response_target(question) == raw
    result, events, grounding_calls, counter_calls = _seal_with_observed_real_guards(
        question, raw,
    )
    _assert_real_order(result, events, grounding_calls, counter_calls)
    assert result['response'] == raw
    assert result['response_control']['changed'] is False
