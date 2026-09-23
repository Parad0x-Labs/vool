from __future__ import annotations

import hashlib
import sqlite3
import threading
from typing import Any

from storage.db import get_connection

# Serializes the defensive column-ensure across threads so two concurrent
# background anchor writers can never both issue the ADD COLUMN at once.
_ENSURE_COLUMN_LOCK = threading.Lock()


def _table_columns(conn: Any, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _ensure_columns(conn: Any) -> None:
    """Additive, idempotent + thread-safe ensure for late columns.

    The current schema (storage/migrations.py) creates the signature, content hash,
    and lineage columns in its DDL and additive migrations, so this ensure finds them present and
    does nothing. It survives as a defensive fallback for a connection that
    somehow predates those migrations.

    Two background anchor threads can call this concurrently. Without coordination
    both could see a column missing and both run ``ALTER TABLE ADD COLUMN`` —
    the second raising ``duplicate column name``. We take a process-wide lock and
    treat that specific OperationalError as success so the add is effectively
    idempotent and never propagates as an error that would lose the signature.
    """
    with _ENSURE_COLUMN_LOCK:
        columns = _table_columns(conn, "finalized_responses")
        for name in ("anchored_signature", "content_hash", "finalization_id", "request_id"):
            if name in columns:
                continue
            type_def = "TEXT" if name == "anchored_signature" else "TEXT NOT NULL DEFAULT ''"
            try:
                conn.execute(f"ALTER TABLE finalized_responses ADD COLUMN {name} {type_def}")
            except sqlite3.OperationalError as exc:
                # Another process can add this column despite our thread lock. Keep
                # ensuring the remaining columns; an outer catch would abandon them.
                if "duplicate column name" not in str(exc).lower():
                    raise


def content_identity_hash(raw: str, rendered: str, status_marker: str) -> str:
    """Content identity of a finalized swarm answer: sha256 over the exact bytes.

    Covers raw synthesized text, rendered persona text, and status marker so any
    change to the durable answer representation changes the identity.
    """
    payload = "\x1f".join((str(raw or ""), str(rendered or ""), str(status_marker or "")))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class FinalizationRejected(Exception):
    """Raised when different finalized content is offered for an existing truth.

    The first content wins durably; a divergent retry must surface this instead
    of silently replacing user-visible truth (A7 Law 7/9).
    """

    def __init__(self, parent_task_id: str, stored_content_hash: str, attempted_content_hash: str) -> None:
        super().__init__(
            "finalized response already exists with different content "
            f"(parent_task_id={parent_task_id!r} stored={stored_content_hash} attempted={attempted_content_hash})"
        )
        self.parent_task_id = parent_task_id
        self.stored_content_hash = stored_content_hash
        self.attempted_content_hash = attempted_content_hash


def store_final_response(
    parent_task_id: str,
    raw: str,
    rendered: str,
    status: str,
    confidence: float,
    *,
    finalization_id: str = "",
    request_id: str = "",
) -> dict[str, Any]:
    """Durably establish finalized swarm answer truth — immutable-first-insert.

    Engine-enforced immutability (A7 W1): the unique primary key plus
    ``ON CONFLICT DO NOTHING`` decides ownership IN SQLite, not under a process
    lock. Classification:

    - rowcount == 1 → ``ACCEPTED_FIRST`` (this writer owns the truth);
    - rowcount == 0 + stored hash equal → ``ACCEPTED_IDENTICAL`` (idempotent);
    - rowcount == 0 + stored hash different → raises :class:`FinalizationRejected`
      (honest rejection; no write occurred; v1 stays canonical).

    A8 pass-001 payload lineage: ``finalization_id``/``request_id`` stamp the
    legacy lane's rows with their governing identity at write time ('' when
    none is bound — never fabricated).
    """
    conn = get_connection()
    try:
        _ensure_columns(conn)
        content_hash = content_identity_hash(raw, rendered, status)
        cursor = conn.execute(
            """
            INSERT INTO finalized_responses (
                parent_task_id, raw_synthesized_text, rendered_persona_text,
                status_marker, confidence_score, content_hash, finalization_id, request_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(parent_task_id) DO NOTHING
            """,
            (
                parent_task_id,
                raw,
                rendered,
                status,
                confidence,
                content_hash,
                str(finalization_id or "").strip(),
                str(request_id or "").strip(),
            ),
        )
        if cursor.rowcount == 1:
            conn.commit()
            return {"outcome": "ACCEPTED_FIRST", "content_hash": content_hash}
        row = conn.execute(
            "SELECT content_hash FROM finalized_responses WHERE parent_task_id = ?",
            (parent_task_id,),
        ).fetchone()
        conn.commit()
        stored_hash = str(row["content_hash"] if row else "") or ""
        if stored_hash == content_hash:
            return {"outcome": "ACCEPTED_IDENTICAL", "content_hash": content_hash}
        raise FinalizationRejected(parent_task_id, stored_hash, content_hash)
    finally:
        conn.close()


def set_anchored_signature(
    parent_task_id: str,
    signature: str,
    *,
    expected_content_hash: str | None = None,
) -> bool:
    """Persist the on-chain anchor tx signature on an already-finalized row.

    Additive: only updates the ``anchored_signature`` column, leaving the
    synthesized/rendered text, status, confidence, and content hash untouched.

    A7 W1 binding rule: when ``expected_content_hash`` is provided it must equal
    the stored content identity — a signature computed over one version of the
    answer must never land on different stored bytes. Returns True only when a
    matching, hash-verified row was updated; False otherwise so the caller never
    has to assume success.
    """
    if not parent_task_id or not signature:
        return False
    conn = get_connection()
    try:
        _ensure_columns(conn)
        if expected_content_hash is not None:
            row = conn.execute(
                "SELECT content_hash FROM finalized_responses WHERE parent_task_id = ?",
                (parent_task_id,),
            ).fetchone()
            if not row or str(row["content_hash"] or "") != expected_content_hash:
                return False
            cursor = conn.execute(
                "UPDATE finalized_responses SET anchored_signature = ? "
                "WHERE parent_task_id = ? AND content_hash = ?",
                (signature, parent_task_id, expected_content_hash),
            )
        else:
            cursor = conn.execute(
                "UPDATE finalized_responses SET anchored_signature = ? WHERE parent_task_id = ?",
                (signature, parent_task_id),
            )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_final_response(parent_task_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        _ensure_columns(conn)
        row = conn.execute(
            "SELECT * FROM finalized_responses WHERE parent_task_id = ?",
            (parent_task_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
