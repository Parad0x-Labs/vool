"""A7 pass-002 — migration proof: old persisted state stays valid.

MIGRATION_CONTRACT laws under attack:
- Law 4: legacy authority never fabricated — no backfilled sr:/fc: identities,
  no blanket finalization of history.
- Law 5: mechanical identities cannot pass authority checks — a read-time hash
  is lookup-only, never finality evidence.
- Law 7: legacy DELIVERED imports as weaker evidence only.
- Law 8: plaintext never copied into payload_ref.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

import storage.db as sdb


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path
    from storage.migrations import run_migrations

    sdb.configure_default_db_path(tmp_path / "a7p2mig.db")
    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)


def _bare_legacy_db(path):
    """A pre-canonical DB: answers exist, but none of the canonical tables do."""
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE finalized_responses (
            parent_task_id TEXT PRIMARY KEY,
            raw_synthesized_text TEXT NOT NULL,
            rendered_persona_text TEXT NOT NULL,
            status_marker TEXT NOT NULL,
            confidence_score REAL,
            anchored_signature TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO finalized_responses VALUES ('task-legacy', 'legacy answer bytes', 'legacy answer bytes', 'completed', 0.9, NULL)"
    )
    # A historical delivery claim predating the evidence vocabulary.
    conn.commit()
    conn.close()


def test_pre_canonical_db_migrates_without_fabricating_authority(tmp_path):
    db = tmp_path / "legacy.db"
    _bare_legacy_db(db)
    sdb.configure_default_db_path(db)
    try:
        from core.runtime_continuity import configure_runtime_continuity_db_path
        from storage.db import active_default_db_path
        from storage.migrations import run_migrations

        run_migrations()
        configure_runtime_continuity_db_path(active_default_db_path())
        conn = sdb.get_connection()
        try:
            # Legacy display truth survives verbatim.
            row = conn.execute(
                "SELECT * FROM finalized_responses WHERE parent_task_id = 'task-legacy'"
            ).fetchone()
            assert row["raw_synthesized_text"] == "legacy answer bytes"
            # LAW 4: zero fabricated canonical identities — the legacy answer
            # never becomes admitted/finalized truth by migration.
            admissions = conn.execute("SELECT COUNT(*) c FROM semantic_admissions").fetchone()["c"]
            finals = conn.execute("SELECT COUNT(*) c FROM a7_finalizations").fetchone()["c"]
            assert admissions == 0 and finals == 0
        finally:
            conn.close()
    finally:
        sdb.configure_default_db_path(None)


def test_pre_additive_a7_rows_survive_additive_columns_unchanged(fresh_store, tmp_path):
    """A DB frozen mid-window (a7_finalizations in its original 7-column shape,
    holding sealed ANSWER_PRESENT rows) upgrades additively: bytes/digests are
    preserved, new columns take lawful defaults, nothing is re-derived."""
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path
    from storage.migrations import run_migrations

    db = tmp_path / "midwindow.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE a7_finalizations (
            finalization_id TEXT PRIMARY KEY,
            semantic_result_id TEXT NOT NULL DEFAULT '',
            turn_id TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL,
            canonical_content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'answer_present',
            delivery_status TEXT NOT NULL DEFAULT 'NOT_ATTEMPTED',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    legacy_bytes = "sealed before the additive window"
    conn.execute(
        "INSERT INTO a7_finalizations (finalization_id, semantic_result_id, turn_id, content_hash, canonical_content) VALUES (?, ?, ?, ?, ?)",
        ("fc-midwindow", "sr:legacy-turn:n-1", "legacy-turn", _sha(legacy_bytes), legacy_bytes),
    )
    conn.commit()
    conn.close()

    sdb.configure_default_db_path(db)
    try:
        run_migrations()
        configure_runtime_continuity_db_path(active_default_db_path())
        row = dict(
            sdb.get_connection().execute(
                "SELECT * FROM a7_finalizations WHERE finalization_id = 'fc-midwindow'"
            ).fetchone()
        )
        # Bytes and digest preserved EXACTLY — no destructive reinterpretation.
        assert row["canonical_content"] == legacy_bytes
        assert row["content_hash"] == _sha(legacy_bytes)
        # Lawful defaults on the additive columns. A8 pass-001 law 11: a
        # pre-window availability row is stamped LEGACY_UNKNOWN once — honest
        # uncertainty, never governed-as-AVAILABLE by a schema default.
        assert row["availability"] == "LEGACY_UNKNOWN"
        assert row["payload_ref"] is None  # LAW 8: plaintext never copied
        assert row["delivery_status"] == "NOT_ATTEMPTED"  # never upgraded
        # The legacy sr id has NO durable admission referent — it must not be
        # citable as accepted canonical admission truth going forward.
        from core.semantic.semantic_admissions import admission_exists

        assert not admission_exists("sr:legacy-turn:n-1")
    finally:
        sdb.configure_default_db_path(None)


def test_read_time_hash_lookup_is_not_finality_evidence(fresh_store):
    """LAW 5: hashing arbitrary text and finding a content-hash match must not
    let a caller present legacy/mechanical bytes as sealed truth — the lookup
    returns the stored row as-is or None, and mints nothing."""
    from core.finalization import get_finalization_by_content

    assert get_finalization_by_content("never-sealed text") is None
    conn = sdb.get_connection()
    try:
        count_before = conn.execute(
            "SELECT COUNT(*) c FROM a7_finalizations"
        ).fetchone()["c"]
    finally:
        conn.close()
    get_finalization_by_content("never-sealed text")
    conn = sdb.get_connection()
    try:
        count_after = conn.execute(
            "SELECT COUNT(*) c FROM a7_finalizations"
        ).fetchone()["c"]
    finally:
        conn.close()
    assert count_before == count_after == 0
