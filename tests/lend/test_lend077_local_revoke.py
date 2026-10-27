"""Turning lending off while Drop is off finishes the local revoke immediately."""
import hashlib
import json
import sqlite3
import time

from core import runtime_paths
from tests.lend.test_invites import call, ok
from tests.lend.test_invites import homes as homes
from tests.lend.test_jobs import ready, submit


def rows(home, table):
    con = sqlite3.connect(home/'lend'/'lend.sqlite')
    try:
        return [json.loads(row[0]) for row in con.execute(f'SELECT data FROM {table} ORDER BY rowid')]
    finally:
        con.close()


def drop_invite_tokens(home):
    con = sqlite3.connect(home/'drop'/'drops.db')
    try:
        return {row[0] for row in con.execute('SELECT token FROM invites')}
    finally:
        con.close()


def drop_sessions(home):
    con = sqlite3.connect(home/'drop'/'drops.db')
    try:
        return sorted((row[0], row[1]) for row in con.execute('SELECT sid, status FROM drops'))
    finally:
        con.close()


def relay_snapshot(relay):
    snapshot = {}
    for path in sorted(relay.rglob('*')):
        if path.is_file():
            snapshot[str(path.relative_to(relay))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def open_lend_items():
    from core.operator import notification_center

    return [item for item in notification_center.list_items()['items']
            if item.get('payload', {}).get('lend_job')]


def now_ms():
    return int(time.time()*1000)


def disable_both(homes, home):
    ok(homes, home, 'settings', door='drop', enabled=False, relay=str(homes[3]), allow_digest=False)
    assert ok(homes, home, 'settings', enabled=False)['enabled'] is False


def enable_both(homes, home):
    ok(homes, home, 'settings', door='drop', enabled=True, relay=str(homes[3]), allow_digest=False)
    assert ok(homes, home, 'settings', enabled=True)['enabled'] is True


def test_t1_unpaired_invite_token_is_discarded_and_joining_never_pairs(homes):
    from core.lend.terms import parse_link

    _, a, b, relay = homes
    for home in (a, b):
        ok(homes, home, 'settings', door='drop', enabled=True, relay=str(relay), allow_digest=False)
        ok(homes, home, 'settings', enabled=True)
    host = ok(homes, a, 'identity', door='drop')['fingerprint']
    guest = ok(homes, b, 'identity', door='drop')['fingerprint']
    link = ok(homes, a, 'invite', fingerprint=guest, confirmed=True,
              terms=ok(homes, a, 'status')['defaults'])['link']
    token = parse_link(link, now=now_ms())['drop'].rsplit('/', 1)[1]
    assert token in drop_invite_tokens(a)
    disable_both(homes, a)
    assert token not in drop_invite_tokens(a)
    enable_both(homes, a)
    ok(homes, b, 'join', link=link, fingerprint=host, confirmed=True)
    assert 'rejected' in ok(homes, a, 'poll')['outcomes']
    ok(homes, b, 'poll')
    assert ok(homes, b, 'list')['invites'][-1]['state'] == 'confirming'


def test_t2_pending_host_job_is_revoked_immediately_with_its_inbox_item(homes):
    key = ready(homes)
    job = submit(homes, key, b'Selected document for the immediate local revoke')
    ok(homes, homes[1], 'poll')
    assert rows(homes[1], 'jobs')[0]['state'] == 'pending'
    runtime_paths.configure_runtime_home(homes[1])
    assert len(open_lend_items()) == 1
    disable_both(homes, homes[1])
    state = rows(homes[1], 'jobs')[0]
    assert state['state'] == 'revoked'
    assert state['receipt']['state'] == 'revoked' and state['receipt']['result_digest'] is None
    assert not (homes[1]/'lend'/job).exists()
    runtime_paths.configure_runtime_home(homes[1])
    assert open_lend_items() == []
    enable_both(homes, homes[1])
    assert call(homes, homes[1], 'run', job=job)[1]['error'] == 'REVOKED'
    assert not list((homes[1]/'lend').glob('worker-*'))


def test_t3_relay_bytes_and_off_states_are_untouched_by_the_disable(homes):
    key = ready(homes)
    submit(homes, key, b'Relay bytes stay identical while both features turn off')
    ok(homes, homes[1], 'poll')
    before = relay_snapshot(homes[3])
    assert before
    disable_both(homes, homes[1])
    assert ok(homes, homes[1], 'settings', enabled=False)['enabled'] is False
    status = ok(homes, homes[1], 'status')
    assert status['enabled'] is False and status['drop_enabled'] is False
    assert relay_snapshot(homes[3]) == before


def test_t4_only_the_revoked_unpaired_lend_token_is_discarded(homes):
    from core.lend.terms import parse_link

    _, a, b, relay = homes
    ready(homes)
    guest = ok(homes, b, 'identity', door='drop')['fingerprint']
    terms = ok(homes, a, 'status')['defaults']
    plain_token = ok(homes, a, 'invite', door='drop', fingerprint=guest,
                     confirmed=True)['link'].rsplit('/', 1)[1]
    expiring = dict(terms, expires=now_ms()+4000)
    expired_link = ok(homes, a, 'invite', fingerprint=guest, confirmed=True, terms=expiring)['link']
    fresh_link = ok(homes, a, 'invite', fingerprint=guest, confirmed=True, terms=terms)['link']
    expired = parse_link(expired_link, now=now_ms())
    fresh = parse_link(fresh_link, now=now_ms())
    fresh_token = fresh['drop'].rsplit('/', 1)[1]
    time.sleep(max(0, (expiring['expires']-now_ms())/1000)+.05)
    ok(homes, a, 'list')
    states = {row['id']: row['state'] for row in rows(a, 'invites')}
    assert states[expired['id']] == 'expired'
    assert states[fresh['id']] == 'offered'
    sessions_before = drop_sessions(a)
    guest_invites_before = rows(b, 'invites')
    tokens_before = drop_invite_tokens(a)
    assert {plain_token, fresh_token} <= tokens_before
    disable_both(homes, a)
    tokens_after = drop_invite_tokens(a)
    assert fresh_token not in tokens_after
    assert plain_token in tokens_after
    assert drop_sessions(a) == sessions_before
    assert rows(b, 'invites') == guest_invites_before
    after = {row['id']: row['state'] for row in rows(a, 'invites')}
    assert after[expired['id']] == 'expired'
    assert after[fresh['id']] == 'revoked'


def test_t5_turning_lending_off_twice_changes_nothing_the_second_time(homes):
    key = ready(homes)
    job = submit(homes, key, b'A second disable must be a complete no-op')
    ok(homes, homes[1], 'poll')
    disable_both(homes, homes[1])
    snapshot = (rows(homes[1], 'invites'), rows(homes[1], 'jobs'),
                drop_invite_tokens(homes[1]), relay_snapshot(homes[3]))
    assert ok(homes, homes[1], 'settings', enabled=False)['enabled'] is False
    assert snapshot == (rows(homes[1], 'invites'), rows(homes[1], 'jobs'),
                        drop_invite_tokens(homes[1]), relay_snapshot(homes[3]))
    assert ok(homes, homes[1], 'status')['drop_enabled'] is False
