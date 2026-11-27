"""A chat app (Telegram, Discord) reaches the same chat as the app: real daemon, only the chat services are stand-ins.

Each question below is built from a pool of wordings with a fresh subject every run, so no fixed sentence
is the thing that passes.
"""
import random
import re
import secrets
import time

import pytest

from tests.channels.test_managed_chat_apps import allow_chat, bots, receipts, record, wait_for  # bots is a fixture
from tests.mobile.test_phone_listener_served import http


@pytest.fixture(autouse=True)
def _desktop_clock_clear_of_the_small_hours(monkeypatch):
    """The served desktop keeps the computer's own time zone (TZ), set here to one where it is daytime now.

    The reminder wordings say "tomorrow" and mean the next day. Before 04:00 local VOOL asks which day "tomorrow"
    means (core.operator.when, owner decision 2026-11-23), so on a UTC runner the test ran red every night between
    midnight and 04:00 (CI on 412af1a79). The desktop is a separate process: its clock cannot be moved, its zone can.
    Picked before the desktop starts (autouse runs first)."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    offset = next(hours for hours in range(0, 24) if 9 <= (now + timedelta(hours=hours)).hour <= 15)
    offset = offset if offset <= 12 else offset - 24
    # Etc/GMT names count the other way round: Etc/GMT-3 is three hours ahead of UTC.
    monkeypatch.setenv("TZ", "Etc/UTC" if offset == 0 else f"Etc/GMT{-offset:+d}")

WORKING = 'Your computer is working on it\u2026'
CHORES = ("water the plants", "call the dentist", "pay the gas bill", "take the bins out", "book the car service")
REMIND = ("remind me tomorrow at {h}am to {c}", "set a reminder for {h}am tomorrow: {c}", "tomorrow {h}am remind me to {c}")
LIST = ("what reminders do I have?", "show my reminders", "which reminders are pending?")
FACT = ("remember that my favourite {k} is {v}", "please remember my favourite {k} is {v}", "note that my favourite {k} is {v}")
KINDS = (("tea", ("rooibos", "sencha", "earl grey")), ("colour", ("teal", "amber", "violet")), ("city", ("Porto", "Ghent", "Kaunas")))


def pair(base, host, platform):
    status, connection = http(base, '/api/mobile/settings', {'operation': 'bot_connect', 'platform': platform,
        'token': secrets.token_urlsafe(40), 'channel_link': 'https://discord.com/channels/@me/444' if platform == 'discord' else ''})
    assert status == 200, connection
    host.add(platform, '/pair ' + connection['code'])
    wait_for(lambda: record(base, platform)['binding_state'] == 'bound')
    allow_chat(base, platform)


def ask(host, platform, text, timeout=90):
    with host.lock:
        before = len(host.outgoing)
    host.add(platform, text)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with host.lock:
            if len(host.outgoing) > before:
                row = host.outgoing[before]
                said = str((row or {}).get('text') or (row or {}).get('content') or '')
                # The first message is the working note; the answer is that same message, edited.
                if said and said != WORKING:
                    return said
                assert row is not None, 'The working note was removed and no answer took its place.'
        time.sleep(0.1)
    raise AssertionError('No answer arrived for: ' + text)


@pytest.mark.parametrize('platform', ['telegram', 'discord'])
def test_every_turn_in_a_chat_app_chat_is_answered_with_where_it_ran(bots, platform):
    """Several turns in one chat each get their own answer (a turn's state once leaked into the next one,
    which then failed and told the person to check the computer), each with the line saying where it ran."""
    desktop, hosts = bots
    base, host = desktop[0], hosts[platform]
    pair(base, host, platform)
    rng = random.Random()
    kind, values = rng.choice(KINDS)
    replies = [
        ask(host, platform, rng.choice(FACT).format(k=kind, v=rng.choice(values))),
        ask(host, platform, f'what is {rng.randint(11, 49)} times {rng.randint(11, 49)}?'),
        ask(host, platform, 'which model are you?'),
        ask(host, platform, 'thanks, and ' + rng.choice(('what time is it?', 'how are you?', 'who are you?'))),
    ]
    for reply in replies:
        assert 'Check the ongoing turn' not in reply and 'Still working on that' not in reply, replies
        assert reply.strip(), replies
    assert not any(r['kind'] == 'bot.connection_unavailable' for r in receipts(base)), receipts(base)
    # The memory save is read back from the memory store, as under an answer in the app.
    assert 'Remember something about you' in replies[0] and 'Done, checked' in replies[0], replies[0]
    # The model question is answered in plain words, as in the app: no internal names or code marks.
    assert not any(w in replies[2].lower() for w in ('composer pin', 'footer', 'lane', '`')), replies[2]
    # Read away from the computer, the computer is "your desktop" (sls 2026-11-27); the app keeps "On this computer".
    assert all(reply.rstrip().splitlines()[-1].startswith(('On your desktop', 'No AI model', 'Online', 'Your desktop + online', 'On your own network'))
               or reply.rstrip().splitlines()[-1].startswith('Done') for reply in replies[:2]), replies
    assert not any('On this computer' in reply for reply in replies), replies


@pytest.mark.parametrize('platform', ['telegram', 'discord'])
def test_reminders_need_the_owners_switch_and_then_say_what_was_set(bots, platform):
    desktop, hosts = bots
    base, host = desktop[0], hosts[platform]
    pair(base, host, platform)
    rng = random.Random()
    chore, hour = rng.choice(CHORES), rng.randint(6, 10)
    refused = ask(host, platform, rng.choice(REMIND).format(h=hour, c=chore))
    # Said as what it is, not as a missing files switch that would not open it.
    assert 'reminders, calendar, notes or contacts' in refused and 'files' not in refused.lower(), refused
    status, result = http(base, '/api/mobile/settings', {'operation': 'device_rules', 'device_id': 'bot:' + platform,
                                                         'rules': {'chat': True, 'everyday_tools': True}})
    assert status == 200, result
    assert record(base, platform)['right_expiry'].get('everyday_tools')  # on for 30 days at a time
    status, result = http(base, '/api/mobile/settings', {'operation': 'device_rules', 'device_id': 'bot:' + platform,
                                                         'rules': {'chat': True, 'everyday_tools': True, 'cloud_models': True}})
    assert status == 200 and record(base, platform)['right_expiry'].get('cloud_models'), result
    done = ask(host, platform, rng.choice(REMIND).format(h=hour, c=chore))
    # One plain confirmation: what and when, to the minute, no id; the receipt under it is its state alone, as the
    # reply already says what was set (sls 2026-11-27: the same reminder was said twice, with seconds and an id).
    assert chore in done and 'Done, checked' in done and 'Set a reminder' not in done, done
    assert 'reminder id' not in done and not re.search(r'\d\d:\d\d:\d\d', done), done
    listed = ask(host, platform, rng.choice(LIST))
    assert chore in listed, listed
    # Paying, apps and approvals still are not a chat app's.
    for right in ('tool_approval', 'payment_approve', 'paid_services'):
        status, _ = http(base, '/api/mobile/settings', {'operation': 'device_rules', 'device_id': 'bot:' + platform, 'rules': {right: True}})
        assert status == 400, right


GREET = ("hi there, are you awake?", "hello! what can you do for me?", "good morning, quick question: what day is it?",
         "hey, how is it going?", "yo, tell me a fun fact")


@pytest.mark.parametrize('platform', ['telegram', 'discord'])
def test_the_working_note_turns_into_the_answer(bots, platform):
    """One message per ask: "working on it" at once, then that same message edited into the answer.

    sls 2026-11-27: "I say hello, right then it shows something like VOOL working, when the answer comes it
    replaces that working message with the answer". A note the service will not edit is removed and the answer
    sent new, so no note is ever left claiming work."""
    desktop, hosts = bots
    host, base = hosts[platform], desktop[0]
    pair(base, host, platform)

    with host.lock:
        before, edits = len(host.outgoing), len(host.edits)
    said = ask(host, platform, random.choice(GREET))
    with host.lock:
        rows, edited = list(host.outgoing[before:]), list(host.edits[edits:])
    assert said != WORKING and len(rows) == 1 and rows[0].get('edited'), rows
    assert [kind for kind, _ in edited] == (['editMessageText'] if platform == 'telegram' else ['PATCH']), edited
    if platform == 'telegram':
        assert rows[0]['link_preview_options'] == {'is_disabled': True}
    else:
        assert int(rows[0]['flags']) & (1 << 2) and rows[0]['allowed_mentions'] == {'parse': []}

    # The person deleted the note, or the app will not edit it: the answer arrives as its own message.
    host.edit_refused = True
    with host.lock:
        before = len(host.outgoing)
    host.add(platform, random.choice(GREET))
    wait_for(lambda: len([r for r in host.outgoing[before:] if r and (r.get('text') or r.get('content'))]) >= 1
             and host.outgoing[before] is None, timeout=90)
    with host.lock:
        rows = list(host.outgoing[before:])
    assert rows[0] is None and len(rows) == 2 and str(rows[1].get('text') or rows[1].get('content')) != WORKING, rows

    if platform == 'telegram':
        # Telegram's Start button pressed again once paired opens the chat; it is no command for a turn.
        host.edit_refused = False
        turns = sum(r['kind'] == 'bot.turn_finished' for r in receipts(base))
        with host.lock:
            before = len(host.outgoing)
        host.add(platform, '/start')
        wait_for(lambda: len(host.outgoing) > before)
        time.sleep(1)
        with host.lock:
            rows = list(host.outgoing[before:])
        assert len(rows) == 1 and rows[0]['text'].startswith('Paired.'), rows
        assert sum(r['kind'] == 'bot.turn_finished' for r in receipts(base)) == turns


MAIL = ("how many unread emails do I have?", "any new mail in my inbox?", "check my email for anything unread")


@pytest.mark.parametrize('platform', ['telegram'])
def test_mail_is_its_own_switch_and_never_a_folder_refusal(bots, platform):
    """The owner's choice, 2026-11-27 (v0.8.1 item 46): a mail ask from a chat app is never told about folders or
    "apps and accounts only on the computer". No mailbox is connected here, so either way the answer says that or
    names the mail switch; the switch's own gating is tests/channels/test_chat_app_mail_switch.py."""
    desktop, hosts = bots
    base, host = desktop[0], hosts[platform]
    pair(base, host, platform)
    rng = random.Random()
    before = ask(host, platform, rng.choice(MAIL))
    assert 'folders you chose' not in before and "only work in VOOL on the computer itself" not in before, before
    assert "can't use your mail yet" in before or 'No mailbox is connected' in before, before
    status, result = http(base, '/api/mobile/settings', {'operation': 'device_rules', 'device_id': 'bot:' + platform,
                                                         'rules': {'chat': True, 'mail': True}})
    assert status == 200 and record(base, platform)['right_expiry'].get('mail'), result  # 30 days at a time
    after = ask(host, platform, rng.choice(MAIL))
    assert "can't use your mail yet" not in after and 'folders you chose' not in after and 'No mailbox is connected' in after, after
    assert "only work in VOOL on the computer itself" not in after, after
