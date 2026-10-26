"""Review r4 M2: one batched card request per chat, polled only while a card is live."""
from __future__ import annotations

import json
import time

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
