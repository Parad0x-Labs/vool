"""F0-A / K-03 store version gate tests.

Red mutations covered:
- old binary opens upgraded DB => loud refusal (simulated older binary contract
  reading a newer user_version);
- wrong application_id => refusal;
- fresh DB + legacy fixture both bootstrap through run_migrations and stamp;
- version bump LAST: a crash before the stamp means the unit reruns idempotently.
"""
from __future__ import annotations

import sqlite3

import pytest

import storage.db as sdb
from storage.db import StoreVersionError
from storage.migrations import run_migrations


def _open_raw(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def test_fresh_db_bootstraps_and_stamps_version(tmp_path):
    db = tmp_path / "fresh.db"
    run_migrations(db)
    conn = _open_raw(db)
    try:
        assert int(conn.execute("PRAGMA application_id").fetchone()[0]) == sdb.STORE_APPLICATION_ID
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == sdb.STORE_USER_VERSION
    finally:
        conn.close()
    # Ordinary open through the gate must succeed on a stamped current DB.
    sdb.get_connection(db).execute("SELECT 1")


def test_downgrade_refused(tmp_path, monkeypatch):
    db = tmp_path / "newer.db"
    conn = _open_raw(db)
    try:
        conn.execute(f"PRAGMA application_id = {sdb.STORE_APPLICATION_ID}")
        conn.execute(f"PRAGMA user_version = {sdb.STORE_USER_VERSION + 1}")
        conn.commit()
    finally:
        conn.close()
    # RED MUTATION: an "older binary" (lower expected version) must refuse-open.
    monkeypatch.setattr(sdb, "STORE_USER_VERSION", sdb.STORE_USER_VERSION - 1)
    with pytest.raises(StoreVersionError, match="downgrade refused"):
        sdb.get_connection(db)


def test_wrong_application_id_refused(tmp_path):
    db = tmp_path / "alien.db"
    conn = _open_raw(db)
    try:
        conn.execute("PRAGMA application_id = 12345")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(StoreVersionError, match="application_id"):
        sdb.get_connection(db)


def test_legacy_fixture_upgrades_and_reopens(tmp_path):
    """A pre-gate legacy DB (tables but 0/0 stamps) upgrades cleanly."""
    db = tmp_path / "legacy.db"
    conn = _open_raw(db)
    try:
        conn.execute(
            "CREATE TABLE finalized_responses (parent_task_id TEXT PRIMARY KEY,"
            " raw_synthesized_text TEXT, rendered_persona_text TEXT, status_marker TEXT,"
            " confidence_score REAL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.commit()
    finally:
        conn.close()
    run_migrations(db)
    conn = _open_raw(db)
    try:
        assert int(conn.execute("PRAGMA application_id").fetchone()[0]) == sdb.STORE_APPLICATION_ID
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='finalized_responses'"
        ).fetchall()
        assert len(rows) == 1
    finally:
        conn.close()


def test_migration_rerun_idempotent_crash_before_stamp(tmp_path):
    """Crash before the version stamp => unit reruns; schema changes idempotent."""
    db = tmp_path / "crash.db"
    # Simulate crash-before-stamp by running migrations then wiping the stamps.
    run_migrations(db)
    conn = _open_raw(db)
    try:
        conn.execute("PRAGMA application_id = 0")
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
    finally:
        conn.close()
    # Rerun completes the unit.
    run_migrations(db)
    conn = _open_raw(db)
    try:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == sdb.STORE_USER_VERSION
    finally:
        conn.close()
