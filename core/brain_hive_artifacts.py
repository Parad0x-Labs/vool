from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.liquefy_bridge import load_packed_bytes, pack_json_artifact
from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_tables() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_manifests (
                artifact_id TEXT PRIMARY KEY,
                source_kind TEXT NOT NULL,
                topic_id TEXT NOT NULL DEFAULT '',
                claim_id TEXT NOT NULL DEFAULT '',
                candidate_id TEXT NOT NULL DEFAULT '',
                session_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                tags_json TEXT NOT NULL DEFAULT '[]',
                search_text TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                file_path TEXT NOT NULL,
                storage_backend TEXT NOT NULL DEFAULT 'local_archive',
                content_sha256 TEXT NOT NULL DEFAULT '',
                raw_bytes INTEGER NOT NULL DEFAULT 0,
                compressed_bytes INTEGER NOT NULL DEFAULT 0,
                compression_ratio REAL NOT NULL DEFAULT 1.0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifact_manifests_topic_created "
            "ON artifact_manifests(topic_id, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifact_manifests_source_created "
            "ON artifact_manifests(source_kind, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifact_manifests_session_created "
            "ON artifact_manifests(session_id, created_at DESC)"
        )
        conn.commit()
    finally:
        conn.close()


def store_artifact_manifest(
    *,
    source_kind: str,
    title: str,
    summary: str,
    payload: Any,
    topic_id: str | None = None,
    claim_id: str | None = None,
    candidate_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _init_tables()
    clean_title = " ".join(str(title or "").split()).strip()[:200]
    clean_summary = " ".join(str(summary or "").split()).strip()[:2000]
    clean_tags = [str(item).strip()[:64] for item in list(tags or []) if str(item).strip()][:24]
    clean_source_kind = str(source_kind or "artifact").strip()[:64] or "artifact"
    if not clean_title or not clean_summary:
        raise ValueError("title and summary are required")
    artifact_id = f"artifact-{uuid.uuid4().hex}"
    packed = pack_json_artifact(
        artifact_id=artifact_id,
        payload=payload,
        category="artifacts",
        file_stem=f"{clean_source_kind}-{artifact_id[-12:]}",
    )
    timestamp = _utcnow()
    payload_preview = _searchable_text(payload)
    manifest = {
        "artifact_id": artifact_id,
        "source_kind": clean_source_kind,
        "topic_id": str(topic_id or "").strip(),
        "claim_id": str(claim_id or "").strip(),
        "candidate_id": str(candidate_id or "").strip(),
        "session_id": str(session_id or "").strip(),
        "title": clean_title,
        "summary": clean_summary,
        "tags": clean_tags,
        "search_text": " ".join(part for part in [clean_title, clean_summary, " ".join(clean_tags), payload_preview] if part).strip()[:50000],
        "metadata": dict(metadata or {}),
        "file_path": str(packed["path"]),
        "storage_backend": str(packed["storage_backend"]),
        "content_sha256": str(packed["content_sha256"]),
        "raw_bytes": int(packed["raw_bytes"]),
        "compressed_bytes": int(packed["compressed_bytes"]),
        "compression_ratio": float(packed["compression_ratio"]),
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO artifact_manifests (
                artifact_id, source_kind, topic_id, claim_id, candidate_id, session_id,
                title, summary, tags_json, search_text, metadata_json, file_path,
                storage_backend, content_sha256, raw_bytes, compressed_bytes, compression_ratio,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                manifest["artifact_id"],
                manifest["source_kind"],
                manifest["topic_id"],
                manifest["claim_id"],
                manifest["candidate_id"],
                manifest["session_id"],
                manifest["title"],
                manifest["summary"],
                json.dumps(manifest["tags"], sort_keys=True),
                manifest["search_text"],
                json.dumps(manifest["metadata"], sort_keys=True),
                manifest["file_path"],
                manifest["storage_backend"],
                manifest["content_sha256"],
                manifest["raw_bytes"],
                manifest["compressed_bytes"],
                manifest["compression_ratio"],
                manifest["created_at"],
                manifest["updated_at"],
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return manifest


def get_artifact_manifest(artifact_id: str) -> dict[str, Any] | None:
    _init_tables()
    clean_artifact_id = str(artifact_id or "").strip()
    if not clean_artifact_id:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM artifact_manifests
            WHERE artifact_id = ?
            LIMIT 1
            """,
            (clean_artifact_id,),
        ).fetchone()
        return _row_to_manifest(dict(row)) if row else None
    finally:
        conn.close()


def list_artifact_manifests(
    *,
    topic_id: str | None = None,
    source_kind: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    _init_tables()
    clauses: list[str] = []
    params: list[Any] = []
    if str(topic_id or "").strip():
        clauses.append("topic_id = ?")
        params.append(str(topic_id or "").strip())
    if str(source_kind or "").strip():
        clauses.append("source_kind = ?")
        params.append(str(source_kind or "").strip())
    query = "SELECT * FROM artifact_manifests"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    conn = get_connection()
    try:
        rows = conn.execute(query, tuple(params)).fetchall()
        return [_row_to_manifest(dict(row)) for row in rows]
    finally:
        conn.close()


def count_artifact_manifests(*, topic_id: str | None = None, source_kind: str | None = None) -> int:
    _init_tables()
    clauses: list[str] = []
    params: list[Any] = []
    if str(topic_id or "").strip():
        clauses.append("topic_id = ?")
        params.append(str(topic_id or "").strip())
    if str(source_kind or "").strip():
        clauses.append("source_kind = ?")
        params.append(str(source_kind or "").strip())
    query = "SELECT COUNT(*) AS c FROM artifact_manifests"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    conn = get_connection()
    try:
        row = conn.execute(query, tuple(params)).fetchone()
        return int(row["c"] or 0) if row else 0
    finally:
        conn.close()


def search_artifact_manifests(
    query_text: str,
    *,
    topic_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    _init_tables()
    clean_query = " ".join(str(query_text or "").split()).strip().lower()
    rows = list_artifact_manifests(topic_id=topic_id, limit=max(limit * 5, 40))
    if not clean_query:
        return rows[:limit]
    tokens = [token for token in _tokenize(clean_query) if token]
    ranked: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        haystack = str(row.get("search_text") or "").lower()
        score = sum(1 for token in tokens if token in haystack)
        if score <= 0:
            continue
        ranked.append((score, row))
    ranked.sort(key=lambda item: (item[0], str(item[1].get("created_at") or "")), reverse=True)
    return [item[1] for item in ranked[: max(1, min(int(limit), 100))]]


def load_artifact_manifest_payload(
    *,
    artifact_id: str | None = None,
    manifest: dict[str, Any] | None = None,
) -> Any | None:
    manifest_row = dict(manifest or {})
    if not manifest_row and str(artifact_id or "").strip():
        loaded = get_artifact_manifest(str(artifact_id or "").strip())
        manifest_row = dict(loaded or {})
    if not manifest_row:
        return None
    artifact_path = Path(str(manifest_row.get("file_path") or "")).expanduser()
    if not artifact_path.is_file():
        return None
    try:
        raw = load_packed_bytes(
            payload=artifact_path.read_bytes(),
            storage_backend=str(manifest_row.get("storage_backend") or "local_archive"),
        )
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def _row_to_manifest(row: dict[str, Any]) -> dict[str, Any]:
    row["tags"] = json.loads(row.pop("tags_json") or "[]")
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _searchable_text(payload: Any) -> str:
    try:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    except Exception:
        raw = str(payload)
    return " ".join(raw.split())


def _tokenize(text: str) -> list[str]:
    return [token for token in "".join(ch if ch.isalnum() else " " for ch in text.lower()).split() if len(token) >= 2]
