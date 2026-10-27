"""File-only lending task transport, quotas, local decisions and matching receipts."""
from __future__ import annotations

import hashlib
import math
import secrets
import shutil
import time

from core.drop import DropError
from core.drop.service import CHUNK, _private_directory, _read, _write
from core.drop.session import b64, unb64

from .terms import MAX_OUTPUT, LendError, demand, file_name, identity, sha

TERMINAL = {'complete', 'declined', 'failed', 'timeout', 'revoked', 'expired', 'used_up', 'delivery_failed'}


class Jobs:
    def envelope(self, value, op, **fields):
        return {'op': op, 'invite': value['id'], 'digest': value['digest'], **fields}

    def folder(self, key, *, create=False):
        path = self.root/identity(key)
        if create:
            _private_directory(path, create=True)
        return path

    def clear_bytes(self, key):
        path = self.folder(key)
        if path.exists():
            _private_directory(path)
            shutil.rmtree(path)

    def submit(self, key, data, name, *, job_id=None):
        value = self.live(key)
        demand(value['role'] == 'guest' and value['state'] == 'ready', 'HOST_NOT_CONFIRMED')
        demand(value['used'] < value['terms']['jobs'], 'USED_UP')
        demand(isinstance(data, bytes) and 0 < len(data) <= value['terms']['max_bytes'], 'INPUT_TOO_BIG')
        file_name(name)
        key = identity(job_id) if job_id is not None else secrets.token_hex(16)
        demand(not self.db.execute('SELECT 1 FROM jobs WHERE id=?', (key,)).fetchone(), 'REPLAY')
        job = {'id': key, 'invite': value['id'], 'sid': value['sid'], 'role': 'guest', 'name': name,
               'size': len(data), 'input_digest': hashlib.sha256(data).hexdigest(),
               'state': 'submitted', 'created': self.drop.clock(), 'receipt': None}
        self.put('jobs', job)
        value['used'] += 1
        self.put('invites', value)
        self.send(value['sid'], self.envelope(value, 'job', job=key, name=name,
                                            size=len(data), input_digest=job['input_digest']))
        self.send_chunks(value, key, data)
        return key

    def send_chunks(self, value, job, data):
        for index in range(math.ceil(len(data)/CHUNK)):
            self.send(value['sid'], self.envelope(value, 'chunk', job=job, index=index,
                                                 data=b64(data[index*CHUNK:(index+1)*CHUNK])))

    def state(self, job, state, *, seconds=None):
        job['state'] = state
        value = self.get('invites', job['invite'])
        # A restarted host cannot reconstruct execution time from a wall clock.
        # Jobs that never started used zero worker time; interrupted runs are unknown.
        if seconds is None and 'started' not in job:
            seconds = 0
        if seconds is None and job.get('execution_receipt'):
            seconds = job['execution_receipt']['seconds']
        job['receipt'] = self.receipt(job, value, state, seconds=seconds)
        self.put('jobs', job)
        self.clear_bytes(job['id'])
        self.send(job['sid'], self.envelope(value, 'state', job=job['id'], state=state, receipt=job['receipt']))

    def receipt(self, job, value, state, *, result=None, seconds=0):
        return {'job': job['id'], 'terms_digest': value['digest'], 'input_digest': job['input_digest'],
                'result_digest': result, 'seconds': seconds, 'finished': self.drop.clock(), 'state': state}

    def check_receipt(self, receipt, job, value, state):
        demand(isinstance(receipt, dict) and set(receipt) ==
               {'job', 'terms_digest', 'input_digest', 'result_digest', 'seconds', 'finished', 'state'})
        demand(receipt['job'] == job['id'] and receipt['terms_digest'] == value['digest']
               and receipt['input_digest'] == job['input_digest'] and receipt['state'] == state)
        seconds = receipt['seconds']
        demand((seconds is None and state != 'complete') or
               (type(seconds) in {int, float} and math.isfinite(seconds) and 0 <= seconds <= 604800))
        demand(type(receipt['finished']) is int and receipt['finished'] > 0)
        if state == 'complete':
            demand(seconds is not None and seconds <= value['terms']['max_seconds'])
            sha(receipt['result_digest'])
            demand(receipt['finished'] <= value['terms']['expires'])
        else:
            demand(receipt['result_digest'] is None)

    def receive_job(self, sid, payload, value):
        base = {'type', 'op', 'invite', 'digest'}
        op = payload['op']
        demand(value['sid'] == sid and (value['state'] == 'ready' or
               (op == 'state' and value['role'] == 'guest') or
               (op == 'delivered' and value['role'] == 'host')), 'WRONG_SESSION')
        if op == 'job':
            demand(value['role'] == 'host', 'WRONG_ROLE')
            demand(set(payload) == base | {'job', 'name', 'size', 'input_digest'})
            key = identity(payload['job'])
            demand(not self.db.execute('SELECT 1 FROM jobs WHERE id=?', (key,)).fetchone(), 'REPLAY')
            demand(type(payload['size']) is int and 0 < payload['size'] <= value['terms']['max_bytes'], 'INPUT_TOO_BIG')
            name = payload['name']
            file_name(name)
            sha(payload['input_digest'])
            if value['used'] >= value['terms']['jobs']:
                receipt = self.receipt({'id': key, 'input_digest': payload['input_digest']}, value, 'used_up')
                self.send(sid, self.envelope(value, 'state', job=key, state='used_up', receipt=receipt))
                return
            # Reserve before accepting any bytes. Replays, failures, declines
            # and interrupted transfers never restore an execution allowance.
            value['used'] += 1
            self.put('invites', value)
            self.put('jobs', {'id': key, 'invite': value['id'], 'sid': sid, 'role': 'host',
                              'name': name, 'size': payload['size'], 'input_digest': payload['input_digest'],
                              'state': 'receiving', 'created': self.drop.clock(), 'receipt': None})
            self.folder(key, create=True)
            return
        key = identity(payload.get('job'))
        if op == 'chunk' and not self.db.execute('SELECT 1 FROM jobs WHERE id=?', (key,)).fetchone():
            demand(value['role'] == 'host' and value['used'] < value['terms']['jobs'], 'REPLAY')
            raise DropError('TASK_BEFORE_PACKET')
        job = self.get('jobs', key)
        demand(job['invite'] == value['id'] and job['sid'] == sid, 'WRONG_SESSION')
        if op == 'chunk':
            demand(set(payload) == base | {'job', 'index', 'data'})
            if job['role'] == 'guest' and job['state'] in {'submitted', 'host_unreachable', 'waiting_approval'}:
                raise DropError('TASK_BEFORE_PACKET')
            demand(job['state'] in {'receiving', 'receiving_result'}, 'REPLAY')
            size = job['size'] if job['role'] == 'host' else job['result_size']
            expected_digest = job['input_digest'] if job['role'] == 'host' else job['output_digest']
            count = math.ceil(size/CHUNK)
            index = payload['index']
            demand(type(index) is int and 0 <= index < count)
            data = unb64(payload['data'], 0, CHUNK)
            demand(len(data) == min(CHUNK, size-index*CHUNK))
            folder = self.folder(key, create=True)
            path = folder/f'{index:04d}.part'
            if path.exists():
                demand(_read(path, CHUNK) == data, 'CHUNK_CONFLICT')
            _write(path, data)
            job['last_progress'] = self.drop.clock()
            self.put('jobs', job)
            parts = [folder/f'{n:04d}.part' for n in range(count)]
            if all(part.exists() for part in parts):
                complete = b''.join(_read(part, CHUNK) for part in parts)
                if hashlib.sha256(complete).hexdigest() != expected_digest:
                    if job['role'] == 'host':
                        self.state(job, 'failed')
                    else:
                        job['state'] = 'delivery_failed'
                        self.put('jobs', job)
                        self.clear_bytes(key)
                    return
                _write(folder/'file.bin', complete)
                for part in parts:
                    part.unlink()
                job['state'] = 'pending' if job['role'] == 'host' else 'complete'
                self.put('jobs', job)
                if job['role'] == 'guest':
                    self.send(sid, self.envelope(value, 'delivered', job=key, receipt=job['receipt']))
                else:
                    self.send(sid, self.envelope(value, 'queued', job=key))
        elif op == 'queued':
            demand(set(payload) == base | {'job'})
            demand(job['role'] == 'guest' and job['state'] in {'submitted', 'host_unreachable'}, 'REPLAY')
            job['state'] = 'waiting_approval'
            self.put('jobs', job)
        elif op == 'result':
            demand(set(payload) == base | {'job', 'size', 'name', 'receipt'})
            demand(job['role'] == 'guest' and job['state'] in {'submitted', 'host_unreachable', 'waiting_approval'}, 'REPLAY')
            receipt = payload['receipt']
            self.check_receipt(receipt, job, value, 'complete')
            demand(type(payload['size']) is int and 0 < payload['size'] <= MAX_OUTPUT)
            demand(payload['name'] in {'document.pdf', 'contact-sheet.pdf', 'recording.wav'})
            job.update(state='receiving_result', result_size=payload['size'], result_name=payload['name'],
                       output_digest=receipt['result_digest'], receipt=receipt, last_progress=self.drop.clock())
            self.put('jobs', job)
        elif op == 'delivered':
            demand(set(payload) == base | {'job', 'receipt'})
            demand(job['role'] == 'host' and job['state'] in {'returning', 'revoked', 'expired'}
                   and isinstance(job.get('execution_receipt'), dict))
            self.check_receipt(payload['receipt'], job, value, 'complete')
            demand(payload['receipt'] == job['execution_receipt'])
            job['state'] = 'complete'
            job['receipt'] = payload['receipt']
            self.put('jobs', job)
        elif op == 'state':
            demand(set(payload) == base | {'job', 'state', 'receipt'})
            demand(job['role'] == 'guest' and job['state'] in {'submitted', 'host_unreachable', 'waiting_approval',
                                                                           'receiving_result', 'delivery_failed', 'revoked', 'expired'})
            demand(payload['state'] in {'declined', 'failed', 'timeout', 'revoked', 'expired', 'used_up'})
            if job['state'] in {'receiving_result', 'delivery_failed'}:
                demand(payload['state'] in {'revoked', 'expired'})
            if job['state'] in {'revoked', 'expired'}:
                demand(payload['state'] == job['state'])
            self.check_receipt(payload['receipt'], job, value, payload['state'])
            job['state'] = payload['state']
            job['receipt'] = payload['receipt']
            self.put('jobs', job)
            self.clear_bytes(key)
        else:
            demand(False, 'INVALID_TASK')

    def run(self, key):
        from .worker import execute

        with self.drop.scope():
            job = self.get('jobs', key)
            value = self.live(job['invite'])
            demand(job['role'] == 'host' and job['state'] == 'pending', 'APPROVAL_REQUIRED_OR_ALREADY_RUN')
            job.update(state='running', started=self.drop.clock())
            self.put('jobs', job)
            try:
                data = _read(self.folder(key)/'file.bin', value['terms']['max_bytes'])
            except (DropError, OSError):
                self.state(job, 'failed', seconds=0)
                return self.get('jobs', key)
        failure = None
        started = time.monotonic()
        try:
            demand(hashlib.sha256(data).hexdigest() == job['input_digest'], 'INPUT_CHANGED')
            output, name, seconds = execute(self.root, data, value['terms'], job_id=key)
            demand(self.drop.clock() < value['terms']['expires'], 'EXPIRED')
            demand(seconds <= value['terms']['max_seconds'], 'TIMEOUT')
        except (LendError, DropError, OSError) as exc:
            failure = 'expired' if self.drop.clock() >= value['terms']['expires'] else (
                'timeout' if getattr(exc, 'code', '') == 'TIMEOUT' else 'failed')
        with self.drop.scope():
            job = self.get('jobs', key)
            value = self.get('invites', job['invite'])
            if value['state'] in {'revoked', 'expired'} or self.drop.clock() >= value['terms']['expires']:
                failure = 'revoked' if value['state'] == 'revoked' else 'expired'
            if failure:
                self.state(job, failure, seconds=round(time.monotonic()-started, 3))
            else:
                demand(job['state'] == 'running', 'JOB_FAILED')
                # No global/Drop lock is held while the worker runs. A revoke
                # can refuse queued jobs immediately and withhold this result.
                self.clear_bytes(key)
                receipt = self.receipt(job, value, 'complete', result=hashlib.sha256(output).hexdigest(), seconds=seconds)
                job.update(state='returning', receipt=receipt, execution_receipt=receipt)
                self.put('jobs', job)
                self.send(job['sid'], self.envelope(value, 'result', job=key, size=len(output), name=name, receipt=receipt))
                self.send_chunks(value, key, output)
            return self.get('jobs', key)

    def decline(self, key):
        job = self.get('jobs', key)
        demand(job['role'] == 'host' and job['state'] in {'receiving', 'pending'}, 'JOB_NOT_PENDING')
        self.state(job, 'declined')

    def revoke(self, key):
        value = self.get('invites', key)
        demand(value['role'] == 'host', 'WRONG_ROLE')
        value['state'] = 'revoked'
        self.put('invites', value)
        for job in self.all('jobs'):
            if job['invite'] == key and job['state'] in {'receiving', 'pending'}:
                self.state(job, 'revoked')
        if value['sid']:
            self.send(value['sid'], self.envelope(value, 'revoked'))
        else:
            self.drop._discard_invites('token=?', (value['drop'].rsplit('/', 1)[1],))

    def sweep(self):
        now = self.drop.clock()
        for value in self.all('invites'):
            if value['terms']['expires'] <= now and value['state'] not in {'revoked', 'expired'}:
                value['state'] = 'expired'
                self.put('invites', value)
                if value['role'] == 'host' and not value['sid']:
                    self.drop._discard_invites('token=?', (value['drop'].rsplit('/', 1)[1],))
            elif value['role'] == 'guest' and value['state'] == 'confirming' and now-value['created'] > 30000:
                value['state'] = 'host_unreachable'
                self.put('invites', value)
        for job in self.all('jobs'):
            value = self.get('invites', job['invite'])
            if value['state'] in {'revoked', 'expired'}:
                if job['state'] not in TERMINAL:
                    if job['role'] == 'host':
                        self.state(job, value['state'])
                    else:
                        job['state'] = value['state']
                        self.put('jobs', job)
                self.clear_bytes(job['id'])
            elif job['state'] == 'submitted' and now-job['created'] > 30000:
                job['state'] = 'host_unreachable'
                self.put('jobs', job)
            elif job['state'] == 'receiving' and now-job['created'] > 30000:
                self.state(job, 'timeout')
            elif job['state'] == 'receiving_result' and now-job['last_progress'] > 30000:
                job['state'] = 'delivery_failed'
                self.put('jobs', job)
                self.clear_bytes(job['id'])
            elif job['state'] == 'running' and now-job['started'] > value['terms']['max_seconds']*1000+2000:
                self.state(job, 'failed')

    def poll(self):
        outcomes = self.drop.poll()
        pending = None
        with self.drop.scope():
            self.sweep()
            for job in self.all('jobs'):
                value = self.get('invites', job['invite'])
                if job['role'] == 'host' and job['state'] == 'pending' and value['terms']['auto']:
                    pending = job['id']
                    break  # One bounded job per poll; control returns between jobs.
        if pending:
            try:
                self.run(pending)
            except LendError as exc:
                if exc.code not in {'REVOKED', 'EXPIRED', 'APPROVAL_REQUIRED_OR_ALREADY_RUN'}:
                    raise
        return outcomes
