"""Revisioned local cosmetics with content-addressed, lazily served media."""
from __future__ import annotations

import base64
import json
import sqlite3
from contextlib import closing

from core import pet_cosmetics as contract
from core.pet_world.model import MAX_REVISION, YardError, integer, keys, require
from storage.db import get_connection

MAX_PETS = 16
MAX_SOUNDS = 32
MAX_ASSET_BYTES = 32*1024*1024


def _ensure(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS pet_cosmetic_assets_v1 (digest TEXT PRIMARY KEY,mime TEXT NOT NULL,data BLOB NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS pet_cosmetic_pets_v1 (digest TEXT PRIMARY KEY,metadata TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS pet_cosmetic_sounds_v1 (digest TEXT PRIMARY KEY,name TEXT NOT NULL,seconds REAL NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS pet_cosmetic_options_v1 (singleton INTEGER PRIMARY KEY CHECK(singleton=1),revision INTEGER NOT NULL,document TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS pet_cosmetic_options_backups_v1 (id INTEGER PRIMARY KEY,revision INTEGER NOT NULL,document TEXT NOT NULL)')
    conn.commit()


def _read(conn):
    pets, sounds, damaged = {}, {}, False
    for row in conn.execute('SELECT digest,CASE WHEN length(metadata)<=131072 THEN metadata END AS metadata FROM pet_cosmetic_pets_v1 LIMIT 100'):
        try:
            metadata = contract.validate_metadata(row['metadata'], row['digest'])
            pets['custom-'+row['digest']] = metadata
        except (ValueError, TypeError, KeyError):
            damaged = True
    for row in conn.execute('SELECT digest,substr(name,1,65) AS name,seconds FROM pet_cosmetic_sounds_v1 LIMIT 100'):
        try:
            require(type(row['digest']) is str and contract.SHA.fullmatch(row['digest']))
            contract.text(row['name'], 64)
            require(type(row['seconds']) in (int, float) and .1 <= row['seconds'] <= 5)
            sounds[row['digest']] = {'name': row['name'], 'seconds': row['seconds']}
        except (ValueError, TypeError):
            damaged = True
    available = {*sounds, *(clip['sha256'] for p in pets.values() for clip in p['clips'].values())}
    row = conn.execute('SELECT revision,length(document) AS bytes,CASE WHEN length(document)<=131072 THEN document END AS document FROM pet_cosmetic_options_v1 WHERE singleton=1').fetchone()
    revision, config, read_only, stamp = 0, contract.default_config(), False, ''
    if row:
        revision = row['revision'] if integer(row['revision'], 0, MAX_REVISION) else 0
        stamp = contract.digest(f"{row['revision']}|{row['bytes']}|{row['document']}".encode())
        try:
            require(integer(row['revision'], 0, MAX_REVISION), 'corrupt')
            config = contract.validate_config(row['document'], pets, available)
        except (ValueError, TypeError, KeyError):
            read_only = True
    return {'revision': revision, 'config': config, 'pets': pets, 'sounds': sounds,
            'read_only': read_only, 'notice': 'corrupt' if read_only or damaged else '', 'stamp': stamp}


def _public(state):
    result = {key: state[key] for key in ('revision', 'config', 'read_only', 'notice', 'stamp')}
    result.update(ok=True, pets=[{**metadata, 'source_id': metadata['id'], 'id': identifier} for identifier, metadata in state['pets'].items()],
                  sounds=[{'id': identifier, **metadata} for identifier, metadata in state['sounds'].items()])
    return result


def library():
    try:
        with closing(get_connection()) as conn:
            _ensure(conn)
            return _public(_read(conn))
    except (sqlite3.Error, OSError):
        return {'ok': True, 'revision': 0, 'config': contract.default_config(), 'pets': [], 'sounds': [],
                'read_only': True, 'notice': 'unavailable', 'stamp': ''}


def _asset(conn, sha):
    require(type(sha) is str and contract.SHA.fullmatch(sha))
    row = conn.execute('SELECT mime,CASE WHEN length(data)<=? THEN data END AS data FROM pet_cosmetic_assets_v1 WHERE digest=?',
                       (contract.PNG_BYTES, sha)).fetchone()
    require(row is not None, 'missing', 404)
    require(row['mime'] in ('image/png', 'audio/wav') and type(row['data']) is bytes
            and contract.digest(row['data']) == sha, 'corrupt', 409)
    return row['mime'], row['data']


def asset(sha):
    try:
        with closing(get_connection()) as conn:
            _ensure(conn)
            return _asset(conn, sha)
    except (sqlite3.Error, OSError) as exc:
        raise YardError('unavailable', 503) from exc


def _insert_assets(conn, assets):
    current = conn.execute('SELECT COALESCE(SUM(length(data)),0) FROM pet_cosmetic_assets_v1').fetchone()[0]
    for sha, (mime, data) in assets.items():
        existing = conn.execute('SELECT 1 FROM pet_cosmetic_assets_v1 WHERE digest=?', (sha,)).fetchone()
        if existing:
            require(_asset(conn, sha) == (mime, data), 'corrupt', 409)
        else:
            current += len(data)
            require(current <= MAX_ASSET_BYTES, 'quota', 409)
            conn.execute('INSERT INTO pet_cosmetic_assets_v1 VALUES (?,?,?)', (sha, mime, data))


def _collect(conn, state):
    # A damaged manifest may contain references we cannot reconstruct safely.
    if state['notice']:
        return
    used = set(state['sounds'])
    for pet in state['pets'].values():
        used.add(pet['atlas']['sha256'])
        used.update(c['sha256'] for c in pet['clips'].values())
    for choices in state['config']['bindings'].values():
        used.update(c['sound'] for c in choices.values() if contract.SHA.fullmatch(c['sound']))
    for row in conn.execute('SELECT digest FROM pet_cosmetic_assets_v1'):
        if row[0] not in used:
            conn.execute('DELETE FROM pet_cosmetic_assets_v1 WHERE digest=?', (row[0],))


def change(action, body):
    fields = {'import': ('revision', 'document'), 'sound': ('revision', 'name', 'wav'),
              'configure': ('revision', 'config'), 'remove': ('revision', 'id', 'confirmed'),
              'reset': ('revision', 'stamp', 'confirmed')}
    require(action in fields)
    keys(body, fields[action])
    require(integer(body['revision'], 0, MAX_REVISION))
    prepared = contract.validate_pack(body['document']) if action == 'import' else None
    audio = None
    if action == 'sound':
        contract.text(body['name'], 64)
        audio = contract.wav(contract.unpack(body['wav'], contract.WAV_BYTES))
    try:
        with closing(get_connection()) as conn:
            _ensure(conn)
            try:
                conn.execute('BEGIN IMMEDIATE')
                state = _read(conn)
                require(state['revision'] == body['revision'] and state['revision'] < MAX_REVISION, 'conflict', 409)
                require(not state['read_only'] or action == 'reset', 'corrupt', 409)
                config = state['config']
                if action == 'import':
                    _metadata, canonical, sha, assets = prepared
                    existing = conn.execute('SELECT 1 FROM pet_cosmetic_pets_v1 WHERE digest=?', (sha,)).fetchone()
                    count = conn.execute('SELECT COUNT(*) FROM pet_cosmetic_pets_v1').fetchone()[0]
                    require(existing or count < MAX_PETS, 'quota', 409)
                    _insert_assets(conn, assets)
                    conn.execute('INSERT INTO pet_cosmetic_pets_v1 VALUES (?,?) ON CONFLICT(digest) DO NOTHING', (sha, canonical))
                elif action == 'sound':
                    raw, seconds = audio
                    sha = contract.digest(raw)
                    count = conn.execute('SELECT COUNT(*) FROM pet_cosmetic_sounds_v1').fetchone()[0]
                    existing = conn.execute('SELECT 1 FROM pet_cosmetic_sounds_v1 WHERE digest=?', (sha,)).fetchone()
                    require(existing or count < MAX_SOUNDS, 'quota', 409)
                    _insert_assets(conn, {sha: ('audio/wav', raw)})
                    conn.execute('INSERT INTO pet_cosmetic_sounds_v1 VALUES (?,?,?) ON CONFLICT(digest) DO UPDATE SET name=excluded.name',
                                 (sha, body['name'], seconds))
                elif action == 'configure':
                    sounds = {*state['sounds'], *(c['sha256'] for p in state['pets'].values() for c in p['clips'].values())}
                    config = contract.validate_config(json.dumps(body['config']), state['pets'], sounds)
                elif action == 'reset':
                    require(body['confirmed'] is True and body['stamp'] == state['stamp'], 'conflict', 409)
                    conn.execute('INSERT INTO pet_cosmetic_options_backups_v1 (revision,document) SELECT revision,document FROM pet_cosmetic_options_v1 WHERE singleton=1')
                    config = contract.default_config()
                elif action == 'remove':
                    require(body['confirmed'] is True and type(body['id']) is str)
                    identifier = body['id']
                    if identifier.startswith('custom-'):
                        require(contract.CUSTOM_ID.fullmatch(identifier))
                        from storage.pet_world_store import referenced_pet_ids

                        yard_pets = referenced_pet_ids(conn)
                        require(yard_pets is not None and identifier not in yard_pets, 'pet_in_yard', 409)
                        conn.execute('DELETE FROM pet_cosmetic_pets_v1 WHERE digest=?', (identifier[7:],))
                        config['bindings'].pop(identifier, None)
                        if config['selected'] == identifier:
                            config['selected'] = ''
                    else:
                        require(contract.SHA.fullmatch(identifier))
                        conn.execute('DELETE FROM pet_cosmetic_sounds_v1 WHERE digest=?', (identifier,))
                        for choices in config['bindings'].values():
                            for choice in choices.values():
                                if choice['sound'] == identifier:
                                    choice['sound'] = ''
                conn.execute('INSERT INTO pet_cosmetic_options_v1 VALUES (1,?,?) ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision,document=excluded.document',
                             (state['revision']+1, json.dumps(config, sort_keys=True, separators=(',', ':'))))
                updated = _read(conn)
                require(not updated['read_only'], 'corrupt', 409)
                _collect(conn, updated)
                conn.commit()
                return _public(updated)
            except BaseException:
                conn.rollback()
                raise
    except (sqlite3.Error, OSError) as exc:
        raise YardError('unavailable', 503) from exc


def export(identifier):
    require(type(identifier) is str and contract.CUSTOM_ID.fullmatch(identifier))
    with closing(get_connection()) as conn:
        _ensure(conn)
        state = _read(conn)
        require(identifier in state['pets'], 'missing', 404)
        metadata = state['pets'][identifier]
        result = {key: metadata[key] for key in ('schema', 'version', 'id', 'name', 'author', 'license', 'description', 'frames', 'bindings')}
        result['atlas'] = {'cell': contract.CELL, 'png': base64.b64encode(_asset(conn, metadata['atlas']['sha256'])[1]).decode('ascii')}
        result['clips'] = {key: {'name': c['name'], 'wav': base64.b64encode(_asset(conn, c['sha256'])[1]).decode('ascii')}
                           for key, c in metadata['clips'].items()}
        raw = json.dumps(result, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        require(contract.validate_pack(raw)[2] == identifier[7:], 'corrupt', 409)
        return raw
