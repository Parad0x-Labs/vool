"""Foreground growth from successful facts; execution truth is read-only here."""
from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import date, datetime, timezone

from core.companion.lifeform import appearance, genesis, progression, signals
from core.companion.lifeform.schema import from_json, to_json
from core.cross_process_lock import LockUnavailable, PublicationLock
from core.pet_world.catalog import REAL_USE_GROWTH
from core.pet_world.model import YardError, require
from core.runtime_paths import active_data_dir
from storage import lifeform_store
from storage.db import get_connection

BATCH = 256
MAX_LOG_BYTES = 64 * 1024 * 1024
_THREAD = threading.RLock()


@contextmanager
def serialized():
    acquired = _THREAD.acquire(timeout=5)
    require(acquired, 'unavailable', 503)
    try:
        with PublicationLock(active_data_dir()/'.pet_growth.lock'):
            yield
    except LockUnavailable as exc:
        raise YardError('unavailable', 503) from exc
    finally:
        _THREAD.release()


def _authority():
    from core.command_registry.groups import lifeform_group

    return lifeform_group


def signing_key():
    # The existing owner reads persisted material with strip(). Read it back before
    # signing a newly minted record so first-use and subsequent verification agree.
    _authority()._signing_key()
    return _authority()._signing_key()


def _read_conn():
    from core.runtime_continuity import _runtime_db_path

    conn = get_connection(_runtime_db_path())
    conn.execute('PRAGMA query_only=ON')
    return conn


def _main():
    authority = _authority()
    with closing(get_connection()) as conn:
        conn.execute('PRAGMA query_only=ON')
        try:
            rows = conn.execute('SELECT lifeform_id,created_day,length(canonical_json) AS doc_bytes,length(event_log) AS log_bytes,length(snapshot) AS snapshot_bytes FROM lifeform_v1 WHERE owner_id=? LIMIT 2',
                                (authority._owner_id(),)).fetchall()
        except sqlite3.OperationalError as exc:
            if 'no such table' in str(exc):
                return None
            raise
    if not rows:
        return None
    require(len(rows) == 1 and rows[0]['doc_bytes'] <= 65536 and rows[0]['log_bytes'] <= MAX_LOG_BYTES
            and (rows[0]['snapshot_bytes'] or 0) <= 65536, 'growth_unverified')
    record = lifeform_store.load_lifeform(rows[0]['lifeform_id'])
    require(record is not None and record['doc'].get('created_day') == rows[0]['created_day']
            and record['doc'].get('lifeform_id') == rows[0]['lifeform_id'], 'growth_unverified')
    return record


def _verified(record):
    require(type(record) is dict and set(record) == {'doc', 'event_log', 'snapshot'}, 'growth_unverified')
    doc = from_json(record['doc'])
    authority = _authority()
    require(doc.owner_id == authority._owner_id() and doc.genesis_seed == genesis.genesis_seed(doc.owner_id, doc.lifeform_id), 'growth_unverified')
    date.fromisoformat(doc.created_day)
    require(type(record['event_log']) is list and type(record['snapshot']) is dict, 'growth_unverified')
    fields = {'event_id', 'lifeform_id', 'type', 'turn_key', 'fact_id', 'family', 'surface',
              'verified', 'degraded', 'tokens', 'day', 'seq', 'prev_hash', 'issued_by', 'hash'}
    for event in record['event_log']:
        require(type(event) is dict and set(event) == fields, 'growth_unverified')
        require(all(type(event[k]) is str for k in fields-{'verified', 'degraded', 'tokens', 'seq'}), 'growth_unverified')
        require(type(event['verified']) is bool and type(event['degraded']) is bool
                and type(event['tokens']) is int and 0 <= event['tokens'] <= 2**53
                and type(event['seq']) is int and event['seq'] > 0, 'growth_unverified')
        require(event['type'] in progression.SIGNAL_TYPES and event['surface'] in ('local', 'cloud')
                and re.fullmatch('[a-f0-9]{32}', event['event_id']) and re.fullmatch('[a-f0-9]{64}', event['hash']), 'growth_unverified')
        date.fromisoformat(event['day'])
    # A missing key is not recreated during a read of an existing record.
    require((active_data_dir()/'lifeform_signing.key').is_file(), 'growth_unverified')
    key = authority._signing_key()
    ok, _ = progression.verify_snapshot(record['snapshot'], record['event_log'], {authority._SIGNING_KEY_ID: key}, doc.genesis_seed)
    state = progression.reduce_events(record['event_log'], doc.genesis_seed)
    require(ok and state['chain_ok'] and not state['duplicate_event_ids'], 'growth_unverified')
    require(all(e.get('lifeform_id') == doc.lifeform_id for e in record['event_log']), 'growth_unverified')
    require(all(doc.development.get(k) == state[k] for k in ('stage', 'energy_game', 'energy_observed', 'active_days'))
            and doc.hatched == state['hatched'], 'growth_unverified')
    return doc, state


def _cache_read():
    with closing(get_connection()) as conn:
        conn.execute('PRAGMA query_only=ON')
        try:
            row = conn.execute('SELECT cursor,CASE WHEN length(verified_json)<=? THEN verified_json END AS record FROM pet_yard_growth_v1 WHERE owner_id=?',
                               (MAX_LOG_BYTES+131072, _authority()._owner_id())).fetchone()
        except sqlite3.OperationalError as exc:
            if 'no such table' in str(exc):
                return 0, None
            raise
    if row is None:
        return 0, None
    cursor = row['cursor'] if type(row['cursor']) is int and row['cursor'] >= 0 else 0
    try:
        record = json.loads(row['record']) if row['record'] else None
        doc, _ = _verified(record)
        with closing(get_connection()) as conn:
            conn.execute('PRAGMA query_only=ON')
            require(conn.execute('SELECT 1 FROM lifeform_v1 WHERE lifeform_id=? AND owner_id=? AND created_day=?',
                                 (doc.lifeform_id, doc.owner_id, doc.created_day)).fetchone() is not None, 'growth_unverified')
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return 0, None
    return cursor, record


def _cache_write(cursor, record):
    with closing(get_connection()) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS pet_yard_growth_v1 (owner_id TEXT PRIMARY KEY,cursor INTEGER NOT NULL,verified_json TEXT NOT NULL)')
        conn.execute('INSERT INTO pet_yard_growth_v1 VALUES (?,?,?) ON CONFLICT(owner_id) DO UPDATE SET cursor=excluded.cursor,verified_json=excluded.verified_json',
                     (_authority()._owner_id(), cursor, json.dumps(record, separators=(',', ':'))))
        conn.commit()


def _view(record, *, verified=True, notice='', more=False, full=False):
    doc, state = _verified(record)
    genome = state.get('genome') or {}
    drawing = appearance.render_spec(doc, stage=state['stage'], genesis_seed=doc.genesis_seed,
                                     archetype=genome.get('archetype'), lineage=genome.get('lineage'),
                                     rings=len(state['growth_rings']))
    result = {'exists': True, 'verified': verified, 'notice': notice, 'more': more,
              'stage': state['stage'], 'spec': drawing, 'manifest': appearance.manifest(drawing)}
    if full:
        result.update(state=state, display_name=doc.display_name, genome=genome)
    return result


def inspect(full=False):
    """Read-only projection. A damaged main record never repairs itself."""
    try:
        with serialized():
            try:
                record = _main()
                if record is None:
                    return {'exists': False, 'verified': False, 'notice': '', 'more': False}
                return _view(record, full=full)
            except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
                _, cached = _cache_read()
                if cached:
                    return _view(cached, verified=False, notice='growth_unverified', full=full)
                return {'exists': True, 'verified': False, 'notice': 'growth_unverified', 'more': False}
    except (sqlite3.Error, OSError, YardError):
        return {'exists': False, 'verified': False, 'notice': 'unavailable', 'more': False}


def _facts(cursor):
    # This connection cannot write, including through a trigger or future accidental SQL.
    with closing(_read_conn()) as conn:
        maximum = conn.execute('SELECT COALESCE(MAX(rowid),0) FROM execution_facts').fetchone()[0]
        if cursor > maximum:
            cursor = 0
        rows = conn.execute('''SELECT rowid AS sequence,fact_id,turn_key,session_id,kind,
             substr(name,1,256) AS name,ok,status,created_at,
             CASE WHEN length(detail_json)<=8192 AND json_valid(detail_json) THEN json_extract(detail_json,'$.duration_s') END AS duration_s,
             CASE WHEN length(detail_json)<=8192 AND json_valid(detail_json) THEN json_extract(detail_json,'$.tokens_total') END AS tokens_total,
             CASE WHEN length(detail_json)<=8192 AND json_valid(detail_json) THEN json_extract(detail_json,'$.categories') END AS categories
             FROM execution_facts WHERE rowid>? ORDER BY rowid LIMIT ?''', (cursor, BATCH+1)).fetchall()
    return rows[:BATCH], len(rows) > BATCH


def _number(value, maximum):
    if type(value) not in (int, float) or not math.isfinite(value):
        return 0
    return max(0, min(maximum, value))


def refresh():
    """A bounded, operator-invoked feed. Save the signed log before its cursor."""
    if not REAL_USE_GROWTH:
        return inspect()
    with serialized():
        try:
            record = _main()
            if record is None:
                return {'exists': False, 'verified': False, 'notice': '', 'more': False}
            doc, _ = _verified(record)
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            _, cached = _cache_read()
            return _view(cached, verified=False, notice='growth_unverified') if cached else {
                'exists': True, 'verified': False, 'notice': 'growth_unverified', 'more': False}
        cursor, cached = _cache_read()
        if cached is None or cached['doc']['lifeform_id'] != doc.lifeform_id:
            cursor = 0
        rows, more = _facts(cursor)
        log = record['event_log']
        seen = {e['event_id'] for e in log}
        presence = {e['day'] for e in log if e['type'] == 'daily_active_use' and e['verified'] and not e['degraded']}
        now = datetime.now(timezone.utc)
        for row in rows:
            cursor = row['sequence']
            fact = dict(row)
            if not signals.is_successful_fact(fact) or not fact['fact_id'] or not fact['turn_key']:
                continue
            try:
                stamped = datetime.fromisoformat(str(fact['created_at']).replace('Z', '+00:00'))
                require(stamped.tzinfo is not None and stamped <= now and stamped.astimezone(timezone.utc).date() >= date.fromisoformat(doc.created_day))
                fact['created_at'] = stamped.astimezone(timezone.utc).isoformat()
                fact['detail'] = {'duration_s': _number(fact.pop('duration_s'), 365*86400),
                                  'tokens_total': int(_number(fact.pop('tokens_total'), 10**9))}
                categories = json.loads(fact.get('categories') or '[]')
                fact['categories'] = categories if type(categories) is list else []
                drafts = signals.fact_to_signals(fact)
            except (ValueError, TypeError, OverflowError):
                continue
            day = fact['created_at'][:10]
            if day not in presence:
                drafts.append(signals.daily_presence_signal(day, fact['turn_key'], fact['fact_id']))
                presence.add(day)
            for draft in drafts:
                event_id = progression.derive_event_id(draft['turn_key'], draft['fact_id'], draft['signal_type'])
                if event_id in seen:
                    continue
                event = progression.make_event(lifeform_id=doc.lifeform_id, seq=len(log)+1,
                                                prev_hash=log[-1]['hash'] if log else '', **draft)
                binding = (fact['turn_key'], fact['fact_id'])
                log, _ = progression.append_event(log, event, fact_resolver=lambda turn, fid, _, binding=binding: (turn, fid) == binding)
                seen.add(event_id)
        authority = _authority()
        state = progression.reduce_events(log, doc.genesis_seed)
        updated = progression.apply_state(doc, state)
        snapshot = progression.sign_snapshot(log, authority._signing_key(), authority._SIGNING_KEY_ID, doc.genesis_seed)
        lifeform_store.save_lifeform(to_json(updated), log, snapshot, authority._utcnow_iso())
        saved = {'doc': json.loads(to_json(updated)), 'event_log': log, 'snapshot': snapshot}
        _cache_write(cursor, saved)
        return _view(saved, more=more)
