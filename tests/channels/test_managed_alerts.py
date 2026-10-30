"""The real managed workers deliver the PC's noise choices to outside HTTP fakes."""
import secrets
import time
from datetime import datetime, timezone

import pytest

from tests.channels.test_managed_chat_apps import FakeChatHost, record, wait_for
from tests.mobile.test_phone_listener_served import desktop as desktop
from tests.mobile.test_phone_listener_served import http
from tests.mobile.test_phone_notifications import record as notify


@pytest.mark.parametrize('platform', ['telegram', 'discord'])
def test_bot_each_type_presets_quiet_outer_gate_and_disconnect(desktop, monkeypatch, platform):
    from core.mobile_companion import notifications
    from core.mobile_companion.bot_runtime import BotManager
    base, runtime, _ = desktop
    host = FakeChatHost()
    runtime.phone_bot_manager = BotManager(runtime, provider_origins={platform: host.url})
    try:
        assert http(base, '/api/mobile/settings', {'enabled': True, 'address': '127.0.0.1', 'port': 0})[0] == 200
        status, connection = http(base, '/api/mobile/settings', {'operation': 'bot_connect', 'platform': platform,
            'token': secrets.token_urlsafe(40), 'channel_link': 'https://discord.com/channels/@me/444' if platform == 'discord' else ''})
        assert status == 200, connection
        host.add(platform, '/pair ' + connection['code'])
        wait_for(lambda: record(base, platform)['binding_state'] == 'bound')
        assert http(base, '/api/mobile/settings', {'operation': 'device_rules', 'device_id': 'bot:' + platform, 'rules': {'alerts': True}})[0] == 200
        def preferences(value):
            status, result = http(base, '/api/mobile/settings', {'operation': 'bot_notifications', 'device_id': 'bot:' + platform, 'preferences': value})
            assert status == 200, result
        for kind in notifications.TYPES:
            preferences({'preset': 'off', 'types': {kind: {'enabled': True}}})
            before = len(host.outgoing)
            notify(kind)
            other = 'task_failed' if kind != 'task_failed' else 'task_completed'
            notify(other)
            wait_for(lambda before=before: len(host.outgoing) > before)
            additions = host.outgoing[before:]
            def text(row):
                return row.get('text') or row.get('content')
            assert [text(row) for row in additions] == [notifications.TYPES[kind]]
            assert 'Private task details' not in str(additions)
        for preset, expected in [('everything', 3), ('important', 1), ('off', 0)]:
            preferences({'preset': preset})
            before = len(host.outgoing)
            for kind in ['task_completed', 'task_failed', 'reminder']: notify(kind)
            time.sleep(2.5)
            assert len(host.outgoing) - before == expected
        monkeypatch.setattr(notifications, '_now', lambda: datetime(2026, 10, 20, 23, 30, tzinfo=timezone.utc))
        preferences({'preset': 'everything', 'quiet': {'enabled': True, 'start': '22:00', 'end': '07:00', 'approvals_through': False}})
        before = len(host.outgoing); notify('reminder'); time.sleep(2.5)
        assert len(host.outgoing) == before
        monkeypatch.setattr(notifications, '_now', lambda: datetime(2026, 10, 21, 8, 0, tzinfo=timezone.utc))
        wait_for(lambda: len(host.outgoing) == before + 1)
        assert http(base, '/api/mobile/settings', {'operation': 'device_rules', 'device_id': 'bot:' + platform, 'rules': {'alerts': False}})[0] == 200
        before = len(host.outgoing); notify('task_failed'); time.sleep(2.5)
        assert len(host.outgoing) == before
        assert http(base, '/api/mobile/settings', {'operation': 'bot_disconnect', 'platform': platform})[0] == 200
        before = len(host.requests); notify('task_failed'); time.sleep(2.5)
        assert len(host.requests) == before
    finally:
        runtime.phone_bot_manager.close(); host.close()
