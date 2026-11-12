"""Two genuine Drop homes through the served owner-local controller."""
import base64
import json
import time

import pytest

from core import runtime_paths
from tests.asgi_harness import asgi_request


@pytest.fixture
def homes(tmp_path, monkeypatch):
    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices

    old = runtime_paths._VOOL_HOME_OVERRIDE
    monkeypatch.delenv('VOOL_DROP', raising=False)
    a, b = tmp_path/'host', tmp_path/'guest'
    a.mkdir(mode=0o700)
    b.mkdir(mode=0o700)
    runtime_paths.configure_runtime_home(a)
    app = create_app(RuntimeServices(display_name='VOOL'))
    try:
        yield app, a, b, tmp_path/'relay'
    finally:
        runtime_paths.configure_runtime_home(old)


def call(homes, home, action, *, door='lend', headers=None, **body):
    runtime_paths.configure_runtime_home(home)
    status, _, raw = asgi_request(homes[0], method='POST', path='/api/'+door,
                                  headers=headers or {'Host': '127.0.0.1', 'Content-Type': 'application/json'},
                                  body=json.dumps({'action': action, **body}).encode())
    return status, json.loads(raw)


def ok(homes, home, action, **body):
    code, value = call(homes, home, action, **body)
    assert code == 200 and value['ok'], (code, value)
    return value


def connect(homes, **changes):
    _, a, b, relay = homes
    for home in (a, b):
        ok(homes, home, 'settings', door='drop', enabled=True, relay=str(relay), allow_digest=False)
        ok(homes, home, 'settings', enabled=True)
    host = ok(homes, a, 'identity', door='drop')['fingerprint']
    guest = ok(homes, b, 'identity', door='drop')['fingerprint']
    terms = ok(homes, a, 'status')['defaults']
    terms.update(changes)
    link = ok(homes, a, 'invite', fingerprint=guest, confirmed=True, terms=terms)['link']
    key = ok(homes, b, 'join', link=link, fingerprint=host, confirmed=True)['invite']
    return key, link, terms, host, guest


def test_fresh_home_is_off_and_requires_drop(homes):
    _, a, _, relay = homes
    value = ok(homes, a, 'status')
    assert value['enabled'] is False and value['drop_enabled'] is False and value['reviewed'] is False
    assert value['defaults']['jobs'] == 1 and value['defaults']['auto'] is False
    assert not (a/'lend').exists() and not relay.exists()
    assert call(homes, a, 'settings', enabled=True)[1]['error'] == 'DROP_DISABLED'
    assert call(homes, a, 'list')[1]['error'] == 'DROP_DISABLED'


def test_two_homes_confirm_identical_terms_before_input(homes):
    key, link, terms, host, _ = connect(homes)
    _, a, b, _ = homes
    preview = ok(homes, b, 'preview', link=link)
    assert preview['terms'] == terms and preview['host'] == host
    assert preview['trusted_processor'] is True and preview['confirmed_by_host'] is False
    assert ok(homes, b, 'list')['invites'][0]['state'] == 'confirming'
    ok(homes, a, 'poll')
    state = ok(homes, b, 'poll')['invites'][0]
    assert state['id'] == key and state['state'] == 'ready' and state['terms'] == terms
    assert preview['digest'] == state['digest']


@pytest.mark.parametrize('headers', [
    {'Host': '127.0.0.1', 'Content-Type': 'text/plain'},
    {'Host': '127.0.0.1', 'Content-Type': 'application/json', 'Origin': 'https://elsewhere.invalid'},
    {'Host': '127.0.0.1', 'Content-Type': 'application/json', 'Sec-Fetch-Site': 'cross-site'},
])
def test_owner_origin_guards(homes, headers):
    ok(homes, homes[1], 'status')
    assert call(homes, homes[1], 'settings', enabled=True, headers=headers)[0] in {403, 415}


def test_terms_reject_unknown_fields_cloud_and_expiry(homes):
    connect(homes)
    _, a, b, _ = homes
    guest = ok(homes, b, 'identity', door='drop')['fingerprint']
    terms = ok(homes, a, 'status')['defaults']
    for change in [{'tool': 'shell'}, {'local_only': False}, {'expires': 0},
                   {'jobs': True}, {'trim_length': 301}, {'expires': int(time.time()*1000) + 8*86400000}]:
        assert call(homes, a, 'invite', fingerprint=guest, confirmed=True, terms={**terms, **change})[0] == 409


def test_invite_has_a_local_qr_image(homes):
    connect(homes)
    _, a, b, _ = homes
    guest = ok(homes, b, 'identity', door='drop')['fingerprint']
    offer = ok(homes, a, 'invite', fingerprint=guest, confirmed=True, terms=ok(homes, a, 'status')['defaults'])
    svg = base64.b64decode(offer['qr'].split(',', 1)[1]).decode()
    assert '<svg' in svg and '<rect' in svg and 'http' not in offer['link']
    assert ok(homes, b, 'preview', link=offer['link'])['trusted_processor'] is True


def test_default_expiry_uses_tonights_local_offset_across_clock_changes(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from core.lend.terms import defaults

    try:
        with monkeypatch.context() as env:
            env.setenv('TZ', 'Europe/Vilnius')
            time.tzset()
            zone = ZoneInfo('Europe/Vilnius')
            for month, day in [(3, 29), (10, 25)]:
                morning = datetime(2026, month, day, 0, 30, tzinfo=zone)
                tonight = datetime(2026, month, day, 23, 59, tzinfo=zone)
                assert defaults(int(morning.timestamp()*1000))['expires'] == int(tonight.timestamp()*1000)
    finally:
        time.tzset()
