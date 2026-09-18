from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any

from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS candidate_knowledge_lane (
                candidate_id TEXT PRIMARY KEY,
                task_hash TEXT NOT NULL,
                task_id TEXT,
                trace_id TEXT,
                task_class TEXT NOT NULL,
                task_kind TEXT NOT NULL,
                output_mode TEXT NOT NULL,
                provider_name TEXT NOT NULL,
                model_name TEXT NOT NULL,
                raw_output TEXT NOT NULL,
                normalized_output TEXT NOT NULL,
                structured_output_json TEXT,
                confidence REAL NOT NULL DEFAULT 0.0,
                trust_score REAL NOT NULL DEFAULT 0.0,
                validation_state TEXT NOT NULL DEFAULT 'candidate',
                promotion_state TEXT NOT NULL DEFAULT 'candidate',
                review_state TEXT NOT NULL DEFAULT 'unreviewed',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                provenance_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                expires_at TEXT,
                invalidated_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_candidate_lane_task_hash ON candidate_knowledge_lane(task_hash, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_candidate_lane_created ON candidate_knowledge_lane(created_at DESC)"
        )
        conn.commit()
    finally:
        conn.close()


def build_task_hash(
    *,
    normalized_input: str,
    task_class: str,
    output_mode: str,
    scope_id: str = "",
) -> str:
    clean_scope = str(scope_id or "").strip()
    payload = f"{task_class}\n{output_mode}\n{normalized_input.strip().lower()}"
    if clean_scope:
        payload = f"{clean_scope}\n{payload}"
    return sha256(payload.encode()).hexdigest()


def record_candidate_output(
    *,
    task_hash: str,
    task_id: str | None,
    trace_id: str | None,
    task_class: str,
    task_kind: str,
    output_mode: str,
    provider_name: str,
    model_name: str,
    raw_output: str,
    normalized_output: str,
    structured_output: Any,
    confidence: float,
    trust_score: float,
    validation_state: str,
    metadata: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    ttl_seconds: int = 3600,
) -> str:
    _init_table()
    candidate_id = str(uuid.uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=max(60, int(ttl_seconds)))).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO candidate_knowledge_lane (
                candidate_id, task_hash, task_id, trace_id, task_class, task_kind, output_mode,
                provider_name, model_name, raw_output, normalized_output, structured_output_json,
                confidence, trust_score, validation_state, promotion_state, review_state,
                metadata_json, provenance_json, created_at, expires_at, invalidated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', 'unreviewed', ?, ?, ?, ?, NULL)
            """,
            (
                candidate_id,
                task_hash,
                task_id,
                trace_id,
                task_class,
                task_kind,
                output_mode,
                provider_name,
                model_name,
                raw_output,
                normalized_output,
                None if structured_output is None else json.dumps(structured_output, sort_keys=True),
                float(confidence),
                float(trust_score),
                validation_state,
                json.dumps(metadata or {}, sort_keys=True),
                json.dumps(provenance or {}, sort_keys=True),
                _utcnow(),
                expires_at,
            ),
        )
        conn.commit()
        return candidate_id
    finally:
        conn.close()


def get_exact_candidate(task_hash: str, *, output_mode: str | None = None) -> dict[str, Any] | None:
    _init_table()
    conn = get_connection()
    try:
        query = """
            SELECT *
            FROM candidate_knowledge_lane
            WHERE task_hash = ?
              AND invalidated_at IS NULL
              -- Never replay an answer whose own evidence refuted it. Without this a contradicted
              -- audit is served again, unannotated, on the next identical request.
              AND validation_state != 'claims_contradicted'
              -- A9: expiry is part of the store's contract, not just the caller's duty — any
              -- future consumer inherits TTL enforcement instead of silent stale replay.
              AND (expires_at IS NULL OR expires_at > ?)
        """
        params: list[Any] = [task_hash, _utcnow()]
        if output_mode:
            query += " AND output_mode = ?"
            params.append(output_mode)
        query += " ORDER BY created_at DESC LIMIT 1"
        row = conn.execute(query, tuple(params)).fetchone()
        return _row_to_candidate(dict(row)) if row else None
    finally:
        conn.close()


def recent_candidates(limit: int = 20) -> list[dict[str, Any]]:
    _init_table()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM candidate_knowledge_lane
            WHERE invalidated_at IS NULL
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_row_to_candidate(dict(row)) for row in rows]
    finally:
        conn.close()


def get_candidate_by_id(candidate_id: str) -> dict[str, Any] | None:
    _init_table()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM candidate_knowledge_lane
            WHERE candidate_id = ?
              AND invalidated_at IS NULL
            LIMIT 1
            """,
            (str(candidate_id or "").strip(),),
        ).fetchone()
        return _row_to_candidate(dict(row)) if row else None
    finally:
        conn.close()


def mark_candidate_claims_contradicted(candidate_id: str, *, kinds: tuple[str, ...]) -> bool:
    """Record that this stored answer contains claims its own evidence refutes.

    `validation_state` was written `"valid"` from a SHAPE check — did the output parse, did it match
    the contract — so an audit whose every load-bearing claim was false was stored `valid` at full
    trust and could be replayed from cache on the next identical request. A state that says nothing
    about whether the content is true is a state that makes a wrong answer durable.

    Deliberately not `invalidate_candidate`: the answer is not garbage and the operator saw it, with
    the contradictions annotated. It must simply never be served again as though it had been
    verified, so it is excluded from the exact-match cache and its verdict is on the row.
    """

    clean_id = str(candidate_id or "").strip()
    if not clean_id:
        return False
    _init_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT metadata_json FROM candidate_knowledge_lane WHERE candidate_id = ? LIMIT 1",
            (clean_id,),
        ).fetchone()
        if row is None:
            return False
        try:
            metadata = dict(json.loads(str(row["metadata_json"] or "{}")))
        except (TypeError, ValueError):
            metadata = {}
        metadata["claims_contradicted"] = sorted(set(kinds))
        metadata["claims_contradicted_at"] = _utcnow()
        conn.execute(
            """
            UPDATE candidate_knowledge_lane
            SET validation_state = 'claims_contradicted',
                metadata_json = ?
            WHERE candidate_id = ?
            """,
            (json.dumps(metadata, ensure_ascii=False), clean_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def invalidate_candidate(candidate_id: str, *, reason: str) -> None:
    _init_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT metadata_json FROM candidate_knowledge_lane WHERE candidate_id = ? LIMIT 1",
            (candidate_id,),
        ).fetchone()
        metadata = json.loads((row["metadata_json"] if row else "{}") or "{}")
        metadata["invalidated_reason"] = reason
        conn.execute(
            """
            UPDATE candidate_knowledge_lane
            SET invalidated_at = ?, metadata_json = ?
            WHERE candidate_id = ?
            """,
            (_utcnow(), json.dumps(metadata, sort_keys=True), candidate_id),
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_candidate(row: dict[str, Any]) -> dict[str, Any]:
    row["structured_output"] = json.loads(row.pop("structured_output_json") or "null")
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    row["provenance"] = json.loads(row.pop("provenance_json") or "{}")
    return row
