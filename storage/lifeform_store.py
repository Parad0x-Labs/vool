"""
storage/lifeform_store.py
=========================
Persistence for VOOLemon lifeforms (greenfield, additive-only).

Follows the storage canon: dedicated isolated table, lazy ``_ensure_table`` via
the pooled connection, no shared-table writes, no migrations-file coupling.
Holds only the canonical lifeform document, the append-only event log (JSON)
and the last signed snapshot. Authorizes nothing; the lifeform is presentation-
adjacent state and never touches execution truth.

No free-text beyond the bounded display_name travels through here — the schema
layer rejects anything else before a byte reaches this store.
"""
from __future__ import annotations

import json
from typing import Any

from storage.db import get_connection

_TABLE = "lifeform_v1"


def _ensure_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_TABLE} (
                lifeform_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                created_day TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                canonical_json TEXT NOT NULL,
                event_log TEXT NOT NULL DEFAULT '[]',
                snapshot TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def save_lifeform(doc_json: str, event_log: list[dict[str, Any]],
                  snapshot: dict[str, Any] | None, updated_at: str) -> None:
    doc = json.loads(doc_json)
    _ensure_table()
    conn = get_connection()
    try:
        conn.execute(
            f"""
            INSERT INTO {_TABLE} (lifeform_id, owner_id, created_day, display_name,
                                  canonical_json, event_log, snapshot, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(lifeform_id) DO UPDATE SET
                canonical_json=excluded.canonical_json,
                event_log=excluded.event_log,
                snapshot=excluded.snapshot,
                updated_at=excluded.updated_at
            """,
            (str(doc["lifeform_id"]), str(doc["owner_id"]), str(doc.get("created_day") or ""),
             str(doc.get("display_name") or ""), doc_json,
             json.dumps(event_log, separators=(",", ":")),
             json.dumps(snapshot) if snapshot else None, str(updated_at)),
        )
        conn.commit()
    finally:
        conn.close()


def load_lifeform(lifeform_id: str) -> dict[str, Any] | None:
    _ensure_table()
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT canonical_json, event_log, snapshot FROM {_TABLE} "
            "WHERE lifeform_id = ?",
            (str(lifeform_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "doc": json.loads(row["canonical_json"]),
        "event_log": json.loads(row["event_log"] or "[]"),
        "snapshot": json.loads(row["snapshot"]) if row["snapshot"] else None,
    }


def load_for_owner(owner_id: str) -> dict[str, Any] | None:
    """A local operator owns at most one lifeform in this ruleset."""
    _ensure_table()
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT lifeform_id FROM {_TABLE} WHERE owner_id = ? LIMIT 1",
            (str(owner_id),),
        ).fetchone()
    finally:
        conn.close()
    return load_lifeform(str(row["lifeform_id"])) if row else None
