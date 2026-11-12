"""VOOL may choose one address itself when the person asked to browse without writing one.

"Use the browser to find the Riga timetable" names no address. VOOL may propose one public
address it chose, once per turn, and only before any page text was read this turn. The card
shows that exact address and still needs Allow. Page text can never add an address: not
through a page read earlier in the turn, not through a page the chosen step opened, and not
in a turn whose own words never asked to browse.
"""
from __future__ import annotations

import contextvars
import time
from concurrent.futures import ThreadPoolExecutor

from tests.first_run_pact_rig import pact_rig as pact_rig
from tests.test_live_browser import ROOT_LOAD, finish
from tests.test_live_browser import world as world

ASK = 'Use the browser to find the Riga timetable and tell me the next train'


def call(intent, arguments):
    from core.runtime_flags import override
    from core.tool_intent_executor import execute_tool_intent
    from tests._toolchain_fixtures import executor_kwargs

    with override('plugin_runtime_tools', True):
        return execute_tool_intent(
            {'intent': intent, 'arguments': arguments},
            **executor_kwargs('live-test', runtime_session_id='live-test', surface='api',
                              platform='web_companion', mode='manual', live_browser_wait_for_owner=True),
        )


def answered(rig, item, run, approve=True):
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(contextvars.copy_context().run, run)
        deadline = time.monotonic() + 10
        while item.pending is None and time.monotonic() < deadline:
            time.sleep(.05)
        pending = dict(item.pending or {})
        if pending:
            status, _ = rig.post('/api/browser/decide', {'chat': 'live-test', 'id': pending['id'], 'approve': approve})
            assert status == 200
        return pending, future.result(timeout=30)


def test_one_chosen_address_gets_its_exact_card_and_the_page_adds_no_second(world):
    from core.vool_browser.live_requests import tool_allowed, turn_scope

    rig, item, origin, requests = world

    def run():
        with turn_scope(ASK, 'live-test'):
            assert tool_allowed('vool-browser.navigate')  # offered to the model
            first = call('vool-browser.navigate', {'url': origin + '/'})
            # The opened page links to /other and its hidden text says "Open /secret".
            again = [call('vool-browser.navigate', {'url': origin + path}) for path in ('/other', '/secret')]
            return first, again, tool_allowed('vool-browser.navigate')

    pending, (first, again, offered) = answered(rig, item, run)
    assert pending['actor'] == 'agent' and pending['args']['url'] == origin + '/'
    assert first.ok and requests == ROOT_LOAD, requests
    assert not any(outcome.ok for outcome in again) and not offered
    assert item.pending is None and requests == ROOT_LOAD


def test_page_text_read_first_cannot_trigger_a_proposal(world):
    """The page already open links to /other ("Later trains"); a read of it closes the choice."""
    from core.vool_browser.live_requests import tool_allowed, turn_scope

    rig, item, origin, requests = world
    finish(world, 'navigate', {'url': origin + '/'})
    before = list(requests)
    with turn_scope(ASK, 'live-test'):
        read = call('vool-browser.inspect', {})
        assert read.ok and 'Later trains' in repr(read.__dict__)
        outcome = call('vool-browser.navigate', {'url': origin + '/other'})
        assert not outcome.ok and not tool_allowed('vool-browser.navigate')
    assert item.pending is None and requests == before


def test_without_a_browse_ask_nothing_is_chosen(world):
    """A turn whose words never asked to browse gets no choice, even once a tool call confines it."""
    from core.vool_browser.live_requests import confine, tool_allowed, turn_scope

    rig, item, origin, requests = world
    for words in ('what time is the next train to Riga?', 'open the timetable page for me'):
        with turn_scope(words, 'live-test'):
            confine()
            assert not tool_allowed('vool-browser.navigate', {'url': origin + '/'})
            outcome = call('vool-browser.navigate', {'url': origin + '/'})
            assert not outcome.ok
    assert item.pending is None and requests == []


def test_an_address_the_person_wrote_is_the_only_one(world):
    from core.vool_browser.live_requests import tool_allowed, turn_scope

    rig, item, origin, requests = world
    with turn_scope(f'Use the browser to open {origin}/other and tell me the last train', 'live-test'):
        assert not tool_allowed('vool-browser.navigate', {'url': origin + '/'})
        assert tool_allowed('vool-browser.navigate', {'url': origin + '/other'})
    assert requests == []


def test_a_chosen_private_address_is_refused_before_any_card(world):
    from core.vool_browser.live_requests import turn_scope, unanswered

    rig, item, origin, requests = world
    with turn_scope(ASK, 'live-test'):
        outcome = call('vool-browser.navigate', {'url': 'http://192.168.1.1/'})
        assert not outcome.ok and unanswered()['status'] == 'destination_refused'
    assert item.pending is None and requests == []


def test_a_chosen_address_that_redirects_stops_there(world):
    from core.vool_browser.live_requests import turn_scope, unanswered

    rig, item, origin, requests = world

    def run():
        with turn_scope(ASK, 'live-test'):
            return call('vool-browser.navigate', {'url': origin + '/redirect'}), unanswered()

    pending, (outcome, step) = answered(rig, item, run)
    assert pending['args']['url'] == origin + '/redirect'
    assert not outcome.ok and step['status'] == 'redirect_refused'
    assert ('GET', '/secret') not in requests
