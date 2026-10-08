"""Independent, durable forget authority and the common serving/write fence.

Ordinary readers and writers do not own process-lifetime barriers. An explicit
admission lease may be armed before accepting a forget; abandoning that lease
is durable even when the pending record cannot be written. Without either a
lease or a published pending record, admission fails without changing memory.
"""
from __future__ import annotations

import atexit
import contextlib
import dataclasses
import fcntl
import functools
import inspect
import json
import os
import sqlite3
import threading
import uuid
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
_LEASES: dict[Path, Any] = {}
_POISONED: set[Path] = set()
_LOCAL = threading.local()
_RAISE_REFUSAL = object()


class ForgetStartError(RuntimeError):
    """Whether failed admission left a durable barrier for this operation."""

    def __init__(self, *, started: bool, operation_id: str = ""):
        super().__init__("independent forget authority failed" if started else "forget could not start")
        self.started = started
        self.operation_id = operation_id


def _authority_errors(fn):
    """Expose unavailable authority consistently without hiding its cause."""
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError('independent forget authority is unavailable') from exc
    return wrapped


def _home(runtime_home=None) -> Path:
    from core.runtime_paths import active_vool_home
    return Path(runtime_home).expanduser().resolve() if runtime_home else active_vool_home()


def _directory(runtime_home=None) -> Path:
    return _home(runtime_home) / '.forget-authority'


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _durable_write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex)
    try:
        with tmp.open('x', encoding='utf-8') as out:
            json.dump(payload, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        _sync_directory(path.parent)
        # Includes creation of the independent authority directory itself.
        _sync_directory(path.parent.parent)
    finally:
        if tmp.exists():
            tmp.unlink()


@contextlib.contextmanager
def serialized(runtime_home=None):
    """Cross-process fence shared by barrier, completion and ordinary writers."""
    directory = _directory(runtime_home)
    with _LOCK:
        held = getattr(_LOCAL, 'held', {})
        if directory in held:
            yield
            return
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / 'lock').open('a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            _LOCAL.held = {**held, directory: lock}
            try:
                yield
            finally:
                _LOCAL.held = held
                fcntl.flock(lock, fcntl.LOCK_UN)


def _read(directory: Path) -> dict:
    value = json.loads((directory / 'ledger.json').read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or set(value) != {'version', 'operations'}
            or type(value.get('version')) is not int or value['version'] != 1
            or not isinstance(value.get('operations'), dict)):
        raise RuntimeError('forget authority is invalid')
    for operation_id, row in value['operations'].items():
        if (not isinstance(operation_id, str) or not operation_id
                or not isinstance(row, dict) or set(row) not in ({'chat', 'token', 'status'}, {'chat', 'token', 'status', 'kind'})
                or not isinstance(row['chat'], str) or not isinstance(row['token'], str)
                or not row['token'].strip() or not isinstance(row['status'], str)
                or row.get('kind', 'forget') not in {'forget', 'admission'}
                or row['status'] not in {'pending', 'complete'}):
            raise RuntimeError('forget authority operation is invalid')
    return value


def _check_leases(directory: Path) -> None:
    if directory in _POISONED:
        raise RuntimeError('forget authority lease was abandoned')
    own = _LEASES.get(directory)
    if own is not None:
        live = os.fstat(own.fileno())
        retained = Path(own.name).stat()
        if (live.st_dev, live.st_ino) != (retained.st_dev, retained.st_ino):
            raise RuntimeError('forget authority lease identity changed')
    for path in directory.glob('lease-*'):
        if path.read_bytes() != b'armed\n':
            raise RuntimeError('forget authority lease is invalid')
        if own is not None and path == Path(own.name):
            continue
        with path.open('rb') as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            fcntl.flock(lease, fcntl.LOCK_UN)
            raise RuntimeError('forget authority has an unclean lease')


def _existing_carriers(home: Path) -> bool:
    data = home / 'data'
    return any(p.exists() for p in (
        data / 'memory' / 'vool_memory.db', data / 'memory' / 'nulla_memory.db',
        data / 'memory_entries.jsonl', data / 'conversation_log.jsonl',
        data / 'session_summaries.jsonl', data / 'MEMORY.md',
    ))


@_authority_errors
def prepare(runtime_home=None, *, allow_initialize=False, arm=True) -> None:
    directory = _directory(runtime_home)
    with serialized(runtime_home):
        if not (directory / 'ledger.json').exists():
            if not allow_initialize or _existing_carriers(_home(runtime_home)):
                raise RuntimeError('forget authority is absent for retained memory')
            _durable_write(directory / 'ledger.json', {'version': 1, 'operations': {}})
        _read(directory)
        _check_leases(directory)
        if arm and directory not in _LEASES:
            path = directory / ('lease-' + uuid.uuid4().hex)
            lease = path.open('x+b')
            try:
                fcntl.flock(lease, fcntl.LOCK_EX)
                lease.write(b'armed\n')
                lease.flush()
                os.fsync(lease.fileno())
                _sync_directory(directory)
                _LEASES[directory] = lease
            except BaseException:
                lease.close()
                _POISONED.add(directory)
                raise


@_authority_errors
def poison(runtime_home=None) -> None:
    """No write is required: unlock the pre-existing marker and retain it."""
    directory = _directory(runtime_home)
    with _LOCK:
        _POISONED.add(directory)
        lease = _LEASES.pop(directory, None)
        if lease is not None:
            try:
                fcntl.flock(lease, fcntl.LOCK_UN)
            finally:
                lease.close()


def _disarm(directory: Path) -> None:
    """A published operation replaces its admission lease as the barrier."""
    lease = _LEASES.get(directory)
    if lease is None:
        return
    Path(lease.name).unlink()
    _sync_directory(directory)
    _LEASES.pop(directory, None)
    lease.close()


def begin(chat_id, token, runtime_home=None, *, _kind='forget') -> str:
    from core.vool_memory import _normalize_forget_token
    directory = _directory(runtime_home)
    operation_id = uuid.uuid4().hex
    started = directory in _LEASES
    publication_attempted = False
    try:
        with serialized(runtime_home):
            if not (directory / 'ledger.json').exists():
                prepare(runtime_home, allow_initialize=True, arm=False)
            else:
                _check_leases(directory)
            data = _read(directory)
            data['operations'][operation_id] = {'chat': str(chat_id or ''), 'token': _normalize_forget_token(token), 'status': 'pending'}
            if _kind != 'forget':
                if _kind != 'admission':
                    raise ValueError('unknown forget operation kind')
                data['operations'][operation_id]['kind'] = _kind
            publication_attempted = True
            _durable_write(directory / 'ledger.json', data)
            started = True
            if _read(directory) != data:
                raise RuntimeError('forget barrier could not be verified')
            # A new process must not lose an otherwise recordable forget
            # merely because creating its own lease is unavailable. Retain
            # this pending record if arming that lease fails.
            prepare(runtime_home)
            _disarm(directory)
            return operation_id
    except BaseException as exc:
        if not started and publication_attempted:
            try:
                started = operation_id in _read(directory)['operations']
            except FileNotFoundError:
                pass
            except Exception:
                # An unreadable authority never certifies failed publication.
                started = True
        if started:
            poison(runtime_home)
        raise ForgetStartError(started=started, operation_id=operation_id if started else '') from exc


def complete(operation_id, runtime_home=None) -> None:
    try:
        with serialized(runtime_home):
            data = _read(_directory(runtime_home))
            _check_leases(_directory(runtime_home))
            row = data['operations'][operation_id]
            # A final sweep under this fence reconciles earlier attempts of
            # this exact erasure, never another scope/token or later attempt.
            for earlier_id, earlier in data['operations'].items():
                if (earlier['chat'] == row['chat'] and earlier['token'] == row['token']
                        and earlier.get('kind', 'forget') == row.get('kind', 'forget')):
                    earlier['status'] = 'complete'
                if earlier_id == operation_id:
                    break
            _durable_write(_directory(runtime_home) / 'ledger.json', data)
            if _read(_directory(runtime_home)) != data:
                raise RuntimeError('forget completion could not be verified')
    except BaseException as exc:
        poison(runtime_home)
        raise RuntimeError('independent forget authority failed') from exc


@_authority_errors
def snapshot(chat_id=None, runtime_home=None) -> tuple[str, ...]:
    with serialized(runtime_home):
        prepare(runtime_home, arm=False)
        data = _read(_directory(runtime_home))
        rows = [row for row in data['operations'].values()
                if not chat_id or not row['chat'] or row['chat'] == str(chat_id)]
        if any(row['status'] != 'complete' for row in rows):
            raise RuntimeError('memory erasure is incomplete')
        return tuple(sorted({row['token'] for row in rows if row['token'] and row.get('kind', 'forget') == 'forget'}))


@_authority_errors
def generation(chat_id=None, runtime_home=None) -> str:
    with serialized(runtime_home):
        prepare(runtime_home, arm=False)
        return json.dumps(_read(_directory(runtime_home)), sort_keys=True)


def revoked_tokens(chat_id=None, runtime_home=None, *, owner_memory=None) -> tuple[str, ...]:
    """One authority for independent barriers and every selected owner ledger."""
    homes = {_home(runtime_home)}
    if owner_memory is None:
        homes.add(_home())
    with contextlib.ExitStack() as locks:
        for home in sorted(homes):
            locks.enter_context(serialized(home))
        return _revoked_tokens_locked(chat_id, runtime_home, owner_memory=owner_memory)


def _revoked_tokens_locked(chat_id=None, runtime_home=None, *, owner_memory=None) -> tuple[str, ...]:
    tokens = set(snapshot(chat_id, runtime_home))
    from core import context_retrieval as cr
    opened = []
    try:
        memories = [owner_memory] if owner_memory is not None else []
        if owner_memory is None:
            for opener in (lambda: cr._open_memory_for_runtime(runtime_home), cr._open_memory):
                memory = opener()
                if memory is None:
                    raise RuntimeError('owner revocation authority is unavailable')
                opened.append(memory)
                memories.append(memory)
        for memory in memories:
            tokens.update(snapshot(chat_id, memory._authority_home))
            tokens.update(memory.revoked_tokens(chat_id, strict=True, include_pending=True))
        return tuple(sorted(tokens))
    finally:
        for memory in opened:
            memory.close()


def authorize_payload(payload, chat_id=None, runtime_home=None, *, owner_memory=None) -> bool:
    from core.memory.carriers import carries
    from core.memory.withdrawals import payload_is_withdrawn
    try:
        tokens = revoked_tokens(chat_id, runtime_home, owner_memory=owner_memory)
        return not any(carries(payload, token) for token in tokens) and not payload_is_withdrawn(payload, tokens)
    except Exception:
        return False


@contextlib.contextmanager
def writing(payload, chat_id=None, runtime_home=None):
    """Writers refuse revoked bytes while holding the completion fence."""
    from core.memory.carriers import carries
    with serialized(runtime_home):
        prepare(runtime_home, allow_initialize=True, arm=False)
        rows = _read(_directory(runtime_home))['operations'].values()
        if any(row.get('kind', 'forget') == 'forget'
               and (not chat_id or not row['chat'] or row['chat'] == str(chat_id))
               and carries(payload, row['token']) for row in rows):
            raise RuntimeError('memory write carries revoked content')
        yield


def fenced_writer(fn=None, *, on_refused=_RAISE_REFUSAL, refusal_error=None):
    """Fence a memory writer's full input shape, including future fields."""
    if fn is None:
        return lambda decorated: fenced_writer(decorated, on_refused=on_refused, refusal_error=refusal_error)
    signature = inspect.signature(fn)
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        values = dict(bound.arguments)
        owner = values.pop('self', None) or values.get('mem')
        if values.get('mem') is owner:
            values.pop('mem', None)
        runtime_home = getattr(owner, '_authority_home', None) or values.get('runtime_home')
        context = values.get('source_context') or {}
        runtime_home = runtime_home or context.get('runtime_home')
        scope = values.get('chat_scope') or values.get('session_id') or values.get('chat_id')
        for name in ('scope', 'page', 'access_policy'):
            value = values.get(name)
            scope = scope or getattr(value, 'session_id', None) or getattr(value, 'chat_id', None)
        scope = scope or context.get('chat_id') or context.get('session_id')
        payload = values.get('payload') or {}
        if isinstance(payload, Mapping):
            scope = scope or payload.get('session_id') or payload.get('origin_chat_id') or payload.get('chat_id')
        if not scope and values.get('tags'):
            from core.vool_memory import _session_scopes
            scopes = _session_scopes(tags=values['tags'], context_description=values.get('context_description', ''))
            scope = next(iter(scopes)) if len(scopes) == 1 else None
        with contextlib.ExitStack() as fence:
            try:
                projected = {name: _writer_projection(value) for name, value in values.items()
                             if not isinstance(value, sqlite3.Connection)
                             and not (name in {'path', 'runtime_home', 'db_path'} and isinstance(value, (Path, str)))}
                fence.enter_context(writing(projected, scope, runtime_home))
            except RuntimeError as exc:
                if refusal_error is not None:
                    raise refusal_error(str(exc)) from exc
                if on_refused is not _RAISE_REFUSAL:
                    return on_refused
                if fn.__name__ in {'append_conversation_event', 'append_assistant_artifact_event'}:
                    return False
                raise
            # Execution errors are not admission refusals.
            return fn(*args, **kwargs)
    return wrapped


def _writer_projection(value):
    """Dataclass fields participate in the same recursive payload contract."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: _writer_projection(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {key: _writer_projection(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_writer_projection(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, bytes, int, float, bool)):
        return value
    raise RuntimeError('memory writer has an opaque payload')


def _clean_exit() -> None:
    for directory, lease in list(_LEASES.items()):
        try:
            with serialized(directory.parent):
                if directory not in _POISONED:
                    Path(lease.name).unlink()
                    _sync_directory(directory)
                lease.close()
                _LEASES.pop(directory, None)
        except Exception:
            # Retaining a lease is the safe failure; never certify clean exit.
            lease.close()


def _after_fork() -> None:
    global _LOCK, _LOCAL
    for lease in _LEASES.values():
        lease.close()
    _LEASES.clear()
    for lock in getattr(_LOCAL, 'held', {}).values():
        lock.close()
    _LOCK = threading.RLock()
    _LOCAL = threading.local()


atexit.register(_clean_exit)
os.register_at_fork(after_in_child=_after_fork)
