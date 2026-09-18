"""SQLite persistence for MediaEditProject documents.

Follows the storage/*_store.py convention: thin wrapper over storage.db with
its own table. Projects are stored as JSON docs keyed by project_id so a
closed editor can be reopened with full graph state (G4).
"""

from __future__ import annotations

import json
from typing import Any

from storage.db import execute_query

_SCHEMA = """
CREATE TABLE IF NOT EXISTS media_edit_projects (
    project_id TEXT PRIMARY KEY,
    source_sha256 TEXT NOT NULL DEFAULT '',
    edit_revision INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0,
    doc TEXT NOT NULL
)
"""


def _ensure_schema() -> None:
    execute_query(_SCHEMA)


def save_project(doc: dict[str, Any], *, now: float) -> None:
    _ensure_schema()
    execute_query(
        "INSERT INTO media_edit_projects (project_id, source_sha256, edit_revision,"
        " updated_at, doc) VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(project_id) DO UPDATE SET source_sha256=excluded.source_sha256,"
        " edit_revision=excluded.edit_revision, updated_at=excluded.updated_at,"
        " doc=excluded.doc",
        (
            str(doc["project_id"]),
            str(doc.get("source_asset", {}).get("sha256", "")),
            int(doc.get("edit_revision", 0)),
            float(now),
            json.dumps(doc),
        ),
    )


def load_project(project_id: str) -> dict[str, Any] | None:
    _ensure_schema()
    rows = execute_query(
        "SELECT doc FROM media_edit_projects WHERE project_id = ?", (str(project_id),)
    )
    if not rows:
        return None
    try:
        return json.loads(rows[0]["doc"])
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def list_projects(limit: int = 50) -> list[dict[str, Any]]:
    _ensure_schema()
    rows = execute_query(
        "SELECT project_id, source_sha256, edit_revision, updated_at"
        " FROM media_edit_projects ORDER BY updated_at DESC LIMIT ?",
        (int(limit),),
    )
    return [
        {"project_id": r["project_id"], "source_sha256": r["source_sha256"],
         "edit_revision": int(r["edit_revision"]), "updated_at": float(r["updated_at"])}
        for r in rows
    ]


def delete_project(project_id: str) -> bool:
    """Operator-explicit deletion only; closing an editor never calls this."""
    _ensure_schema()
    before = len(list_projects(limit=10_000))
    execute_query("DELETE FROM media_edit_projects WHERE project_id = ?", (str(project_id),))
    return len(list_projects(limit=10_000)) < before
