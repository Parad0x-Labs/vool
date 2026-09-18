from __future__ import annotations

import sqlite3
import threading
import uuid

from core.final_response_store import (
    get_final_response,
    set_anchored_signature,
    store_final_response,
)
from storage.db import get_connection
from storage.migrations import run_migrations


def _new_task_id() -> str:
    return f"task-{uuid.uuid4().hex}"


def _column_present(table: str, column: str) -> bool:
    conn = get_connection()
    try:
        cols = {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()
    return column in cols


def test_fresh_migration_creates_anchored_signature_column() -> None:
    """The migrated schema must own the column so NO runtime ALTER is needed."""
    run_migrations()
    assert _column_present("finalized_responses", "anchored_signature")


def test_concurrent_set_anchored_signature_both_persist() -> None:
    """Two concurrent writers on a fresh-migrated DB both persist their signature.

    Regression for the unguarded runtime ALTER race: previously the column was
    absent from the DDL, so two background anchor threads both saw it missing,
    both issued ``ALTER TABLE ... ADD COLUMN`` and the second raised
    ``duplicate column name`` — swallowed upstream, so the real tx signature was
    never written. With the column now in the migration and a thread-safe ensure,
    both updates succeed and neither row is left NULL.
    """
    run_migrations()

    task_a = _new_task_id()
    task_b = _new_task_id()
    for task_id in (task_a, task_b):
        store_final_response(
            parent_task_id=task_id,
            raw="raw",
            rendered="rendered",
            status="finalized",
            confidence=0.9,
        )

    sig_a = "A" + "z" * 80
    sig_b = "B" + "z" * 80

    errors: list[BaseException] = []
    results: dict[str, bool] = {}
    barrier = threading.Barrier(2)

    def _write(task_id: str, signature: str) -> None:
        try:
            barrier.wait(timeout=5)
            results[task_id] = set_anchored_signature(task_id, signature)
        except BaseException as exc:  # surface any race failure
            errors.append(exc)

    threads = [
        threading.Thread(target=_write, args=(task_a, sig_a)),
        threading.Thread(target=_write, args=(task_b, sig_b)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, f"concurrent writers raised: {errors!r}"
    assert results.get(task_a) is True
    assert results.get(task_b) is True

    row_a = get_final_response(task_a)
    row_b = get_final_response(task_b)
    assert row_a is not None and row_b is not None
    assert row_a["anchored_signature"] == sig_a
    assert row_b["anchored_signature"] == sig_b


def test_concurrent_ensure_on_legacy_db_does_not_lose_signature() -> None:
    """Reproduces the original race directly: a legacy DB missing the column.

    Even when the column is absent (a connection that predates the migration),
    two concurrent writers must not surface a ``duplicate column name`` error and
    must both persist their signature. Pre-fix this raised in one thread and the
    signature was lost; post-fix the thread-safe, duplicate-tolerant ensure makes
    both writes land.
    """
    run_migrations()

    # Force the pre-migration shape on the live DB so set_anchored_signature must
    # run its defensive column-ensure under contention.
    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS finalized_responses")
        conn.execute(
            """
            CREATE TABLE finalized_responses (
                parent_task_id TEXT PRIMARY KEY,
                raw_synthesized_text TEXT,
                rendered_persona_text TEXT,
                status_marker TEXT,
                confidence_score REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    assert not _column_present("finalized_responses", "anchored_signature")

    task_a = _new_task_id()
    task_b = _new_task_id()
    for task_id in (task_a, task_b):
        store_final_response(
            parent_task_id=task_id,
            raw="raw",
            rendered="rendered",
            status="finalized",
            confidence=0.9,
        )

    sig_a = "A" + "y" * 80
    sig_b = "B" + "y" * 80

    errors: list[BaseException] = []
    results: dict[str, bool] = {}
    barrier = threading.Barrier(2)

    def _write(task_id: str, signature: str) -> None:
        try:
            barrier.wait(timeout=5)
            results[task_id] = set_anchored_signature(task_id, signature)
        except BaseException as exc:  # surface any race failure
            errors.append(exc)

    threads = [
        threading.Thread(target=_write, args=(task_a, sig_a)),
        threading.Thread(target=_write, args=(task_b, sig_b)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    duplicate_errors = [e for e in errors if isinstance(e, sqlite3.OperationalError)]
    assert not duplicate_errors, f"duplicate-column race resurfaced: {duplicate_errors!r}"
    assert not errors, f"concurrent writers raised: {errors!r}"
    assert results.get(task_a) is True
    assert results.get(task_b) is True

    row_a = get_final_response(task_a)
    row_b = get_final_response(task_b)
    assert row_a is not None and row_b is not None
    assert row_a["anchored_signature"] == sig_a
    assert row_b["anchored_signature"] == sig_b
