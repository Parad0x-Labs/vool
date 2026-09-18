from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.cloud_provider_contract import CloudModelMetadata
from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_table() -> None:
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cloud_model_catalog (
                provider_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                discovered_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (provider_id, model_id)
            );
            CREATE INDEX IF NOT EXISTS idx_cloud_model_catalog_expiry
              ON cloud_model_catalog(provider_id, expires_at);
            """
        )
        conn.commit()
    finally:
        conn.close()


def replace_provider_catalog(provider_id: str, models: tuple[CloudModelMetadata, ...]) -> None:
    """Atomically replace one provider snapshot, removing models that disappeared."""
    provider = str(provider_id or "").strip()
    if not provider:
        raise ValueError("provider_id is required")
    if any(model.provider_id != provider for model in models):
        raise ValueError("catalog contains a model for a different provider")
    _init_table()
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM cloud_model_catalog WHERE provider_id = ?", (provider,))
        now = _utcnow()
        conn.executemany(
            """INSERT INTO cloud_model_catalog
            (provider_id, model_id, payload_json, discovered_at, expires_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (
                    model.provider_id,
                    model.model_id,
                    json.dumps(model.to_dict(), sort_keys=True, separators=(",", ":")),
                    model.discovered_at,
                    model.expires_at,
                    now,
                )
                for model in models
            ],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_provider_catalog(
    provider_id: str,
    *,
    include_stale: bool = False,
    now: str | None = None,
) -> tuple[CloudModelMetadata, ...]:
    _init_table()
    moment = str(now or _utcnow())
    conn = get_connection()
    try:
        where = "provider_id = ?" if include_stale else "provider_id = ? AND expires_at > ?"
        params: tuple[Any, ...] = (provider_id,) if include_stale else (provider_id, moment)
        rows = conn.execute(
            f"SELECT payload_json FROM cloud_model_catalog WHERE {where} ORDER BY model_id ASC",
            params,
        ).fetchall()
        return tuple(CloudModelMetadata.from_dict(json.loads(row["payload_json"])) for row in rows)
    finally:
        conn.close()


def get_catalog_model(provider_id: str, model_id: str) -> CloudModelMetadata | None:
    _init_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT payload_json FROM cloud_model_catalog WHERE provider_id = ? AND model_id = ?",
            (provider_id, model_id),
        ).fetchone()
        return CloudModelMetadata.from_dict(json.loads(row["payload_json"])) if row else None
    finally:
        conn.close()


def clear_provider_catalog(provider_id: str) -> None:
    _init_table()
    conn = get_connection()
    try:
        conn.execute("DELETE FROM cloud_model_catalog WHERE provider_id = ?", (provider_id,))
        conn.commit()
    finally:
        conn.close()


__all__ = [
    "clear_provider_catalog",
    "get_catalog_model",
    "list_provider_catalog",
    "replace_provider_catalog",
]
