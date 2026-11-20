"""A chat app fetches a file from folders the owner chose, and only from them.

Asked over Telegram 2026-11-20: "can you fetch me the latest picture from Downloads folder pls?" was
taken for a web search and refused with words about another device; chat apps had no way to reach a
file or to send one back. Real daemon and real bot workers; only the Telegram and Discord servers are
stand-ins (tests/channels/test_managed_chat_apps.FakeChatHost).
"""
import secrets

import pytest

from tests.channels.test_managed_chat_apps import FakeChatHost, receipts, record, wait_for
from tests.mobile.test_phone_listener_served import http, process_desktop

ASK = 'can you fetch me the latest picture from Downloads folder pls?'
PICTURE = b'\x89PNG\r\n\x1a\n' + secrets.token_bytes(3000)


@pytest.fixture
def owner_home(tmp_path, monkeypatch):
    home = tmp_path / 'person'
    for name in ('Downloads', 'Documents', 'Desktop'):
        (home / name).mkdir(parents=True)
    (home / 'Downloads' / 'old.png').write_bytes(b'\x89PNG\r\n\x1a\n' + b'old' * 100)
    (home / 'Downloads' / 'beach.png').write_bytes(PICTURE)
    import os
    import time
    os.utime(home / 'Downloads' / 'old.png', (time.time() - 3600, time.time() - 3600))
    (home / 'Documents' / 'secret.png').write_bytes(b'\x89PNG\r\n\x1a\n' + b'secret' * 50)
    monkeypatch.setenv('HOME', str(home))
    tg, dc = FakeChatHost(), FakeChatHost()
    monkeypatch.setenv('VOOL_TEST_BOT_TELEGRAM_URL', tg.url)
    monkeypatch.setenv('VOOL_TEST_BOT_DISCORD_URL', dc.url)
    vool = tmp_path / 'vool'
    vool.mkdir()
    try:
        for desktop in process_desktop(vool):
            yield desktop[0], home, {'telegram': tg, 'discord': dc}
    finally:
        tg.close()
        dc.close()


def _bind(base, host, platform):
    key = ('7' + str(secrets.randbelow(10**9)).zfill(9) + ':' + secrets.token_urlsafe(26)[:35]) if platform == 'telegram' \
        else 'MTA' + 'x' * 21 + '.' + 'G' * 6 + '.' + 'z' * 27
    status, connection = http(base, '/api/mobile/settings', {'operation': 'bot_connect', 'platform': platform, 'token': key,
                                                            'channel_link': 'https://discord.com/channels/@me/444' if platform == 'discord' else ''})
    assert status == 200, connection
    host.add(platform, '/pair ' + connection['code'])
    wait_for(lambda: record(base, platform)['binding_state'] == 'bound')


def _replies(host):
    with host.lock:
        return [row.get('text') or row.get('content') or '' for row in host.outgoing]


def _rules(base, platform, **body):
    return http(base, '/api/mobile/settings', {'operation': 'device_rules', 'device_id': 'bot:' + platform, **body})


@pytest.mark.parametrize('platform', ['telegram', 'discord'])
def test_chat_app_fetches_the_newest_picture_from_a_chosen_folder_only(owner_home, platform):
    base, home, hosts = owner_home
    host = hosts[platform]
    _bind(base, host, platform)

    # Off by default: the person is told where to turn it on, and nothing leaves the computer.
    before = len(_replies(host))
    host.add(platform, ASK)
    wait_for(lambda: len(_replies(host)) > before + 0 and any('Files in chosen folders' in r for r in _replies(host)[before:]), timeout=60)
    assert not host.uploads

    # Never the whole computer, and never on without a folder.
    status, refused = _rules(base, platform, rules={'computer_tools': True}, file_scope={'mode': 'all'})
    assert status == 400 and 'folders you choose' in refused['error'], refused
    status, refused = _rules(base, platform, rules={'computer_tools': True})
    assert status == 400 and 'at least one folder' in refused['error'], refused

    status, saved = _rules(base, platform, rules={'computer_tools': True},
                           file_scope={'mode': 'folders', 'folders': [str(home / 'Downloads')]})
    assert status == 200, saved
    assert record(base, platform)['right_expiry'].get('computer_tools')  # on for 30 days at a time

    before = len(_replies(host))
    host.add(platform, ASK)
    wait_for(lambda: len(host.uploads) == 1, timeout=60)
    path, kind, body = host.uploads[0]
    assert kind.startswith('multipart/form-data; boundary=')
    assert path.endswith('/sendDocument') if platform == 'telegram' else path == '/channels/444/messages'
    assert PICTURE in body and b'filename="beach.png"' in body
    wait_for(lambda: any('beach.png' in r for r in _replies(host)[before:]), timeout=30)
    assert 'old.png' not in str(host.uploads)  # the newest one
    assert any(r['kind'] == 'bot.file_sent' for r in receipts(base))

    # Outside the chosen folder: no file, and the reply names the folder it may use.
    before = len(_replies(host))
    host.add(platform, 'send me the newest picture from documents')
    wait_for(lambda: len(_replies(host)) > before, timeout=60)
    assert len(host.uploads) == 1
    said = ' '.join(_replies(host)[before:])
    assert 'secret' not in said and 'only find and send files from the folders you chose' in said, said

    # Turned off again: the next ask sends nothing.
    assert _rules(base, platform, rules={'computer_tools': False})[0] == 200
    before = len(_replies(host))
    host.add(platform, ASK)
    wait_for(lambda: len(_replies(host)) > before, timeout=60)
    assert len(host.uploads) == 1
