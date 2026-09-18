"""F1-F migration round-trip + legacy weaker-evidence classification tests."""
from __future__ import annotations

import sqlite3

import pytest

import storage.db as sdb


def _open_raw(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


_LEGACY_A7 = """
CREATE TABLE a7_finalizations (
    finalization_id TEXT PRIMARY KEY,
    semantic_result_id TEXT NOT NULL DEFAULT '',
    turn_id TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    canonical_content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'answer_present',
    delivery_status TEXT NOT NULL DEFAULT 'NOT_ATTEMPTED',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def test_legacy_delivered_imports_as_weaker_evidence(tmp_path):
    db = tmp_path / "legacy-delivered.db"
    conn = _open_raw(db)
    try:
        conn.execute(_LEGACY_A7)
        conn.execute(
            "INSERT INTO a7_finalizations (finalization_id, semantic_result_id,"
            " content_hash, canonical_content, delivery_status)"
            " VALUES ('fc-legacy-1', 'sr-legacy-1', 'sha256:aa', 'old bytes', 'DELIVERED')"
        )
        conn.commit()
    finally:
        conn.close()

    from storage.migrations import run_migrations

    run_migrations(db)

    conn = _open_raw(db)
    try:
        row = conn.execute(
            "SELECT * FROM a7_finalizations WHERE finalization_id = 'fc-legacy-1'"
        ).fetchone()
        # Law 7: never proven recipient-class truth.
        assert row["delivery_evidence_class"] == "LEGACY_UNVERIFIED"
        assert row["delivery_status"] == "DELIVERED"
        # Law 4/5: no repaired identities fabricated onto legacy rows.
        assert row["semantic_result_id"] == "sr-legacy-1"
    finally:
        conn.close()


def test_no_plaintext_multiplied_into_payload_ref(tmp_path):
    """Law 8: the K-09 additive columns land empty-legal; no migration copies
    plaintext into payload_ref."""
    db = tmp_path / "no-copy.db"
    conn = _open_raw(db)
    try:
        conn.execute(_LEGACY_A7)
        conn.execute(
            "INSERT INTO a7_finalizations (finalization_id, semantic_result_id,"
            " content_hash, canonical_content) VALUES ('fc-l2', 'sr-l2', 'sha256:bb', 'private text')"
        )
        conn.commit()
    finally:
        conn.close()
    from storage.migrations import run_migrations

    run_migrations(db)
    conn = _open_raw(db)
    try:
        row = conn.execute(
            "SELECT payload_ref FROM a7_finalizations WHERE finalization_id = 'fc-l2'"
        ).fetchone()
        assert row["payload_ref"] is None
    finally:
        conn.close()


def test_roundtrip_fresh_and_upgraded_both_pass_foundation_suite(tmp_path):
    from storage.migrations import run_migrations

    for name in ("fresh", "upgraded"):
        db = tmp_path / f"{name}.db"
        if name == "upgraded":
            conn = _open_raw(db)
            try:
                conn.execute(
                    "CREATE TABLE finalized_responses (parent_task_id TEXT PRIMARY KEY,"
                    " raw_synthesized_text TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
                )
                conn.commit()
            finally:
                conn.close()
        run_migrations(db)
        # Gate accepts both afterwards; version stamped.
        sdb.get_connection(db).execute("SELECT 1").fetchall()
        conn = _open_raw(db)
        try:
            assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == sdb.STORE_USER_VERSION
        finally:
            conn.close()
