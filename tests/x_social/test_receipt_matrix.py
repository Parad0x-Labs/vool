from __future__ import annotations

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.test_x_actions_approval import _settings, fake_x
from tests.x_social.test_cards import _cards, _context, _propose


@pytest.mark.parametrize('code,body,expected', [
    (201, {'data': {'id': '999'}}, 'sent'),
    (201, {}, 'unknown'),
    (201, {'data': {}}, 'unknown'),
    (200, 'junk', 'unknown'),
    (200, {'data': {'id': 'invalid'}}, 'unknown'),
    (403, {'title': 'Forbidden'}, 'failed'),
    (503, {'title': 'Unavailable'}, 'unknown'),
    (0, None, 'unknown'),
])
def test_send_receipt_matrix_never_retries_ambiguous_success(fake_x, monkeypatch, code, body, expected):
    from core import x_api

    if code:
        fake_x.write_answer = (code, body)
    else:
        fake_x.hang.set()
        monkeypatch.setattr(x_api, 'REQUEST_TIMEOUT_S', 0.05)
    assert _propose(_context()).ok
    [card] = _cards()[1]['cards']
    _, outcome = _settings({'action': 'approve', 'action_id': card['id'], 'hash': card['hash']})
    [shown] = _cards()[1]['cards']
    assert shown['state'] == expected, outcome
    assert shown['state'] == _settings({'action': 'overview'})[1]['recent'][0]['state']
    if expected == 'unknown':
        assert 'check X before trying again' in shown['message']
        assert 'post_id' not in shown.get('result', {})
    assert len(fake_x.writes()) == 1
    assert _settings({'action': 'approve', 'action_id': card['id'], 'hash': card['hash']})[0] == 400
    assert len(fake_x.writes()) == 1
