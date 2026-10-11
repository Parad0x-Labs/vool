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
import functools
import inspect
import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # Windows: no flock; lock one byte far past any content instead.
    fcntl = None
    import msvcrt

_WINDOWS_LOCK_OFFSET = 0x7FFFFFFE
_WINDOWS = os.name == 'nt'


def _flock(handle, *, blocking: bool = True) -> None:
    """Exclusive advisory lock on ``handle``; ``BlockingIOError`` when ``blocking`` is false and it is held."""
    if fcntl is not None:
        fcntl.flock(handle, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    position = handle.tell()
    handle.seek(_WINDOWS_LOCK_OFFSET)
    try:
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if not blocking:
                    raise BlockingIOError(str(exc)) from exc
                time.sleep(0.05)
    finally:
        handle.seek(position)


def _funlock(handle) -> None:
    if fcntl is not None:
        fcntl.flock(handle, fcntl.LOCK_UN)
        return
    position = handle.tell()
    handle.seek(_WINDOWS_LOCK_OFFSET)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.seek(position)

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
    # The Windows CRT cannot open directories for fsync. File contents are
    # flushed before publication, and the Windows rename uses WRITE_THROUGH.
    if _WINDOWS:
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace_file(source: Path, target: Path) -> None:
    if not _WINDOWS:
        os.replace(source, target)
        return
    import ctypes
    from ctypes import wintypes

    move = ctypes.WinDLL('kernel32', use_last_error=True).MoveFileExW
    move.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
    move.restype = wintypes.BOOL
    # Same-directory publication: never allow a copy across volumes.
    if not move(str(source), str(target), 0x1 | 0x8):
        raise ctypes.WinError(ctypes.get_last_error())


def _durable_write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex)
    try:
        with tmp.open('x', encoding='utf-8') as out:
            json.dump(payload, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        _replace_file(tmp, path)
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
            _flock(lock)
            _LOCAL.held = {**held, directory: lock}
            try:
                yield
            finally:
                _LOCAL.held = held
                _funlock(lock)


def _read(directory: Path) -> dict:
    value = json.loads((directory / 'ledger.json').read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or set(value) != {'version', 'operations'}
            or type(value.get('version')) is not int or value['version'] not in {1, 2}
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


def _routing(home: Path) -> dict:
    value = json.loads((_directory(home) / 'dependencies.json').read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or set(value) != {'version', 'scopes'}
            or value['version'] != 1 or not isinstance(value['scopes'], dict)
            or any(not isinstance(scope, str) or not isinstance(homes, list)
                   or any(not isinstance(item, str) or not Path(item).is_absolute() for item in homes)
                   for scope, homes in value['scopes'].items())):
        raise RuntimeError('forget authority dependencies are invalid')
    return value


def _dependencies(home: Path, chat_id=None) -> set[Path]:
    """Only authorities participating in this scope govern its serving reads."""
    return {Path(item).resolve() for scope, homes in _routing(home)['scopes'].items()
            if not chat_id or not scope or scope == str(chat_id) for item in homes}


def _authority_set(runtime_home=None, chat_id=None) -> dict[Path, dict]:
    pending = {_home(runtime_home)}
    authorities = {}
    while pending:
        home = pending.pop()
        if home in authorities:
            continue
        directory = _directory(home)
        data = _read(directory)
        _check_leases(directory)
        authorities[home] = data
        if data['version'] == 2:
            pending.update(_dependencies(home, chat_id) - authorities.keys())
    return authorities


def _bind_homes(homes: set[Path], chat_id) -> None:
    """Complete routing before publishing any operation.

    A partial routing write carries no forget and changes no content. Publication
    is forbidden until every participant can discover every other authority.
    Readers reread this routing on every call; absent dependencies fail closed.
    """
    participants = set(homes)
    for home in sorted(homes):
        _prepare_forget_home(home)
        participants.update(_authority_set(home, chat_id))
    for home in sorted(homes):
        dependencies = participants - {home}
        routing = _routing(home)
        key = str(chat_id or '')
        expected = sorted(map(str, dependencies))
        if routing['scopes'].get(key, []) != expected:
            routing['scopes'][key] = expected
            _durable_write(_directory(home) / 'dependencies.json', routing)
        if _routing(home)['scopes'].get(key, []) != expected:
            raise RuntimeError('forget authority routing could not be verified')


def _prepare_forget_home(home: Path) -> None:
    """Explicit erasure may upgrade a legacy home before opening its stores.

    Serving readers must never invent authority for retained content. A forget
    can bootstrap a home that has never had this authority, then publish its
    barrier normally. Existing routing or leases distinguish lost authority
    from a legacy home and must still fail closed.
    """
    directory = _directory(home)
    with serialized(home):
        if not (directory / 'ledger.json').exists():
            _check_leases(directory)
            if _initialization_witnesses(directory):
                raise RuntimeError('forget authority is absent for initialized memory')
            # Version 1 is a complete standalone authority. The existing
            # version migration adds routing durably before group publication.
            _durable_write(directory / 'ledger.json', {'version': 1, 'operations': {}})
        prepare(home, arm=False)


def begin_group(chat_id, token, homes, *, _kind='forget') -> list[tuple[Path, str]]:
    """Publish across an already connected set; any persisted part fences all."""
    homes = {_home(home) for home in homes}
    operations = []
    failures = []
    with contextlib.ExitStack() as locks:
        for home in sorted(homes):
            locks.enter_context(serialized(home))
        try:
            _bind_homes(homes, chat_id)
        except Exception as exc:
            raise ForgetStartError(started=False) from exc
        for home in sorted(homes):
            try:
                operations.append((home, begin(chat_id, token, home, _kind=_kind)))
            except ForgetStartError as exc:
                failures.append(exc)
        if failures:
            raise ForgetStartError(started=bool(operations) or any(exc.started for exc in failures)) from failures[0]
    return operations


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
                _flock(lease, blocking=False)
            except BlockingIOError:
                continue
            _funlock(lease)
            raise RuntimeError('forget authority has an unclean lease')


def _carrier_authority_home(path) -> Path | None:
    """The home owning the carrier store at ``path``, re-derived from the store itself.

    Every reader, writer and forget in every process resolves the same owner
    from the same store, so a configured database or a carrier file placed
    outside the layout default keeps its own home's authority across restarts
    and processes. A carrier under a home's ``data/`` directory (at any depth)
    belongs to that home; a carrier placed directly in a directory belongs to
    the nearest ancestor that holds the durable authority directory. A carrier
    with neither has no persisted owner of its own: ``None`` lets the caller
    govern it by the caller's own authority, and never by another home's.
    """
    try:
        resolved = Path(path).expanduser().resolve()
    except Exception:
        return None
    for ancestor in resolved.parents:
        if ancestor.name == "data":
            return ancestor.parent
    for ancestor in resolved.parents:
        if (ancestor / ".forget-authority").is_dir():
            return ancestor
    return None


def _existing_carriers(home: Path, *, including_legacy: bool = False) -> bool:
    """Retained memory in the home's carrier inventory.

    The explicit-forget bootstrap's freshness line is the owner-store
    inventory. An ordinary writer's line (``including_legacy``) covers the
    whole inventory the forget sweep verifies: heuristics, dense profiles
    and session metadata are retained memory too, with the metadata
    filename family's recovery/staging copies, so no public writer may
    initialize empty authority over any of them.
    """
    data = home / 'data'
    if any(p.exists() for p in (
        data / 'memory' / 'vool_memory.db', data / 'memory' / 'nulla_memory.db',
        data / 'memory_entries.jsonl', data / 'conversation_log.jsonl',
        data / 'session_summaries.jsonl', data / 'MEMORY.md',
    )):
        return True
    if not including_legacy:
        return False
    if any(p.exists() for p in (
        data / 'user_heuristics.jsonl', data / 'operator_dense_profile.json',
    )):
        return True
    return any(data.glob('chat_session_meta.json*'))


def _initialization_witnesses(directory: Path) -> bool:
    """Durable proof this home already had initialized authority.

    Routing and admission leases outlive the ledger they served; finding
    either without a ledger means authority was lost, not absent.
    """
    return (directory / 'dependencies.json').exists() or any(directory.glob('lease-*'))


@_authority_errors
def initialize_memory_home(runtime_home=None, *, db_path=None) -> None:
    """Migrate a legacy home once, before opening or creating its memory.

    Readers still require an existing authority. Routing, leases and owner
    erasure rows are durable witnesses: none may be replaced by empty state.
    A damaged ledger or owner store is never treated as a legacy installation.
    """
    home = _home(runtime_home)
    directory = _directory(home)
    with serialized(home):
        ledger = directory / 'ledger.json'
        if not ledger.exists():
            _check_leases(directory)
            if _initialization_witnesses(directory):
                raise RuntimeError('forget authority is absent for initialized memory')
            stores = {home / 'data' / 'memory' / name
                      for name in ('vool_memory.db', 'nulla_memory.db')}
            if db_path is not None:
                stores.add(Path(db_path).resolve())
            for store in stores:
                if not store.exists():
                    continue
                with contextlib.closing(sqlite3.connect(store.as_uri() + '?mode=ro', uri=True)) as conn:
                    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    for table in ('memory_revocations', 'memory_erasure_operations'):
                        if table in tables and conn.execute('SELECT 1 FROM ' + table + ' LIMIT 1').fetchone():
                            raise RuntimeError('forget authority is absent for recorded erasure')
            _durable_write(directory / 'dependencies.json', {'version': 1, 'scopes': {}})
            _durable_write(ledger, {'version': 2, 'operations': {}})
        prepare(home, arm=False)


@_authority_errors
def prepare(runtime_home=None, *, allow_initialize=False, arm=True,
            allow_writer_initialize=False) -> None:
    """Open (or deliberately create) this home's forget authority.

    One policy tells a genuinely new home apart from lost initialized
    authority using durable witnesses over the whole carrier inventory:

    - ``allow_initialize`` is the explicit-forget bootstrap. It may upgrade a
      legacy home that never had authority, but retained owner stores or a
      durable witness (routing, an admission lease) mean that memory
      predates this call or this authority was initialized and then lost;
      recreating an empty ledger would silently discard its pending
      forgets, so it fails closed instead.
    - ``allow_writer_initialize`` is the only initialization an ordinary
      writer may perform, and only over a genuinely new home: any retained
      carrier — the whole sweep inventory, legacy carriers included — or
      witness means memory predates this authority.
    - Neither flag may read or write retained content on a missing,
      unreadable or invalid ledger; those also fail closed below.
    """
    directory = _directory(runtime_home)
    with serialized(runtime_home):
        if not (directory / 'ledger.json').exists():
            witnesses = _initialization_witnesses(directory)
            if allow_initialize:
                if witnesses:
                    raise RuntimeError('forget authority is absent for initialized memory')
                if _existing_carriers(_home(runtime_home)):
                    raise RuntimeError('forget authority is absent for retained memory')
            elif allow_writer_initialize:
                if witnesses or _existing_carriers(_home(runtime_home), including_legacy=True):
                    raise RuntimeError('forget authority is absent for retained memory')
            else:
                raise RuntimeError('forget authority is absent for retained memory')
            _durable_write(directory / 'dependencies.json', {'version': 1, 'scopes': {}})
            _durable_write(directory / 'ledger.json', {'version': 2, 'operations': {}})
        data = _read(directory)
        if data['version'] == 1:
            _durable_write(directory / 'dependencies.json', {'version': 1, 'scopes': {}})
            _durable_write(directory / 'ledger.json', {**data, 'version': 2})
        _dependencies(_home(runtime_home))
        _check_leases(directory)
        if arm and directory not in _LEASES:
            path = directory / ('lease-' + uuid.uuid4().hex)
            lease = path.open('x+b')
            try:
                _flock(lease)
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
                _funlock(lease)
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
        rows = [row for data in _authority_set(runtime_home, chat_id).values() for row in data['operations'].values()
                if not chat_id or not row['chat'] or row['chat'] == str(chat_id)]
        if any(row['status'] != 'complete' for row in rows):
            raise RuntimeError('memory erasure is incomplete')
        return tuple(sorted({row['token'] for row in rows if row['token'] and row.get('kind', 'forget') == 'forget'}))


@_authority_errors
def generation(chat_id=None, runtime_home=None) -> str:
    with serialized(runtime_home):
        prepare(runtime_home, arm=False)
        return json.dumps({str(home): data for home, data in _authority_set(runtime_home, chat_id).items()}, sort_keys=True)


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
        prepare(runtime_home, allow_writer_initialize=True, arm=False)
        rows = [row for data in _authority_set(runtime_home, chat_id).values() for row in data['operations'].values()]
        if any(row.get('kind', 'forget') == 'forget'
               and (not chat_id or not row['chat'] or row['chat'] == str(chat_id))
               and carries(payload, row['token']) for row in rows):
            raise RuntimeError('memory write carries revoked content')
        yield


@contextlib.contextmanager
def _writing_rows(rows, runtime_home=None):
    """Public multi-row rewrites admit each row under the shared fence.

    A rewritten file serves several chats at once, so every row answers to
    the scopes its own identity declares — exactly like the serving reader.
    Another chat's retained row therefore survives an erasure rewrite while
    this scope's revoked bytes are refused. The fence spans the actual
    write, so no erasure verification or completion races a public rewrite.
    """
    from core.memory.carriers import carries
    with serialized(runtime_home):
        prepare(runtime_home, allow_writer_initialize=True, arm=False)
        operations = [row for data in _authority_set(runtime_home, None).values()
                      for row in data['operations'].values()]
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            scope = row.get('session_id') or row.get('chat_id') or row.get('chat_scope')
            if any(operation.get('kind', 'forget') == 'forget'
                   and (not scope or not operation['chat'] or operation['chat'] == str(scope))
                   and carries(row, operation['token']) for operation in operations):
                raise RuntimeError('memory write carries revoked content')
        yield


def _row_scope(row) -> str:
    """The chat a carrier row declares, '' when it declares none."""
    if not isinstance(row, Mapping):
        return ""
    return str(row.get("session_id") or row.get("chat_id") or row.get("chat_scope")
               or row.get("origin_chat_id") or row.get("origin_session_id") or "")


def _carrier_text_rows(text):
    """The rows a raw carrier payload declares, as (scope, payload) pairs.

    A JSON document of per-chat rows (the metadata carrier) and JSONL lines
    declare one scope per row; any other shape is one unattributed payload.
    A payload that does not fully parse is admitted whole — partial bytes are
    never smuggled through their parseable subset.
    """
    stripped = str(text).strip()
    if not stripped:
        return [(None, stripped)]
    try:
        document = json.loads(stripped)
    except Exception:
        rows = []
        for line in stripped.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                return [(None, text)]
        if not rows:
            return [(None, text)]
        return [(_row_scope(row), row) for row in rows]
    if isinstance(document, Mapping) and document and all(isinstance(item, Mapping) for item in document.values()):
        return [(str(key), item) for key, item in document.items()]
    return [(_row_scope(document), document)]


@contextlib.contextmanager
def _writing_carrier_text(text, runtime_home=None):
    """Public raw-carrier writes admit the complete payload under the fence.

    The same row law as the public rewrite applies to the text boundary: a
    payload whose rows declare their own scopes answers operation by
    operation, so another chat's retained row survives this write while this
    scope's revoked bytes are refused; a payload without row structure is
    admitted whole and refused on any operation's token.
    """
    from core.memory.carriers import carries
    with serialized(runtime_home):
        prepare(runtime_home, allow_writer_initialize=True, arm=False)
        operations = [row for data in _authority_set(runtime_home, None).values()
                      for row in data['operations'].values()]
        for scope, row in _carrier_text_rows(text):
            if any(operation.get('kind', 'forget') == 'forget'
                   and (not scope or not operation['chat'] or operation['chat'] == str(scope))
                   and carries(row, operation['token']) for operation in operations):
                raise RuntimeError('memory write carries revoked content')
        yield


def fenced_writer(fn=None, *, on_refused=_RAISE_REFUSAL, refusal_error=None, store_home=None):
    """Fence a memory writer's full input shape, including future fields.

    ``store_home`` resolves the authority home from the ACTUAL store the
    writer touches (the bound arguments), so a writer aimed at a configured
    database or an explicit carrier path is fenced by that store's own home —
    never silently by the caller's current runtime home.
    """
    if fn is None:
        return lambda decorated: fenced_writer(decorated, on_refused=on_refused, refusal_error=refusal_error,
                                               store_home=store_home)
    signature = inspect.signature(fn)
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        values = dict(bound.arguments)
        owner = values.pop('self', None) or values.get('mem')
        if values.get('mem') is owner:
            values.pop('mem', None)
        runtime_home = getattr(owner, '_authority_home', None) or values.get('runtime_home')
        if runtime_home is None and store_home is not None:
            runtime_home = store_home(values)
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
if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_after_fork)
