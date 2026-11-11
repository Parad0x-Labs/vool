"""Supervised browser: what the owner's own hand may run at once, what always keeps its
review, and the page look (styles, pictures, fonts) that loads only inside an approved load.

Real Chromium, real owner HTTP door, local fixture pages from tests.test_live_browser.
"""
from __future__ import annotations

import time

import pytest

from tests.first_run_pact_rig import pact_rig as pact_rig
from tests.test_live_browser import ASSET_HEADERS, PNG, finish, world as world

CONTEXT = {'session_id': 'live-test', 'surface': 'api', 'platform': 'web_companion'}


def settle(item, timeout=20):
    deadline = time.monotonic() + timeout
    while item.state == 'running' and time.monotonic() < deadline:
        time.sleep(.05)
    assert item.state != 'running'
    return item.snapshot()


def owner_now(rig, item, op, args):
    """The pane's own control: one propose with now=true, as live_page posts it."""
    status, data = rig.post('/api/browser/propose', {'chat': 'live-test', 'op': op, 'args': args, 'now': True})
    if status == 200 and data.get('state') == 'running':
        return status, data, settle(item)
    return status, data, item.snapshot()


def paths(requests):
    return [r[1] for r in requests]


def test_owner_plain_steps_run_at_once_and_everything_that_sends_keeps_its_review(world):
    rig, item, origin, requests = world
    status, data, snap = owner_now(rig, item, 'navigate', {'url': origin + '/'})
    assert status == 200 and data == {'ok': True, 'state': 'running'}, data
    assert snap['state'] == 'ready' and snap['pending'] is None and 'Next train: 14:36' in snap['result']['page']['text']
    loaded = list(requests)
    nodes = {n['label']: n for n in snap['nodes']}

    # Scrolling and filling a field run at once and send nothing.
    status, data, snap = owner_now(rig, item, 'scroll', {'direction': 'down'})
    assert status == 200 and snap['state'] == 'ready' and requests == loaded
    status, data, snap = owner_now(rig, item, 'type', {'selector': nodes['address']['selector'], 'text': 'Example Road 21'})
    assert status == 200 and snap['state'] == 'ready' and snap['pending'] is None and requests == loaded

    # A field filled and sent, a submit button, an upload and a download all wait for review,
    # even from the owner's own hand with now=true; nothing leaves before the second click.
    nodes = {n['label']: n for n in snap['nodes']}
    cases = [
        ('type', {'selector': nodes['address']['selector'], 'text': 'Example Road 21', 'submit': True}),
        ('click', {'selector': nodes['Send address']['selector']}),
        ('upload', {'selector': nodes['document']['selector'], 'file': {'name': 'a.txt', 'mimeType': 'text/plain', 'buffer': 'QQ=='}}),
        ('download', {'url': origin + '/file'}),
    ]
    for op, args in cases:
        status, data, snap = owner_now(rig, item, op, args)
        assert status == 200 and data['status'] == 'approval_required', (op, data)
        assert snap['pending']['actor'] == 'owner' and snap['pending']['op'] == op
        assert item.run_owner_step(data['proposal']['id']) is None and item.pending
        assert requests == loaded, (op, requests)
        rig.post('/api/browser/decide', {'chat': 'live-test', 'id': data['proposal']['id'], 'approve': False})
        assert requests == loaded and item.download() is None

    # Money never takes the short path either: a payment form is refused before any review.
    finish(world, 'navigate', {'url': origin + '/payment?kind=card'})
    snap = item.snapshot()
    before = list(requests)
    field = next(n for n in snap['nodes'] if n['tag'] == 'input')
    submit = next(n for n in snap['nodes'] if n['type'] == 'submit')
    for op, args in [('type', {'selector': field['selector'], 'text': '4111'}), ('click', {'selector': submit['selector']})]:
        status, data, _ = owner_now(rig, item, op, args)
        assert status == 409 and data['error'] == 'use_wallet'
    assert item.pending is None and requests == before


def test_agent_navigate_always_waits_for_allow_and_cannot_borrow_the_owner_path(world):
    rig, item, origin, requests = world
    from core.vool_browser.live import BrowserFault, tool_intent
    from core.vool_browser.live_requests import turn_scope

    # The model's own "now" means nothing: tool calls only ever propose.
    with turn_scope(f'Use the browser to open {origin}/', 'live-test'):
        proposed = tool_intent('vool-browser.navigate', {'url': origin + '/', 'now': True}, CONTEXT)
    assert proposed['status'] == 'approval_required' and proposed['proposal']['actor'] == 'agent'
    agent_id = proposed['proposal']['id']
    assert requests == [] and not item.profile.exists()
    # Neither the owner-step runner nor a racing owner request can carry the agent's step.
    assert item.run_owner_step(agent_id) is None and item.pending['id'] == agent_id
    status, data = rig.post('/api/browser/propose', {'chat': 'live-test', 'op': 'navigate', 'args': {'url': origin + '/'}, 'now': True})
    assert status == 409 and data['error'] == 'busy'
    time.sleep(.5)
    assert requests == [] and item.pending['id'] == agent_id and item.state == 'approval_required'
    # Owner-only history moves are not a model tool.
    rig.post('/api/browser/decide', {'chat': 'live-test', 'id': agent_id, 'approve': False})
    for op in ['back', 'forward', 'reload']:
        with pytest.raises(BrowserFault, match='unsupported'):
            item.propose(op, {}, actor='agent')
        with pytest.raises(BrowserFault, match='unsupported'):
            with turn_scope(f'Use the browser to open {origin}/', 'live-test'):
                tool_intent('vool-browser.' + op, {}, CONTEXT)
    assert requests == []
    # Only Allow runs it.
    with turn_scope(f'Use the browser to open {origin}/', 'live-test'):
        proposed = tool_intent('vool-browser.navigate', {'url': origin + '/'}, CONTEXT)
    status, data = rig.post('/api/browser/decide', {'chat': 'live-test', 'id': proposed['proposal']['id'], 'approve': True})
    assert status == 200
    assert settle(item)['state'] == 'ready' and requests[0] == ('GET', '/')


def test_page_that_moves_itself_on_is_never_followed(world):
    rig, item, origin, requests = world
    status, data, snap = owner_now(rig, item, 'navigate', {'url': origin + '/refresh'})
    assert snap['state'] == 'ready' and 'Departures board' in snap['result']['page']['text'], snap
    time.sleep(2)
    # A meta refresh or page script is not the owner's hand: no request, the page stays on
    # screen, and the pane only offers the address for the owner to open.
    snap = item.snapshot()
    assert paths(requests) == ['/refresh'] and item.url == origin + '/refresh'
    assert snap['state'] == 'ready' and snap['result']['redirect']['text'] == origin + '/other'
    status, data, snap = owner_now(rig, item, 'navigate', {'url': origin + '/other'})
    assert snap['state'] == 'ready' and paths(requests) == ['/refresh', '/other']


def test_back_forward_reload_walk_only_the_owners_own_history(world):
    rig, item, origin, requests = world
    # Nothing to go back to on a fresh session.
    status, data, _ = owner_now(rig, item, 'back', {})
    assert status == 409 and data['error'] == 'invalid_input' and requests == []
    owner_now(rig, item, 'navigate', {'url': origin + '/other'})
    snap = owner_now(rig, item, 'navigate', {'url': origin + '/find?destination=Riga&service=day'})[2]
    assert snap['can_back'] and not snap['can_forward']
    snap = owner_now(rig, item, 'back', {})[2]
    assert snap['url'] == origin + '/other' and snap['can_forward'] and not snap['can_back']
    snap = owner_now(rig, item, 'forward', {})[2]
    assert snap['url'].endswith('/find?destination=Riga&service=day') and not snap['can_forward']
    snap = owner_now(rig, item, 'reload', {})[2]
    assert 'Riga day 18:42' in snap['result']['page']['text']
    assert paths(requests) == ['/other', '/find?destination=Riga&service=day', '/other',
                               '/find?destination=Riga&service=day', '/find?destination=Riga&service=day']
    # A new page after going back drops the forward entries, as any browser does.
    owner_now(rig, item, 'back', {})
    snap = owner_now(rig, item, 'navigate', {'url': origin + '/'})[2]
    assert snap['can_back'] and not snap['can_forward']
    # Stopping clears the history with the rest of the session.
    rig.post('/api/browser/stop', {'chat': 'live-test'})
    assert item.history == [] and item.history_index == -1 and item.title == ''


@pytest.fixture
def opened(monkeypatch):
    """Every address the browser gateway really connects to."""
    from core import remote_fetch_policy

    seen = []
    real = remote_fetch_policy.open_remote

    def spy(req, *args, **kwargs):
        seen.append(req.full_url)
        return real(req, *args, **kwargs)

    monkeypatch.setattr(remote_fetch_policy, 'open_remote', spy)
    return seen


def test_page_look_loads_only_inside_the_approved_load(world, opened):
    rig, item, origin, requests = world
    snap = finish(world, 'navigate', {'url': origin + '/styled'})
    assert snap['state'] == 'ready' and 'Platform board' in snap['result']['page']['text'], snap
    # The cookie the first load set travels only with the next approved document request.
    del requests[:]
    ASSET_HEADERS.clear()
    snap = finish(world, 'navigate', {'url': origin + '/styled'})
    got = paths(requests)
    assert got[0] == '/styled' and 'journey=private' in ASSET_HEADERS['/styled'].get('cookie', '')
    # Stylesheet, picture, SVG picture, and the font and background the stylesheet asks for.
    assert {'/s.css', '/p.png', '/v.svg', '/bg.png', '/f.woff2'} <= set(got), got
    for path in ['/s.css', '/p.png', '/v.svg', '/bg.png', '/f.woff2']:
        headers = ASSET_HEADERS[path]
        assert 'cookie' not in headers and 'referer' not in headers and 'origin' not in headers, (path, headers)
        assert set(headers) <= {'host', 'accept', 'user-agent', 'accept-encoding', 'connection'}, (path, headers)
    # An SVG stays a picture: it loads nothing and runs nothing, and never opens as a document.
    assert '/svg-inner.png' not in got and '/svg-script' not in got
    assert '/o.svg' not in got and '/f.svg' not in got
    # A picture that redirects is not followed, so it cannot reach a private or local address.
    assert '/redir.png' in got and not any('169.254.169.254' in url for url in opened), opened
    # The wrong type is fetched once and refused.
    assert got.count('/wrong.png') == 1
    assert '/hover.png' not in got and '/target.png' not in got
    assert not any(r[0] == 'POST' for r in requests)
    # After the page was observed nothing more is fetched. Clicking the jump link hovers it and
    # targets the end of the page, and both styles ask for a picture; scrolling asks for nothing.
    # The load the owner approved is over, so none of it reaches the network.
    loaded = list(requests)
    refused = item.page_request_refusals
    jump = next(n for n in snap['nodes'] if n['label'] == 'Jump to end')
    # The stylesheet really reached the page and was applied (it sizes every link to 333 px).
    assert jump['box'][2] == 333, jump
    status, data, snap = owner_now(rig, item, 'click', {'selector': jump['selector']})
    assert snap['state'] == 'ready' and 'End of board' in snap['result']['page']['text']
    for _ in range(2):
        status, data, snap = owner_now(rig, item, 'scroll', {'direction': 'up'})
        assert snap['state'] == 'ready'
    time.sleep(1)
    assert requests == loaded, requests[len(loaded):]
    # The hover and target pictures were really asked for, and refused before the network.
    assert item.page_request_refusals >= refused + 2, (refused, item.page_request_refusals)
    # Agent inspect reads the page without any fetch at all.
    from core.vool_browser.live import tool_intent
    tool_intent('vool-browser.inspect', {}, CONTEXT)
    assert requests == loaded
    assert 'box' not in str(item.model_nodes())


def test_page_look_shares_one_budget_per_load(world, monkeypatch):
    rig, item, origin, requests = world
    from core.vool_browser import live

    monkeypatch.setattr(live, 'ASSET_REQUESTS', 5)
    snap = finish(world, 'navigate', {'url': origin + '/many'})
    assert snap['state'] == 'ready' and 'Gallery' in snap['result']['page']['text']
    assert len([p for p in paths(requests) if p.startswith('/many-')]) == 5
    # Fonts and pictures the stylesheet asks for count against the same budget.
    monkeypatch.setattr(live, 'ASSET_REQUESTS', 2)
    del requests[:]
    finish(world, 'navigate', {'url': origin + '/styled'})
    assert len(requests) == 3 and requests[0] == ('GET', '/styled'), requests
    # The byte budget stops the load too; a fresh approved load gets a fresh budget.
    monkeypatch.setattr(live, 'ASSET_REQUESTS', 80)
    monkeypatch.setattr(live, 'ASSET_BYTES', 3 * len(PNG))
    del requests[:]
    finish(world, 'navigate', {'url': origin + '/many'})
    fetched = len([p for p in paths(requests) if p.startswith('/many-')])
    assert 3 <= fetched < 100, fetched
