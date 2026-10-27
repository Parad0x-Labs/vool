"""Local lending authority. A sealed guest message can never approve a job."""
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import secrets
import sqlite3
import threading
import time

from core.drop.service import _private_directory, discard_local_invite_tokens
from core.drop.store import secure_file

from . import settings
from .jobs import TERMINAL, Jobs
from .terms import canonical, demand, digest, identity, make_link, parse_link, terms

_LEDGER_LOCK = threading.RLock()


@contextlib.contextmanager
def _ledger_files(root):
    # Only opening/closing needs this lock. A worker never holds it while
    # processing a file, so host decisions remain available during execution.
    with _LEDGER_LOCK:
        path = root/'ledger.lock'
        secure_file(path, create=True)
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def _check_sidecars(root):
    # SQLite removes these when its last connection closes. Absence is normal;
    # an existing unsafe file must still be refused before open.
    for suffix in ('-wal', '-shm'):
        with contextlib.suppress(FileNotFoundError):
            secure_file(root/('lend.sqlite'+suffix))


class LendHome(Jobs):
    def __init__(self, drop):
        settings.require()
        self.drop = drop
        self.root = drop.home / 'lend'
        _private_directory(self.root, create=True)
        with _ledger_files(self.root):
            self._open()

    @classmethod
    def revoke_local_offers(cls):
        """Retire offers without enabling Drop or accessing its relay.

        An offer nobody joined loses its Drop pairing token from the local
        invite store (never the relay folder), and queued host jobs are marked
        revoked with a closing receipt and a resolved inbox item right away.
        A job already running or returning keeps its state; its own finish
        path records the revocation with the real worker time. Nothing here
        sends or waits for the features to be turned on again.
        """
        from types import SimpleNamespace

        from core.runtime_paths import active_vool_home

        root = active_vool_home()/'lend'
        if not root.exists():
            return
        _private_directory(root)
        owner = cls.__new__(cls)
        owner.root = root
        # Only the receipt's local timestamp is needed; no Drop home opens.
        owner.drop = SimpleNamespace(clock=lambda: int(time.time()*1000))
        with _ledger_files(root):
            owner._open()
        with owner:
            revoked = set()
            for value in owner.all('invites'):
                if value['role'] == 'host' and value['state'] not in {'revoked', 'expired'}:
                    value['state'] = 'revoked'
                    owner.put('invites', value)
                    revoked.add(value['id'])
                    if not value['sid']:
                        discard_local_invite_tokens(root.parent, (value['drop'].rsplit('/', 1)[1],))
            for job in owner.all('jobs'):
                if job['role'] == 'host' and job['invite'] in revoked and job['state'] not in TERMINAL:
                    if job['state'] in {'receiving', 'pending'}:
                        job['state'] = 'revoked'
                        job['receipt'] = owner.receipt(job, owner.get('invites', job['invite']), 'revoked', seconds=0)
                        owner.put('jobs', job)
                    owner.clear_bytes(job['id'])

    def _open(self):
        secure_file(self.root/'lend.sqlite', create=True)
        _check_sidecars(self.root)
        self.db = None
        try:
            self.db = sqlite3.connect(self.root/'lend.sqlite', timeout=15)
            self.db.row_factory = sqlite3.Row
            self.db.executescript('''
                PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA secure_delete=ON;
                CREATE TABLE IF NOT EXISTS invites (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            ''')
            _check_sidecars(self.root)
        except BaseException:
            if self.db is not None:
                with contextlib.suppress(sqlite3.Error):
                    self.db.close()
            raise

    def close(self):
        with _ledger_files(self.root):
            if self.db is not None:
                self.db.close()
                self.db = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def put(self, table, value):
        demand(table in {'invites', 'jobs'})
        self.db.execute(f'INSERT INTO {table}(id,data) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                        (identity(value['id']), canonical(value)))
        self.db.commit()
        if table == 'jobs' and value['role'] == 'host':
            try:
                from core.operator import notification_center as center

                if value['state'] == 'pending':
                    invite = self.get('invites', value['invite'])
                    center.record_item(dedupe_key='lend:'+value['id'], source_kind='background_run',
                        title='Lend jobs to friends', body=value['name'],
                        payload={'section':'needs', 'lend_job':value['id'], 'lend_name':value['name'],
                                 'lend_capability':invite['terms']['capability'], 'lend_bytes':value['size']})
                elif value['state'] in TERMINAL | {'running', 'returning'}:
                    center.supersede_by_key('lend:'+value['id'], source_kind='background_run')
            except (OSError, sqlite3.Error):
                logging.getLogger(__name__).warning('Lending inbox update unavailable')

    def get(self, table, key):
        demand(table in {'invites', 'jobs'})
        row = self.db.execute(f'SELECT data FROM {table} WHERE id=?', (identity(key),)).fetchone()
        demand(row is not None, 'UNKNOWN_INVITE' if table == 'invites' else 'UNKNOWN_JOB')
        return json.loads(row['data'])

    def all(self, table):
        demand(table in {'invites', 'jobs'})
        return [json.loads(row[0]) for row in self.db.execute(f'SELECT data FROM {table} ORDER BY rowid')]

    def invite(self, peer, value, *, confirmed):
        demand(confirmed is True, 'FINGERPRINT_CONFIRMATION_REQUIRED')
        value = terms(value, now=self.drop.clock(), fresh=True)
        from .worker import available

        demand(available(value['capability']), 'CAPABILITY_UNAVAILABLE')
        active = [v for v in self.all('invites') if v['role'] == 'host'
                  and v['state'] not in {'revoked', 'expired'} and v['terms']['expires'] > self.drop.clock()]
        demand(len(active) < 100, 'INVITE_LIMIT')
        link = self.drop.invite(peer)
        payload = {'id': secrets.token_hex(16), 'drop': link, 'host': self.drop.fingerprint,
                   'guest': peer, 'terms': value, 'digest': digest(value), 'version': 1}
        self.put('invites', {**payload, 'role': 'host', 'state': 'offered', 'sid': '', 'used': 0})
        return make_link(payload)

    def join(self, link, peer, *, confirmed):
        demand(confirmed is True, 'FINGERPRINT_CONFIRMATION_REQUIRED')
        value = parse_link(link, now=self.drop.clock())
        demand(value['host'] == peer and value['guest'] == self.drop.fingerprint, 'WRONG_FINGERPRINT')
        demand(not self.db.execute('SELECT 1 FROM invites WHERE id=?', (value['id'],)).fetchone(), 'REPLAY')
        sid = self.drop.join(value['drop'], peer)
        self.put('invites', {**value, 'role': 'guest', 'state': 'confirming', 'sid': sid, 'used': 0,
                             'created': self.drop.clock()})
        self.send(sid, {'op': 'claim', 'invite': value['id'], 'digest': value['digest']})
        return value['id']

    def send(self, sid, payload):
        self.drop._send(sid, {'type': 'lend', **payload})

    def live(self, key):
        value = self.get('invites', key)
        demand(value['state'] not in {'revoked', 'expired'}, value['state'].upper())
        terms(value['terms'], now=self.drop.clock())
        return value

    def receive(self, sid, payload):
        demand(isinstance(payload, dict) and payload.get('type') == 'lend')
        value = self.get('invites', payload.get('invite'))
        row = self.drop._live(sid)
        expected = value['guest'] if value['role'] == 'host' else value['host']
        demand(row['peer'] == expected, 'WRONG_FINGERPRINT')
        demand(payload.get('digest') == value['digest'], 'TERMS_CHANGED')
        final_notice = ((value['role'] == 'guest' and payload.get('op') in {'state', 'revoked'}) or
                        (value['role'] == 'host' and payload.get('op') == 'delivered'))
        if not final_notice:
            value = self.live(payload.get('invite'))
        if payload.get('op') == 'claim':
            demand(set(payload) == {'type', 'op', 'invite', 'digest'})
            demand(value['role'] == 'host' and value['sid'] in {'', sid}, 'REPLAY')
            value.update(sid=sid, state='ready')
            self.put('invites', value)
            self.send(sid, {'op': 'confirmed', 'invite': value['id'], 'digest': value['digest']})
        elif payload.get('op') == 'confirmed':
            demand(set(payload) == {'type', 'op', 'invite', 'digest'})
            demand(value['role'] == 'guest' and value['sid'] == sid, 'WRONG_SESSION')
            value['state'] = 'ready'
            self.put('invites', value)
        elif payload.get('op') == 'revoked':
            demand(set(payload) == {'type', 'op', 'invite', 'digest'})
            demand(value['role'] == 'guest' and value['sid'] == sid, 'WRONG_SESSION')
            value['state'] = 'revoked'
            self.put('invites', value)
        else:
            self.receive_job(sid, payload, value)


def receive(drop, sid, payload):
    """Called only after the unchanged Drop session layer authenticates a payload."""
    from core.drop import DropError

    from .terms import LendError

    try:
        with LendHome(drop) as home:
            home.receive(sid, payload)
    except LendError as exc:
        raise DropError('INVALID_TASK') from exc
