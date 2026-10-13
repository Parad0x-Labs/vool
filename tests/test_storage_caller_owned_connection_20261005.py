from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from core import audit_logger
from storage import event_hash_chain, event_log
from storage.db import active_default_db_path, configure_default_db_path, get_connection


@pytest.fixture
def store(tmp_path):
    previous = active_default_db_path()
    path = tmp_path / "caller-owned.db"
    configure_default_db_path(path)
    audit_logger.reset_schema_ready_flag()
    yield path
    configure_default_db_path(previous)
    audit_logger.reset_schema_ready_flag()


def _counts(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return tuple(
        conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0] if name in tables else 0
        for name in ("audit_log", "event_log_v2", "event_hash_chain")
    )


def _write(conn, value="one"):
    audit_logger.log("caller_owned", value, {"value": value}, trace_id="trace-one", conn=conn)


@pytest.mark.parametrize("begin", [None, "BEGIN", "BEGIN IMMEDIATE"])
def test_commit_is_one_transaction_and_not_visible_early(store, begin):
    conn = get_connection()
    observer = get_connection()
    try:
        if begin:
            conn.execute(begin)
        _write(conn)
        assert conn.in_transaction
        assert _counts(conn) == (1, 1, 1)
        assert _counts(observer) == (0, 0, 0)
        conn.commit()
        assert _counts(observer) == (1, 1, 1)
        ids = [
            conn.execute(f"SELECT event_id FROM {t}").fetchone()[0]
            for t in ("audit_log", "event_log_v2", "event_hash_chain")
        ]
        assert len(set(ids)) == 1
        assert event_hash_chain.verify_chain()
        assert event_log.events_for_trace("trace-one")[0]["payload"] == {"value": "one"}
    finally:
        conn.rollback()
        conn.close()
        observer.close()


@pytest.mark.parametrize("autocommit", [False, True])
def test_rollback_leaves_no_rows_and_connection_remains_owned(store, autocommit):
    conn = get_connection()
    try:
        if autocommit:
            conn.isolation_level = None
        _write(conn, "discard")
        conn.rollback()
        assert _counts(conn) == (0, 0, 0)
        _write(conn, "keep")
        conn.commit()
        assert _counts(conn) == (1, 1, 1)
        assert event_hash_chain.verify_chain()
    finally:
        conn.close()


def test_rollback_preserves_caller_work_before_savepoint(store):
    conn = get_connection()
    try:
        conn.execute("CREATE TABLE caller_state(value TEXT)")
        conn.execute("INSERT INTO caller_state VALUES ('prior')")
        conn.execute("SAVEPOINT audit_attempt")
        _write(conn, "discard")
        conn.execute("ROLLBACK TO audit_attempt")
        conn.execute("RELEASE audit_attempt")
        assert _counts(conn) == (0, 0, 0)
        assert conn.execute("SELECT value FROM caller_state").fetchone()[0] == "prior"
        conn.commit()
    finally:
        conn.close()


def test_rollback_does_not_poison_default_schema_cache(store):
    conn = get_connection()
    try:
        _write(conn, "discard")
        conn.rollback()
        audit_logger.log("legacy", "keep", {"value": "keep"})
        assert _counts(conn) == (1, 1, 1)
        assert event_hash_chain.verify_chain()
    finally:
        conn.close()


def test_chain_survives_rollback_between_commits(store):
    audit_logger.log("legacy", "first")
    first = event_hash_chain.latest_hash()
    conn = get_connection()
    try:
        _write(conn, "discard")
        conn.rollback()
        assert event_hash_chain.latest_hash() == first
        _write(conn, "last")
        conn.commit()
        assert _counts(conn) == (2, 2, 2)
        assert event_hash_chain.verify_chain()
    finally:
        conn.close()


@pytest.mark.parametrize("writer", ["audit", "event", "hash"])
def test_closed_connection_fails_without_default_store_write(store, writer):
    conn = get_connection()
    conn.close()
    with pytest.raises(sqlite3.ProgrammingError):
        _call(writer, conn)
    observer = get_connection()
    try:
        assert _counts(observer) == (0, 0, 0)
    finally:
        observer.close()


def _call(writer, conn, identity="direct"):
    if writer == "audit":
        _write(conn, identity)
    elif writer == "event":
        return event_log.append_event(
            category="direct",
            actor="system",
            target_type="test",
            target_id=identity,
            payload={"id": identity},
            event_id=identity,
            conn=conn,
        )
    else:
        return event_hash_chain.append_hashed_event(identity, {"id": identity}, conn=conn)


@pytest.mark.parametrize("writer,expected", [("audit", (1, 1, 1)), ("event", (0, 1, 1)), ("hash", (0, 0, 1))])
def test_direct_entry_points_pin_path_and_wait_for_caller_commit(store, tmp_path, writer, expected):
    conn = get_connection()
    other = tmp_path / "next-session.db"
    configure_default_db_path(other)
    try:
        result = _call(writer, conn)
        assert _counts(conn) == expected
        assert conn.in_transaction
        if writer != "audit":
            assert isinstance(result, str) and result
        conn.commit()
        observer = get_connection()
        try:
            assert _counts(observer) == (0, 0, 0)
        finally:
            observer.close()
    finally:
        conn.close()


def test_hash_duplicate_and_conflict_keep_caller_transaction(store):
    conn = get_connection()
    try:
        first = event_hash_chain.append_hashed_event("same", {"value": 1}, conn=conn)
        assert event_hash_chain.append_hashed_event("same", {"value": 1}, conn=conn) == first
        with pytest.raises(event_hash_chain.EventConflictError):
            event_hash_chain.append_hashed_event("same", {"value": 2}, conn=conn)
        assert conn.in_transaction
        conn.rollback()
        assert _counts(conn) == (0, 0, 0)
        event_hash_chain.append_hashed_event("same", {"value": 2}, conn=conn)
        conn.commit()
        assert event_hash_chain.verify_chain()
    finally:
        conn.close()


def test_default_output_matches_transactional_redaction_and_payload(store):
    details = {"endpoint": "198.51.100.1:1234", "peer_id": "p" * 64, "plain": "retain"}
    kwargs = dict(
        event_type="redaction",
        target_id="198.51.100.2:5678",
        details=details,
        actor="operator",
        target_type="transport",
        trace_id="trace-safe",
    )
    assert audit_logger.log(**kwargs) is None
    conn = get_connection()
    try:
        assert audit_logger.log(**kwargs, conn=conn) is None
        conn.commit()
        rows = conn.execute(
            "SELECT event_type,actor,target_type,target_id,details_json FROM audit_log ORDER BY rowid"
        ).fetchall()
        assert tuple(rows[0]) == tuple(rows[1])
        assert "198.51.100" not in rows[0][3] + rows[0][4]
        assert json.loads(rows[0][4])["plain"] == "retain"
        assert _counts(conn) == (2, 2, 2)
        assert event_hash_chain.verify_chain()
    finally:
        conn.close()


def test_busy_connection_failure_keeps_no_partial_rows(store):
    holder = get_connection()
    writer = get_connection()
    try:
        holder.execute("BEGIN IMMEDIATE")
        writer.execute("PRAGMA busy_timeout=25")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            _write(writer)
        writer.rollback()
        holder.rollback()
        assert _counts(writer) == (0, 0, 0)
        _write(writer)
        writer.commit()
        assert event_hash_chain.verify_chain()
    finally:
        holder.close()
        writer.close()


def test_caller_write_does_not_wait_on_legacy_chain_lock(store):
    audit_logger.log("warmup")
    completed = threading.Event()
    errors = []

    def write():
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _write(conn)
            conn.commit()
        except Exception as exc:
            errors.append(exc)
        finally:
            conn.close()
            completed.set()

    thread = threading.Thread(target=write)
    # A legacy writer may own this lock while SQLite waits on this caller.
    # The caller must use its DB writer lock, never invert those locks.
    assert event_hash_chain._CHAIN_LOCK.acquire(timeout=1)
    try:
        thread.start()
        finished_under_lock = completed.wait(timeout=2)
    finally:
        event_hash_chain._CHAIN_LOCK.release()
        thread.join(timeout=10)
    assert not thread.is_alive()
    assert not errors
    assert finished_under_lock, "caller waited on the legacy chain lock"
    assert event_hash_chain.verify_chain()


def test_plain_autocommit_connection_is_transactional_without_row_factory(store):
    conn = sqlite3.connect(store, isolation_level=None)
    try:
        _write(conn)
        assert conn.in_transaction
        conn.rollback()
        assert _counts(conn) == (0, 0, 0)
        _write(conn)
        conn.commit()
        assert _counts(conn) == (1, 1, 1)
        assert event_hash_chain.verify_chain()
    finally:
        conn.close()


def test_concurrent_legacy_and_caller_writers_leave_one_valid_chain(store):
    audit_logger.log("warmup")
    barrier = threading.Barrier(8)

    def writer(index):
        barrier.wait(timeout=5)
        for turn in range(5):
            if index % 2:
                conn = get_connection()
                try:
                    _write(conn, f"{index}-{turn}")
                    if turn == 0:
                        conn.rollback()
                    else:
                        conn.commit()
                finally:
                    conn.close()
            else:
                audit_logger.log("legacy", f"{index}-{turn}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(writer, range(8)))
    conn = get_connection()
    try:
        assert _counts(conn) == (37, 37, 37)
        assert event_hash_chain.verify_chain()
    finally:
        conn.close()
