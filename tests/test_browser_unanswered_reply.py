"""When the browser step the model asked for never ran, the reply says why, truthfully.

The old reply said "You linked a URL and I did not open it on this turn ... Ask me to fetch
it" even when VOOL had asked to open the page and the request expired unseen, which blamed
the person. The reply now names what really happened, and the K-08 verify call of the same
backstop must leave it byte for byte unchanged.
"""
from __future__ import annotations

import contextvars
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.agent_runtime.action_honesty_validator import (
    _UNFETCHED_URL_RESPONSE,
    _UNFETCHED_URL_SENTINEL,
    enforce_url_grounding,
)
from tests.first_run_pact_rig import pact_rig as pact_rig
from tests.test_live_browser import world as world

URL = 'https://timetable.example/today'
ASK = f'Use the browser to open {URL} and tell me the next train'
INVENTED = 'The next train leaves at 14:36 from platform 2.'


def grounded_twice(user_input: str) -> dict:
    first = enforce_url_grounding({'response': INVENTED}, user_input=user_input, fetch_attempts=0)
    # The runtime's K-08 check calls the same backstop again on the sealed reply.
    second = enforce_url_grounding(dict(first), user_input=user_input, fetch_attempts=0)
    assert second['response'] == first['response']
    return first


@pytest.mark.parametrize('status,needles', [
    ('stale_approval', ['not approved within two minutes', 'press Approve once on the card that appears in this chat']),
    ('operation_timeout', ['not approved within two minutes']),
    ('refused', ['You said no']),
    ('paused', ['You took over the browser']),
    ('web_off', ['Web access was switched off', 'Settings']),
    ('cancelled', ['The browser stopped before']),
])
def test_unanswered_browser_step_gets_its_own_true_reply(status, needles):
    from core.vool_browser.live_requests import note_unanswered, turn_scope

    with turn_scope(ASK, 'reply-test'):
        note_unanswered('navigate', URL, status)
        out = grounded_twice(ASK)
    text = out['response']
    assert URL in text and _UNFETCHED_URL_SENTINEL in text.lower()
    assert all(needle in text for needle in needles), text
    assert 'Ask me to fetch it' not in text and '14:36' not in text
    assert out['url_grounding_validator']['reason'] == 'browser_request_not_approved'


def test_without_a_browser_step_the_plain_decline_stays():
    from core.vool_browser.live_requests import turn_scope

    with turn_scope(ASK, 'reply-test'):
        out = grounded_twice(ASK)
    assert out['response'] == _UNFETCHED_URL_RESPONSE
    assert out['url_grounding_validator']['reason'] == 'url_named_but_not_fetched'
    # Outside any turn nothing leaks in from an earlier one.
    out = grounded_twice(ASK)
    assert out['response'] == _UNFETCHED_URL_RESPONSE


def test_a_real_unanswered_request_reaches_the_reply(world, monkeypatch):
    """Real tool path: the model asks, nobody answers in time, the reply says so."""
    from core.runtime_flags import override
    from core.tool_intent_executor import execute_tool_intent
    from core.vool_browser.live_requests import turn_scope, unanswered
    from tests._toolchain_fixtures import executor_kwargs

    rig, item, origin, requests = world
    real = item.propose

    def short(op, args, **kwargs):
        # Two seconds instead of two minutes; everything else is the real proposal.
        result = real(op, args, **kwargs)
        item.pending['expires'] = result['proposal']['expires'] = time.monotonic() + 2
        return result

    monkeypatch.setattr(item, 'propose', short)
    ask = f'Use the browser to open {origin}/ and tell me the next train'
    with override('plugin_runtime_tools', True), turn_scope(ask, 'live-test'):
        started = time.monotonic()
        outcome = execute_tool_intent(
            {'intent': 'vool-browser.navigate', 'arguments': {'url': origin + '/'}},
            **executor_kwargs('live-test', runtime_session_id='live-test', surface='api',
                              platform='web_companion', mode='manual', live_browser_wait_for_owner=True),
        )
        assert time.monotonic() - started < 15
        assert not outcome.ok and requests == []
        step = unanswered()
        assert step == {'op': 'navigate', 'url': origin + '/', 'status': 'stale_approval'}
        out = grounded_twice(ask)
    assert 'not approved within two minutes' in out['response'] and origin + '/' in out['response']
    assert item.pending is None and requests == []


def test_a_refusal_in_the_pane_reaches_the_reply(world):
    from core.runtime_flags import override
    from core.tool_intent_executor import execute_tool_intent
    from core.vool_browser.live_requests import turn_scope, unanswered
    from tests._toolchain_fixtures import executor_kwargs

    rig, item, origin, requests = world
    ask = f'Use the browser to open {origin}/ and tell me the next train'

    def run():
        with override('plugin_runtime_tools', True), turn_scope(ask, 'live-test'):
            outcome = execute_tool_intent(
                {'intent': 'vool-browser.navigate', 'arguments': {'url': origin + '/'}},
                **executor_kwargs('live-test', runtime_session_id='live-test', surface='api',
                                  platform='web_companion', mode='manual', live_browser_wait_for_owner=True),
            )
            return outcome, unanswered(), grounded_twice(ask)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(contextvars.copy_context().run, run)
        deadline = time.monotonic() + 10
        while item.pending is None and time.monotonic() < deadline:
            time.sleep(.05)
        assert item.pending and item.pending['actor'] == 'agent' and requests == []
        status, _ = rig.post('/api/browser/decide', {'chat': 'live-test', 'id': item.pending['id'], 'approve': False})
        assert status == 200
        outcome, step, out = future.result(timeout=20)
    assert not outcome.ok and step['status'] == 'refused'
    assert out['response'].startswith('You said no to opening ' + origin + '/') and requests == []


def test_the_failed_action_message_names_the_unanswered_browser_step():
    """The runtime's own failed-action text (what the activity row shows) says what happened too."""
    from types import SimpleNamespace

    from core.agent_runtime.hive_topic_facade import HiveTopicFacadeMixin
    from core.vool_browser.live_requests import note_unanswered, turn_scope

    class Agent(HiveTopicFacadeMixin):
        def _session_hive_state(self, session_id):
            return {"pending_topic_ids": ["topic-9"]}

        def _interaction_pending_topic_ids(self, state):
            return []

    execution = SimpleNamespace(user_safe_response_text='', status='failed')
    with turn_scope(ASK, 'reply-test'):
        note_unanswered('navigate', URL, 'refused')
        text = Agent()._tool_failure_user_message(execution=execution, effective_input=ASK, session_id='s1')
    assert text == f'You said no to opening {URL} in the browser, so I did not open it on this turn.'
    # Without a browser step nothing changes: the generic text and the Hive offer stay as they were.
    text = Agent()._tool_failure_user_message(execution=execution, effective_input=ASK, session_id='s1')
    assert text.startswith("I wasn't able to turn that into a completed action") and 'Hive' in text
