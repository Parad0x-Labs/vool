"""Review r4 M2: one batched card request per chat, polled only while a card is live."""
from __future__ import annotations

import json
import time

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.test_x_actions_approval import fake_x


def _cards(body, client_host='127.0.0.1'):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    res = dispatch_post(path='/api/x/cards', body=body, headers={'Content-Type': 'application/json'},
                        runtime=RuntimeServices(display_name='test'), model_name='fixture',
                        client_host=client_host, workspace_root_provider=lambda: '.')
    return res.status, json.loads(res.body)


def test_batched_card_request_returns_each_card_with_its_request(fake_x):
    from tests.x_social.test_cards import _context, _propose

    assert _propose(_context(request_id='turn-a')).ok
    assert _propose(_context(request_id='turn-b')).ok
    code, out = _cards({'session_id': 'cards-chat', 'request_ids': ['turn-a', 'turn-b', 'turn-none']})
    assert code == 200, out
    assert sorted(card['request_id'] for card in out['cards']) == ['turn-a', 'turn-b']
    assert _cards({'session_id': 'cards-chat', 'request_id': 'turn-a'})[1]['cards'][0]['request_id'] == 'turn-a'
    for bad in ({'session_id': 'cards-chat', 'request_ids': []},
                {'session_id': 'cards-chat', 'request_ids': ['x'] * 201},
                {'session_id': 'cards-chat', 'request_ids': 'turn-a'},
                {'session_id': 'cards-chat', 'request_ids': ['turn-a', 7]},
                {'session_id': 'cards-chat', 'request_id': 'turn-a', 'request_ids': ['turn-b']}):
        assert _cards(bad)[0] == 400, bad
    assert _cards({'session_id': 'cards-chat', 'request_ids': ['turn-a']}, client_host='198.51.100.9')[0] == 403
    assert fake_x.writes() == []


def test_page_flag_is_off_without_x_or_a_stored_card(vault_home):
    from core.x_social_fragment import render_x_social_fragment, x_social_active

    assert x_social_active() is False
    assert 'let active=false;' in render_x_social_fragment()
    assert 'let active=true;' in render_x_social_fragment(True)


_PAGE = '<!doctype html><html><body><div id="chat"></div>{fragment}</body></html>'


def _browser_page(active, answers):
    """Serve the fragment from a fixture origin and answer /api/x/cards from ``answers``."""
    from core.x_social_fragment import render_x_social_fragment
    from tests.served_browser import launch_chromium

    try:
        fragment = render_x_social_fragment(active)
    except TypeError:  # the r4 fragment took no flag; kept so this test can run against it
        fragment = render_x_social_fragment()
    manager, browser = launch_chromium()
    page = browser.new_page()
    page.clock.install()
    seen = []

    def cards(route):
        body = json.loads(route.request.post_data or '{}')
        seen.append(body)
        route.fulfill(status=200, content_type='application/json', body=json.dumps(answers(body)))

    page.route('http://vool.test/api/x/cards', cards)
    page.route('http://vool.test/chat', lambda route: route.fulfill(
        status=200, content_type='text/html', body=_PAGE.format(fragment=fragment)))
    page.goto('http://vool.test/chat')
    return manager, browser, page, seen


def _mount_history(page, count, chat='chat-one'):
    page.evaluate("""([count, chat]) => { const root = document.getElementById('chat');
      for (let i = 0; i < count; i++) { const m = document.createElement('div'); m.id = 'm' + i; root.appendChild(m);
        window.VoolXSocial.mount(m, chat, 'req-' + i); } }""", [count, chat])


def _card(request_id, state):
    return {'id': 'act-' + request_id, 'request_id': request_id, 'state': state, 'kind': 'post',
            'text': 'A draft.', 'account': {'username': 'owner'}, 'weighted_length': 8, 'hash': 'h'}


def _settle(page, seen, expected):
    """Let routed requests arrive (real time), then check the count is exactly ``expected``."""
    deadline = time.monotonic() + 10
    while len(seen) < expected and time.monotonic() < deadline:
        page.evaluate('1')
        time.sleep(0.05)
    for _ in range(6):
        page.evaluate('1')
        time.sleep(0.05)
    assert len(seen) == expected, seen


def test_chat_page_makes_no_card_request_when_x_is_off_and_no_card_exists(vault_home):
    manager, browser, page, seen = _browser_page(False, lambda body: {'ok': True, 'cards': []})
    try:
        _mount_history(page, 100)
        page.clock.run_for(30000)
        _settle(page, seen, 0)
    finally:
        browser.close()
        manager.stop()


def test_chat_page_batches_polls_only_live_cards_and_stops_after_empty(vault_home):
    state = {'card': 'pending'}

    def answers(body):
        return {'ok': True, 'cards': [_card('req-7', state['card'])] if state['card'] else []}

    manager, browser, page, seen = _browser_page(True, answers)
    activity = []

    def act(route):
        activity.append(json.loads(route.request.post_data or '{}'))
        state['card'] = 'declined'
        route.fulfill(status=200, content_type='application/json', body=json.dumps({'ok': True, 'message': 'Declined'}))

    page.route('http://vool.test/api/x/activity', act)
    try:
        _mount_history(page, 100)                  # a 100-answer chat: one batched request
        page.clock.run_for(100)
        _settle(page, seen, 1)
        assert len(seen[0].get('request_ids') or ()) == 100 and seen[0]['session_id'] == 'chat-one'
        page.wait_for_selector('#m7 .vx-approval')
        page.clock.run_for(3100)                   # a pending card: one poll per interval for the chat
        _settle(page, seen, 2)
        page.locator('#m7 .vx-approval').get_by_role('button', name='Decline').click()
        _settle(page, seen, 3)                     # an owner action refreshes at once
        assert activity and activity[0]['action'] == 'decline'
        page.clock.run_for(30000)                  # nothing pending or sending: polling stopped
        _settle(page, seen, 3)
        page.evaluate("""() => { for (let i = 0; i < 100; i++)
          window.VoolXSocial.mount(document.getElementById('m' + i), 'chat-one', 'req-' + i); }""")
        page.clock.run_for(100)                    # the same messages mounted again: nothing to ask
        _settle(page, seen, 3)
        state['card'] = None
        page.evaluate("""() => { const m = document.createElement('div'); m.id = 'new';
          document.getElementById('chat').appendChild(m); window.VoolXSocial.mount(m, 'chat-one', 'req-new'); }""")
        page.clock.run_for(100)                    # a new assistant message refreshes
        _settle(page, seen, 4)
        assert len(seen[3].get('request_ids') or ()) == 101
        page.clock.run_for(30000)                  # an empty answer stops it
        _settle(page, seen, 4)
    finally:
        browser.close()
        manager.stop()


def test_a_chat_drawn_again_gets_its_pending_card_back(vault_home):
    """Review r5 N1: switch to another chat and back; the fresh elements get their cards."""
    manager, browser, page, seen = _browser_page(True, lambda body: {
        'ok': True, 'cards': [_card('req-1', 'pending')] if body['session_id'] == 'chat-one' else []})
    try:
        _mount_history(page, 3)
        page.clock.run_for(100)
        _settle(page, seen, 1)
        page.wait_for_selector('#m1 .vx-approval')
        page.evaluate("() => { document.getElementById('chat').innerHTML = ''; }")
        _mount_history(page, 2, chat='chat-two')
        page.clock.run_for(100)
        _settle(page, seen, 2)
        page.evaluate("() => { document.getElementById('chat').innerHTML = ''; }")
        _mount_history(page, 3)
        page.clock.run_for(100)
        _settle(page, seen, 3)
        card = page.locator('#m1 .vx-approval')
        card.wait_for()
        assert card.count() == 1
        assert card.get_by_role('button', name='Approve and send').count() == 1
        assert card.get_by_role('button', name='Decline').count() == 1
    finally:
        browser.close()
        manager.stop()


def test_a_tab_opened_before_x_was_connected_asks_once_when_an_answer_finishes(vault_home):
    """Review r5 N2: the page flag was false; a finished answer still asks, once, batched."""
    manager, browser, page, seen = _browser_page(False, lambda body: {
        'ok': True, 'cards': [_card('req-new', 'sent')] if 'req-new' in body.get('request_ids', ()) else []})
    try:
        _mount_history(page, 100)                  # history with X off and no card: no request
        page.clock.run_for(30000)
        _settle(page, seen, 0)
        page.evaluate("""() => { const m = document.createElement('div'); m.id = 'new';
          document.getElementById('chat').appendChild(m);
          window.VoolXSocial.mount(m, 'chat-one', 'req-new', true);
          window.VoolXSocial.mount(m, 'chat-one', 'req-new', true); }""")
        page.clock.run_for(100)                    # both finish paths for one answer: one request
        _settle(page, seen, 1)
        assert len(seen[0]['request_ids']) == 101 and seen[0]['session_id'] == 'chat-one'
        page.wait_for_selector('#new .vx-approval')
        page.clock.run_for(30000)                  # a sent card is not live: no polling
        _settle(page, seen, 1)
    finally:
        browser.close()
        manager.stop()


def test_a_failed_card_request_retries_with_backoff_then_stops(vault_home):
    """Review r5 N3: a failed request re-arms at 3s, 6s, 12s (cap 60s); success with nothing live stops."""
    from core.x_social_fragment import render_x_social_fragment
    from tests.served_browser import launch_chromium

    plan = ['abort', 500, 500, 'pending', 'sent']
    seen = []
    manager, browser = launch_chromium()
    page = browser.new_page()
    page.clock.install()

    def cards(route):
        seen.append(json.loads(route.request.post_data or '{}'))
        step = plan.pop(0) if plan else 'sent'
        if step == 'abort':
            route.abort()
        elif step == 500:
            route.fulfill(status=500, content_type='application/json', body='{"ok": false}')
        else:
            route.fulfill(status=200, content_type='application/json',
                          body=json.dumps({'ok': True, 'cards': [_card('req-1', step)]}))

    page.route('http://vool.test/api/x/cards', cards)
    page.route('http://vool.test/chat', lambda route: route.fulfill(
        status=200, content_type='text/html', body=_PAGE.format(fragment=render_x_social_fragment(True))))
    page.goto('http://vool.test/chat')
    page.clock.pause_at(page.evaluate('Date.now()') + 1000)   # time moves only by run_for
    try:
        _mount_history(page, 3)
        page.clock.run_for(100)
        _settle(page, seen, 1)                     # fails (network)
        for wait in (3000, 6000, 12000):
            page.clock.run_for(wait - 300)
            _settle(page, seen, len(seen))
            before = len(seen)
            page.clock.run_for(400)
            _settle(page, seen, before + 1)
        assert len(seen) == 4                      # 4th succeeds with a pending card
        page.wait_for_selector('#m1 .vx-approval')
        page.clock.run_for(3100)                   # live card: ordinary 3s poll, answer is sent
        _settle(page, seen, 5)
        page.clock.run_for(120000)                 # success with nothing live: stopped
        _settle(page, seen, 5)
    finally:
        browser.close()
        manager.stop()


def test_failed_retries_cap_at_sixty_seconds(vault_home):
    manager, browser, page, seen = _browser_page(True, lambda body: None)
    page.route('http://vool.test/api/x/cards', lambda route: (seen.append(1), route.fulfill(
        status=503, content_type='application/json', body='{"ok": false}')))
    page.clock.pause_at(page.evaluate('Date.now()') + 1000)   # time moves only by run_for
    try:
        _mount_history(page, 1)
        page.clock.run_for(100)
        _settle(page, seen, 1)
        for wait in (3000, 6000, 12000, 24000, 48000, 60000, 60000):
            before = len(seen)
            page.clock.run_for(wait - 300)
            _settle(page, seen, before)
            page.clock.run_for(400)
            _settle(page, seen, before + 1)
    finally:
        browser.close()
        manager.stop()


@pytest.mark.parametrize('status', [400, 403])
def test_a_refused_card_request_is_not_retried(vault_home, status):
    """Review r6 R2: a refusal will not clear by asking again, so the chat stops asking."""
    manager, browser, page, seen = _browser_page(True, lambda body: None)
    page.route('http://vool.test/api/x/cards', lambda route: (seen.append(1), route.fulfill(
        status=status, content_type='application/json', body='{"ok": false}')))
    page.clock.pause_at(page.evaluate('Date.now()') + 1000)   # time moves only by run_for
    try:
        _mount_history(page, 1)
        page.clock.run_for(100)
        _settle(page, seen, 1)
        page.clock.run_for(180000)
        _settle(page, seen, 1)
    finally:
        browser.close()
        manager.stop()
