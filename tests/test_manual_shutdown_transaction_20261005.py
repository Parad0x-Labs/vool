"""The presence sync's audit writes are caller-owned transactions on the session's pinned store.

The entered-write race this closes: the sync's audit writer had passed its epoch check and was
inside the storage write path when runtime stop closed the epoch and the active default database
switched to the next session's store. The legacy writer re-resolved the active path per sub-write
and committed each row independently, so the stopped session's audit row (and its event/hash rows)
landed in the NEWLY ACTIVE database -- a write after shutdown, redirected across databases, and a
non-atomic triple that can split between stores.

The integration: the session's store is pinned at runtime start (before any sync is queued), each
audit event is written through ONE connection opened on that pinned path, BEGIN IMMEDIATE on it
acquires SQLite's writer lock (ownership, not a preflight probe), the audit row, event row and
hash commit together, and the caller -- the sync thread -- owns commit, rollback and teardown.
The commit runs under the sync write lock with the epoch re-checked there, and stop closes the
epoch under that lock: a write still uncommitted when stop closes it is rolled back, so nothing
of a stopped sync commits after stop returns. The pass's effect budget is pinned to the same
store behind the same gate, and the pinned connection keeps the store version gate.

Every lifecycle test here drives the real bootstrapped runtime from the First-Run Pact rig (real
agent, real ``RuntimeServices.shutdown``, real audit storage, real agent restarts through
``agent.start()``). Only the network edge (a gated public hive bridge) and explicit scheduling
gates at product seams (the audit entry point the agent hands to the sync lane, the store-opening
helper) are controlled, so "in flight" and "entered the write" are facts, not timing guesses.

Negative controls: the race-proof and patched-writer tests are run against the unpatched caller in
this job's evidence (they are red there); nothing here is skipped when the integration is absent.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import random
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from core import policy_engine
from core.agent_runtime import presence
from tests.first_run_pact_rig import pact_rig  # fixture: pytest discovers it on the module
from tests.test_presence_sync_shutdown import background_network_consent as background_network_consent

_SYNC_EVENTS = ("public_hive_presence_sync_failed", "public_hive_presence_sync_error")


class _GatedBridge:
    """The public hive bridge with its presence POST held at a gate (the network edge only)."""

    def __init__(self, inner: Any, *, label: str = "gated", ok: bool = False) -> None:
        self._inner = inner
        self.label = label
        self.ok = ok
        self.entered = threading.Event()
        self.release = threading.Event()
        self.returned = threading.Event()
        self.calls = 0
        self.sync_calls = 0
        self.heartbeat_calls = 0

    def _answer(self) -> dict[str, Any]:
        self.calls += 1
        self.entered.set()
        assert self.release.wait(20.0), "test never released the gate"
        self.returned.set()
        if self.ok:
            return {"ok": True, "status": "registered", "posted_to": ["gate"], "errors": []}
        return {"ok": False, "status": "unreachable", "posted_to": [], "errors": [self.label]}

    def sync_presence(self, **_kwargs: Any) -> dict[str, Any]:
        self.sync_calls += 1
        return self._answer()

    def heartbeat_presence(self, **kwargs: Any) -> dict[str, Any]:
        self.heartbeat_calls += 1
        return self._answer()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _AuditGate:
    """Scheduling gate at the product's own audit seam: the fn the agent hands the sync lane.

    Calls from the sync thread block until the test releases them; every call (blocked or not)
    delegates to the real ``audit_logger.log`` with its arguments unchanged. This is the same
    discipline as the gated bridge: control the schedule, run the real writer.
    """

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.proceed = threading.Event()
        self.saw_conn: list[bool] = []

    def log(self, *args: Any, **kwargs: Any) -> None:
        from core import audit_logger

        if threading.current_thread().name == "vool-public-presence-sync":
            self.saw_conn.append("conn" in kwargs)
            self.entered.set()
            assert self.proceed.wait(20.0), "test never released the audit gate"
        return audit_logger.log(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        from core import audit_logger

        return getattr(audit_logger, name)


def _settled_agent(rig):
    """The rig's real agent after its start-up sync finished, with the gated bridge fitted."""
    agent = rig.runtime.agent
    with agent._public_presence_lock:
        boot_thread = agent._public_presence_sync_thread
    if boot_thread is not None:
        boot_thread.join(10.0)
        assert not boot_thread.is_alive(), "the start-up presence sync never finished"
    bridge = _GatedBridge(agent.public_hive_bridge)
    agent.public_hive_bridge = bridge
    return agent, bridge


def _db(rig) -> Path:
    return rig.home / "data" / "vool_web0_v2.db"


def _sync_rows(db: Path) -> list[tuple[str, dict[str, Any]]]:
    if not db.exists():
        return []
    with sqlite3.connect(db) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "audit_log" not in tables:
            return []
        rows = conn.execute(
            "SELECT event_type, details_json FROM audit_log WHERE event_type IN (?, ?) ORDER BY rowid",
            _SYNC_EVENTS,
        ).fetchall()
    return [(event, json.loads(details or "{}")) for event, details in rows]


def _triple_counts(db: Path) -> tuple[int, int, int] | None:
    if not db.exists():
        return None
    with sqlite3.connect(db) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"audit_log", "event_log_v2", "event_hash_chain"} <= tables:
            return None
        return tuple(  # type: ignore[return-value]
            conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            for name in ("audit_log", "event_log_v2", "event_hash_chain")
        )


def _tables(db: Path) -> list[str]:
    if not db.exists():
        return []
    with sqlite3.connect(db) as conn:
        return sorted(row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"))


def _chain_valid(db: Path) -> bool:
    import storage.db as sdb
    from storage.event_hash_chain import verify_chain

    previous = sdb.active_default_db_path()
    sdb.configure_default_db_path(db)
    try:
        return bool(verify_chain())
    finally:
        sdb.configure_default_db_path(previous)


def _hold_the_database(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db, timeout=1, isolation_level=None, check_same_thread=False)
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _timed_stop(agent, join_timeout: float) -> tuple[dict[str, bool] | None, float | None]:
    out: dict[str, Any] = {}

    def _stop() -> None:
        started = time.monotonic()
        out["report"] = presence.stop_background_runtime_threads(agent, join_timeout=join_timeout)
        out["elapsed"] = time.monotonic() - started

    stopper = threading.Thread(target=_stop, name="test-presence-stop", daemon=True)
    stopper.start()
    stopper.join(15.0)
    return out.get("report"), out.get("elapsed")


def _sync_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "vool-public-presence-sync" and t.is_alive()]


# --- the integration is present -----------------------------------------------------------


def test_the_patched_transactional_writer_is_active(pact_rig):
    """Fail fast when the sync lane still writes through the probe-and-independent-commit path."""
    assert getattr(presence, "PRESENCE_SYNC_SESSION_TRANSACTIONS", False), (
        "presence sync audit writes are not caller-owned transactions in this build"
    )
    assert not hasattr(presence, "_storage_write_probe"), (
        "the stale-able preflight probe is still the write's ownership test"
    )
    assert callable(getattr(presence, "_write_session_audit", None)), (
        "the session-transaction audit writer is absent"
    )
    agent = pact_rig.runtime.agent
    assert str(agent._public_presence_sync_store_path) == str(_db(pact_rig)), (
        "the runtime did not pin its session store at start"
    )


# --- the race, proven and closed ----------------------------------------------------------


def test_an_entered_write_under_stop_and_store_switch_is_rolled_back_everywhere(pact_rig, monkeypatch, caplog):
    """The entered-write race: stop closed the epoch and the active store switched mid-write.

    The sync's audit call is gated INSIDE the storage write path: past its epoch check, inside its
    own BEGIN IMMEDIATE on the pinned store. While it is held there, the real bounded stop runs and
    the active default database switches to the next session's store. On the unpatched caller the
    write re-resolves the active path per sub-write and lands in the NEWLY ACTIVE database; the
    first caller-owned-transaction head committed it late into its own store instead. The contract
    (no late write, including one already inside SQLite when stop began; the caller-owned
    connection exists so presence can roll the write back on stop): the write is rolled back at its
    commit gate. No row in either store, no partial triple, no schema created in the new store.
    """
    agent, bridge = _settled_agent(pact_rig)
    session_db = _db(pact_rig)
    next_db = pact_rig.home / "next-session" / "vool_web0_v2.db"
    next_db.parent.mkdir(parents=True, exist_ok=True)
    rows_before = _sync_rows(session_db)
    counts_before = _triple_counts(session_db)

    gate = _AuditGate()
    monkeypatch.setattr("core.agent_runtime.agent.audit_logger", gate)
    import core.audit_logger as real_audit_logger
    from storage.db import configure_default_db_path

    agent._sync_public_presence(status="busy")
    assert bridge.entered.wait(10.0), "the sync runs on its own thread"
    sync_thread = agent._public_presence_sync_thread
    assert sync_thread is not None
    bridge.release.set()
    assert gate.entered.wait(10.0), "the audit write entered the storage path"

    with caplog.at_level(logging.WARNING, logger=presence.__name__):
        report, elapsed = _timed_stop(agent, join_timeout=0.05)
        assert elapsed is not None and elapsed < 0.35, f"stop took {elapsed:.3f}s against a 0.05s bound"
        assert report["_public_presence_sync_thread"] is False, "the entered write's thread must be reported, not hidden"
        stop_returned = time.monotonic()
        # The next session's store becomes active while the stopped session's write is in flight.
        configure_default_db_path(next_db)
        real_audit_logger.reset_schema_ready_flag()
        next_tables_before = _tables(next_db)
        gate.proceed.set()
        sync_thread.join(10.0)
        assert not sync_thread.is_alive()
        assert time.monotonic() > stop_returned

    assert _sync_rows(session_db) == rows_before, "the entered write committed into its session store after stop returned"
    assert _triple_counts(session_db) == counts_before, "the rolled-back write left a partial triple"
    assert _chain_valid(session_db), "the session store's hash chain broke"
    assert _sync_rows(next_db) == [], "the stopped session's write was redirected to the new store"
    assert _triple_counts(next_db) is None, "the stopped session created audit storage in the new store"
    assert _tables(next_db) == next_tables_before, "the stopped pass created schema in the new store"
    assert gate.saw_conn == [True], "the sync's audit write did not go through the caller-owned connection"
    assert any("rolled back, not written" in r.getMessage() for r in caplog.records), "the rollback was silent"

    configure_default_db_path(session_db)
    real_audit_logger.reset_schema_ready_flag()
    pact_rig.runtime.shutdown()


class _CommitHold:
    """Holds the sync thread's real COMMIT statement inside SQLite (scheduling only).

    `at="start"`: an SQL trace callback holds the COMMIT when it starts executing, after the
    product's pre-check and before SQLite reaches its commit point. `at="past_commit_point"`: the
    COMMIT's own commit-point callback is wrapped and the hold placed on its next call, after the
    fence has passed the commit and before SQLite has written it. Either way the commit is real
    SQLite; nothing is faked.
    """

    def __init__(self, monkeypatch, at: str) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        real_open = presence._open_session_store
        hold = self

        def _open(store_path):
            conn = real_open(store_path)
            if conn is None or threading.current_thread().name != "vool-public-presence-sync":
                return conn
            state = {"audit": False}  # only the audit transaction's COMMIT is held

            def _trace(sql):
                if "INSERT INTO AUDIT_LOG" in sql.upper():
                    state["audit"] = True
                if at == "start" and sql.strip().upper() == "COMMIT" and state["audit"] and not hold.entered.is_set():
                    hold.entered.set()
                    hold.release.wait(20.0)

            conn.set_trace_callback(_trace)
            if at != "start":
                real_set = conn.set_progress_handler

                def _set(handler, n):
                    if handler is None:
                        return real_set(None, n)
                    calls = {"n": 0}

                    def _wrapped():
                        calls["n"] += 1
                        decided = handler()
                        if calls["n"] == 2 and decided == 0 and state["audit"] and not hold.entered.is_set():
                            hold.entered.set()
                            hold.release.wait(20.0)
                        return decided

                    return real_set(_wrapped, n)

                conn.set_progress_handler = _set
            return conn

        monkeypatch.setattr(presence, "_open_session_store", _open)


def test_a_commit_already_running_when_stop_runs_is_refused_and_stop_stays_bounded(pact_rig, monkeypatch, caplog):
    """The sync's real COMMIT statement has started inside SQLite (past every Python-side check)
    when stop runs. Stop must neither wait for it nor let it land: the epoch closes at once, and the
    COMMIT, when it resumes, is refused at SQLite's commit point and rolled back. Before, stop waited
    its 5 s lock bound, returned, and the COMMIT then landed one row after stop had returned."""
    agent, bridge = _settled_agent(pact_rig)
    session_db = _db(pact_rig)
    rows_before = _sync_rows(session_db)
    counts_before = _triple_counts(session_db)
    hold = _CommitHold(monkeypatch, at="start")
    bridge.release.set()
    agent._sync_public_presence(status="busy")
    worker = agent._public_presence_sync_thread
    try:
        assert hold.entered.wait(10.0), "the sync's audit transaction never reached its COMMIT"
        with caplog.at_level(logging.WARNING, logger=presence.__name__):
            report, elapsed = _timed_stop(agent, join_timeout=0.05)
            assert elapsed is not None and elapsed < 0.35, f"stop took {elapsed:.3f}s with a COMMIT held before its commit point"
            assert report["_public_presence_sync_thread"] is False
            assert worker.is_alive(), "the COMMIT is still held inside SQLite"
            rows_at_stop = _sync_rows(session_db)
            hold.release.set()
            worker.join(10.0)
            assert not worker.is_alive()
    finally:
        hold.release.set()
    assert rows_at_stop == rows_before
    assert _sync_rows(session_db) == rows_before, "the COMMIT that was running at stop landed after stop returned"
    assert _triple_counts(session_db) == counts_before, "the refused COMMIT left a partial triple"
    assert _chain_valid(session_db)
    assert any("rolled back, not written" in r.getMessage() for r in caplog.records), "the refusal was silent"
    pact_rig.runtime.shutdown()


def test_a_commit_past_its_commit_point_lands_before_stop_returns(pact_rig, monkeypatch):
    """The other side of the fence: a COMMIT the fence passed before stop closed the epoch is being
    written by SQLite. Stop waits for it, however long SQLite takes, so the row is already in the
    store when stop returns: it never lands after."""
    agent, bridge = _settled_agent(pact_rig)
    session_db = _db(pact_rig)
    rows_before = _sync_rows(session_db)
    hold = _CommitHold(monkeypatch, at="past_commit_point")
    bridge.release.set()
    agent._sync_public_presence(status="busy")
    worker = agent._public_presence_sync_thread
    seen: dict[str, Any] = {}

    def _stop() -> None:
        started = time.monotonic()
        seen["report"] = presence.stop_background_runtime_threads(agent, join_timeout=0.05)
        seen["elapsed"] = time.monotonic() - started
        seen["rows_at_return"] = _sync_rows(session_db)

    stopper = threading.Thread(target=_stop, name="test-presence-stop", daemon=True)
    try:
        assert hold.entered.wait(10.0), "the sync's COMMIT never passed its commit point"
        stopper.start()
        stopper.join(1.0)
        assert stopper.is_alive(), "stop returned while a commit it had let through was still being written"
        hold.release.set()
        stopper.join(10.0)
        assert not stopper.is_alive()
        worker.join(10.0)
        assert not worker.is_alive()
    finally:
        hold.release.set()
    new_rows = seen["rows_at_return"][len(rows_before):]
    assert [d.get("errors") for _e, d in new_rows] == [["gated"]], "the passed commit was not in the store when stop returned"
    assert _sync_rows(session_db) == seen["rows_at_return"], "a write landed after stop returned"
    assert _sync_orphans(session_db) == 0 and _chain_valid(session_db)
    pact_rig.runtime.shutdown()


def test_a_waiting_write_is_abandoned_and_lands_in_no_database(pact_rig, caplog):
    """A write still WAITING for the SQLite writer lock when stop closes the epoch writes nowhere.

    Another writer really holds the session database (BEGIN IMMEDIATE from a second connection);
    stop stays bounded, the waiting write is woken and abandoned, and releasing the lock later
    lands nothing in either the session store or a newly active one. This holds on the unpatched
    caller too: it is the preserved half of the bounded-stop contract.
    """
    agent, bridge = _settled_agent(pact_rig)
    session_db = _db(pact_rig)
    next_db = pact_rig.home / "next-session" / "vool_web0_v2.db"
    next_db.parent.mkdir(parents=True, exist_ok=True)
    rows_before = _sync_rows(session_db)

    holder = _hold_the_database(session_db)
    holder_released = False
    try:
        agent._sync_public_presence(status="busy")
        assert bridge.entered.wait(10.0)
        bridge.release.set()
        thread = agent._public_presence_sync_thread
        thread.join(0.3)
        assert thread.is_alive(), "the audit write is waiting on the held database"

        with caplog.at_level(logging.WARNING, logger=presence.__name__):
            report, elapsed = _timed_stop(agent, join_timeout=0.05)
            assert elapsed is not None and elapsed < 0.35, f"stop took {elapsed:.3f}s against a 0.05s bound"
            assert report["_public_presence_sync_thread"] is False
            from storage.db import configure_default_db_path

            configure_default_db_path(next_db)
            holder.execute("ROLLBACK")
            holder.close()
            holder_released = True
            thread.join(5.0)
            assert not thread.is_alive(), "the waiting write was not abandoned after the wake"
            configure_default_db_path(session_db)
            from core import audit_logger

            audit_logger.reset_schema_ready_flag()
    finally:
        if not holder_released:
            with contextlib.suppress(Exception):
                if holder.in_transaction:
                    holder.execute("ROLLBACK")
            with contextlib.suppress(Exception):
                holder.close()

    assert any("not written" in r.getMessage() for r in caplog.records), "the abandonment was silent"
    assert _sync_rows(session_db) == rows_before, "the abandoned write landed in its session store"
    if next_db.exists():
        assert _sync_rows(next_db) == [], "the abandoned write landed in the newly active store"
    pact_rig.runtime.shutdown()


def test_a_transaction_begun_before_epoch_close_is_rolled_back_empty(pact_rig, monkeypatch, caplog):
    """A transaction that BEGAN but whose effects had not started when stop closed the epoch.

    The store-opening helper is gated (a product seam) so the writer sits between opening the
    pinned connection and its effects when the real stop runs. The epoch re-check after the writer
    lock is acquired rolls the transaction back: no row, no partial triple, nothing redirected.
    """
    assert callable(getattr(presence, "_open_session_store", None)), (
        "the pinned-store writer is absent: the epoch re-check under test belongs to it"
    )
    agent, bridge = _settled_agent(pact_rig)
    session_db = _db(pact_rig)
    rows_before = _sync_rows(session_db)
    counts_before = _triple_counts(session_db)

    real_open = presence._open_session_store
    entered = threading.Event()
    proceed = threading.Event()

    def _gated_open(store_path: str):
        conn = real_open(store_path)
        if threading.current_thread().name == "vool-public-presence-sync":
            entered.set()
            assert proceed.wait(20.0), "test never released the store gate"
        return conn

    monkeypatch.setattr(presence, "_open_session_store", _gated_open)

    agent._sync_public_presence(status="busy")
    assert bridge.entered.wait(10.0)
    sync_thread = agent._public_presence_sync_thread
    assert sync_thread is not None
    bridge.release.set()
    assert entered.wait(10.0), "the writer opened its store and is about to begin effects"

    with caplog.at_level(logging.WARNING, logger=presence.__name__):
        report, elapsed = _timed_stop(agent, join_timeout=0.05)
        assert elapsed is not None and elapsed < 0.35
        assert report["_public_presence_sync_thread"] is False
        proceed.set()
        sync_thread.join(10.0)
        assert not sync_thread.is_alive()

    assert any("not written" in r.getMessage() for r in caplog.records)
    assert _sync_rows(session_db) == rows_before, "a transaction begun under a closed epoch committed"
    assert _triple_counts(session_db) == counts_before, "the rolled-back transaction left partial rows"
    assert _chain_valid(session_db)
    pact_rig.runtime.shutdown()


# --- rollback on stop: restart overlap, effect budget, store version, the gate itself -------


class _OneShotAuditGate:
    """The audit seam gate for the FIRST sync-thread call only (run 1's write); later calls pass."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.proceed = threading.Event()
        self.gated_ident: int | None = None
        self._lock = threading.Lock()

    def log(self, *args: Any, **kwargs: Any) -> None:
        from core import audit_logger

        me = threading.current_thread()
        block = False
        with self._lock:
            if me.name == "vool-public-presence-sync" and self.gated_ident is None:
                self.gated_ident = me.ident
                block = True
        if block:
            self.entered.set()
            assert self.proceed.wait(20.0), "test never released the audit gate"
        return audit_logger.log(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        from core import audit_logger

        return getattr(audit_logger, name)


class _PresenceWarnings(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _record_store_opens(monkeypatch) -> list[tuple[int | None, str]]:
    """Observation only: (thread ident, real path) of every store the runtime opens."""
    import storage.db as sdb

    opened: list[tuple[int | None, str]] = []
    real_make = sdb._make_connection
    real_open = presence._open_session_store

    def _make(db_path):
        opened.append((threading.current_thread().ident, os.path.realpath(str(db_path))))
        return real_make(db_path)

    def _open(store_path):
        opened.append((threading.current_thread().ident, os.path.realpath(str(store_path))))
        return real_open(store_path)

    monkeypatch.setattr(sdb, "_make_connection", _make)
    monkeypatch.setattr(presence, "_open_session_store", _open)
    return opened


def _opened_by(opened: list[tuple[int | None, str]], ident: int | None, db: Path) -> int:
    want = os.path.realpath(str(db))
    return sum(1 for who, path in opened if who == ident and path == want)


def test_an_entered_write_overlapping_a_restart_onto_a_new_home_is_rolled_back(pact_rig, monkeypatch):
    """Run 1's write is inside SQLite when stop runs; run 2 really starts on a NEW home meanwhile.

    Run 2's own sync audits into run 2's store while run 1's transaction is still open (the sync
    write lock is held only across a commit, never across a write). Run 1's write reaches its
    commit gate after stop and the restart and is rolled back: its row lands in neither store and
    its thread never opens run 2's store.
    """
    import core.audit_logger as real_audit_logger
    from core.runtime_continuity import configure_runtime_continuity_db_path, reset_runtime_continuity_state
    from core.runtime_paths import configure_runtime_home
    from storage.db import configure_default_db_path

    agent, first = _settled_agent(pact_rig)
    session_db = _db(pact_rig)
    run2_home = pact_rig.home / "run2-home"
    next_db = run2_home / "data" / "vool_web0_v2.db"
    next_db.parent.mkdir(parents=True, exist_ok=True)
    rows_before = _sync_rows(session_db)
    counts_before = _triple_counts(session_db)
    opened = _record_store_opens(monkeypatch)
    gate = _OneShotAuditGate()
    monkeypatch.setattr("core.agent_runtime.agent.audit_logger", gate)

    first.release.set()
    agent._sync_public_presence(status="busy")
    run1 = agent._public_presence_sync_thread
    assert run1 is not None
    assert gate.entered.wait(10.0), "run 1's audit write never entered the writer"
    # After the in-process restart caplog's root handler no longer receives the presence logger's
    # records (observed), so they are collected at that logger itself.
    warnings = _PresenceWarnings()
    presence_logger = logging.getLogger(presence.__name__)
    presence_logger.addHandler(warnings)
    try:
        report, elapsed = _timed_stop(agent, join_timeout=0.05)
        assert elapsed is not None and elapsed < 0.35, f"stop took {elapsed:.3f}s against a 0.05s bound"
        assert report["_public_presence_sync_thread"] is False

        # Run 2: another home becomes active and the runtime really restarts in this process.
        monkeypatch.setenv("VOOL_HOME", str(run2_home))
        configure_runtime_home(run2_home)
        # The operator gave run 2's home the same background-network consent, so its sync runs.
        policy_engine.set_operator_policy_values({"network.outbound_enabled": True})
        configure_default_db_path(next_db)
        configure_runtime_continuity_db_path(next_db)
        reset_runtime_continuity_state()
        real_audit_logger.reset_schema_ready_flag()
        second = _GatedBridge(first._inner, label="second-run")
        second.release.set()
        agent.public_hive_bridge = second
        agent.start()
        run2 = agent._public_presence_sync_thread
        assert run2 is not None and run2 is not run1
        run2.join(10.0)
        assert not run2.is_alive(), "run 2's sync waited on run 1's open transaction"
        assert run1.is_alive(), "run 1's write is still held inside its transaction"
        assert [d.get("errors") for _e, d in _sync_rows(next_db)] == [["second-run"]]

        gate.proceed.set()
        run1.join(10.0)
        assert not run1.is_alive()
        assert _sync_rows(session_db) == rows_before, "run 1's write committed into its store after stop"
        assert _triple_counts(session_db) == counts_before, "run 1's rolled-back write left a partial triple"
        assert [d.get("errors") for _e, d in _sync_rows(next_db)] == [["second-run"]], "run 1's row reached run 2"
        assert _chain_valid(session_db) and _chain_valid(next_db)
        assert _opened_by(opened, run1.ident, next_db) == 0, "run 1's thread opened run 2's store"
        assert any("rolled back, not written" in m for m in warnings.messages), "the rollback was silent"
    finally:
        presence_logger.removeHandler(warnings)
        gate.proceed.set()
        monkeypatch.setenv("VOOL_HOME", str(pact_rig.home))
        configure_runtime_home(pact_rig.home)
        configure_default_db_path(session_db)
        configure_runtime_continuity_db_path(session_db)
        reset_runtime_continuity_state()
        real_audit_logger.reset_schema_ready_flag()
    pact_rig.runtime.shutdown()


class _ReservingBridge(_GatedBridge):
    """The presence POST authorized at the real effect gate -- a budget unit reserved in the store
    -- then held at the network edge; it never begins its attempt (the unit is never consumed)."""

    def _answer(self) -> dict[str, Any]:
        from core.effect_gateway import (
            DECISION_ALLOWED,
            EFFECT_NETWORK_FETCH,
            LIFECYCLE_AUTHORIZED,
            EffectReceipt,
            current_effect_ledger,
        )

        ledger = current_effect_ledger()
        assert ledger is not None, "the presence pass runs inside its named effect scope"
        ledger.open_effect(
            EffectReceipt(
                effect_class=EFFECT_NETWORK_FETCH,
                decision=DECISION_ALLOWED,
                lifecycle=LIFECYCLE_AUTHORIZED,
                reason="presence POST",
            )
        )
        return super()._answer()


def _set_network_budget() -> None:
    """A real operator rule (outside any effect scope) binding the presence POST's class."""
    from core import effect_budget as eb

    token = eb.grant_operator_budget_authority("presence pinned-store test")
    eb.apply_operator_adjustment(
        token,
        [eb.BudgetAdjustment(budget_class=eb.BUDGET_CLASS_NETWORK_FETCH, scope=eb.SCOPE_PROJECT, new_limit=50, note="test")],
    )


def _reservations(db: Path) -> list[dict[str, Any]]:
    if "effect_budget_reservations" not in _tables(db):
        return []
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in conn.execute(
                "SELECT reservation_id, state, owner_ref, settle_reason FROM effect_budget_reservations ORDER BY rowid"
            )
        ]


def test_a_stopped_pass_holding_a_budget_unit_writes_no_budget_state_after_stop(pact_rig, monkeypatch):
    """The stopped pass's effect-scope close opens no store: not the next session's, not its own.

    A real budget rule binds the presence POST at the real gate, so the pass holds a reserved unit
    when stop runs; its scope closes after stop returned and after the active store switched.
    Before, that close resolved the ACTIVE store, created the budget schema there and would have
    released the unit against it. Now the pass's budget is pinned to its session store and closed
    with its epoch: the next store is never opened, and the unit stays held in the session store
    (a held unit never widens a budget) instead of being written late.
    """
    import core.audit_logger as real_audit_logger
    from storage.db import configure_default_db_path

    agent, gated = _settled_agent(pact_rig)
    session_db = _db(pact_rig)
    next_db = pact_rig.home / "next-session" / "vool_web0_v2.db"
    next_db.parent.mkdir(parents=True, exist_ok=True)
    _set_network_budget()
    opened = _record_store_opens(monkeypatch)
    bridge = _ReservingBridge(gated._inner, label="budgeted")
    agent.public_hive_bridge = bridge
    rows_before = _sync_rows(session_db)

    agent._sync_public_presence(status="busy")
    assert bridge.entered.wait(10.0)
    thread = agent._public_presence_sync_thread
    held = [r for r in _reservations(session_db) if r["state"] == "reserved"]
    assert len(held) == 1, f"the presence POST did not reserve its unit in the session store: {held}"
    try:
        report, elapsed = _timed_stop(agent, join_timeout=0.05)
        assert elapsed is not None and elapsed < 0.35, f"stop took {elapsed:.3f}s against a 0.05s bound"
        assert report["_public_presence_sync_thread"] is False
        configure_default_db_path(next_db)
        real_audit_logger.reset_schema_ready_flag()
        budget_after_stop = _reservations(session_db)
        bridge.release.set()
        thread.join(10.0)
        assert not thread.is_alive()
    finally:
        bridge.release.set()
        configure_default_db_path(session_db)
        real_audit_logger.reset_schema_ready_flag()

    assert _opened_by(opened, thread.ident, next_db) == 0, "the stopped pass opened the next session's store"
    assert _tables(next_db) == [], "the stopped pass created schema in the next session's store"
    assert _reservations(session_db) == budget_after_stop, "the stopped pass wrote budget state after stop returned"
    assert [r["state"] for r in budget_after_stop if r["reservation_id"] == held[0]["reservation_id"]] == ["reserved"]
    assert _sync_rows(session_db) == rows_before, "the stopped pass audited after stop"
    pact_rig.runtime.shutdown()


def test_a_live_pass_returns_its_unexecuted_budget_unit_to_its_own_store(pact_rig, monkeypatch):
    """The pinned budget store on the live path: the active store switches mid-pass (no stop), the
    POST never begins its attempt, and the scope's close returns the unit in the session store
    that reserved it; the newly active store is never opened by the pass, which still audits home."""
    import core.audit_logger as real_audit_logger
    from storage.db import configure_default_db_path

    agent, gated = _settled_agent(pact_rig)
    session_db = _db(pact_rig)
    next_db = pact_rig.home / "next-session" / "vool_web0_v2.db"
    next_db.parent.mkdir(parents=True, exist_ok=True)
    _set_network_budget()
    opened = _record_store_opens(monkeypatch)
    bridge = _ReservingBridge(gated._inner, label="budgeted-live")
    agent.public_hive_bridge = bridge
    rows_before = _sync_rows(session_db)

    agent._sync_public_presence(status="busy")
    assert bridge.entered.wait(10.0)
    thread = agent._public_presence_sync_thread
    held = [r for r in _reservations(session_db) if r["state"] == "reserved"]
    assert len(held) == 1
    try:
        configure_default_db_path(next_db)
        real_audit_logger.reset_schema_ready_flag()
        bridge.release.set()
        thread.join(10.0)
        assert not thread.is_alive()
    finally:
        bridge.release.set()
        configure_default_db_path(session_db)
        real_audit_logger.reset_schema_ready_flag()

    released = [r for r in _reservations(session_db) if r["reservation_id"] == held[0]["reservation_id"]]
    assert [r["state"] for r in released] == ["released"], released
    assert "background scope closed" in released[0]["settle_reason"]
    assert _opened_by(opened, thread.ident, next_db) == 0, "the live pass opened the newly active store"
    assert _tables(next_db) == []
    new_rows = _sync_rows(session_db)[len(rows_before):]
    assert [d.get("errors") for _e, d in new_rows] == [["budgeted-live"]], "the live pass audits into its own store"
    pact_rig.runtime.shutdown()


def test_the_pinned_store_keeps_the_store_version_gate(pact_rig, caplog):
    """A store this binary must not open (stamped by a newer one) is refused by the presence
    writer exactly as by the default open: no row, and the refusal is logged with its code."""
    import storage.db as sdb

    agent, bridge = _settled_agent(pact_rig)
    session_db = _db(pact_rig)
    rows_before = _sync_rows(session_db)
    counts_before = _triple_counts(session_db)
    with sqlite3.connect(session_db) as conn:
        original = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.execute(f"PRAGMA user_version = {int(sdb.STORE_USER_VERSION) + 1}")
    try:
        with pytest.raises(sdb.StoreVersionError):
            sdb.get_connection(session_db).close()
        bridge.release.set()
        with caplog.at_level(logging.WARNING, logger=presence.__name__):
            agent._sync_public_presence(status="busy")
            thread = agent._public_presence_sync_thread
            assert thread is not None
            thread.join(10.0)
            assert not thread.is_alive()
        assert bridge.calls == 1
        assert _sync_rows(session_db) == rows_before, "the presence writer wrote into a store the default open refuses"
        assert _triple_counts(session_db) == counts_before
        assert any("runtime store is refused (VOOL_E_STORE" in r.getMessage() for r in caplog.records)
    finally:
        with sqlite3.connect(session_db) as conn:
            conn.execute(f"PRAGMA user_version = {int(original)}")
    pact_rig.runtime.shutdown()


def test_a_closed_sync_gate_lets_no_statement_form_commit(tmp_path):
    """The commit gate has no side door. DDL SQLite would autocommit, autocommit DML, a literal
    COMMIT, the connection's context manager, an outermost savepoint's RELEASE, a cursor,
    executemany and executescript all meet it once the epoch is closed; an unbound connection
    commits nothing at all."""
    db = tmp_path / "gate.db"
    sqlite3.connect(db).close()
    fence = presence._SyncCommitFence()
    gate_open = {"value": True}

    def _connection(bound: bool = True):
        conn = presence._open_session_store(str(db))
        assert conn is not None
        if bound:
            conn.bind_sync_gate(fence, lambda: gate_open["value"])
        return conn

    conn = _connection()
    conn.execute("CREATE TABLE kept (a)")  # open gate: an autocommit DDL commits through it
    conn.close()
    gate_open["value"] = False

    def _literal_commit(c):
        c.execute("BEGIN IMMEDIATE")
        c.execute("INSERT INTO kept VALUES (2)")
        c.execute("COMMIT")

    def _context_manager(c):
        with c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT INTO kept VALUES (3)")

    def _savepoint(c):
        c.execute("SAVEPOINT outer_sp")
        c.execute("INSERT INTO kept VALUES (7)")
        c.execute("RELEASE outer_sp")

    attempts = {
        "ddl": lambda c: c.execute("CREATE TABLE late_ddl (a)"),
        "autocommit_dml": lambda c: c.execute("INSERT INTO kept VALUES (1)"),
        "literal_commit": _literal_commit,
        "context_manager": _context_manager,
        "savepoint_release": _savepoint,
        "cursor": lambda c: c.cursor().execute("CREATE TABLE late_cursor (a)"),
        "executemany": lambda c: c.executemany("INSERT INTO kept VALUES (?)", [(4,), (5,)]),
        "executescript": lambda c: c.executescript("CREATE TABLE late_script (a);"),
    }
    refused: dict[str, str] = {}
    for name, attempt in attempts.items():
        c = _connection()
        try:
            attempt(c)
        except (presence.SyncStoppedError, sqlite3.NotSupportedError) as exc:
            refused[name] = type(exc).__name__
        finally:
            c.close()
    unbound = _connection(bound=False)
    try:
        unbound.execute("BEGIN IMMEDIATE")
        unbound.execute("INSERT INTO kept VALUES (6)")
        with pytest.raises(presence.SyncStoppedError):
            unbound.commit()
    finally:
        unbound.close()

    assert sorted(refused) == sorted(attempts), f"a statement form committed past the closed gate: {refused}"
    assert fence.passed == set()
    assert _tables(db) == ["kept"]
    with sqlite3.connect(db) as check:
        assert check.execute("SELECT count(*) FROM kept").fetchone()[0] == 0


_SECOND_PROCESS = r'''
import json, os, sys
from pathlib import Path

sys.path.insert(0, os.getcwd())
from core.unattended_preflight import preflight

preflight("presence-second-process")
from tests import _network_seal

seal = _network_seal.push("second-process:no-network", _network_seal.deny_everything("the second runtime has no network authority"))
import pytest

from tests.first_run_pact_rig import pact_rig

patch = pytest.MonkeyPatch()
fixture = pact_rig.__wrapped__(Path(sys.argv[1]), patch)
out = {"pid": os.getpid()}
print("BOOTING", flush=True)
import time

started = time.monotonic()
rig = next(fixture)
out["boot_seconds"] = round(time.monotonic() - started, 3)
try:
    agent = rig.runtime.agent
    thread = agent._public_presence_sync_thread
    if thread is not None:
        thread.join(30.0)
    out["startup_sync_finished"] = thread is None or not thread.is_alive()
    out["store"] = str(agent._public_presence_sync_store_path)
finally:
    rig.runtime.shutdown()
    try:
        next(fixture)
    except StopIteration:
        pass
    patch.undo()
    _network_seal.release(seal)
print("RESULT " + json.dumps(out), flush=True)
'''


def test_a_restart_in_a_second_process_on_the_same_home_never_receives_the_stopped_write(pact_rig, monkeypatch, tmp_path, caplog):
    """The production restart: a SECOND OS process starts a runtime on the same home while the
    stopped first runtime's write is still inside SQLite (holding the writer lock).

    The second runtime boots on the shared store, waits only on SQLite's own lock, and audits its
    startup sync; the first runtime's write is rolled back when released. The shared store ends
    with the second process's row, never the stopped write, and an intact chain.
    """
    import subprocess
    import sys

    agent, bridge = _settled_agent(pact_rig)
    session_db = _db(pact_rig)
    rows_before = _sync_rows(session_db)
    gate = _AuditGate()
    monkeypatch.setattr("core.agent_runtime.agent.audit_logger", gate)

    agent._sync_public_presence(status="busy")
    assert bridge.entered.wait(10.0)
    sync_thread = agent._public_presence_sync_thread
    bridge.release.set()
    assert gate.entered.wait(10.0), "the first runtime's write entered its transaction"
    report, elapsed = _timed_stop(agent, join_timeout=0.05)
    assert elapsed is not None and elapsed < 0.35
    assert report["_public_presence_sync_thread"] is False

    script = tmp_path / "second_runtime.py"
    script.write_text(_SECOND_PROCESS)
    second_log = tmp_path / "second_runtime.log"
    repo = Path(__file__).resolve().parents[1]
    with second_log.open("w") as log:
        second = subprocess.Popen(
            [sys.executable, str(script), str(pact_rig.home.parent)],
            cwd=repo,
            env=dict(os.environ, VOOL_TEST_MODE="1", PYTHONDONTWRITEBYTECODE="1"),
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
        )
    output: list[str] = []
    try:
        for line in second.stdout:  # the second runtime is about to boot on the shared store
            output.append(line)
            if line.startswith("BOOTING"):
                break
        assert second.poll() is None, "".join(output) + second_log.read_text()[-4000:]
        # Hold the first runtime's write (and SQLite's writer lock) while the second one boots
        # onto the shared store; storage's own busy wait (5 s) covers this.
        time.sleep(2.0)
        with caplog.at_level(logging.WARNING, logger=presence.__name__):
            gate.proceed.set()
            sync_thread.join(10.0)
        assert not sync_thread.is_alive()
        rest, _ = second.communicate(timeout=120)
        output.append(rest)
    finally:
        gate.proceed.set()
        if second.poll() is None:  # only this test's own child, by its handle
            second.kill()
            second.wait(10)
    text = "".join(output)
    assert second.returncode == 0, text + second_log.read_text()[-4000:]
    result = json.loads(text.split("RESULT ", 1)[1].splitlines()[0])
    assert result["startup_sync_finished"] is True
    assert os.path.realpath(result["store"]) == os.path.realpath(str(session_db))

    new_rows = _sync_rows(session_db)[len(rows_before):]
    assert [d.get("errors") for _e, d in new_rows if d.get("errors") == ["gated"]] == [], "the stopped write landed"
    assert [d.get("status") for _e, d in new_rows] == ["disabled"], f"the second process's startup sync row: {new_rows}"
    assert _sync_orphans(session_db) == 0
    assert _chain_valid(session_db)
    assert any("rolled back, not written" in r.getMessage() for r in caplog.records)
    pact_rig.runtime.shutdown()


# --- atomicity, provenance, fallback ------------------------------------------------------


def test_committed_sync_rows_are_atomic_with_preserved_provenance(pact_rig):
    """Each committed sync event is one atomic triple whose ids agree across the three tables."""
    agent = pact_rig.runtime.agent
    with agent._public_presence_lock:
        boot_thread = agent._public_presence_sync_thread
    if boot_thread is not None:
        boot_thread.join(10.0)
    session_db = _db(pact_rig)
    before = _triple_counts(session_db)

    inner = agent.public_hive_bridge
    for status in ("busy", "idle", "busy"):
        bridge = _GatedBridge(inner, label=f"fast-{status}")
        bridge.release.set()
        agent.public_hive_bridge = bridge
        agent._sync_public_presence(status=status)
        thread = agent._public_presence_sync_thread
        if thread is not None:
            thread.join(10.0)
            assert not thread.is_alive()

    after = _triple_counts(session_db)
    assert after is not None and before is not None
    deltas = [a - b for a, b in zip(after, before, strict=True)]
    assert deltas[0] == deltas[1] == deltas[2] == 3, f"the audit/event/hash triple is not atomic: {deltas}"
    with sqlite3.connect(session_db) as conn:
        orphans = conn.execute(
            """
            SELECT count(*) FROM audit_log a
            LEFT JOIN event_log_v2 e ON e.event_id = a.event_id
            LEFT JOIN event_hash_chain h ON h.event_id = a.event_id
            WHERE a.event_type IN (?, ?) AND (e.event_id IS NULL OR h.event_id IS NULL)
            """,
            _SYNC_EVENTS,
        ).fetchone()[0]
    assert orphans == 0, "a committed sync event lost its event or hash provenance"
    assert _chain_valid(session_db)
    pact_rig.runtime.shutdown()


def test_failed_heartbeat_falls_back_and_audits_the_failure_transactionally(pact_rig):
    """Remote failure fallback: a failed heartbeat retries registration once, then audits once."""
    agent, registering = _settled_agent(pact_rig)
    registering.ok = True
    registering.release.set()
    agent._sync_public_presence(status="busy")
    agent._public_presence_sync_thread.join(10.0)
    assert agent._public_presence_registered is True, "a real successful sync registered this run"

    session_db = _db(pact_rig)
    rows_before = _sync_rows(session_db)
    counts_before = _triple_counts(session_db)

    failing = _GatedBridge(registering._inner, label="heartbeat-failed")
    failing.release.set()
    agent.public_hive_bridge = failing
    agent._sync_public_presence(status="idle")
    thread = agent._public_presence_sync_thread
    thread.join(10.0)
    assert not thread.is_alive()

    assert failing.heartbeat_calls == 1
    assert failing.sync_calls == 1, "the failed heartbeat did not fall back to a full registration sync"
    new_rows = _sync_rows(session_db)[len(rows_before):]
    assert [details.get("errors") for _event, details in new_rows] == [["heartbeat-failed"]]
    counts_after = _triple_counts(session_db)
    deltas = [a - b for a, b in zip(counts_after, counts_before, strict=True)]
    assert deltas[0] == deltas[1] == deltas[2] == 1, f"the fallback failure row is not an atomic triple: {deltas}"
    assert _chain_valid(session_db)
    pact_rig.runtime.shutdown()


def test_a_delayed_success_after_stop_and_store_switch_records_nothing_anywhere(pact_rig):
    """A late SUCCESS from the bridge: not recorded as registration, written into no database."""
    agent, first = _settled_agent(pact_rig)
    assert agent._public_presence_registered is False, "the rig's hive is disabled: never registered"
    first.ok = True
    session_db = _db(pact_rig)
    next_db = pact_rig.home / "next-session" / "vool_web0_v2.db"
    next_db.parent.mkdir(parents=True, exist_ok=True)
    rows_before = _sync_rows(session_db)

    agent._sync_public_presence(status="busy")
    assert first.entered.wait(10.0)
    first_thread = agent._public_presence_sync_thread
    report = presence.stop_background_runtime_threads(agent, join_timeout=0.2)
    assert report["_public_presence_sync_thread"] is False

    from storage.db import configure_default_db_path

    configure_default_db_path(next_db)
    first.release.set()  # the stopped run's POST now succeeds, inside the switched store's epoch
    first_thread.join(10.0)
    assert not first_thread.is_alive()
    configure_default_db_path(session_db)
    from core import audit_logger

    audit_logger.reset_schema_ready_flag()

    assert agent._public_presence_registered is False, "a stopped run's late success was recorded"
    assert _sync_rows(session_db) == rows_before
    if next_db.exists():
        assert _sync_rows(next_db) == [], "the stopped run's late success landed in the new store"
    pact_rig.runtime.shutdown()


# --- restart lifecycle ---------------------------------------------------------------------


def test_two_session_restart_routes_each_run_to_its_own_rows_only(pact_rig):
    """Real restart lifecycle: run 1's hung sync, stop, real ``agent.start()`` run 2.

    Exercised lifecycle: the REAL rig runtime (bootstrapped services, real audit storage) with
    run 1 stopped through the presence stop path and run 2 started through ``agent.start()`` in
    this same process -- the same restart surface the First-Run Pact shutdown family drives.
    Run 1's late return writes nothing; run 2's own sync audits; the lane stays open for run 2.
    """
    agent, first = _settled_agent(pact_rig)
    session_db = _db(pact_rig)
    rows_before = _sync_rows(session_db)
    agent._sync_public_presence(status="busy")
    assert first.entered.wait(10.0)
    first_thread = agent._public_presence_sync_thread
    report = presence.stop_background_runtime_threads(agent, join_timeout=0.2)
    assert report["_public_presence_sync_thread"] is False

    second = _GatedBridge(first._inner, label="second-run")
    second.release.set()
    agent.public_hive_bridge = second
    agent.start()
    second_thread = agent._public_presence_sync_thread
    assert second_thread is not None and second_thread is not first_thread
    second_thread.join(10.0)
    assert not second_thread.is_alive() and second.calls == 1
    assert str(agent._public_presence_sync_store_path) == str(session_db), (
        "the restarted run re-pinned its session store"
    )

    first.release.set()  # run 1's hung POST returns into run 2's lifetime
    first_thread.join(10.0)
    assert not first_thread.is_alive()

    new_rows = _sync_rows(session_db)[len(rows_before):]
    assert [details.get("errors") for _event, details in new_rows] == [
        ["second-run"]
    ], "only the restarted run audited; the stopped run's late return wrote nothing"
    assert agent._public_presence_sync_inflight is False
    assert agent._public_presence_sync_thread is None

    third = _GatedBridge(second._inner, label="third-run")
    third.release.set()
    agent.public_hive_bridge = third
    agent._sync_public_presence(status="busy")  # the lane is open for the second run
    third_thread = agent._public_presence_sync_thread
    assert third_thread is not None
    assert third.entered.wait(10.0), "the second run's queued pass reached the bridge"
    pact_rig.runtime.shutdown()
    third_thread.join(10.0)
    assert not third_thread.is_alive() and third.calls == 1
    assert _chain_valid(session_db)


# --- interleaved stress: real locks, real writers, real restarts ----------------------------


def _sync_orphans(db: Path) -> int:
    """Sync-lane audit rows whose event or hash provenance is missing (atomicity violation)."""
    if not db.exists():
        return 0
    with sqlite3.connect(db) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"audit_log", "event_log_v2", "event_hash_chain"} <= tables:
            return 0
        return conn.execute(
            """
            SELECT count(*) FROM audit_log a
            LEFT JOIN event_log_v2 e ON e.event_id = a.event_id
            LEFT JOIN event_hash_chain h ON h.event_id = a.event_id
            WHERE a.event_type IN (?, ?) AND (e.event_id IS NULL OR h.event_id IS NULL)
            """,
            _SYNC_EVENTS,
        ).fetchone()[0]


def test_fifty_interleaved_cycles_stay_bounded_atomic_and_never_redirect(pact_rig, monkeypatch):
    """50+ repeated interleavings of sync / contention / stop / restart / store switch.

    Real SQLite locks (a second connection holds the writer lock on some cycles), real legacy
    writers (``audit_logger.log`` on the default path, with its documented independent commits,
    concurrent with the sync lane), real bounded stops at varied join bounds, real restarts
    through ``agent.start()``, and periodic active-store switches to a second database. After
    every cycle: no leftover sync thread, stop stayed inside its bound, the hash chain is valid,
    no sync-lane row lost its event/hash provenance, and the other store received no presence
    row.

    Total-table count equality is deliberately NOT asserted here: a legacy-path write caught
    mid-flight by the external lock holder can commit its audit row without its hash (the
    pre-existing independent-commit behaviour this integration removes only for the sync lane).
    The sync lane's own atomicity is asserted exactly, through its own rows.
    """
    agent, bridge = _settled_agent(pact_rig)
    session_db = _db(pact_rig)
    other_db = pact_rig.home / "other-session" / "vool_web0_v2.db"
    other_db.parent.mkdir(parents=True, exist_ok=True)
    from storage.db import configure_default_db_path

    rng = random.Random(20261005)  # fixed seed: the schedule is reproducible
    legacy_errors: list[BaseException] = []
    legacy_stop = threading.Event()

    def _legacy_writer() -> None:
        from core import audit_logger

        i = 0
        while not legacy_stop.is_set():
            for attempt in range(5):  # transient lock contention is a retry, not a failure
                try:
                    audit_logger.log("legacy_probe", f"legacy-{i}", {"cycle": i})
                    break
                except sqlite3.OperationalError:
                    if attempt == 4:
                        legacy_errors.append(RuntimeError(f"legacy write starved after 5 attempts at {i}"))
                    time.sleep(0.01)
                except Exception as exc:  # anything else is a real failure
                    legacy_errors.append(exc)
                    break
            i += 1
            legacy_stop.wait(0.005)

    legacy = threading.Thread(target=_legacy_writer, name="test-legacy-audit-writer", daemon=True)
    legacy.start()
    monkeypatch.setattr(threading, "excepthook", lambda args: legacy_errors.append(args.exc_value))

    cycles = 52
    try:
        for cycle in range(cycles):
            gate = _GatedBridge(bridge._inner, label=f"c{cycle}", ok=rng.random() < 0.3)
            if rng.random() < 0.6:
                gate.release.set()  # immediate return: the write path starts at once
            agent.public_hive_bridge = gate
            agent._sync_public_presence(status="busy" if cycle % 2 else "idle")
            sync_thread = agent._public_presence_sync_thread

            time.sleep(rng.uniform(0.0, 0.008))
            holder = _hold_the_database(session_db) if rng.random() < 0.35 else None
            switched = False
            try:
                if rng.random() < 0.2:
                    gate.release.set()
                join_bound = rng.choice([0.05, 0.2, 2.0])
                report, elapsed = _timed_stop(agent, join_timeout=join_bound)
                assert elapsed is not None, f"cycle {cycle}: stop never returned"
                assert elapsed < join_bound + 0.45, f"cycle {cycle}: stop took {elapsed:.3f}s over its bound"
                if rng.random() < 0.25 and holder is None:
                    # A rapid restart onto a DIFFERENT active store, then back: the stopped
                    # session's write must not follow the switch.
                    configure_default_db_path(other_db)
                    switched = True
                gate.release.set()
                if sync_thread is not None:
                    sync_thread.join(5.0)
                    assert not sync_thread.is_alive(), f"cycle {cycle}: a sync thread outlived its stop"
            finally:
                if holder is not None:
                    with contextlib.suppress(Exception):
                        if holder.in_transaction:
                            holder.execute("ROLLBACK")
                    with contextlib.suppress(Exception):
                        holder.close()
                if switched:
                    configure_default_db_path(session_db)
            gate.release.set()
            if rng.random() < 0.4:
                agent.start()  # a real restart reopens the lane and re-pins the store
                restarted = agent._public_presence_sync_thread
                if restarted is not None:
                    restarted.join(5.0)

            settle = time.monotonic() + 2.0
            while _sync_threads() and time.monotonic() < settle:
                time.sleep(0.005)
            leftovers = _sync_threads()
            assert leftovers == [], f"cycle {cycle}: leftover sync threads {leftovers}"
            if other_db.exists():
                assert _sync_rows(other_db) == [], f"cycle {cycle}: a presence row reached the other store"
            assert _sync_orphans(session_db) == 0, f"cycle {cycle}: a sync event lost its provenance"
            assert _chain_valid(session_db), f"cycle {cycle}: the hash chain broke"
    finally:
        legacy_stop.set()
        legacy.join(10.0)
        configure_default_db_path(session_db)
        from core import audit_logger

        audit_logger.reset_schema_ready_flag()

    assert not legacy.is_alive()
    assert legacy_errors == [], f"writer threads escaped or legacy writers failed: {legacy_errors[:3]!r}"
    assert _sync_orphans(session_db) == 0
    assert _chain_valid(session_db)
    pact_rig.runtime.shutdown()
    assert _sync_threads() == []
