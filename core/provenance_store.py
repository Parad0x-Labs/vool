from __future__ import annotations

import json
from datetime import datetime, timezone

from core.context_manifest import (
    ContextManifest,
    context_manifest_storage_dict,
)
from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS provenance_manifests (
                manifest_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_provenance_trace_id ON provenance_manifests(trace_id)"
        )
        conn.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS provenance_manifest_no_update
            BEFORE UPDATE ON provenance_manifests
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'context assembly manifests are immutable'
                );
            END;

            CREATE TRIGGER IF NOT EXISTS provenance_manifest_no_delete
            BEFORE DELETE ON provenance_manifests
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'context assembly manifests are immutable'
                );
            END;
            """
        )
        conn.commit()
    finally:
        conn.close()


def store_manifest(manifest: ContextManifest) -> None:
    payload = context_manifest_storage_dict(manifest)
    _init_table()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO provenance_manifests (
                manifest_id, task_id, trace_id, manifest_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                manifest.manifest_id,
                manifest.task_id,
                manifest.trace_id,
                json.dumps(payload, sort_keys=True),
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def load_manifest(manifest_id: str) -> dict | None:
    _init_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT manifest_json FROM provenance_manifests WHERE manifest_id = ? LIMIT 1",
            (manifest_id,),
        ).fetchone()
        return json.loads(row["manifest_json"]) if row else None
    finally:
        conn.close()
