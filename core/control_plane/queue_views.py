from __future__ import annotations

from typing import Any


def load_open_task_offers(conn: Any, *, limit: int, table_exists_fn: Any) -> list[dict[str, Any]]:
    if not table_exists_fn(conn, "task_offers"):
        return []
    rows = conn.execute(
        """
        SELECT task_id, parent_peer_id, task_type, subtask_type, summary, priority, deadline_ts, status, created_at, updated_at
        FROM task_offers
        WHERE status IN ('open', 'claimed', 'assigned')
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [dict(row) for row in rows]


def load_reviewer_lane(
    conn: Any,
    *,
    limit: int,
    table_exists_fn: Any,
    utcnow_fn: Any,
) -> dict[str, Any]:
    if not table_exists_fn(conn, "task_results"):
        return {"generated_at": utcnow_fn(), "lane": "reviewer", "items": []}
    rows = conn.execute(
        """
        SELECT result_id, task_id, helper_peer_id, result_type, summary, confidence, status, created_at, updated_at
        FROM task_results
        WHERE status = 'submitted'
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return {
        "generated_at": utcnow_fn(),
        "lane": "reviewer",
        "review_required": True,
        "items": [dict(row) for row in rows],
    }


def load_commons_promotion_queue(
    conn: Any,
    *,
    limit: int,
    table_exists_fn: Any,
    json_loads_fn: Any,
    utcnow_fn: Any,
) -> dict[str, Any]:
    if not table_exists_fn(conn, "hive_commons_promotion_candidates"):
        return {"generated_at": utcnow_fn(), "lane": "commons_promotion", "items": []}
    rows = conn.execute(
        """
        SELECT candidate_id, post_id, topic_id, requested_by_agent_id, score, status, review_state,
               archive_state, promoted_topic_id, support_weight, challenge_weight, cite_weight,
               comment_count, evidence_depth, downstream_use_count, training_signal_count,
               reasons_json, metadata_json, created_at, updated_at
        FROM hive_commons_promotion_candidates
        ORDER BY score DESC, updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["reasons"] = json_loads_fn(item.pop("reasons_json", "[]"), fallback=[])
        item["metadata"] = json_loads_fn(item.pop("metadata_json", "{}"), fallback={})
        items.append(item)
    return {
        "generated_at": utcnow_fn(),
        "lane": "commons_promotion",
        "review_required": True,
        "items": items,
    }


def load_archivist_lane(
    conn: Any,
    *,
    limit: int,
    table_exists_fn: Any,
    utcnow_fn: Any,
) -> dict[str, Any]:
    if table_exists_fn(conn, "useful_outputs"):
        rows = conn.execute(
            """
            SELECT useful_output_id, source_type, source_id, task_id, topic_id, summary,
                   quality_score, archive_state, eligibility_state, source_updated_at
            FROM useful_outputs
            WHERE archive_state IN ('candidate', 'approved')
            ORDER BY quality_score DESC, source_updated_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return {
            "generated_at": utcnow_fn(),
            "lane": "archivist",
            "review_required": False,
            "archive_mode": "approved_summaries_only",
            "items": [dict(row) for row in rows],
        }
    if not table_exists_fn(conn, "task_results"):
        return {"generated_at": utcnow_fn(), "lane": "archivist", "items": []}
    rows = conn.execute(
        """
        SELECT result_id, task_id, helper_peer_id, result_type, summary, confidence, status, created_at, updated_at
        FROM task_results
        WHERE status IN ('accepted', 'partial', 'reviewed')
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return {
        "generated_at": utcnow_fn(),
        "lane": "archivist",
        "review_required": False,
        "archive_mode": "approved_summaries_only",
        "items": [dict(row) for row in rows],
    }


def load_active_assignments(conn: Any, *, limit: int, table_exists_fn: Any) -> list[dict[str, Any]]:
    if not table_exists_fn(conn, "task_assignments"):
        return []
    rows = conn.execute(
        """
        SELECT assignment_id, task_id, claim_id, parent_peer_id, helper_peer_id, assignment_mode,
               status, capability_token_id, lease_expires_at, last_progress_state, last_progress_note,
               assigned_at, updated_at, progress_updated_at, completed_at
        FROM task_assignments
        WHERE status = 'active'
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [dict(row) for row in rows]


def load_active_hive_claims(
    conn: Any,
    *,
    limit: int,
    table_exists_fn: Any,
    json_loads_fn: Any,
) -> list[dict[str, Any]]:
    if not table_exists_fn(conn, "hive_topic_claims"):
        return []
    rows = conn.execute(
        """
        SELECT claim_id, topic_id, agent_id, status, note, capability_tags_json, created_at, updated_at
        FROM hive_topic_claims
        WHERE status = 'active'
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["capability_tags"] = json_loads_fn(item.pop("capability_tags_json", "[]"), fallback=[])
        out.append(item)
    return out


def load_pending_operator_actions(
    conn: Any,
    *,
    limit: int,
    table_exists_fn: Any,
    json_loads_fn: Any,
) -> list[dict[str, Any]]:
    if not table_exists_fn(conn, "operator_action_requests"):
        return []
    rows = conn.execute(
        """
        SELECT action_id, session_id, task_id, action_kind, scope_json, status, created_at, updated_at
        FROM operator_action_requests
        WHERE status = 'pending_approval'
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["scope"] = json_loads_fn(item.pop("scope_json", "{}"), fallback={})
        out.append(item)
    return out


def load_pending_runtime_checkpoints(conn: Any, *, limit: int, table_exists_fn: Any) -> list[dict[str, Any]]:
    if not table_exists_fn(conn, "runtime_checkpoints") or not table_exists_fn(conn, "runtime_sessions"):
        return []
    rows = conn.execute(
        """
        SELECT c.checkpoint_id, c.session_id, c.task_id, c.task_class, c.status, c.step_count, c.last_tool_name,
               c.created_at, c.updated_at
        FROM runtime_checkpoints c
        JOIN runtime_sessions s
          ON s.last_checkpoint_id = c.checkpoint_id
        WHERE c.status = 'pending_approval'
          AND s.status = 'pending_approval'
        ORDER BY c.updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [dict(row) for row in rows]


def load_failed_runtime_sessions(conn: Any, *, limit: int, table_exists_fn: Any) -> list[dict[str, Any]]:
    if not table_exists_fn(conn, "runtime_sessions"):
        return []
    rows = conn.execute(
        """
        SELECT session_id, started_at, updated_at, event_count, last_event_type,
               last_message, request_preview, task_class, status, last_checkpoint_id
        FROM runtime_sessions
        WHERE status IN ('failed', 'interrupted')
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [dict(row) for row in rows]


def load_rejected_results(conn: Any, *, limit: int, table_exists_fn: Any) -> list[dict[str, Any]]:
    if not table_exists_fn(conn, "task_results"):
        return []
    rows = conn.execute(
        """
        SELECT result_id, task_id, helper_peer_id, summary, confidence, status, created_at, updated_at
        FROM task_results
        WHERE status IN ('rejected', 'harmful', 'failed')
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [dict(row) for row in rows]
