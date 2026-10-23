"""Bound bot owners can remove payment authority even when chat rights are off."""
import secrets

import pytest

from tests.channels.test_managed_chat_apps import allow_chat, http, receipts, record, wait_for
from tests.channels.test_managed_chat_apps import bots as bots


@pytest.mark.parametrize('platform', ['telegram', 'discord'])
def test_private_bound_owner_freezes_but_never_unfreezes_or_approves(bots, platform):
    desktop, hosts = bots
    base, host = desktop[0], hosts[platform]
    status, linked = http(base, '/api/mobile/settings', {'operation': 'bot_connect', 'platform': platform,
        'token': secrets.token_urlsafe(40),
        'channel_link': 'https://discord.com/channels/@me/444' if platform == 'discord' else ''})
    assert status == 200, linked
    host.add(platform, '/freeze', user='333')
    wait_for(lambda: any(row['kind'] == 'bot.request_refused' for row in receipts(base)))
    assert http(base, '/api/mobile/settings')[1]['frozen'] is False
    host.add(platform, '/pair ' + linked['code'])
    wait_for(lambda: record(base, platform)['binding_state'] == 'bound')
    assert record(base, platform)['rules']['chat'] is False
    host.add(platform, '/freeze', user='333')
    wait_for(lambda: any(row['kind'] == 'bot.request_refused' and row['detail']['reason'] == 'stranger'
                         for row in receipts(base)))
    assert http(base, '/api/mobile/settings')[1]['frozen'] is False
    host.add(platform, '/freeze')
    wait_for(lambda: http(base, '/api/mobile/settings')[1]['frozen'] is True)
    wait_for(lambda: any('Payments are frozen' in str(row) for row in host.outgoing))
    assert any(row['kind'] == 'payments.frozen' and row['device_id'] == 'bot:' + platform for row in receipts(base))
    allow_chat(base, platform)
    for text in ['/unfreeze', '/approve pending-request']:
        host.add(platform, text)
    wait_for(lambda: sum(row['kind'] == 'bot.request_refused' and row['detail']['reason'] == 'pc_action_required'
                         for row in receipts(base)) >= 2)
    assert http(base, '/api/mobile/settings')[1]['frozen'] is True
    assert not any(row['kind'] == 'bot.turn_finished' for row in receipts(base))
    assert http(base, '/api/mobile/settings', {'operation': 'bot_disconnect', 'platform': platform})[0] == 200
    assert record(base, platform)['status'] == 'revoked'
