from __future__ import annotations

from pathlib import Path
from typing import Any

from core.runtime_execution_history import build_runtime_execution_history
from storage.db import DEFAULT_DB_PATH, get_connection


def _worker_is_live(checkpoint_id: str) -> bool:
    """Whether a worker is executing this checkpoint right now.

    Fails OPEN (False) if the registry cannot be read, so a read-only view can never be the reason
    a request errors; the cost is the old, over-permissive answer for that one call.
    """
    try:
        from core.live_turns import is_checkpoint_live

        return is_checkpoint_live(checkpoint_id)
    except Exception:
        return False


def load_runtime_sessions(
    conn: Any,
    *,
    limit: int,
    event_limit: int,
    table_exists_fn: Any,
    runtime_checkpoint_fn: Any,
    runtime_events_fn: Any,
    runtime_receipts_fn: Any,
    paths_from_payload_fn: Any,
) -> list[dict[str, Any]]:
    if not table_exists_fn(conn, "runtime_sessions"):
        return []
    rows = conn.execute(
        """
        SELECT session_id, started_at, updated_at, event_count, last_event_type, last_message,
               request_preview, task_class, status, last_checkpoint_id
        FROM runtime_sessions
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        checkpoint_id = str(item.get("last_checkpoint_id") or "").strip()
        item["checkpoint"] = runtime_checkpoint_fn(conn, checkpoint_id) if checkpoint_id else None
        # Same rule as list_runtime_sessions: a checkpoint whose worker is executing right now is
        # not resumable, it is already running. Two surfaces answering this differently is how one
        # of them ends up offering a Resume that duplicates live work.
        item["worker_live"] = bool(checkpoint_id and _worker_is_live(checkpoint_id))
        if item["checkpoint"]:
            item["resume_available"] = bool(
                str(item["checkpoint"].get("status") or "") in {"running", "interrupted", "pending_approval"}
                and not item["worker_live"]
            )
            item["checkpoint_status"] = str(item["checkpoint"].get("status") or "")
            item["checkpoint_step_count"] = int(item["checkpoint"].get("step_count") or 0)
        else:
            item["resume_available"] = False
            item["checkpoint_status"] = ""
            item["checkpoint_step_count"] = 0
        item["recent_events"] = runtime_events_fn(conn, str(item["session_id"]), limit=event_limit)
        receipts = runtime_receipts_fn(conn, str(item["session_id"]), limit=12)
        item["tool_receipts"] = receipts
        item["execution_history"] = build_runtime_execution_history(
            session=item,
            checkpoint=item["checkpoint"],
            events=item["recent_events"],
            receipts=receipts,
        )
        item["touched_paths"] = sorted(
            {
                *[path for receipt in receipts for path in paths_from_payload_fn(receipt)],
                *list(item["execution_history"].get("touched_paths") or []),
            }
        )
        out.append(item)
    return out


def load_runtime_checkpoints(conn: Any, *, limit: int, table_exists_fn: Any) -> list[dict[str, Any]]:
    if not table_exists_fn(conn, "runtime_checkpoints"):
        return []
    rows = conn.execute(
        """
        SELECT checkpoint_id, session_id, task_id, task_class, status, step_count, last_tool_name,
               final_response, failure_text, resume_count, created_at, updated_at, completed_at
        FROM runtime_checkpoints
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [dict(row) for row in rows]


def load_recent_task_results(
    conn: Any,
    *,
    limit: int,
    table_exists_fn: Any,
    json_loads_fn: Any,
) -> list[dict[str, Any]]:
    if not table_exists_fn(conn, "task_results"):
        return []
    rows = conn.execute(
        """
        SELECT result_id, task_id, helper_peer_id, result_type, summary, confidence,
               evidence_json, abstract_steps_json, risk_flags_json, status, created_at, updated_at
        FROM task_results
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["evidence"] = json_loads_fn(item.pop("evidence_json", "[]"), fallback=[])
        item["abstract_steps"] = json_loads_fn(item.pop("abstract_steps_json", "[]"), fallback=[])
        item["risk_flags"] = json_loads_fn(item.pop("risk_flags_json", "[]"), fallback=[])
        out.append(item)
    return out


def list_useful_outputs_for_workspace(
    db_path: str | Path | None = None,
    *,
    limit: int = 64,
    table_exists_fn: Any,
    json_loads_fn: Any,
) -> list[dict[str, Any]]:
    rows = []
    conn = get_connection(db_path or DEFAULT_DB_PATH)
    try:
        if table_exists_fn(conn, "useful_outputs"):
            fetched = conn.execute(
                """
                SELECT useful_output_id, source_type, source_id, task_id, topic_id, summary,
                       quality_score, archive_state, eligibility_state, durability_reasons_json,
                       eligibility_reasons_json, source_updated_at
                FROM useful_outputs
                ORDER BY quality_score DESC, source_updated_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
            rows = [dict(row) for row in fetched]
    finally:
        conn.close()
    for item in rows:
        item["durability_reasons"] = json_loads_fn(item.pop("durability_reasons_json", "[]"), fallback=[])
        item["eligibility_reasons"] = json_loads_fn(item.pop("eligibility_reasons_json", "[]"), fallback=[])
    return rows


def runtime_checkpoint(conn: Any, checkpoint_id: str, *, table_exists_fn: Any) -> dict[str, Any] | None:
    if not checkpoint_id or not table_exists_fn(conn, "runtime_checkpoints"):
        return None
    row = conn.execute(
        """
        SELECT checkpoint_id, session_id, task_id, task_class, status, step_count, last_tool_name,
               final_response, failure_text, resume_count, created_at, updated_at, completed_at
        FROM runtime_checkpoints
        WHERE checkpoint_id = ?
        LIMIT 1
        """,
        (checkpoint_id,),
    ).fetchone()
    return dict(row) if row else None


def runtime_events(
    conn: Any,
    session_id: str,
    *,
    limit: int,
    table_exists_fn: Any,
    json_loads_fn: Any,
) -> list[dict[str, Any]]:
    if not table_exists_fn(conn, "runtime_session_events"):
        return []
    rows = conn.execute(
        """
        SELECT session_id, seq, event_type, message, details_json, created_at
        FROM runtime_session_events
        WHERE session_id = ?
        ORDER BY seq DESC
        LIMIT ?
        """,
        (session_id, max(1, int(limit))),
    ).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows[::-1]:
        item = dict(row)
        item["details"] = json_loads_fn(item.pop("details_json", "{}"), fallback={})
        events.append(item)
    return events


def runtime_receipts(
    conn: Any,
    session_id: str,
    *,
    limit: int,
    table_exists_fn: Any,
    json_loads_fn: Any,
) -> list[dict[str, Any]]:
    if not table_exists_fn(conn, "runtime_tool_receipts"):
        return []
    rows = conn.execute(
        """
        SELECT receipt_key, session_id, checkpoint_id, tool_name, idempotency_key,
               arguments_json, execution_json, created_at, updated_at
        FROM runtime_tool_receipts
        WHERE session_id = ?
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (session_id, max(1, int(limit))),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["arguments"] = json_loads_fn(item.pop("arguments_json", "{}"), fallback={})
        item["execution"] = json_loads_fn(item.pop("execution_json", "{}"), fallback={})
        out.append(item)
    return out
