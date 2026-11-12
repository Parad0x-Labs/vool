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


def test_a_model_failure_after_an_approved_browser_step_does_not_deny_the_page_was_opened():
    """Mac run 2026-11-11: the page opened after Approve, qwen3:8b then timed out, the fallback was
    uncertified, and the refusal said "nothing this turn retrieved, computed or observed backs it"."""
    from core.final_answer_authorship import (
        REASON_BLOCKED_BEFORE_GENERATION,
        AuthorshipDecision,
        gate_authored_content,
        record_authorship_attempts,
        record_authorship_decision,
    )
    from core.vool_browser.live_requests import note_completed, turn_scope

    decision = AuthorshipDecision(eligible=False, author_role='answer_generation', task_class='direct',
                                  requested_model='ollama-local:qwen2.5:7b', selected_model='',
                                  reason=REASON_BLOCKED_BEFORE_GENERATION)
    turn = 'turn-browser-then-model-timeout'
    context = {'cancel_turn_id': turn, 'request_id': 'req-' + turn}
    with turn_scope(ASK, 'reply-test'):
        record_authorship_decision(context, decision, blocked_model='ollama-local:qwen2.5:7b', request_text=ASK)
        record_authorship_attempts(context, ('ollama-local:qwen3:8b',))
        note_completed('navigate', URL)
        gated, payload = gate_authored_content(INVENTED, turn_id=turn)
        again, _ = gate_authored_content(gated, turn_id=turn)
    assert payload['publication'] == 'refused' and again == gated
    assert gated.startswith("I can't publish this answer: I opened " + URL + ' in the browser')
    assert 'nothing this turn retrieved, computed or observed' not in gated
    assert 'qwen3:8b' in gated and 'still open in the Browser pane' in gated and '14:36' not in gated
    # Without a browser step the refusal keeps its former words.
    assert 'nothing this turn retrieved, computed or observed' in decision.refusal_text()


@pytest.mark.parametrize('status,needle', [
    ('destination_refused', 'only opens public web addresses'),
    ('page_failed', 'the page did not load'),
    ('redirect_refused', 'sends visitors on to a different address'),
    ('page_too_large', 'too large for the browser to show'),
    ('download_refused', 'is a file rather than a page'),
])
def test_a_browser_step_that_ran_but_failed_gets_its_own_reply(status, needle):
    from core.vool_browser.live_requests import note_unanswered, turn_scope

    with turn_scope(ASK, 'reply-test'):
        note_unanswered('navigate', URL, status)
        out = grounded_twice(ASK)
    assert needle in out['response'] and URL in out['response'] and '14:36' not in out['response']


def test_any_answer_about_a_page_that_never_opened_is_replaced():
    """Mac run 3: after an expired card qwen3:8b answered "that is not mentioned in the records I
    have", which the old wording check let through because it does not describe the address."""
    from core.vool_browser.live_requests import note_completed, note_unanswered, turn_scope

    ask = 'pls use teh browser to check timetable.example/today and tell me the next train'
    for guess in ('That is not mentioned in the records I have.', INVENTED, '…'):
        with turn_scope(ask, 'reply-test'):
            note_unanswered('navigate', URL, 'stale_approval')
            out = grounded_twice(ask) if guess == INVENTED else enforce_url_grounding(
                {'response': guess}, user_input=ask, fetch_attempts=0)
            again = enforce_url_grounding(dict(out), user_input=ask, fetch_attempts=0)
        assert 'not approved within two minutes' in out['response'] and again == out
    # A later step of the same turn that did open a page keeps the model's own answer.
    with turn_scope(ask, 'reply-test'):
        note_unanswered('navigate', URL, 'stale_approval')
        note_completed('navigate', URL)
        out = enforce_url_grounding({'response': 'The page lists the 14:36 to Riga.'}, user_input=ask, fetch_attempts=1)
    assert out['response'] == 'The page lists the 14:36 to Riga.'


@pytest.mark.parametrize('address', ['http://192.168.1.1/', 'http://localhost:8080/admin', 'http://router.lan/',
                                     'http://[::1]/', 'http://10.0.0.7/status'])
def test_a_private_address_is_refused_before_any_card(world, address):
    """Mac run 3: 192.168.1.1 and localhost got an Allow card that could never run."""
    from core.runtime_flags import override
    from core.tool_intent_executor import execute_tool_intent
    from core.vool_browser.live_requests import turn_scope, unanswered
    from tests._toolchain_fixtures import executor_kwargs

    rig, item, origin, requests = world
    ask = f'open {address} in the browser and tell me what it says'
    with override('plugin_runtime_tools', True), turn_scope(ask, 'live-test'):
        started = time.monotonic()
        outcome = execute_tool_intent(
            {'intent': 'vool-browser.navigate', 'arguments': {'url': address}},
            **executor_kwargs('live-test', runtime_session_id='live-test', surface='api',
                              platform='web_companion', mode='manual', live_browser_wait_for_owner=True),
        )
        assert time.monotonic() - started < 5, 'no card may wait for an answer'
        assert not outcome.ok and item.pending is None and requests == []
        assert unanswered()['status'] == 'destination_refused'
        out = grounded_twice(ask)
    assert out['response'].startswith("I can't open ") and 'only opens public web addresses' in out['response']


@pytest.mark.parametrize('path,needle', [('/huge', 'too large for the browser to show'),
                                         ('/file', 'is a file rather than a page')])
def test_an_allowed_page_that_fails_to_load_reaches_the_reply(world, path, needle):
    from core.runtime_flags import override
    from core.tool_intent_executor import execute_tool_intent
    from core.vool_browser.live_requests import turn_scope, unanswered
    from tests._toolchain_fixtures import executor_kwargs

    rig, item, origin, requests = world
    ask = f'Use the browser to open {origin}{path} and tell me the next train'

    def run():
        with override('plugin_runtime_tools', True), turn_scope(ask, 'live-test'):
            outcome = execute_tool_intent(
                {'intent': 'vool-browser.navigate', 'arguments': {'url': origin + path}},
                **executor_kwargs('live-test', runtime_session_id='live-test', surface='api',
                                  platform='web_companion', mode='manual', live_browser_wait_for_owner=True),
            )
            return outcome, unanswered(), grounded_twice(ask)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(contextvars.copy_context().run, run)
        deadline = time.monotonic() + 10
        while item.pending is None and time.monotonic() < deadline:
            time.sleep(.05)
        assert item.pending and requests == []
        status, _ = rig.post('/api/browser/decide', {'chat': 'live-test', 'id': item.pending['id'], 'approve': True})
        assert status == 200
        outcome, step, out = future.result(timeout=30)
    assert not outcome.ok and step and step['url'] == origin + path
    assert needle in out['response'] and '14:36' not in out['response']


@pytest.mark.parametrize('status', ['refused', 'stale_approval', 'destination_refused', 'page_failed'])
def test_the_step_reply_survives_the_publication_gate(status):
    """Mac run 4: after Refuse and after expiry the step's own reply was replaced by "That is not
    mentioned in the records I have from our conversations, so I am not going to state one."."""
    from core.grounding_lifecycle import GroundingLifecycle, TurnIdentity
    from core.grounding_publication import _RECORDS_ABSENCE_MARKER, is_typed_refusal_text, publication_verdict
    from core.vool_browser.live_requests import note_unanswered, turn_scope

    with turn_scope(ASK, 'reply-test'):
        note_unanswered('navigate', URL, status)
        reply = grounded_twice(ASK)['response']
    # The records-absence lifecycle that replaced it on the Mac: model-authored, no rows.
    lifecycle = GroundingLifecycle(lifecycle_id='browser-' + status, identity=TurnIdentity(), request_text=ASK,
                                   model_authored=True, memory_records=({'summary': _RECORDS_ABSENCE_MARKER},))
    verdict = publication_verdict(lifecycle, reply)
    assert verdict.published and verdict.content == reply
    assert is_typed_refusal_text(reply)
    # A model's guess about the page gets no such pass.
    guess = publication_verdict(lifecycle, f'You said no to opening {URL}, but the next train is at 14:36.')
    assert '14:36' not in guess.content


def test_a_model_timeout_after_an_opened_page_says_so_plainly():
    """Mac run 4: after Approve the page opened, qwen3:8b hit its 60 s limit writing the answer,
    and the reply was "Incomplete — model synthesis failed (empty_synthesis) ... 3 of 3 tool steps
    succeeded. Last result: [Browser](...)"."""
    from core.agent_runtime.action_honesty_validator import browser_synthesis_failed_reply, is_browser_step_reply
    from core.grounding_lifecycle import GroundingLifecycle, TurnIdentity
    from core.grounding_publication import publication_verdict
    from core.vool_browser.live_requests import note_completed, turn_scope

    assert browser_synthesis_failed_reply() == ''
    with turn_scope(ASK, 'reply-test'):
        assert browser_synthesis_failed_reply() == ''
        note_completed('navigate', URL)
        note_completed('click', URL + '#geography')
        reply = browser_synthesis_failed_reply()
        again = enforce_url_grounding({'response': reply}, user_input=ASK, fetch_attempts=1)
    assert reply.startswith('I opened ' + URL + '#geography in the browser, but the model ran out of time')
    assert 'still open in the Browser pane' in reply and 'tool steps' not in reply and again['response'] == reply
    assert is_browser_step_reply(reply)
    lifecycle = GroundingLifecycle(lifecycle_id='browser-timeout', identity=TurnIdentity(), request_text=ASK, model_authored=True)
    assert publication_verdict(lifecycle, reply).content == reply


def test_a_model_timeout_after_reading_the_open_page_says_so_plainly(world):
    """Mac run 5, case 5: a follow-up only read the page already open (status, inspect), the model
    timed out, and the reply was still "Incomplete — model synthesis failed ... 2 of 2 tool steps"."""
    from core.agent_runtime.action_honesty_validator import browser_synthesis_failed_reply, is_browser_step_reply
    from core.vool_browser.live_requests import turn_scope
    from tests.test_live_browser import finish

    rig, item, origin, requests = world
    finish(world, 'navigate', {'url': origin + '/'})
    follow_up = 'use the browser and open the link for Later trains on that page'
    with turn_scope(follow_up, 'live-test'):
        assert browser_synthesis_failed_reply() == ''  # nothing read yet this turn
        from core.vool_browser.live_requests import tool_allowed

        tool_allowed('vool-browser.inspect', {})
        reply = browser_synthesis_failed_reply()
    assert reply.startswith('I read ' + origin + '/ in the browser, but the model ran out of time')
    assert is_browser_step_reply(reply) and browser_synthesis_failed_reply() == ''
