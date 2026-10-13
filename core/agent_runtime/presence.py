from __future__ import annotations

import contextlib
import inspect
import logging
import sqlite3
import threading
import time
import urllib.parse
from collections.abc import Callable
from typing import Any

from core import policy_engine

_LOG = logging.getLogger(__name__)

#: This build's presence sync writes its audit rows through caller-owned storage transactions:
#: one BEGIN IMMEDIATE .. COMMIT on the session's pinned store per event, covering the audit row,
#: the event row and its hash together. Tests assert this to fail fast on an unpatched caller
#: instead of silently exercising the legacy independent-commit write path.
PRESENCE_SYNC_SESSION_TRANSACTIONS = True

#: How long one presence audit write may wait for the SQLite write lock. Storage's own busy
#: timeout is the same 5 s; the difference is that this wait is cancellable: runtime stop wakes it.
PRESENCE_AUDIT_STORAGE_WAIT_SECONDS = 5.0
#: Poll step of that wait while another writer holds the database.
PRESENCE_AUDIT_STORAGE_POLL_SECONDS = 0.01
#: How long stop waits for the sync write lock, which only an audit entry point outside the storage
#: contract (one that cannot take a caller-owned connection) holds across its call; and the interval
#: at which stop logs that it is still waiting for a COMMIT already past its commit point.
PRESENCE_SYNC_COMMIT_WAIT_SECONDS = 5.0
#: The busy wait of the presence lane's pinned effect-budget connection: storage's own 5 s, so the
#: budget module behaves on the session store exactly as on its default path.
PRESENCE_BUDGET_STORE_BUSY_MS = 5000


class SyncStoppedError(RuntimeError):
    """The sync's runtime stopped: its store connections open nothing and commit nothing more."""


class _SyncCommitFence:
    """Where a presence sync COMMIT and runtime stop meet.

    A commit is decided inside SQLite, at the COMMIT statement's last interruptible step before
    the commit itself (a progress callback; SQLite aborts the statement when it returns non-zero).
    Under `cond` it is either refused, because stop has closed the epoch, or passed and recorded.
    Stop closes the epoch under the same `cond`, so every commit is ordered against the close: one
    passed before it is already being written by SQLite and stop waits for it to finish (it lands
    before stop returns); every later one is refused and rolled back. `cond` is never held across
    a SQLite call.
    """

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.passed: set[int] = set()


_FENCE_INIT_LOCK = threading.Lock()


def _sync_commit_fence(agent: Any) -> _SyncCommitFence:
    fence = getattr(agent, "_public_presence_sync_fence", None)
    if fence is None:
        with _FENCE_INIT_LOCK:
            fence = getattr(agent, "_public_presence_sync_fence", None)
            if fence is None:
                fence = _SyncCommitFence()
                agent._public_presence_sync_fence = fence
    return fence


#: Python's legacy transaction control opens a transaction by itself only before these.
_SQL_DML = frozenset({"insert", "update", "delete", "replace"})
#: Leading keywords of statements that write. Outside a transaction SQLite commits each one by
#: itself the moment it runs (DDL always; DML too on a connection without implicit BEGIN).
_SQL_WRITES = _SQL_DML | frozenset({"create", "drop", "alter", "reindex", "analyze", "vacuum", "with"})


def _sql_head(sql: object) -> str:
    """The statement's first keyword, lower-cased, after whitespace and SQL comments."""
    text = str(sql or "")
    i = 0
    while i < len(text):
        if text[i].isspace():
            i += 1
        elif text.startswith("--", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end + 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
        else:
            break
    j = i
    while j < len(text) and text[j].isalpha():
        j += 1
    return text[i:j].lower()


def _commits_on_its_own(conn: sqlite3.Connection, sql: object, head: str) -> bool:
    """True when SQLite would make `sql` durable by itself, without ever reaching commit()."""
    if conn.in_transaction:
        return False
    if head == "pragma":
        return "=" in str(sql)
    if head in _SQL_DML and conn.isolation_level is not None:
        return False  # Python opens the transaction before it runs; commit() is the gate
    return head in _SQL_WRITES


def _gated_statement(conn: _SyncGatedConnection, run: Callable[..., Any], sql: Any, parameters: Any) -> Any:
    head = _sql_head(sql)
    if head in ("commit", "end", "release") and conn.in_transaction:
        # A literal COMMIT/END, or RELEASE of the outermost savepoint, commits: through the gate.
        return conn._commit_through_gate(lambda: run(sql, parameters))
    if not _commits_on_its_own(conn, sql, head):
        return run(sql, parameters)
    # A write SQLite would commit on its own runs inside a transaction of ours instead, so its
    # commit meets the same gate as every other. DEFERRED: a statement that turns out to write
    # nothing (CREATE TABLE IF NOT EXISTS on an existing table) never takes the writer lock.
    sqlite3.Connection.execute(conn, "BEGIN")
    try:
        result = run(sql, parameters)
    except BaseException:
        with contextlib.suppress(Exception):
            conn.rollback()
        raise
    conn.commit()
    return result


class _SyncGatedCursor(sqlite3.Cursor):
    def execute(self, sql: str, parameters: Any = (), /) -> Any:
        return _gated_statement(self.connection, super().execute, sql, parameters)

    def executemany(self, sql: str, parameters: Any, /) -> Any:
        return _gated_statement(self.connection, super().executemany, sql, parameters)

    def executescript(self, sql_script: str, /) -> Any:
        raise sqlite3.NotSupportedError("executescript commits outside the presence sync gate")


class _SyncGatedConnection(sqlite3.Connection):
    """A connection of one presence sync epoch: nothing it writes becomes durable once that
    epoch has closed.

    Every COMMIT is decided inside SQLite at its commit point, through the sync's commit fence
    (_SyncCommitFence): once stop has closed the epoch the COMMIT statement is aborted there and
    the whole transaction rolled back (SyncStoppedError), including a COMMIT that had already
    started when stop ran. A write SQLite would commit by itself (DDL outside a transaction,
    autocommit DML) is run inside a transaction so it meets the same fence; executescript, which
    commits on its own, is refused. Until a fence is bound, nothing commits.
    """

    _sync_gate: tuple[_SyncCommitFence, Callable[[], bool]] | None = None

    def bind_sync_gate(self, fence: _SyncCommitFence, is_open: Callable[[], bool]) -> None:
        self._sync_gate = (fence, is_open)

    def _refuse(self) -> SyncStoppedError:
        with contextlib.suppress(Exception):
            self.rollback()
        return SyncStoppedError("the presence sync stopped before this write committed; it was rolled back")

    def _commit_through_gate(self, do_commit: Callable[[], Any]) -> Any:
        gate = self._sync_gate
        if gate is None or not gate[1]():
            raise self._refuse()
        fence, is_open = gate
        decision = {"passed": False, "refused": False}

        def _at_commit_point() -> int:
            # SQLite calls this inside the COMMIT statement; its first call comes before the commit
            # itself, the last point where the statement can still be aborted.
            if decision["passed"] or decision["refused"]:
                return 1 if decision["refused"] else 0
            with fence.cond:
                if not is_open():
                    decision["refused"] = True
                    return 1
                decision["passed"] = True
                fence.passed.add(id(self))
            return 0

        self.set_progress_handler(_at_commit_point, 1)
        try:
            result = do_commit()
        except sqlite3.OperationalError:
            if decision["refused"]:
                raise self._refuse() from None
            raise
        finally:
            with contextlib.suppress(Exception):
                self.set_progress_handler(None, 0)
            if decision["passed"]:
                with fence.cond:
                    fence.passed.discard(id(self))
                    fence.cond.notify_all()
        if not decision["passed"]:
            # Never observed: SQLite committed without calling back at its commit point.
            _LOG.error("presence sync COMMIT completed without a commit-point decision; the stop fence did not apply")
        return result

    def commit(self) -> None:
        if not self.in_transaction:
            return super().commit()
        self._commit_through_gate(super().commit)

    def execute(self, sql: str, parameters: Any = (), /) -> Any:
        return _gated_statement(self, super().execute, sql, parameters)

    def executemany(self, sql: str, parameters: Any, /) -> Any:
        return _gated_statement(self, super().executemany, sql, parameters)

    def executescript(self, sql_script: str, /) -> Any:
        raise sqlite3.NotSupportedError("executescript commits outside the presence sync gate")

    def cursor(self, factory: Any = None) -> Any:
        return super().cursor(_SyncGatedCursor if factory is None else factory)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        # sqlite3's own __exit__ commits in C, past the gate.
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


def sync_public_presence(
    agent: Any,
    *,
    status: str,
    source_context: dict[str, object] | None = None,
    get_agent_display_name_fn: Callable[[], str],
    audit_log_fn: Callable[..., Any],
) -> None:
    effective_status = agent._normalize_public_presence_status(status)
    with agent._public_presence_lock:
        agent._public_presence_status = effective_status
        if source_context is not None:
            agent._public_presence_source_context = dict(source_context)
    if not policy_engine.automatic_outbound_enabled():
        # The join handshake still reaches its local bridge contract. Admission
        # is explicit and closed, so no identity signing or network work starts.
        agent.public_hive_bridge.sync_presence(
            agent_name=get_agent_display_name_fn(),
            capabilities=agent._public_capabilities(),
            status=effective_status,
            transport_mode=agent._public_transport_mode(source_context),
            allow_outbound=False,
        )
        return
    if _presence_sync_runs_inline(agent):
        _run_public_presence_sync_now(
            agent,
            status=effective_status,
            source_context=source_context,
            get_agent_display_name_fn=get_agent_display_name_fn,
            audit_log_fn=audit_log_fn,
        )
        return
    _queue_public_presence_sync(
        agent,
        get_agent_display_name_fn=get_agent_display_name_fn,
        audit_log_fn=audit_log_fn,
    )


def _presence_sync_runs_inline(agent: Any) -> bool:
    backend_name = str(getattr(agent, "backend_name", "") or "").strip().lower()
    device = str(getattr(agent, "device", "") or "").strip().lower()
    return backend_name.startswith("test-") or device.endswith("-test")


def _queue_public_presence_sync(
    agent: Any,
    *,
    get_agent_display_name_fn: Callable[[], str],
    audit_log_fn: Callable[..., Any],
) -> None:
    with agent._public_presence_lock:
        # A stopped runtime starts no sync: the thread would outlive shutdown and write its audit
        # rows into whatever storage is active by then (the next runtime's home).
        if getattr(agent, "_public_presence_sync_stopped", False):
            return
        agent._public_presence_sync_pending = True
        if agent._public_presence_sync_inflight:
            return
        agent._public_presence_sync_inflight = True
        epoch = int(getattr(agent, "_public_presence_sync_epoch", 0))
        thread = threading.Thread(
            target=_public_presence_sync_worker,
            name="vool-public-presence-sync",
            daemon=True,
            kwargs={
                "agent": agent,
                "epoch": epoch,
                "wake": _sync_wake_event(agent),
                "get_agent_display_name_fn": get_agent_display_name_fn,
                "audit_log_fn": audit_log_fn,
                # The session's store, resolved HERE (under the queue lock, epoch verified live,
                # before any network effect): the worker's writes go to a caller-owned connection
                # on this path and never re-resolve the active database at write time.
                "store_path": _presence_session_store(agent),
            },
        )
        agent._public_presence_sync_thread = thread
        # Started under the lock, so a concurrent shutdown always joins a started thread; the
        # worker's first step waits on this same lock.
        thread.start()


def _sync_write_lock(agent: Any) -> Any:
    """The lock that orders a sync's audit write against shutdown closing its epoch."""
    lock = getattr(agent, "_public_presence_sync_write_lock", None)
    return lock if lock is not None else agent._public_presence_lock


def _sync_wake_event(agent: Any) -> threading.Event:
    """The current epoch's stop signal; shutdown sets it when it closes the epoch."""
    wake = getattr(agent, "_public_presence_sync_wake", None)
    if wake is None:
        wake = threading.Event()
        agent._public_presence_sync_wake = wake
    return wake


def _sync_epoch_is_current(agent: Any, epoch: int) -> bool:
    """False once shutdown closed this sync's epoch (stop changes it under the presence lock and
    the commit fence, so a reader holding either sees a settled value)."""
    return int(getattr(agent, "_public_presence_sync_epoch", 0)) == epoch


def _sync_epoch_is_live(agent: Any, epoch: int) -> bool:
    """Caller holds the presence lock. False once shutdown has begun: no further pass may start."""
    return not getattr(agent, "_public_presence_sync_stopped", False) and _sync_epoch_is_current(agent, epoch)


def _epoch_live_now(agent: Any, epoch: int | None) -> bool:
    if epoch is None:  # inline (test backend) sync: runs on the caller's own thread
        return True
    with agent._public_presence_lock:
        return _sync_epoch_is_live(agent, epoch)


def _presence_session_store(agent: Any) -> str:
    """The store this runtime's presence sync pins its audit writes to.

    Pinned at runtime start (`reopen_public_presence_sync`). An agent constructed without a
    start (unit rigs) falls back to resolving the active default path live — still before any
    effect of the pass, inside the queue lock, never at write time.
    """
    path = getattr(agent, "_public_presence_sync_store_path", None)
    if path:
        return str(path)
    from storage.db import active_default_db_path

    return str(active_default_db_path())


def _open_session_store(store_path: str) -> sqlite3.Connection | None:
    """Open the pinned session store read-write, never creating a database file.

    A vanished store returns None ("gone"): the sync abandons the write instead of
    materialising a fresh database wherever the filesystem happens to point. The connection
    skips the pooled get_connection() path on purpose — that helper re-resolves the ACTIVE
    default path, which is exactly the redirection this writer must not do — but keeps the rest
    of its contract: the store version gate (a store this binary must not open raises
    StoreVersionError, as the default open does) and the shared synchronous/foreign_keys
    settings. Its writes commit only through the sync gate the caller binds to it.

    The connection has no busy wait of its own (the caller's wait is the cancellable one), so a
    store another connection holds exclusively for a moment (a last close checkpointing its WAL)
    raises OperationalError("database is locked"): busy, retryable, never "gone" or "refused".
    """
    from storage.db import StoreVersionError, enforce_store_version_gate

    try:
        uri = "file:" + urllib.parse.quote(str(store_path)) + "?mode=rw"
        conn = sqlite3.connect(uri, uri=True, timeout=0, isolation_level=None, factory=_SyncGatedConnection)
    except Exception:
        return None
    try:
        conn.row_factory = sqlite3.Row
        for pragma in ("PRAGMA busy_timeout=0", "PRAGMA synchronous=NORMAL", "PRAGMA foreign_keys=ON"):
            sqlite3.Connection.execute(conn, pragma)
        enforce_store_version_gate(conn)
    except StoreVersionError as exc:
        with contextlib.suppress(Exception):
            conn.close()
        cause = exc.__cause__
        if isinstance(cause, sqlite3.OperationalError) and _sqlite_says_busy(cause):
            raise sqlite3.OperationalError(str(cause)) from exc  # unreadable only while held
        raise
    except sqlite3.OperationalError as exc:
        with contextlib.suppress(Exception):
            conn.close()
        if _sqlite_says_busy(exc):
            raise
        return None
    except Exception:
        with contextlib.suppress(Exception):
            conn.close()
        return None
    return conn


def _sqlite_says_busy(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def _audit_fn_accepts_conn(audit_log_fn: Callable[..., Any]) -> bool:
    """True when the audit entry point can join a caller-owned transaction (the storage contract)."""
    try:
        signature = inspect.signature(audit_log_fn)
    except (TypeError, ValueError):
        return False
    param = signature.parameters.get("conn")
    if param is not None:
        return param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())


def _session_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _write_session_audit(
    agent: Any,
    epoch: int,
    audit_log_fn: Callable[..., Any],
    store_path: str,
    event_type: str,
    kwargs: dict[str, Any],
) -> str:
    """One audit event as a caller-owned transaction on the pinned session store.

    Outcomes: "written" (committed), "busy" (another writer holds the database; caller may wait
    and retry), or a terminal abandonment ("stopped", "gone", "refused", "failed") which is
    logged here. The caller — the sync thread — owns BEGIN, the effects, commit, rollback and
    teardown; the storage API only writes through the connection it is handed.

    The commit is decided at SQLite's commit point through the sync's commit fence: it goes
    through only if this sync's epoch is still current there. Stop closes the epoch through the
    same fence, so a write not yet past its commit point when stop closes the epoch, including a
    COMMIT statement already running, is rolled back, never committed afterwards.
    """
    from storage.db import StoreVersionError

    persona = getattr(agent, "persona_id", "?")
    if not _audit_fn_accepts_conn(audit_log_fn):
        # An audit entry point outside the storage contract keeps its legacy behaviour: called
        # directly, no transaction of ours (holding one would only block its own writes). It runs
        # under the sync write lock with the epoch checked there, so stop's close waits for it.
        with _sync_write_lock(agent):
            if not _sync_epoch_is_current(agent, epoch):
                _LOG.warning(
                    "presence sync for %s finished after its runtime stopped; audit event %s not written",
                    persona,
                    event_type,
                )
                return "stopped"
            try:
                audit_log_fn(event_type, **kwargs)
            except Exception:
                _LOG.warning("presence sync audit event %s could not be written", event_type, exc_info=True)
        return "written"
    try:
        conn = _open_session_store(store_path)
    except StoreVersionError as exc:
        _LOG.warning(
            "presence sync audit event %s not written: the runtime store is refused (%s: %s)",
            event_type,
            getattr(exc, "code", "VOOL_E_STORE_VERSION"),
            exc,
        )
        return "refused"
    except sqlite3.OperationalError as exc:
        if _sqlite_says_busy(exc):
            return "busy"
        _LOG.warning("presence sync audit event %s could not be written: %s", event_type, exc)
        return "failed"
    if conn is None:
        _LOG.warning(
            "presence sync audit event %s could not be written: the runtime store is gone",
            event_type,
        )
        return "gone"
    try:
        bind_gate = getattr(conn, "bind_sync_gate", None)
        if bind_gate is None:
            _LOG.warning(
                "presence sync audit event %s could not be written: the store connection has no commit gate",
                event_type,
            )
            return "failed"
        bind_gate(_sync_commit_fence(agent), lambda: _sync_epoch_is_current(agent, epoch))
        try:
            # Acquiring SQLite's writer lock IS ownership of the write: atomic, unlike a
            # preflight probe whose "free" can go stale before the write follows it.
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if _sqlite_says_busy(exc):
                return "busy"
            _LOG.warning(
                "presence sync audit event %s could not be written: %s", event_type, exc
            )
            return "failed"
        # The runtime's bootstrap creates audit_log; the event tables are created lazily inside
        # this same transaction. A store MISSING its audit table was dropped or replaced under
        # the runtime: the sync abandons the write rather than recreating a broken store's schema.
        if not _session_table_exists(conn, "audit_log"):
            _LOG.warning(
                "presence sync audit event %s could not be written: the runtime store is gone",
                event_type,
            )
            return "gone"
        if not _sync_epoch_is_current(agent, epoch):
            _LOG.warning(
                "presence sync for %s finished after its runtime stopped; audit event %s not written",
                persona,
                event_type,
            )
            return "stopped"
        try:
            audit_log_fn(event_type, conn=conn, **kwargs)
        except Exception:
            _LOG.warning(
                "presence sync audit event %s could not be written", event_type, exc_info=True
            )
            return "failed"
        try:
            conn.commit()
        except SyncStoppedError:
            _LOG.warning(
                "presence sync for %s stopped before its audit event %s committed; rolled back, not written",
                persona,
                event_type,
            )
            return "stopped"
        except Exception:
            _LOG.warning(
                "presence sync audit event %s could not be written", event_type, exc_info=True
            )
            return "failed"
        return "written"
    finally:
        with contextlib.suppress(Exception):
            if conn.in_transaction:
                conn.rollback()
        with contextlib.suppress(Exception):
            conn.close()


def _epoch_guarded_audit(
    agent: Any,
    epoch: int,
    audit_log_fn: Callable[..., Any],
    wake: threading.Event | None = None,
    store_path: str | None = None,
) -> Callable[..., Any]:
    """The sync thread's audit writer: one caller-owned storage transaction per event.

    The store is pinned (resolved when the pass was queued with its epoch verified live, before
    any network effect) and opened read-write without ever creating a file; the transaction is
    taken with BEGIN IMMEDIATE on that connection, so acquiring SQLite's writer lock is the
    ownership of the write. The audit row, the event row and its hash are written through that
    same connection and committed together — atomically — or rolled back together; the caller
    (this thread) owns commit, rollback and teardown.

    While another writer holds the database, the wait happens outside the sync write lock,
    woken by shutdown, for at most PRESENCE_AUDIT_STORAGE_WAIT_SECONDS: stop never queues
    behind a storage wait, and a waiting write is abandoned without writing afterwards. The
    epoch is re-checked after the writer lock is acquired, before any effect, and decided again
    at SQLite's commit point through the commit fence: a write that finishes while its runtime is
    still up (stop's join included) commits into this session's pinned store; one not past its
    commit point when stop closes the epoch is rolled back. Nothing of a stopped sync commits
    after stop returns, into this store or any other. Neither the fence nor the sync write lock
    is the presence lock: a turn updating its presence status must not wait on storage. A storage
    error stays in this thread's log instead of escaping as an unhandled thread exception into
    whatever runs next.
    """
    signal = wake if wake is not None else threading.Event()
    pinned_store = str(store_path) if store_path else _presence_session_store(agent)

    def _audit(event_type: str, **kwargs: Any) -> None:
        deadline = time.monotonic() + PRESENCE_AUDIT_STORAGE_WAIT_SECONDS
        while True:
            if not _sync_epoch_is_current(agent, epoch):
                _LOG.warning(
                    "presence sync for %s finished after its runtime stopped; audit event %s not written",
                    getattr(agent, "persona_id", "?"),
                    event_type,
                )
                return
            outcome = _write_session_audit(
                agent, epoch, audit_log_fn, pinned_store, event_type, kwargs
            )
            if outcome == "busy":
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _LOG.warning(
                        "presence sync audit event %s could not be written: storage stayed locked for %.1fs",
                        event_type,
                        PRESENCE_AUDIT_STORAGE_WAIT_SECONDS,
                    )
                    return
                signal.wait(min(PRESENCE_AUDIT_STORAGE_POLL_SECONDS, remaining))
                continue
            return

    return _audit


def _session_budget_store(agent: Any, epoch: int, store_path: str) -> Callable[[], sqlite3.Connection]:
    """The sync pass's effect-budget store: the session's pinned store, behind the same gate.

    Every budget read and write of the pass (reservations at its effect doors and the named
    scope's closing release of what it still holds) goes to the store its audit rows go to, never
    to whichever database is active when the pass ends. It is opened like the audit connection
    (never creating a file, through the store version gate) with storage's own transaction mode
    and busy wait, so the budget module behaves as on its default path; its commits meet the sync
    gate. Once stop has closed this sync's epoch it opens nothing: a stopped pass's scope
    teardown reads and writes no store, and a unit it still holds stays held, which the budget
    module treats as the safe direction (a held unit never widens a budget).
    """

    def _connect() -> sqlite3.Connection:
        # storage's own busy wait at open (5 s), polled so that a closed epoch ends it at once
        deadline = time.monotonic() + PRESENCE_BUDGET_STORE_BUSY_MS / 1000.0
        while True:
            if not _sync_epoch_is_current(agent, epoch):
                raise SyncStoppedError("the presence sync stopped: its budget store is closed")
            try:
                conn = _open_session_store(store_path)
                break
            except sqlite3.OperationalError as exc:
                if not _sqlite_says_busy(exc) or time.monotonic() >= deadline:
                    raise
                _sync_wake_event(agent).wait(PRESENCE_AUDIT_STORAGE_POLL_SECONDS)
        if conn is None:
            raise sqlite3.OperationalError("the presence sync's runtime store is gone")
        try:
            sqlite3.Connection.execute(conn, f"PRAGMA busy_timeout={int(PRESENCE_BUDGET_STORE_BUSY_MS)}")
            conn.isolation_level = ""
            conn.bind_sync_gate(_sync_commit_fence(agent), lambda: _sync_epoch_is_current(agent, epoch))
        except BaseException:
            with contextlib.suppress(Exception):
                conn.close()
            raise
        return conn

    return _connect


def _public_presence_sync_worker(
    *,
    agent: Any,
    epoch: int = 0,
    wake: threading.Event | None = None,
    get_agent_display_name_fn: Callable[[], str],
    audit_log_fn: Callable[..., Any],
    store_path: str | None = None,
) -> None:
    pinned_store = str(store_path) if store_path else _presence_session_store(agent)
    guarded_audit = _epoch_guarded_audit(agent, epoch, audit_log_fn, wake, store_path=pinned_store)
    while True:
        with agent._public_presence_lock:
            if not _sync_epoch_is_live(agent, epoch):
                return  # shutdown owns the bookkeeping now; touch nothing of a later run
            agent._public_presence_sync_pending = False
            effective_status = str(agent._public_presence_status or "idle")
            source_context = dict(agent._public_presence_source_context or {})
        _run_public_presence_sync_now(
            agent,
            status=effective_status,
            source_context=source_context,
            get_agent_display_name_fn=get_agent_display_name_fn,
            audit_log_fn=guarded_audit,
            epoch=epoch,
            store_path=pinned_store,
        )
        with agent._public_presence_lock:
            if not _sync_epoch_is_live(agent, epoch):
                return
            if agent._public_presence_sync_pending:
                continue
            agent._public_presence_sync_inflight = False
            agent._public_presence_sync_thread = None
            return


def _run_public_presence_sync_now(
    agent: Any,
    *,
    status: str,
    source_context: dict[str, object] | None,
    get_agent_display_name_fn: Callable[[], str],
    audit_log_fn: Callable[..., Any],
    epoch: int | None = None,
    store_path: str | None = None,
) -> None:
    if not policy_engine.automatic_outbound_enabled():
        return
    # The presence handshake is the owning entry point of its own fetches: without a named
    # background scope the effect gateway denies them as outside any turn (R2b1) before the
    # pytest live-network fence ever sees them, so the sync could not even be blocked BY the
    # authority that reports it.
    from core.effect_gateway import named_background_effect_scope

    with contextlib.ExitStack() as scopes:
        if epoch is not None:
            # The sync thread's pass: its effect budget lives in the session's pinned store, and
            # the scope below closes (releasing what it still holds) inside this pin.
            from core.effect_budget import pinned_budget_store

            scopes.enter_context(
                pinned_budget_store(
                    _session_budget_store(agent, epoch, store_path or _presence_session_store(agent))
                )
            )
        scopes.enter_context(named_background_effect_scope("agent.presence.sync"))
        _run_public_presence_sync_now_inner(
            agent,
            status=status,
            source_context=source_context,
            get_agent_display_name_fn=get_agent_display_name_fn,
            audit_log_fn=audit_log_fn,
            epoch=epoch,
        )


def _run_public_presence_sync_now_inner(
    agent: Any,
    *,
    status: str,
    source_context: dict[str, object] | None,
    get_agent_display_name_fn: Callable[[], str],
    audit_log_fn: Callable[..., Any],
    epoch: int | None = None,
) -> None:
    """One presence pass. `epoch` is the sync thread's epoch (None: inline on the caller's thread).

    A pass that outlives its runtime's stop starts no further network call (no heartbeat
    fallback once stop began) and changes no runtime state: a late success is not recorded as
    registration, so it cannot report into a restarted runtime.
    """
    try:
        if agent._public_presence_registered:
            result = agent.public_hive_bridge.heartbeat_presence(
                agent_name=get_agent_display_name_fn(),
                capabilities=agent._public_capabilities(),
                status=status,
                transport_mode=agent._public_transport_mode(source_context),
            )
            if not result.get("ok"):
                if not _epoch_live_now(agent, epoch):
                    _LOG.warning(
                        "presence heartbeat for %s failed after its runtime stopped; no re-registration attempted",
                        getattr(agent, "persona_id", "?"),
                    )
                else:
                    result = agent.public_hive_bridge.sync_presence(
                        agent_name=get_agent_display_name_fn(),
                        capabilities=agent._public_capabilities(),
                        status=status,
                        transport_mode=agent._public_transport_mode(source_context),
                    )
        else:
            result = agent.public_hive_bridge.sync_presence(
                agent_name=get_agent_display_name_fn(),
                capabilities=agent._public_capabilities(),
                status=status,
                transport_mode=agent._public_transport_mode(source_context),
            )
        if result.get("ok"):
            if epoch is None:
                agent._public_presence_registered = True
            else:
                with agent._public_presence_lock:
                    if _sync_epoch_is_current(agent, epoch):
                        agent._public_presence_registered = True
                    else:
                        _LOG.warning(
                            "presence sync for %s succeeded after its runtime stopped; not recorded",
                            getattr(agent, "persona_id", "?"),
                        )
    except Exception as exc:
        audit_log_fn(
            "public_hive_presence_sync_error",
            target_id=agent.persona_id,
            target_type="agent",
            details={"error": str(exc), "status": status},
        )
        return
    if not result.get("ok"):
        audit_log_fn(
            "public_hive_presence_sync_failed",
            target_id=agent.persona_id,
            target_type="agent",
            details={"status": status, **dict(result or {})},
        )


def start_public_presence_heartbeat(
    agent: Any,
    *,
    thread_factory: Callable[..., Any],
) -> None:
    if agent._public_presence_running:
        return
    agent._public_presence_running = True
    agent._public_presence_thread = thread_factory(
        target=agent._public_presence_heartbeat_loop,
        name="vool-public-presence",
        daemon=True,
    )
    agent._public_presence_thread.start()


def start_idle_commons_loop(
    agent: Any,
    *,
    thread_factory: Callable[..., Any],
) -> None:
    if agent._idle_commons_running:
        return
    agent._idle_commons_running = True
    agent._idle_commons_thread = thread_factory(
        target=agent._idle_commons_loop,
        name="vool-idle-commons",
        daemon=True,
    )
    agent._idle_commons_thread.start()


def public_presence_heartbeat_loop(
    agent: Any,
    *,
    sleep_fn: Callable[[float], Any],
) -> None:
    while agent._public_presence_running:
        sleep_fn(120.0)
        with agent._public_presence_lock:
            last_status = str(agent._public_presence_status or "idle")
            source_context = dict(agent._public_presence_source_context or {})
        agent._sync_public_presence(
            status=agent._normalize_public_presence_status(last_status),
            source_context=source_context,
        )


def idle_commons_loop(
    agent: Any,
    *,
    sleep_fn: Callable[[float], Any],
    audit_log_fn: Callable[..., Any],
) -> None:
    while agent._idle_commons_running:
        sleep_fn(90.0)
        if not agent._idle_commons_running:
            break
        try:
            agent._maybe_run_idle_commons_once()
            agent._maybe_run_autonomous_hive_research_once()
            # The curiosity queue's ONE consumer (F47: topics were queued and nothing in the
            # tree executed them). Same idle thread, same activity gate, one topic per tick.
            _consume = getattr(agent, "_maybe_execute_queued_curiosity_once", None)
            if callable(_consume):
                _consume()
        except Exception as exc:
            audit_log_fn(
                "idle_commons_loop_error",
                target_id=agent.persona_id,
                target_type="agent",
                details={"error": str(exc)},
            )


def maybe_run_idle_commons_once(
    agent: Any,
    *,
    load_preferences_fn: Callable[[], Any],
    time_fn: Callable[[], float],
    audit_log_fn: Callable[..., Any],
) -> None:
    if not policy_engine.automatic_outbound_enabled():
        return
    prefs = load_preferences_fn()
    if not bool(getattr(prefs, "social_commons", True)):
        return
    now = time_fn()
    with agent._activity_lock:
        idle_for_seconds = now - float(agent._last_user_activity_ts)
        since_last_commons = now - float(agent._last_idle_commons_ts)
        seed_index = int(agent._idle_commons_seed_index)
    if idle_for_seconds < 300.0:
        return
    if since_last_commons < 900.0:
        return

    session_id = agent._idle_commons_session_id()
    commons = agent.curiosity.run_idle_commons(
        session_id=session_id,
        task_id="agent-commons",
        trace_id="agent-commons",
        seed_index=seed_index,
    )
    # R-9b (H-7): idle-commons is a CANONICAL semantic lane — its model-authored
    # body traverses A2 admission + A7 finalization exactly like every served
    # answer before it may leave the process. No private mini-authority here.
    from core.finalization import finalize_answer
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    reset_admission()
    _public_body = str(commons.get("public_body") or commons.get("summary") or "")
    admit_semantic_result(
        {"response": _public_body, "route_reason": "idle_commons"},
        source_override=__import__(
            "core.semantic.semantic_result_seam", fromlist=["SemanticSource"]
        ).SemanticSource.MODEL,
        route_id_override="idle_commons",
        turn_id=str(session_id),
    )
    _commons_commit = finalize_answer(
        turn_id=str(session_id),
        canonical_content=_public_body,
    )
    publish_result: dict[str, Any] | None = None
    try:
        publish_result = agent.public_hive_bridge.publish_agent_commons_update(
            topic=str(dict(commons.get("topic") or {}).get("topic") or ""),
            topic_kind=str(dict(commons.get("topic") or {}).get("topic_kind") or "technical"),
            summary=str(commons.get("summary") or ""),
            public_body=str(_commons_commit.get("canonical_content") or ""),
            topic_tags=[str(tag) for tag in list(commons.get("topic_tags") or [])[:8]],
        )
    except Exception as exc:
        audit_log_fn(
            "idle_commons_publish_error",
            target_id=session_id,
            target_type="session",
            details={"error": str(exc), "candidate_id": commons.get("candidate_id")},
        )
    if publish_result and str(publish_result.get("topic_id") or "").strip():
        agent.hive_activity_tracker.note_watched_topic(
            session_id=session_id,
            topic_id=str(publish_result.get("topic_id") or "").strip(),
        )
    with agent._activity_lock:
        agent._last_idle_commons_ts = now
        agent._idle_commons_seed_index = (seed_index + 1) % 64
    audit_log_fn(
        "idle_commons_cycle_complete",
        target_id=session_id,
        target_type="session",
        details={
            "idle_for_seconds": round(idle_for_seconds, 2),
            "candidate_id": commons.get("candidate_id"),
            "topic_id": str((publish_result or {}).get("topic_id") or ""),
            "publish_status": str((publish_result or {}).get("status") or "local_only"),
            "finalization_id": str((_commons_commit or {}).get("finalization_id") or ""),
            "topic": dict(commons.get("topic") or {}).get("topic"),
        },
    )


#: The curiosity consumer's gates. Idle means the user has not sent a turn for this long;
#: the interval keeps one background topic per idle tick even when the loop ticks faster.
CURIOSITY_QUEUE_IDLE_SECONDS = 60.0
CURIOSITY_QUEUE_MIN_INTERVAL_SECONDS = 60.0
#: A `running` row older than this belongs to an executor that died mid-topic; it is re-queued.
CURIOSITY_QUEUE_STALE_RUNNING_SECONDS = 900


def maybe_execute_queued_curiosity_once(
    agent: Any,
    *,
    load_preferences_fn: Callable[[], Any],
    time_fn: Callable[[], float],
    audit_log_fn: Callable[..., Any],
    capacity_fn: Callable[[], bool] | None = None,
) -> dict[str, Any] | None:
    """Execute at most ONE queued curiosity topic, off the user's critical path.

    The roam QUEUES its topics inside the served turn (F46) and this is the lane that runs
    them: the existing idle thread, so no scheduler is added. Every gate is a fact about the
    machine or the user, never about the topic:

    * the curiosity policy is enabled and the user's `idle_research_assist` preference is on;
    * no turn is in flight, the user has been idle for `CURIOSITY_QUEUE_IDLE_SECONDS` since the
      last turn ENDED, and the last background topic ran at least
      `CURIOSITY_QUEUE_MIN_INTERVAL_SECONDS` ago;
    * `core.helper_scheduler.HelperScheduler` -- the authority that already keeps mesh work
      from starving the local user -- says the node has capacity (CPU/memory ceilings);
      an authority that cannot be consulted did not say yes;
    * the claim is atomic in the store, so two consumers never run one topic;
    * execution is bounded by the policy's `max_total_roam_seconds` and is cancelled at the
      next web-call boundary the moment the user sends a turn or the runtime is stopping --
      a cancelled or deadline-cut topic goes back to `queued` with a run row naming why.

    Returns the executor's report (topic, terminal status, candidate id, seconds) or None
    when a gate held. Chat never waits on this: it runs on the idle thread and the user's
    activity is the cancellation signal.
    """
    if not policy_engine.automatic_outbound_enabled():
        return None
    try:
        if not bool(getattr(agent.curiosity.config, "enabled", True)):
            return None
    except Exception:
        return None
    prefs = load_preferences_fn()
    if not bool(getattr(prefs, "idle_research_assist", True)):
        return None
    now = time_fn()
    with agent._activity_lock:
        idle_for_seconds = now - float(agent._last_user_activity_ts)
        since_last = now - float(getattr(agent, "_last_curiosity_execute_ts", 0.0) or 0.0)
        turns_in_flight = int(getattr(agent, "_turns_in_flight", 0) or 0)
    # A turn in flight IS activity, however long it runs (measured: a 163 s research turn
    # looked idle to a start-only mark and the consumer ran beside it).
    if turns_in_flight > 0:
        return None
    if idle_for_seconds < CURIOSITY_QUEUE_IDLE_SECONDS:
        return None
    if since_last < CURIOSITY_QUEUE_MIN_INTERVAL_SECONDS:
        return None
    if capacity_fn is None:
        capacity_fn = _default_curiosity_capacity
    try:
        has_capacity = bool(capacity_fn())
    except Exception as exc:
        audit_log_fn(
            "curiosity_queue_deferred",
            target_id=agent.persona_id if hasattr(agent, "persona_id") else "agent",
            target_type="agent",
            details={"reason": "capacity_authority_unavailable", "error": str(exc)},
        )
        return None
    if not has_capacity:
        audit_log_fn(
            "curiosity_queue_deferred",
            target_id=agent.persona_id if hasattr(agent, "persona_id") else "agent",
            target_type="agent",
            details={"reason": "no_capacity"},
        )
        return None
    from storage.curiosity_state import claim_next_queued_curiosity_topic

    row = claim_next_queued_curiosity_topic(
        stale_running_after_seconds=CURIOSITY_QUEUE_STALE_RUNNING_SECONDS
    )
    if row is None:
        return None
    tick_started = now

    def _cancelled() -> bool:
        if not bool(getattr(agent, "_idle_commons_running", True)):
            return True
        with agent._activity_lock:
            if int(getattr(agent, "_turns_in_flight", 0) or 0) > 0:
                return True
            return float(agent._last_user_activity_ts) > tick_started

    try:
        # THE OWNING ENTRY POINT of legitimate background work opens the named effect scope
        # (`core.effect_gateway.named_background_effect_scope`): every remote door fails closed
        # outside a turn, and the consumer runs on the idle thread outside any turn. Measured on
        # the controlled rig (9d7b6a3d/d4aae964): the stub saw no request during any consumer
        # tick -- each search was refused at the door and the topic ended `empty`.
        from core.effect_gateway import named_background_effect_scope

        with named_background_effect_scope(
            "curiosity.queue_consumer",
            source_context={
                "surface": "background",
                "lane": "curiosity_consumer",
                "session_id": str(row.get("session_id") or ""),
            },
        ):
            report = agent.curiosity.execute_queued_topic(row, cancelled=_cancelled)
    except Exception as exc:
        from storage.curiosity_state import requeue_curiosity_topic

        requeue_curiosity_topic(str(row.get("topic_id") or ""))
        audit_log_fn(
            "curiosity_topic_execution_error",
            target_id=str(row.get("topic_id") or ""),
            target_type="curiosity_topic",
            details={"error": str(exc), "topic": str(row.get("topic") or "")},
        )
        with agent._activity_lock:
            agent._last_curiosity_execute_ts = time_fn()
        return None
    with agent._activity_lock:
        agent._last_curiosity_execute_ts = time_fn()
    audit_log_fn(
        "curiosity_topic_executed",
        target_id=str(row.get("topic_id") or ""),
        target_type="curiosity_topic",
        details={
            "topic": str(report.get("topic") or ""),
            "topic_kind": str(report.get("topic_kind") or ""),
            "status": str(report.get("status") or ""),
            "candidate_id": report.get("candidate_id"),
            "cached": bool(report.get("cached")),
            "seconds": report.get("seconds"),
            "idle_for_seconds": round(idle_for_seconds, 2),
            "session_id": str(row.get("session_id") or ""),
        },
    )
    return report


def _default_curiosity_capacity() -> bool:
    from core.helper_scheduler import HelperScheduler

    return bool(HelperScheduler().can_accept_mesh_task())


def stop_background_runtime_threads(agent: Any, *, join_timeout: float = 2.0) -> dict[str, bool]:
    """Stop the idle-commons and presence loops; report whether each thread exited in time.

    The flags are the loops' own conditions, and the curiosity consumer reads the same flag
    as its cancellation signal, so an in-flight topic stops at its next web-call boundary and
    goes back to `queued`. Threads are daemon threads: one that is still inside a call when
    the timeout passes ends with the process, and the report says so (False).

    The presence sync lane is joined for `join_timeout`, then its epoch is closed through the
    sync's commit fence. Every sync COMMIT is decided at SQLite's commit point against that close:
    one not yet there when the epoch closes (a write still inside its transaction, or a COMMIT
    statement that started but has not reached the commit) is refused and rolled back without
    stop waiting for it; one already past it is being written by SQLite, and stop waits for it so
    it lands before stop returns. That is the only wait here that is not bounded by a timeout, and
    it is a wait on SQLite finishing a commit decided before the close. A sync audit write waiting
    for a busy database is woken and abandoned, and the sync's budget store opens nothing more:
    no write of the stopped sync commits after stop returns, into its own store or any other. A
    thread still running after the join is reported (False); it can no longer commit.
    """
    agent._idle_commons_running = False
    agent._public_presence_running = False
    # The one-shot presence sync is a background lane too. Under the presence lock: refuse new
    # syncs and drop the queued pass; then join the in-flight one, whose row still lands in this
    # runtime's own storage when it finishes inside the bound (and is rolled back when it does not).
    lock = getattr(agent, "_public_presence_lock", None)
    with lock if lock is not None else contextlib.nullcontext():
        agent._public_presence_sync_stopped = True
        agent._public_presence_sync_pending = False
    timeout = max(0.0, float(join_timeout))
    report: dict[str, bool] = {}
    for name in ("_idle_commons_thread", "_public_presence_thread", "_public_presence_sync_thread"):
        thread = getattr(agent, name, None)
        if thread is None:
            continue
        try:
            thread.join(timeout=timeout)
            report[name] = not thread.is_alive()
        except Exception:
            report[name] = False
        if not report[name]:
            _LOG.warning(
                "runtime stop: background thread %s (%s) still running after %.1fs join",
                name,
                getattr(thread, "name", "?"),
                timeout,
            )
    # Close the epoch either way: a sync thread that outlived the join belongs to the ended
    # epoch, commits nothing from here on, and must not hold the lane shut for a later start.
    # Lock order: write lock, commit fence, presence lock. The write lock is held only by an audit
    # entry point outside the storage contract (bounded wait); caller-owned commits never hold it.
    write_lock = getattr(agent, "_public_presence_sync_write_lock", None)
    acquired = True
    if write_lock is not None:
        acquired = write_lock.acquire(True, PRESENCE_SYNC_COMMIT_WAIT_SECONDS)
    fence = _sync_commit_fence(agent)
    try:
        with fence.cond:
            with lock if lock is not None else contextlib.nullcontext():
                agent._public_presence_sync_epoch = int(getattr(agent, "_public_presence_sync_epoch", 0)) + 1
                agent._public_presence_sync_inflight = False
                agent._public_presence_sync_thread = None
                wake = getattr(agent, "_public_presence_sync_wake", None)
                if wake is not None:
                    wake.set()
            # From here every commit point refuses. A COMMIT that passed its commit point before
            # the close is already being written by SQLite: it lands before stop returns.
            waited_since = time.monotonic()
            while fence.passed:
                if not fence.cond.wait(PRESENCE_SYNC_COMMIT_WAIT_SECONDS) and fence.passed:
                    _LOG.warning(
                        "runtime stop: still waiting after %.1fs for %d presence sync commit(s) that "
                        "passed their commit point before stop; they land before stop returns",
                        time.monotonic() - waited_since,
                        len(fence.passed),
                    )
    finally:
        if write_lock is not None and acquired:
            write_lock.release()
    if not acquired:
        report["_public_presence_sync_thread"] = False
        _LOG.warning(
            "runtime stop: an audit entry point outside the storage contract was still writing after "
            "%.1fs; the sync epoch is closed and no later write of that sync can start",
            PRESENCE_SYNC_COMMIT_WAIT_SECONDS,
        )
    return report


def reopen_public_presence_sync(agent: Any) -> None:
    """Runtime start: allow presence syncs again for this (new) epoch, with a fresh stop signal.

    The session's store is pinned here, before this runtime queues any sync: every audit write
    of this session goes through a caller-owned transaction on THIS database, so a later home
    switch (a restarted runtime, test isolation) can never receive an old session's write.
    """
    from storage.db import active_default_db_path

    lock = getattr(agent, "_public_presence_lock", None)
    with lock if lock is not None else contextlib.nullcontext():
        agent._public_presence_sync_stopped = False
        agent._public_presence_sync_store_path = str(active_default_db_path())
        wake = getattr(agent, "_public_presence_sync_wake", None)
        if wake is None or wake.is_set():
            agent._public_presence_sync_wake = threading.Event()


def maybe_run_autonomous_hive_research_once(
    agent: Any,
    *,
    load_preferences_fn: Callable[[], Any],
    time_fn: Callable[[], float],
    pick_signal_fn: Callable[[list[dict[str, Any]]], dict[str, Any] | None],
    research_topic_fn: Callable[..., Any],
    audit_log_fn: Callable[..., Any],
) -> None:
    if not policy_engine.automatic_outbound_enabled():
        return
    prefs = load_preferences_fn()
    if not bool(getattr(prefs, "accept_hive_tasks", True)):
        return
    if not bool(getattr(prefs, "idle_research_assist", True)):
        return
    if not agent.public_hive_bridge.enabled():
        return

    now = time_fn()
    with agent._activity_lock:
        idle_for_seconds = now - float(agent._last_user_activity_ts)
        since_last_research = now - float(agent._last_idle_hive_research_ts)
    if idle_for_seconds < 240.0:
        return
    if since_last_research < 900.0:
        return

    queue_rows = agent.public_hive_bridge.list_public_research_queue(limit=12)
    signal = pick_signal_fn(queue_rows)
    if not signal:
        return

    auto_session_id = f"auto-research:{signal.get('topic_id') or ''!s}"
    lane_context = {"surface": "background", "platform": "openclaw", "lane": "autonomous_research"}
    agent._sync_public_presence(status="busy", source_context=lane_context)
    try:
        result = research_topic_fn(
            signal,
            public_hive_bridge=agent.public_hive_bridge,
            curiosity=agent.curiosity,
            hive_activity_tracker=agent.hive_activity_tracker,
            session_id=auto_session_id,
            auto_claim=True,
        )
        audit_log_fn(
            "idle_hive_research_cycle_complete",
            target_id=str(signal.get("topic_id") or auto_session_id),
            target_type="topic",
            details=result.to_dict(),
        )
        with agent._activity_lock:
            agent._last_idle_hive_research_ts = now
        if result.ok and result.topic_id:
            with contextlib.suppress(Exception):
                agent.hive_activity_tracker.note_watched_topic(session_id=auto_session_id, topic_id=result.topic_id)
    finally:
        agent._sync_public_presence(
            status=agent._idle_public_presence_status(),
            source_context=lane_context,
        )


def idle_commons_session_id(*, get_local_peer_id_fn: Callable[[], str]) -> str:
    return f"agent-commons:{get_local_peer_id_fn()}"


def normalize_public_presence_status(agent: Any, status: str) -> str:
    lowered = str(status or "idle").strip().lower()
    if lowered == "busy":
        return "busy"
    return agent._idle_public_presence_status()


def idle_public_presence_status(*, load_preferences_fn: Callable[[], Any]) -> str:
    prefs = load_preferences_fn()
    return "idle" if bool(getattr(prefs, "accept_hive_tasks", True)) else "limited"
