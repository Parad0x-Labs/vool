from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection

DEFAULT_SOURCE_CONFIG = {
    "include_useful_outputs": True,
    "include_conversations": True,
    "include_final_responses": True,
    "include_hive_posts": True,
    "include_task_results": True,
    "limit_per_source": 250,
}

DEFAULT_FILTERS = {
    "min_instruction_chars": 12,
    "min_output_chars": 24,
    "max_instruction_chars": 6000,
    "max_output_chars": 12000,
    "min_signal_score": 0.34,
    "conversation_min_signal_score": 0.38,
    "max_duplicate_output_fingerprint": 2,
    "max_duplicate_output_fingerprint_conversation": 1,
    "max_conversation_share_when_structured_present": 0.55,
    "max_conversation_ratio": 0.45,
    "min_structured_examples": 12,
    "min_high_signal_examples": 8,
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_loads(raw: str | None, fallback: Any) -> Any:
    try:
        if raw is None or raw == "":
            return fallback
        return json.loads(raw)
    except Exception:
        return fallback


def _row_to_corpus(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "corpus_id": str(row.get("corpus_id") or ""),
        "label": str(row.get("label") or ""),
        "source_config": _json_loads(row.get("source_config_json"), dict(DEFAULT_SOURCE_CONFIG)),
        "filters": _json_loads(row.get("filters_json"), dict(DEFAULT_FILTERS)),
        "output_path": str(row.get("output_path") or ""),
        "example_count": int(row.get("example_count") or 0),
        "source_stats": _json_loads(row.get("source_stats_json"), {}),
        "quality_score": float(row.get("quality_score") or 0.0),
        "quality_details": _json_loads(row.get("quality_details_json"), {}),
        "content_hash": str(row.get("content_hash") or ""),
        "last_scored_at": str(row.get("last_scored_at") or ""),
        "latest_build_at": str(row.get("latest_build_at") or ""),
        "created_at": str(row.get("created_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
    }


def _row_to_job(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": str(row.get("job_id") or ""),
        "corpus_id": str(row.get("corpus_id") or ""),
        "label": str(row.get("label") or ""),
        "base_model_ref": str(row.get("base_model_ref") or ""),
        "base_provider_name": str(row.get("base_provider_name") or ""),
        "base_model_name": str(row.get("base_model_name") or ""),
        "adapter_provider_name": str(row.get("adapter_provider_name") or ""),
        "adapter_model_name": str(row.get("adapter_model_name") or ""),
        "output_dir": str(row.get("output_dir") or ""),
        "status": str(row.get("status") or "queued"),
        "device": str(row.get("device") or ""),
        "dependency_status": _json_loads(row.get("dependency_status_json"), {}),
        "training_config": _json_loads(row.get("training_config_json"), {}),
        "metrics": _json_loads(row.get("metrics_json"), {}),
        "metadata": _json_loads(row.get("metadata_json"), {}),
        "registered_manifest": _json_loads(row.get("registered_manifest_json"), {}),
        "error_text": str(row.get("error_text") or ""),
        "started_at": str(row.get("started_at") or ""),
        "completed_at": str(row.get("completed_at") or ""),
        "promoted_at": str(row.get("promoted_at") or ""),
        "rolled_back_at": str(row.get("rolled_back_at") or ""),
        "created_at": str(row.get("created_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
    }


def _row_to_eval_run(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "eval_id": str(row.get("eval_id") or ""),
        "job_id": str(row.get("job_id") or ""),
        "corpus_id": str(row.get("corpus_id") or ""),
        "eval_kind": str(row.get("eval_kind") or ""),
        "split_name": str(row.get("split_name") or ""),
        "status": str(row.get("status") or "queued"),
        "sample_count": int(row.get("sample_count") or 0),
        "baseline_provider_ref": str(row.get("baseline_provider_ref") or ""),
        "candidate_provider_ref": str(row.get("candidate_provider_ref") or ""),
        "baseline_score": float(row.get("baseline_score") or 0.0),
        "candidate_score": float(row.get("candidate_score") or 0.0),
        "score_delta": float(row.get("score_delta") or 0.0),
        "metrics": _json_loads(row.get("metrics_json"), {}),
        "decision": str(row.get("decision") or ""),
        "error_text": str(row.get("error_text") or ""),
        "created_at": str(row.get("created_at") or ""),
        "completed_at": str(row.get("completed_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
    }


def _row_to_loop_state(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "loop_name": str(row.get("loop_name") or "default"),
        "status": str(row.get("status") or "idle"),
        "base_model_ref": str(row.get("base_model_ref") or ""),
        "base_provider_name": str(row.get("base_provider_name") or ""),
        "base_model_name": str(row.get("base_model_name") or ""),
        "active_job_id": str(row.get("active_job_id") or ""),
        "active_provider_name": str(row.get("active_provider_name") or ""),
        "active_model_name": str(row.get("active_model_name") or ""),
        "previous_job_id": str(row.get("previous_job_id") or ""),
        "previous_provider_name": str(row.get("previous_provider_name") or ""),
        "previous_model_name": str(row.get("previous_model_name") or ""),
        "last_corpus_id": str(row.get("last_corpus_id") or ""),
        "last_corpus_hash": str(row.get("last_corpus_hash") or ""),
        "last_example_count": int(row.get("last_example_count") or 0),
        "last_quality_score": float(row.get("last_quality_score") or 0.0),
        "last_eval_id": str(row.get("last_eval_id") or ""),
        "last_canary_eval_id": str(row.get("last_canary_eval_id") or ""),
        "last_tick_at": str(row.get("last_tick_at") or ""),
        "last_completed_tick_at": str(row.get("last_completed_tick_at") or ""),
        "last_decision": str(row.get("last_decision") or ""),
        "last_reason": str(row.get("last_reason") or ""),
        "last_error_text": str(row.get("last_error_text") or ""),
        "last_metadata_publish_at": str(row.get("last_metadata_publish_at") or ""),
        "metrics": _json_loads(row.get("metrics_json"), {}),
        "created_at": str(row.get("created_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
    }


def create_adaptation_corpus(
    *,
    label: str,
    source_config: dict[str, Any] | None = None,
    filters: dict[str, Any] | None = None,
    output_path: str = "",
) -> dict[str, Any]:
    corpus_id = f"corpus-{uuid.uuid4().hex}"
    now = _utcnow()
    merged_source = dict(DEFAULT_SOURCE_CONFIG)
    merged_source.update(dict(source_config or {}))
    merged_filters = dict(DEFAULT_FILTERS)
    merged_filters.update(dict(filters or {}))
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO adaptation_corpora (
                corpus_id, label, source_config_json, filters_json, output_path,
                example_count, source_stats_json, quality_score, quality_details_json,
                content_hash, last_scored_at, latest_build_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 0, '{}', 0.0, '{}', '', NULL, NULL, ?, ?)
            """,
            (
                corpus_id,
                str(label or "").strip() or corpus_id,
                json.dumps(merged_source, sort_keys=True),
                json.dumps(merged_filters, sort_keys=True),
                str(output_path or "").strip(),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_adaptation_corpus(corpus_id) or {}


def get_adaptation_corpus(corpus_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM adaptation_corpora WHERE corpus_id = ? LIMIT 1",
            (str(corpus_id or "").strip(),),
        ).fetchone()
        return _row_to_corpus(dict(row)) if row else None
    finally:
        conn.close()


def list_adaptation_corpora(*, limit: int = 100) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM adaptation_corpora ORDER BY updated_at DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        return [_row_to_corpus(dict(row)) for row in rows]
    finally:
        conn.close()


def update_corpus_build(
    corpus_id: str,
    *,
    output_path: str,
    example_count: int,
    source_stats: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    now = _utcnow()
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE adaptation_corpora
            SET output_path = ?,
                example_count = ?,
                source_stats_json = ?,
                latest_build_at = ?,
                updated_at = ?
            WHERE corpus_id = ?
            """,
            (
                str(output_path or "").strip(),
                max(0, int(example_count)),
                json.dumps(dict(source_stats or {}), sort_keys=True),
                now,
                now,
                str(corpus_id or "").strip(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_adaptation_corpus(corpus_id)


def update_corpus_analysis(
    corpus_id: str,
    *,
    quality_score: float,
    quality_details: dict[str, Any] | None = None,
    content_hash: str = "",
    last_scored_at: str | None = None,
) -> dict[str, Any] | None:
    now = _utcnow()
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE adaptation_corpora
            SET quality_score = ?,
                quality_details_json = ?,
                content_hash = ?,
                last_scored_at = ?,
                updated_at = ?
            WHERE corpus_id = ?
            """,
            (
                max(0.0, float(quality_score)),
                json.dumps(dict(quality_details or {}), sort_keys=True),
                str(content_hash or "").strip(),
                str(last_scored_at or now).strip(),
                now,
                str(corpus_id or "").strip(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_adaptation_corpus(corpus_id)


def update_corpus_spec(
    corpus_id: str,
    *,
    source_config: dict[str, Any] | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    current = get_adaptation_corpus(corpus_id)
    if not current:
        return None
    merged_source = dict(current.get("source_config") or {})
    if source_config:
        merged_source.update(dict(source_config))
    merged_filters = dict(current.get("filters") or {})
    if filters:
        merged_filters.update(dict(filters))
    now = _utcnow()
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE adaptation_corpora
            SET source_config_json = ?,
                filters_json = ?,
                updated_at = ?
            WHERE corpus_id = ?
            """,
            (
                json.dumps(merged_source, sort_keys=True),
                json.dumps(merged_filters, sort_keys=True),
                now,
                str(corpus_id or "").strip(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_adaptation_corpus(corpus_id)


def create_adaptation_job(
    *,
    corpus_id: str,
    base_model_ref: str,
    label: str = "",
    base_provider_name: str = "",
    base_model_name: str = "",
    adapter_provider_name: str = "",
    adapter_model_name: str = "",
    output_dir: str = "",
    training_config: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    job_id = f"adapt-{uuid.uuid4().hex}"
    now = _utcnow()
    merged_config = dict(training_config or {})
    merged_metadata = dict(metadata or {})
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO adaptation_jobs (
                job_id, corpus_id, label, base_model_ref, base_provider_name, base_model_name,
                adapter_provider_name, adapter_model_name, output_dir, status, device,
                dependency_status_json, training_config_json, metrics_json, metadata_json,
                registered_manifest_json, error_text, started_at, completed_at, promoted_at,
                rolled_back_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', '', '{}', ?, '{}', ?, '{}', '', NULL, NULL, NULL, NULL, ?, ?)
            """,
            (
                job_id,
                str(corpus_id or "").strip(),
                str(label or "").strip() or job_id,
                str(base_model_ref or "").strip(),
                str(base_provider_name or "").strip(),
                str(base_model_name or "").strip(),
                str(adapter_provider_name or "").strip(),
                str(adapter_model_name or "").strip(),
                str(output_dir or "").strip(),
                json.dumps(merged_config, sort_keys=True),
                json.dumps(merged_metadata, sort_keys=True),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    append_adaptation_job_event(job_id, "job_created", "Adaptation job queued.", {"corpus_id": corpus_id})
    return get_adaptation_job(job_id) or {}


def get_adaptation_job(job_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM adaptation_jobs WHERE job_id = ? LIMIT 1",
            (str(job_id or "").strip(),),
        ).fetchone()
        return _row_to_job(dict(row)) if row else None
    finally:
        conn.close()


def list_adaptation_jobs(*, limit: int = 100, statuses: list[str] | tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        if statuses:
            clean_statuses = [str(item).strip() for item in statuses if str(item).strip()]
            placeholders = ", ".join("?" for _ in clean_statuses)
            rows = conn.execute(
                f"SELECT * FROM adaptation_jobs WHERE status IN ({placeholders}) ORDER BY updated_at DESC LIMIT ?",
                (*clean_statuses, max(1, int(limit))),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM adaptation_jobs ORDER BY updated_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [_row_to_job(dict(row)) for row in rows]
    finally:
        conn.close()


def update_adaptation_job(
    job_id: str,
    *,
    status: str | None = None,
    output_dir: str | None = None,
    device: str | None = None,
    dependency_status: dict[str, Any] | None = None,
    training_config: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    registered_manifest: dict[str, Any] | None = None,
    error_text: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    promoted_at: str | None = None,
    rolled_back_at: str | None = None,
) -> dict[str, Any] | None:
    current = get_adaptation_job(job_id)
    if not current:
        return None
    now = _utcnow()
    merged_dependency = dict(current.get("dependency_status") or {})
    if dependency_status is not None:
        merged_dependency.update(dict(dependency_status))
    merged_training = dict(current.get("training_config") or {})
    if training_config is not None:
        merged_training.update(dict(training_config))
    merged_metrics = dict(current.get("metrics") or {})
    if metrics is not None:
        merged_metrics.update(dict(metrics))
    merged_metadata = dict(current.get("metadata") or {})
    if metadata is not None:
        merged_metadata.update(dict(metadata))
    merged_manifest = dict(current.get("registered_manifest") or {})
    if registered_manifest is not None:
        merged_manifest.update(dict(registered_manifest))
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE adaptation_jobs
            SET status = ?,
                output_dir = ?,
                device = ?,
                dependency_status_json = ?,
                training_config_json = ?,
                metrics_json = ?,
                metadata_json = ?,
                registered_manifest_json = ?,
                error_text = ?,
                started_at = ?,
                completed_at = ?,
                promoted_at = ?,
                rolled_back_at = ?,
                updated_at = ?
            WHERE job_id = ?
            """,
            (
                str(status or current.get("status") or "queued"),
                str(output_dir if output_dir is not None else current.get("output_dir") or ""),
                str(device if device is not None else current.get("device") or ""),
                json.dumps(merged_dependency, sort_keys=True),
                json.dumps(merged_training, sort_keys=True),
                json.dumps(merged_metrics, sort_keys=True),
                json.dumps(merged_metadata, sort_keys=True),
                json.dumps(merged_manifest, sort_keys=True),
                str(error_text if error_text is not None else current.get("error_text") or ""),
                started_at if started_at is not None else (current.get("started_at") or None),
                completed_at if completed_at is not None else (current.get("completed_at") or None),
                promoted_at if promoted_at is not None else (current.get("promoted_at") or None),
                rolled_back_at if rolled_back_at is not None else (current.get("rolled_back_at") or None),
                now,
                str(job_id or "").strip(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_adaptation_job(job_id)


def append_adaptation_job_event(
    job_id: str,
    event_type: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_job_id = str(job_id or "").strip()
    now = _utcnow()
    payload = dict(details or {})
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM adaptation_job_events WHERE job_id = ?",
            (clean_job_id,),
        ).fetchone()
        seq = int((dict(row) if row else {}).get("max_seq") or 0) + 1
        conn.execute(
            """
            INSERT INTO adaptation_job_events (job_id, seq, event_type, message, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                clean_job_id,
                seq,
                str(event_type or "").strip() or "event",
                str(message or "").strip(),
                json.dumps(payload, sort_keys=True),
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "job_id": clean_job_id,
        "seq": seq,
        "event_type": str(event_type or "").strip() or "event",
        "message": str(message or "").strip(),
        "details": payload,
        "created_at": now,
    }


def list_adaptation_job_events(job_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM adaptation_job_events
            WHERE job_id = ?
            ORDER BY seq ASC
            LIMIT ?
            """,
            (str(job_id or "").strip(), max(1, int(limit))),
        ).fetchall()
        return [
            {
                "job_id": str(row["job_id"]),
                "seq": int(row["seq"]),
                "event_type": str(row["event_type"]),
                "message": str(row["message"]),
                "details": _json_loads(row["details_json"], {}),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]
    finally:
        conn.close()


def create_adaptation_eval_run(
    *,
    job_id: str,
    corpus_id: str,
    eval_kind: str,
    split_name: str,
    sample_count: int,
    baseline_provider_ref: str,
    candidate_provider_ref: str,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    eval_id = f"eval-{uuid.uuid4().hex}"
    now = _utcnow()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO adaptation_eval_runs (
                eval_id, job_id, corpus_id, eval_kind, split_name, status, sample_count,
                baseline_provider_ref, candidate_provider_ref, baseline_score, candidate_score,
                score_delta, metrics_json, decision, error_text, created_at, completed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, 0.0, 0.0, 0.0, ?, '', '', ?, NULL, ?)
            """,
            (
                eval_id,
                str(job_id or "").strip(),
                str(corpus_id or "").strip(),
                str(eval_kind or "").strip() or "eval",
                str(split_name or "").strip(),
                max(0, int(sample_count)),
                str(baseline_provider_ref or "").strip(),
                str(candidate_provider_ref or "").strip(),
                json.dumps(dict(metrics or {}), sort_keys=True),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_adaptation_eval_run(eval_id) or {}


def get_adaptation_eval_run(eval_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM adaptation_eval_runs WHERE eval_id = ? LIMIT 1",
            (str(eval_id or "").strip(),),
        ).fetchone()
        return _row_to_eval_run(dict(row)) if row else None
    finally:
        conn.close()


def list_adaptation_eval_runs(
    *,
    job_id: str | None = None,
    eval_kind: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if str(job_id or "").strip():
        where.append("job_id = ?")
        params.append(str(job_id or "").strip())
    if str(eval_kind or "").strip():
        where.append("eval_kind = ?")
        params.append(str(eval_kind or "").strip())
    query = "SELECT * FROM adaptation_eval_runs"
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(max(1, int(limit)))
    conn = get_connection()
    try:
        rows = conn.execute(query, tuple(params)).fetchall()
        return [_row_to_eval_run(dict(row)) for row in rows]
    finally:
        conn.close()


def update_adaptation_eval_run(
    eval_id: str,
    *,
    status: str | None = None,
    baseline_score: float | None = None,
    candidate_score: float | None = None,
    score_delta: float | None = None,
    metrics: dict[str, Any] | None = None,
    decision: str | None = None,
    error_text: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any] | None:
    current = get_adaptation_eval_run(eval_id)
    if not current:
        return None
    merged_metrics = dict(current.get("metrics") or {})
    if metrics is not None:
        merged_metrics.update(dict(metrics))
    now = _utcnow()
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE adaptation_eval_runs
            SET status = ?,
                baseline_score = ?,
                candidate_score = ?,
                score_delta = ?,
                metrics_json = ?,
                decision = ?,
                error_text = ?,
                completed_at = ?,
                updated_at = ?
            WHERE eval_id = ?
            """,
            (
                str(status or current.get("status") or "queued"),
                float(baseline_score if baseline_score is not None else current.get("baseline_score") or 0.0),
                float(candidate_score if candidate_score is not None else current.get("candidate_score") or 0.0),
                float(score_delta if score_delta is not None else current.get("score_delta") or 0.0),
                json.dumps(merged_metrics, sort_keys=True),
                str(decision if decision is not None else current.get("decision") or ""),
                str(error_text if error_text is not None else current.get("error_text") or ""),
                completed_at if completed_at is not None else (current.get("completed_at") or None),
                now,
                str(eval_id or "").strip(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_adaptation_eval_run(eval_id)


def get_adaptation_loop_state(loop_name: str = "default") -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM adaptation_loop_state WHERE loop_name = ? LIMIT 1",
            (str(loop_name or "default").strip() or "default",),
        ).fetchone()
        return _row_to_loop_state(dict(row)) if row else None
    finally:
        conn.close()


def upsert_adaptation_loop_state(
    loop_name: str = "default",
    *,
    status: str | None = None,
    base_model_ref: str | None = None,
    base_provider_name: str | None = None,
    base_model_name: str | None = None,
    active_job_id: str | None = None,
    active_provider_name: str | None = None,
    active_model_name: str | None = None,
    previous_job_id: str | None = None,
    previous_provider_name: str | None = None,
    previous_model_name: str | None = None,
    last_corpus_id: str | None = None,
    last_corpus_hash: str | None = None,
    last_example_count: int | None = None,
    last_quality_score: float | None = None,
    last_eval_id: str | None = None,
    last_canary_eval_id: str | None = None,
    last_tick_at: str | None = None,
    last_completed_tick_at: str | None = None,
    last_decision: str | None = None,
    last_reason: str | None = None,
    last_error_text: str | None = None,
    last_metadata_publish_at: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_loop_name = str(loop_name or "default").strip() or "default"
    current = get_adaptation_loop_state(clean_loop_name)
    now = _utcnow()
    merged_metrics = dict((current or {}).get("metrics") or {})
    if metrics is not None:
        merged_metrics.update(dict(metrics))
    if current is None:
        current = {
            "loop_name": clean_loop_name,
            "status": "idle",
            "base_model_ref": "",
            "base_provider_name": "",
            "base_model_name": "",
            "active_job_id": "",
            "active_provider_name": "",
            "active_model_name": "",
            "previous_job_id": "",
            "previous_provider_name": "",
            "previous_model_name": "",
            "last_corpus_id": "",
            "last_corpus_hash": "",
            "last_example_count": 0,
            "last_quality_score": 0.0,
            "last_eval_id": "",
            "last_canary_eval_id": "",
            "last_tick_at": "",
            "last_completed_tick_at": "",
            "last_decision": "",
            "last_reason": "",
            "last_error_text": "",
            "last_metadata_publish_at": "",
            "metrics": {},
            "created_at": now,
            "updated_at": now,
        }
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO adaptation_loop_state (
                loop_name, status, base_model_ref, base_provider_name, base_model_name,
                active_job_id, active_provider_name, active_model_name,
                previous_job_id, previous_provider_name, previous_model_name,
                last_corpus_id, last_corpus_hash, last_example_count, last_quality_score,
                last_eval_id, last_canary_eval_id, last_tick_at, last_completed_tick_at,
                last_decision, last_reason, last_error_text, last_metadata_publish_at,
                metrics_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_loop_name,
                str(status if status is not None else current.get("status") or "idle"),
                str(base_model_ref if base_model_ref is not None else current.get("base_model_ref") or ""),
                str(base_provider_name if base_provider_name is not None else current.get("base_provider_name") or ""),
                str(base_model_name if base_model_name is not None else current.get("base_model_name") or ""),
                str(active_job_id if active_job_id is not None else current.get("active_job_id") or ""),
                str(active_provider_name if active_provider_name is not None else current.get("active_provider_name") or ""),
                str(active_model_name if active_model_name is not None else current.get("active_model_name") or ""),
                str(previous_job_id if previous_job_id is not None else current.get("previous_job_id") or ""),
                str(previous_provider_name if previous_provider_name is not None else current.get("previous_provider_name") or ""),
                str(previous_model_name if previous_model_name is not None else current.get("previous_model_name") or ""),
                str(last_corpus_id if last_corpus_id is not None else current.get("last_corpus_id") or ""),
                str(last_corpus_hash if last_corpus_hash is not None else current.get("last_corpus_hash") or ""),
                int(last_example_count if last_example_count is not None else current.get("last_example_count") or 0),
                float(last_quality_score if last_quality_score is not None else current.get("last_quality_score") or 0.0),
                str(last_eval_id if last_eval_id is not None else current.get("last_eval_id") or ""),
                str(last_canary_eval_id if last_canary_eval_id is not None else current.get("last_canary_eval_id") or ""),
                last_tick_at if last_tick_at is not None else (current.get("last_tick_at") or None),
                last_completed_tick_at if last_completed_tick_at is not None else (current.get("last_completed_tick_at") or None),
                str(last_decision if last_decision is not None else current.get("last_decision") or ""),
                str(last_reason if last_reason is not None else current.get("last_reason") or ""),
                str(last_error_text if last_error_text is not None else current.get("last_error_text") or ""),
                last_metadata_publish_at if last_metadata_publish_at is not None else (current.get("last_metadata_publish_at") or None),
                json.dumps(merged_metrics, sort_keys=True),
                str(current.get("created_at") or now),
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_adaptation_loop_state(clean_loop_name) or {}
