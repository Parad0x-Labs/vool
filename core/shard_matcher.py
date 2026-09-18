from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.context_scope import ContextAccessPolicy
from core.shard_synthesizer import build_generalized_query
from storage.db import get_connection
from storage.shard_fetch_receipts import latest_receipts_for_shards
from storage.shard_reuse_outcomes import summarize_reuse_outcomes_for_shards


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _tokenize(text: str) -> set[str]:
    return {t for t in "".join(ch if ch.isalnum() else " " for ch in (text or "").lower()).split() if len(t) > 2}


def _semantic_match(task_summary: str, shard_summary: str, exact_sig_match: bool) -> float:
    if exact_sig_match:
        return 1.0

    a = _tokenize(task_summary)
    b = _tokenize(shard_summary)

    if not a or not b:
        return 0.0

    overlap = len(a & b)
    union = len(a | b)
    return overlap / max(1, union)


def _environment_match(task: Any, env_tags: dict) -> float:
    score = 0.0
    checks = 0

    for key, task_value in [
        ("os", _get(task, "environment_os", "")),
        ("shell", _get(task, "environment_shell", "")),
        ("runtime", _get(task, "environment_runtime", "")),
        ("version_family", _get(task, "environment_version_hint", "")),
    ]:
        checks += 1
        tag_value = env_tags.get(key)

        if isinstance(tag_value, list):
            if task_value in tag_value:
                score += 1.0
        elif isinstance(tag_value, str):
            if tag_value == task_value or tag_value == "unknown":
                score += 1.0
        elif tag_value is None:
            score += 0.5

    return score / max(1, checks)


def _is_expired(expires_ts: str | None) -> bool:
    if not expires_ts:
        return False
    try:
        dt = datetime.fromisoformat(expires_ts)
        return dt < datetime.now(timezone.utc)
    except Exception:
        return False


def find_local_candidates(
    task: Any,
    classification: dict[str, Any],
    *,
    access_policy: ContextAccessPolicy,
    include_current_chat: bool = True,
) -> list[dict]:
    if not isinstance(access_policy, ContextAccessPolicy):
        raise TypeError("access_policy must be a ContextAccessPolicy")

    query = build_generalized_query(task, classification)
    expected_sig = query["problem_signature"]
    problem_class = query["problem_class"]
    task_class = str(classification.get("task_class") or "").strip()
    current_chat_id = str(access_policy.chat_id or "").strip()
    allow_current_chat = bool(include_current_chat and current_chat_id)
    allow_shared = bool(access_policy.allow_shared_context)
    if not allow_current_chat and not allow_shared:
        return []

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM learning_shards
            WHERE problem_class = ?
              AND quarantine_status = 'active'
              AND (
                    (
                        ? = 1
                        AND share_scope = 'local_only'
                        AND origin_session_id = ?
                        AND origin_session_id != ''
                    )
                    OR
                    (
                        ? = 1
                        AND share_scope IN ('hive_mind', 'public_knowledge')
                        AND (
                            origin_session_id != ''
                            OR (
                                source_type = 'peer_received'
                                AND COALESCE(source_node_id, '') != ''
                            )
                        )
                    )
              )
            ORDER BY updated_at DESC
            LIMIT 50
            """,
            (
                problem_class,
                int(allow_current_chat),
                current_chat_id,
                int(allow_shared),
            ),
        ).fetchall()
    finally:
        conn.close()

    peer_shard_ids = [
        str(row["shard_id"])
        for row in rows
        if str(row["source_type"] or "").strip() == "peer_received"
    ]
    receipt_map = (
        latest_receipts_for_shards(peer_shard_ids)
        if peer_shard_ids
        else {}
    )
    reuse_outcome_map = (
        summarize_reuse_outcomes_for_shards(
            peer_shard_ids,
            task_class=task_class,
        )
        if peer_shard_ids
        else {}
    )
    candidates: list[dict] = []

    for row in rows:
        env_tags = json.loads(row["environment_tags_json"])
        risk_flags = json.loads(row["risk_flags_json"])
        receipt = receipt_map.get(str(row["shard_id"]))

        if _is_expired(row["expires_ts"]):
            continue

        exact_sig = row["problem_signature"] == expected_sig
        semantic = _semantic_match(_get(task, "task_summary", ""), row["summary"], exact_sig)
        env_match = _environment_match(task, env_tags)

        candidates.append(
            {
                "shard_id": row["shard_id"],
                "schema_version": row["schema_version"],
                "problem_class": row["problem_class"],
                "problem_signature": row["problem_signature"],
                "summary": row["summary"],
                "resolution_pattern": json.loads(row["resolution_pattern_json"]),
                "environment_tags": env_tags,
                "source_type": row["source_type"],
                "source_node_id": row["source_node_id"],
                "origin_session_id": row["origin_session_id"],
                "share_scope": row["share_scope"],
                "quality_score": float(row["quality_score"]),
                "trust_score": float(row["trust_score"]),
                "local_validation_count": int(row["local_validation_count"]),
                "local_failure_count": int(row["local_failure_count"]),
                "quarantine_status": row["quarantine_status"],
                "risk_flags": risk_flags,
                "freshness_ts": row["freshness_ts"],
                "expires_ts": row["expires_ts"],
                "signature": row["signature"],
                "retrieval_receipt": dict(receipt or {}),
                "reuse_outcomes": dict(reuse_outcome_map.get(str(row["shard_id"])) or {}),
                "semantic_match": semantic,
                "environment_match": env_match,
            }
        )

    return candidates
