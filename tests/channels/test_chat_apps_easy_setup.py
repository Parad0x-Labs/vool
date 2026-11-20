"""Telegram and Discord with the fewest steps: real daemon, real managed workers, real /api/chat turns.
Only the Telegram and Discord HTTP servers are fake (tests/channels/test_managed_chat_apps.FakeChatHost)."""
import secrets
import time
import uuid

from tests.channels.test_managed_chat_apps import bots, receipts, record, wait_for  # bots is a fixture
from tests.mobile.test_phone_listener_served import http


def phone_off(base):
    status, value = http(base, '/api/mobile/settings', {'enabled': False})
    assert status == 200 and value['enabled'] is False and value['running'] is False, value


def connect(base, platform, token, link=''):
    return http(base, '/api/mobile/settings', {'operation': 'bot_connect', 'platform': platform, 'token': token, 'channel_link': link})


def telegram_key():
    return '7' + str(secrets.randbelow(10**9)).zfill(9) + ':' + secrets.token_urlsafe(26)[:35]


def chat(base, text, session, history=()):
    messages = [*history, {'role': 'user', 'content': text}]
    status, value = http(base, '/api/chat', {'surface': 'desktop', 'model': 'vool', 'messages': messages,
                                             'stream': False, 'session_id': session, 'mode': 'auto'})
    assert status == 200, value
    return value['message']['content']


def test_telegram_start_button_pairs_with_phone_access_off(bots):
    desktop, hosts = bots; base = desktop[0]; host = hosts['telegram']
    phone_off(base)
    # Telegram stalls once on the key check: VOOL waits, asks again, and connects.
    host.stall_identity = 1
    status, connection = connect(base, 'telegram', telegram_key())
    assert status == 200, connection
    assert host.stall_identity == 0
    # Telegram's own deep link: the code rides the Start button, nobody types it.
    assert connection['start_link'] == 'https://t.me/my_vool_bot?start=' + connection['code']
    host.add('telegram', '/start WRONGCODE234')
    wait_for(lambda: any(r['kind'] == 'bot.request_refused' and r['detail']['reason'] == 'wrong_pairing_code' for r in receipts(base)))
    assert record(base, 'telegram')['binding_state'] == 'waiting'
    host.add('telegram', '/start ' + connection['code'])
    wait_for(lambda: record(base, 'telegram')['binding_state'] == 'bound')
    assert {k for k, v in record(base, 'telegram')['rules'].items() if v} == {'chat'}
    with host.lock:
        assert any(m.get('text', '').startswith('Paired.') for m in host.outgoing)
    host.add('telegram', 'A first message ' + secrets.token_hex(4))
    wait_for(lambda: any(r['kind'] == 'bot.turn_finished' for r in receipts(base)))
    # A stranger who finds the bot is refused, and so is a second use of the same Start link.
    host.add('telegram', '/start ' + connection['code'], user='333', channel='777')
    host.add('telegram', 'hello from a stranger', user='333', channel='777')
    wait_for(lambda: sum(r['kind'] == 'bot.request_refused' and r['detail']['reason'] == 'stranger' for r in receipts(base)) >= 2)
    assert http(base, '/api/mobile/settings')[1]['enabled'] is False  # phone access was never needed
    # Revoked: the worker stops talking to Telegram at all.
    # A key Telegram turns away is said plainly and leaves the working connection alone.
    from core.mobile_companion.bot_runtime import KEY_NOT_ACCEPTED
    host.identity_valid = False
    status, answer = connect(base, 'telegram', telegram_key())
    assert status == 400 and answer['error'] == KEY_NOT_ACCEPTED.format(app='Telegram'), answer
    host.identity_valid = True
    assert record(base, 'telegram')['binding_state'] == 'bound'
    assert http(base, '/api/mobile/settings', {'operation': 'bot_disconnect', 'platform': 'telegram'})[0] == 200
    time.sleep(0.5); before = len(host.requests); host.add('telegram', 'after disconnect'); time.sleep(2)
    assert len(host.requests) == before and record(base, 'telegram')['status'] == 'revoked'


def test_discord_owner_is_reached_after_the_invite_without_a_link(bots):
    desktop, hosts = bots; base = desktop[0]; host = hosts['discord']
    host.owner_mode = True
    phone_off(base)
    status, connection = connect(base, 'discord', secrets.token_urlsafe(24) + '.' + secrets.token_urlsafe(5) + '.' + secrets.token_urlsafe(27))
    assert status == 200, connection
    assert connection['invite_url'] == 'https://discord.com/oauth2/authorize?client_id=77&scope=bot&permissions=0'
    time.sleep(3)
    # Before the invite Discord refuses the bot's message: still waiting, nothing bound.
    assert record(base, 'discord')['binding_state'] == 'waiting'
    assert record(base, 'discord')['discord_in_server'] is False
    with host.lock:
        assert not host.outgoing
    host.invited = True
    wait_for(lambda: record(base, 'discord')['binding_state'] == 'bound')
    assert {k for k, v in record(base, 'discord')['rules'].items() if v} == {'chat'}
    with host.lock:
        assert host.outgoing[0]['content'].startswith('Paired.') and host.outgoing[0]['flags'] & (1 << 2)
    host.add('discord', 'Hello from the owner ' + secrets.token_hex(4), user='111')
    wait_for(lambda: any(r['kind'] == 'bot.turn_finished' for r in receipts(base)))
    host.add('discord', 'Someone else in the conversation', user='333')
    wait_for(lambda: any(r['kind'] == 'bot.request_refused' and r['detail']['reason'] == 'stranger' for r in receipts(base)))


def test_a_post_waits_for_the_owner_and_goes_through_the_connected_bot(bots):
    desktop, hosts = bots; base = desktop[0]; host = hosts['telegram']
    session = 'chat-' + uuid.uuid4().hex[:8]
    # Not connected: chat points to Settings and nothing leaves.
    answer = chat(base, 'post to telegram: hello', session)
    assert '/settings#phone' in answer and 'not connected' in answer, answer
    status, connection = connect(base, 'telegram', telegram_key()); assert status == 200, connection
    host.add('telegram', '/start ' + connection['code']); wait_for(lambda: record(base, 'telegram')['binding_state'] == 'bound')
    ask = 'post to telegram @vool_news: Doors open at eight ' + secrets.token_hex(3)
    preview = chat(base, ask, session)
    assert preview.startswith('Post preview ready.') and '@vool_news on Telegram' in preview, preview
    with host.lock:
        assert not host.posts  # staging sends nothing
    # A "yes" in another chat approves nothing.
    other = chat(base, 'yes', 'chat-other', [{'role': 'user', 'content': ask}, {'role': 'assistant', 'content': preview}])
    assert 'Nothing was sent' in other, other
    post_id = preview.rsplit(' ', 1)[-1]
    # The bot's own chat (a remote turn) cannot approve: it never reaches the owner-local check as owner.
    host.add('telegram', 'approve post ' + post_id)
    wait_for(lambda: any(r['kind'] in {'bot.turn_finished', 'bot.turn_refused'} for r in receipts(base)))
    with host.lock:
        assert not host.posts
    sent = chat(base, 'yes', session, [{'role': 'user', 'content': ask}, {'role': 'assistant', 'content': preview}])
    assert sent == 'Posted to @vool_news on Telegram.', sent
    with host.lock:
        assert [p[1]['text'] for p in host.posts] == [ask.split(': ', 1)[1]]
    again = chat(base, 'approve post ' + post_id, session)
    assert 'already handled' in again
    with host.lock:
        assert len(host.posts) == 1  # single use
    # A channel the bot cannot see is named, and nothing is staged.
    assert 'cannot see @nobody_here' in chat(base, 'post to telegram @nobody_here: hi', session)
    # Disconnect between preview and approval: nothing is sent.
    preview = chat(base, 'post to telegram: to my own chat', session)
    assert 'your private chat with the Telegram bot' in preview
    assert http(base, '/api/mobile/settings', {'operation': 'bot_disconnect', 'platform': 'telegram'})[0] == 200
    late = chat(base, 'approve post ' + preview.rsplit(' ', 1)[-1], session)
    assert 'Nothing was sent' in late, late
    with host.lock:
        assert not any(m.get('text') == 'to my own chat' for m in host.outgoing)


def test_discord_post_names_the_channel_in_its_server(bots):
    desktop, hosts = bots; base = desktop[0]; host = hosts['discord']
    session = 'chat-' + uuid.uuid4().hex[:8]
    status, connection = connect(base, 'discord', secrets.token_urlsafe(40), 'https://discord.com/channels/@me/444')
    assert status == 200, connection
    host.add('discord', '/pair ' + connection['code']); wait_for(lambda: record(base, 'discord')['binding_state'] == 'bound')
    preview = chat(base, 'post to discord #news: "We are live tonight."', session)
    assert '#news in Home' in preview, preview
    assert 'cannot see a channel called #nope' in chat(base, 'post to discord #nope: hi', session)
    sent = chat(base, 'approve post ' + preview.rsplit(' ', 1)[-1], session)
    assert sent == 'Posted to #news in Home.', sent
    with host.lock:
        assert host.posts == [('321', {'content': 'We are live tonight.', 'allowed_mentions': {'parse': []}, 'flags': 4})]


def test_start_without_the_code_asks_the_owner_on_the_computer(bots):
    """The plain bot link (what BotFather shows) sends a bare /start: the computer asks "is this you?"."""
    desktop, hosts = bots; base = desktop[0]; host = hosts['telegram']
    status, connection = connect(base, 'telegram', telegram_key()); assert status == 200, connection
    host.add('telegram', '/start', user='333', channel='333', name='Mallory\u202e')
    wait_for(lambda: record(base, 'telegram')['pairing_candidate'] == 'Mallory')
    wait_for(lambda: any('Confirm this connection on your computer' in m.get('text', '') for m in list(host.outgoing)))
    # "Not me": nothing is bound and the stranger is forgotten.
    assert http(base, '/api/mobile/settings', {'operation': 'bot_not_me', 'platform': 'telegram'})[0] == 200
    assert record(base, 'telegram')['pairing_candidate'] is None and record(base, 'telegram')['binding_state'] == 'waiting'
    # Nothing to confirm now: the answer says so instead of binding anyone.
    status, answer = http(base, '/api/mobile/settings', {'operation': 'bot_allow', 'platform': 'telegram'})
    assert status == 400 and 'nothing waiting' in answer['error'], answer
    host.add('telegram', '/start', user='111', channel='444', name='Sam')
    wait_for(lambda: record(base, 'telegram')['pairing_candidate'] == 'Sam')
    assert http(base, '/api/mobile/settings', {'operation': 'bot_allow', 'platform': 'telegram'})[0] == 200
    row = record(base, 'telegram')
    assert row['binding_state'] == 'bound' and row['owner_label'] == 'Sam' and row['pairing_candidate'] is None
    assert {k for k, v in row['rules'].items() if v} == {'chat'}
    wait_for(lambda: any(m.get('text', '').startswith('Paired.') and str(m.get('chat_id')) == '444' for m in list(host.outgoing)))
    host.add('telegram', 'hi again', user='333', channel='333')
    wait_for(lambda: any(r['kind'] == 'bot.request_refused' and r['detail']['reason'] == 'stranger' for r in receipts(base)))


def test_discord_owner_who_blocks_bot_messages_pairs_by_writing_first(bots):
    """Bot added, but the owner's privacy setting stops it writing first: the owner's own hello pairs it."""
    desktop, hosts = bots; base = desktop[0]; host = hosts['discord']
    host.owner_mode = True; host.invited = True; host.dm_closed = True
    status, connection = connect(base, 'discord', secrets.token_urlsafe(24) + '.' + secrets.token_urlsafe(5) + '.' + secrets.token_urlsafe(27))
    assert status == 200, connection
    time.sleep(6)
    row = record(base, 'discord')
    assert row['binding_state'] == 'waiting' and row['invite_url'] == connection['invite_url'] and row['pairing_expires_at'], row
    # The bot is in the server: Settings can now open the bot in Discord for the owner's hello.
    assert row['discord_in_server'] is True and row['bot_user_id'] == '88', row
    with host.lock:
        assert not host.outgoing
    # Someone else in the server writing to the bot does not pair it.
    host.add('discord', 'hi from a stranger', user='333'); time.sleep(6)
    assert record(base, 'discord')['binding_state'] == 'waiting'
    host.add('discord', 'hi', user='111')
    wait_for(lambda: record(base, 'discord')['binding_state'] == 'bound')
    assert {k for k, v in record(base, 'discord')['rules'].items() if v} == {'chat'}
    wait_for(lambda: any(m.get('content', '').startswith('Paired.') for m in list(host.outgoing)))
    wait_for(lambda: any(r['kind'] == 'bot.turn_finished' for r in receipts(base)))
