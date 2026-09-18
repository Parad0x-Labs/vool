from __future__ import annotations

import tempfile
from pathlib import Path

from storage.db import get_connection
from storage.migrations import run_migrations


def test_run_migrations_handles_legacy_learning_shards_without_origin_columns() -> None:
    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS learning_shards")
        conn.execute(
            """
            CREATE TABLE learning_shards (
                shard_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                problem_class TEXT NOT NULL,
                problem_signature TEXT NOT NULL,
                summary TEXT NOT NULL,
                resolution_pattern_json TEXT NOT NULL,
                environment_tags_json TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_node_id TEXT,
                quality_score REAL NOT NULL,
                trust_score REAL NOT NULL,
                local_validation_count INTEGER NOT NULL DEFAULT 0,
                local_failure_count INTEGER NOT NULL DEFAULT 0,
                quarantine_status TEXT NOT NULL DEFAULT 'active',
                risk_flags_json TEXT NOT NULL,
                freshness_ts TEXT NOT NULL,
                expires_ts TEXT,
                signature TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # A real legacy database carries a legacy VERSION STAMP as well as a legacy table.
        # `run_migrations` stamps `user_version` last and restores the pre-update bytes on any
        # failure (the K-03/M2 laws in storage/migrations.py), so a database still stamped at
        # this binary's contract genuinely has this binary's schema and is skipped as a
        # versioned no-op. Rolling the stamp back is what makes this fixture the upgrade it
        # claims to be, rather than an out-of-band table swap no upgrade path produces.
        conn.execute("PRAGMA user_version = 0;")
        conn.commit()
    finally:
        conn.close()

    run_migrations()

    conn = get_connection()
    try:
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(learning_shards)").fetchall()
        }
        indexes = {
            str(row[1])
            for row in conn.execute("PRAGMA index_list(learning_shards)").fetchall()
        }
    finally:
        conn.close()

    assert "origin_task_id" in columns
    assert "origin_session_id" in columns
    assert "share_scope" in columns
    assert "restricted_terms_json" in columns
    assert "idx_learning_shards_session_scope" in indexes


def test_run_migrations_handles_legacy_contribution_ledger_without_finality_columns() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "legacy-ledger.db"
        conn = get_connection(str(db_path))
        try:
            conn.execute(
                """
                CREATE TABLE contribution_ledger (
                    entry_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    helper_peer_id TEXT NOT NULL,
                    parent_peer_id TEXT NOT NULL,
                    contribution_type TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    helpfulness_score REAL NOT NULL DEFAULT 0,
                    points_awarded INTEGER NOT NULL DEFAULT 0,
                    wnull_pending INTEGER NOT NULL DEFAULT 0,
                    wnull_released INTEGER NOT NULL DEFAULT 0,
                    slashed_flag INTEGER NOT NULL DEFAULT 0,
                    fraud_window_end_ts TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO contribution_ledger (
                    entry_id, task_id, helper_peer_id, parent_peer_id, contribution_type,
                    outcome, helpfulness_score, points_awarded, wnull_pending, wnull_released,
                    slashed_flag, fraud_window_end_ts, created_at, updated_at
                ) VALUES (
                    'entry-1', 'task-1', 'helper-1', 'parent-1', 'assist',
                    'pending', 0.9, 12, 0, 0, 0, NULL, '2026-03-11T00:00:00+00:00', '2026-03-11T00:00:00+00:00'
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

        run_migrations(str(db_path))

        conn = get_connection(str(db_path))
        try:
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(contribution_ledger)").fetchall()
            }
            indexes = {
                str(row[1])
                for row in conn.execute("PRAGMA index_list(contribution_ledger)").fetchall()
            }
            row = conn.execute(
                """
                SELECT finality_state, finality_depth, finality_target
                FROM contribution_ledger
                WHERE entry_id = 'entry-1'
                """
            ).fetchone()
        finally:
            conn.close()

    assert "finality_state" in columns
    assert "finality_depth" in columns
    assert "finality_target" in columns
    assert "idx_contribution_ledger_finality" in indexes
    assert row is not None
    assert str(row["finality_state"]) == "pending"
    assert int(row["finality_depth"]) == 0
    assert int(row["finality_target"]) >= 1
