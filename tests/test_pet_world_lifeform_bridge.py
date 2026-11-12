from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from core.command_registry.groups import lifeform_group as group
from core.companion.lifeform import genesis, progression
from core.companion.lifeform.schema import new_lifeform_v1, to_json
from core.execution_truth import record_execution
from core.pet_world import lifeform_bridge as bridge
from storage import lifeform_store
from storage.db import get_connection
from tests._pet_world_support import home as home


def _fact(index, *, ok=True, status='executed', day=None, kind='tool', name='file_write'):
    identifier = record_execution(session_id='local', turn_key='turn-'+str(index%3), kind=kind,
                                  name=name, ok=ok, status=status, dedupe=str(index),
                                  detail={'tokens_total': 100000, 'duration_s': 4000})
    assert identifier
    if day:
        with closing(get_connection()) as conn:
            conn.execute('UPDATE execution_facts SET created_at=? WHERE fact_id=?', (day+'T12:00:00Z', identifier))
            conn.commit()
    return identifier


def _seed(days=0):
    owner, identifier = group._owner_id(), 'lf-yard-test'
    seed = genesis.genesis_seed(owner, identifier)
    born = (datetime.now(timezone.utc)-timedelta(days=days)).strftime('%Y-%m-%d')
    doc = new_lifeform_v1(identifier, owner, seed, born)
    snapshot = progression.sign_snapshot([], bridge.signing_key(), group._SIGNING_KEY_ID, seed)
    lifeform_store.save_lifeform(to_json(doc), [], snapshot, group._utcnow_iso())
    return identifier


def test_evidence_gate_counts_only_successful_facts_and_real_turns(home):
    for i in range(6):
        _fact(i, ok=False, status='failed')
    _fact(20, status='cancelled')
    _fact(21, status='refused')
    assert group._witness_evidence() == (0, 0)
    assert 'gate' in group._handle_egg(None, None).data
    for i in range(5):
        _fact(100+i)
    assert group._witness_evidence() == (5, 3)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(pool.map(lambda _: group._handle_egg(None, None).data, range(2)))
    assert outputs[0]['lifeform_id'] == outputs[1]['lifeform_id']
    with closing(get_connection()) as conn:
        assert conn.execute('SELECT COUNT(*) FROM lifeform_v1').fetchone()[0] == 1


def test_feed_is_replay_safe_capped_and_never_writes_execution_truth(home, monkeypatch):
    identifier = _seed(days=12)
    today = datetime.now(timezone.utc).date()
    for d in range(10):
        for n in range(12):
            _fact(d*100+n, day=(today-timedelta(days=d+1)).isoformat(),
                  kind='model' if n%3 == 0 else 'tool', name='pytest' if n%3 == 1 else 'file_write')
    for n in range(10):
        _fact(2000+n, ok=False, status='failed')
    _fact(3000, day=(today-timedelta(days=30)).isoformat())
    _fact(3001, day=(today+timedelta(days=1)).isoformat())
    original = get_connection
    writes = []
    def trapped(*args, **kwargs):
        conn = original(*args, **kwargs)
        def authorize(action, name, *rest):
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE) and name == 'execution_facts':
                writes.append((action, name))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        conn.set_authorizer(authorize)
        return conn
    monkeypatch.setattr(bridge, 'get_connection', trapped)
    with closing(original()) as conn:
        before = [tuple(r) for r in conn.execute('SELECT * FROM execution_facts ORDER BY fact_id')]
    result = bridge.refresh()
    assert result['verified'] and result['stage'] in ('protoform', 'form', 'specialized', 'ascended')
    record = lifeform_store.load_lifeform(identifier)
    state = progression.reduce_events(record['event_log'], record['doc']['genesis_seed'])
    assert 350 <= state['energy_game'] <= 10*52.5
    assert state['observed_counts']['daily_active_use'] == 10
    assert all(e['day'] != (today+timedelta(days=1)).isoformat() for e in record['event_log'])
    again = bridge.refresh()
    assert again['manifest'] == result['manifest']
    assert lifeform_store.load_lifeform(identifier)['event_log'] == record['event_log']
    # A lost cursor after the canonical save replays the source without double credit.
    with closing(original()) as conn:
        conn.execute('UPDATE pet_yard_growth_v1 SET cursor=0')
        conn.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(r['verified'] for r in pool.map(lambda _: bridge.refresh(), range(2)))
    assert lifeform_store.load_lifeform(identifier)['event_log'] == record['event_log']
    with closing(original()) as conn:
        assert [tuple(r) for r in conn.execute('SELECT * FROM execution_facts ORDER BY fact_id')] == before
    assert not writes
    assert not any(key in json.dumps(result) for key in ('created_day', 'fact_id', 'turn_key', 'event_log'))


@pytest.mark.parametrize('damage', ['hash', 'shape', 'json'])
def test_tampering_keeps_last_verified_appearance_without_repair(home, damage):
    identifier = _seed(days=1)
    _fact(0)
    original = bridge.refresh()
    record = lifeform_store.load_lifeform(identifier)
    if damage == 'json':
        raw = '{'
    elif damage == 'shape':
        raw = '[null]'
    else:
        record['event_log'][0]['hash'] = '0'*64
        raw = json.dumps(record['event_log'])
    with closing(get_connection()) as conn:
        conn.execute('UPDATE lifeform_v1 SET event_log=?', (raw,))
        conn.commit()
    for view in (bridge.inspect(), bridge.refresh()):
        assert not view['verified'] and view['notice'] == 'growth_unverified'
        assert view['manifest'] == original['manifest']
    with closing(get_connection()) as conn:
        assert conn.execute('SELECT event_log FROM lifeform_v1').fetchone()[0] == raw


def test_batching_is_explicit_and_get_does_not_feed(home):
    identifier = _seed()
    for n in range(bridge.BATCH+1):
        _fact(n)
    assert bridge.inspect()['stage'] == 'seed'
    assert lifeform_store.load_lifeform(identifier)['event_log'] == []
    assert bridge.refresh()['more']
    assert not bridge.refresh()['more']
    data = bridge.inspect()
    assert data['verified'] and not data['more']


def test_new_mint_verifies_when_existing_key_reader_strips_first_material(home, monkeypatch):
    from core.runtime_paths import active_data_dir

    # Existing key ownership uses stripped persisted bytes; exercise that public path.
    original = group._signing_key
    def first_key():
        path = active_data_dir()/'lifeform_signing.key'
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'\t'+b'x'*30+b'\n')
            return path.read_bytes()
        return original()
    monkeypatch.setattr(group, '_signing_key', first_key)
    for i in range(5):
        _fact(i)
    assert group._handle_egg(None, None).data['existing'] is False
    assert bridge.inspect()['verified']
