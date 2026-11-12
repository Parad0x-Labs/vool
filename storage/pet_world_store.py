"""One local yard, transactional revisions, and preserved reset copies."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing

from core.pet_world.catalog import MAX_BYTES
from core.pet_world.model import MAX_REVISION, YardError, integer, require, starter, validate
from storage.db import get_connection


def _ensure(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS pet_yard_v1 (singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL, document TEXT NOT NULL, digest TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS pet_yard_backups_v1 (id INTEGER PRIMARY KEY, revision INTEGER NOT NULL, document TEXT NOT NULL, digest TEXT NOT NULL)')
    conn.commit()


def _read(conn):
    header = conn.execute('SELECT revision,substr(digest,1,65) AS digest,length(CAST(document AS BLOB)) AS bytes FROM pet_yard_v1 WHERE singleton=1').fetchone()
    if header is None:
        return {'yard': starter(), 'notice': '', 'read_only': False, 'fresh': True, 'stamp': ''}
    revision = header['revision']
    stamp = hashlib.sha256(f"{revision}|{header['digest']}|{header['bytes']}".encode()).hexdigest()
    try:
        require(integer(revision, 0, MAX_REVISION) and header['bytes'] <= MAX_BYTES)
        raw = conn.execute('SELECT document FROM pet_yard_v1 WHERE singleton=1').fetchone()[0]
        yard, canonical = validate(raw)
        require(yard['revision'] == revision and hashlib.sha256(canonical.encode()).hexdigest() == header['digest'])
        return {'yard': yard, 'notice': '', 'read_only': False, 'fresh': False, 'stamp': stamp}
    except (YardError, TypeError, UnicodeError):
        yard = starter()
        yard['revision'] = revision if integer(revision, 0, MAX_REVISION) else 0
        return {'yard': yard, 'notice': 'corrupt', 'read_only': True, 'fresh': False, 'stamp': stamp}


def load():
    try:
        with closing(get_connection()) as conn:
            _ensure(conn)
            return _read(conn)
    except (sqlite3.Error, OSError):
        return {'yard': starter(), 'notice': 'unavailable', 'read_only': True, 'fresh': False, 'stamp': ''}


def referenced_pet_ids(conn):
    """Read within the caller's transaction; unknown references prevent asset deletion."""
    try:
        row = conn.execute('SELECT CASE WHEN length(CAST(document AS BLOB))<=? THEN document END FROM pet_yard_v1 WHERE singleton=1', (MAX_BYTES,)).fetchone()
    except sqlite3.OperationalError as exc:
        if 'no such table' in str(exc):
            return set()
        raise
    if row is None:
        return set()
    try:
        return {p['id'] for p in validate(row[0])[0]['pets']}
    except (ValueError, TypeError):
        return None


def save(raw, *, archive=False, stamp=None):
    yard, _ = validate(raw)
    try:
        with closing(get_connection()) as conn:
            _ensure(conn)
            try:
                conn.execute('BEGIN IMMEDIATE')
                current = _read(conn)
                require(not current['read_only'], 'corrupt', 409)
                if archive:
                    require(type(stamp) is str and stamp == current['stamp'], 'conflict', 409)
                require(yard['revision'] == current['yard']['revision'], 'conflict', 409)
                require(yard['revision'] < MAX_REVISION, 'conflict', 409)
                yard['revision'] += 1
                _, canonical = validate(json.dumps(yard))
                if archive:
                    conn.execute('INSERT INTO pet_yard_backups_v1 (revision,document,digest) SELECT revision,document,digest FROM pet_yard_v1 WHERE singleton=1')
                conn.execute('INSERT INTO pet_yard_v1 VALUES (1,?,?,?) ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision,document=excluded.document,digest=excluded.digest',
                             (yard['revision'], canonical, hashlib.sha256(canonical.encode()).hexdigest()))
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            return _read(conn)
    except (sqlite3.Error, OSError) as exc:
        raise YardError('unavailable', 503) from exc


def reset(revision, stamp, confirmed):
    require(confirmed is True and integer(revision, 0, MAX_REVISION) and type(stamp) is str)
    try:
        with closing(get_connection()) as conn:
            _ensure(conn)
            try:
                conn.execute('BEGIN IMMEDIATE')
                current = _read(conn)
                require(current['stamp'] == stamp and current['yard']['revision'] == revision, 'conflict', 409)
                require(revision < MAX_REVISION, 'conflict', 409)
                # SQL copies even an oversized damaged document without loading it into memory.
                conn.execute('INSERT INTO pet_yard_backups_v1 (revision,document,digest) SELECT revision,document,digest FROM pet_yard_v1 WHERE singleton=1')
                yard = starter()
                yard['revision'] = revision+1
                _, canonical = validate(json.dumps(yard))
                conn.execute('INSERT INTO pet_yard_v1 VALUES (1,?,?,?) ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision,document=excluded.document,digest=excluded.digest',
                             (yard['revision'], canonical, hashlib.sha256(canonical.encode()).hexdigest()))
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            return _read(conn)
    except (sqlite3.Error, OSError) as exc:
        raise YardError('unavailable', 503) from exc
