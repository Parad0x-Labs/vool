from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import policy_engine, runtime_paths
from core.control_plane import metrics_views as control_plane_metrics_views
from core.control_plane import policies as control_plane_policies
from core.control_plane import queue_views as control_plane_queue_views
from core.control_plane import runtime_views as control_plane_runtime_views
from core.control_plane import schemas as control_plane_schemas
from core.control_plane import templates as control_plane_templates
from core.runtime_execution_history import summarize_runtime_surface
from storage.db import DEFAULT_DB_PATH, get_connection
from storage.migrations import run_migrations
from storage.useful_output_store import summarize_useful_outputs, sync_useful_outputs

CONTROL_DIRS = (
    "queue",
    "leases",
    "runs",
    "budgets",
    "approvals",
    "deadletters",
    "metrics",
    "policies",
    "schemas",
)

TEMPLATE_NAMES = (
    "research_worker",
    "liquefy_worker",
    "monitor_worker",
    "personal_assistant",
    "reviewer",
    "archivist",
    "router",
)

_PATH_KEY_RE = re.compile(r"(?:^|_)(?:path|paths|root|roots|dir|directory|file|target|destination)$", re.IGNORECASE)


def sync_control_plane_workspace(
    *,
    workspace_root: str | Path | None = None,
    db_path: str | Path | None = None,
    queue_limit: int = 64,
    run_limit: int = 32,
    event_limit: int = 24,
) -> dict[str, Any]:
    target_root = Path(workspace_root or _workspace_root()).resolve()
    control_root = target_root / "control"
    templates_root = target_root / "templates"
    db_target = db_path or DEFAULT_DB_PATH
    run_migrations(db_target)
    useful_output_summary = sync_useful_outputs(db_target)

    for name in CONTROL_DIRS:
        (control_root / name).mkdir(parents=True, exist_ok=True)
    (control_root / "runs" / "sessions").mkdir(parents=True, exist_ok=True)
    (templates_root).mkdir(parents=True, exist_ok=True)

    conn = get_connection(db_target)
    try:
        open_offers = _load_open_task_offers(conn, limit=queue_limit)
        reviewer_lane = _load_reviewer_lane(conn, limit=queue_limit)
        archivist_lane = _load_archivist_lane(conn, limit=queue_limit)
        commons_queue = _load_commons_promotion_queue(conn, limit=queue_limit)
        active_assignments = _load_active_assignments(conn, limit=queue_limit)
        active_hive_claims = _load_active_hive_claims(conn, limit=queue_limit)
        runtime_sessions = _load_runtime_sessions(conn, limit=run_limit, event_limit=event_limit)
        runtime_checkpoints = _load_runtime_checkpoints(conn, limit=run_limit)
        task_results = _load_recent_task_results(conn, limit=run_limit)
        pending_actions = _load_pending_operator_actions(conn, limit=queue_limit)
        pending_runtime = _load_pending_runtime_checkpoints(conn, limit=queue_limit)
        failed_runs = _load_failed_runtime_sessions(conn, limit=run_limit)
        rejected_results = _load_rejected_results(conn, limit=run_limit)
        swarm_budget = _load_swarm_budget_summary(conn)
        public_hive_budget = _load_public_hive_budget_summary(conn)
        proof_of_useful_work = _load_proof_of_useful_work_summary(
            conn,
            limit=min(20, max(5, queue_limit // 2)),
            db_path=db_target,
        )
        adaptation_status = _load_adaptation_status(conn, db_path=db_target)
        adaptation_proof = _load_adaptation_proof_summary(conn, db_path=db_target)
    finally:
        conn.close()

    reviewer_policy = _reviewer_lane_policy()
    archivist_policy = _archivist_lane_policy()
    runtime_truth = summarize_runtime_surface(runtime_sessions)
    pending_operator_action_count = len(pending_actions)
    pending_runtime_checkpoint_count = len(pending_runtime)
    approval_backlog_count = pending_operator_action_count + pending_runtime_checkpoint_count
    overview = {
        "generated_at": _utcnow(),
        "workspace_root": str(target_root),
        "open_task_count": len(open_offers),
        "active_assignment_count": len(active_assignments),
        "active_hive_claim_count": len(active_hive_claims),
        "runtime_session_count": len(runtime_sessions),
        "pending_approval_count": approval_backlog_count,
        "approval_backlog_count": approval_backlog_count,
        "pending_operator_action_count": pending_operator_action_count,
        "pending_runtime_checkpoint_count": pending_runtime_checkpoint_count,
        "runtime_pending_approval_count": int(runtime_truth.get("session_pending_approval_count") or 0),
        "active_execution_count": int(runtime_truth.get("active_execution_count") or 0),
        "verifier_pending_count": int(runtime_truth.get("verifier_pending_count") or 0),
        "rollback_pending_count": int(runtime_truth.get("rollback_pending_count") or 0),
        "recovery_pending_count": int(runtime_truth.get("recovery_pending_count") or 0),
        "failed_runtime_count": len(failed_runs),
        "review_pending_count": len(reviewer_lane.get("items") or []),
        "archive_candidate_count": len(archivist_lane.get("items") or []),
        "commons_candidate_count": len(commons_queue.get("items") or []),
        "commons_review_ready_count": sum(1 for item in commons_queue.get("items") or [] if str(item.get("status") or "") == "review_required"),
        "useful_outputs": useful_output_summary,
        "swarm_dispatch_budget_today": swarm_budget,
        "public_hive_budget_today": public_hive_budget,
        "proof_of_useful_work": proof_of_useful_work,
        "adaptation": adaptation_status,
        "adaptation_proof": adaptation_proof,
        "runtime_truth": runtime_truth,
    }

    writes = 0
    writes += _write_json(control_root / "queue" / "open_task_offers.json", {"generated_at": _utcnow(), "items": open_offers})
    writes += _write_json(control_root / "queue" / "reviewer_lane.json", reviewer_lane)
    writes += _write_json(control_root / "queue" / "archivist_lane.json", archivist_lane)
    writes += _write_json(control_root / "queue" / "commons_promotion_queue.json", commons_queue)
    writes += _write_json(
        control_root / "queue" / "useful_outputs.json",
        {
            "generated_at": _utcnow(),
            "summary": useful_output_summary,
            "items": list_useful_outputs_for_workspace(db_target, limit=queue_limit),
        },
    )
    writes += _write_json(control_root / "leases" / "active_assignments.json", {"generated_at": _utcnow(), "items": active_assignments})
    writes += _write_json(control_root / "leases" / "active_hive_claims.json", {"generated_at": _utcnow(), "items": active_hive_claims})
    writes += _write_json(control_root / "runs" / "runtime_sessions.json", {"generated_at": _utcnow(), "items": runtime_sessions})
    writes += _write_json(control_root / "runs" / "runtime_checkpoints.json", {"generated_at": _utcnow(), "items": runtime_checkpoints})
    writes += _write_json(control_root / "runs" / "task_results_recent.json", {"generated_at": _utcnow(), "items": task_results})
    session_dir = control_root / "runs" / "sessions"
    expected_session_files: set[str] = set()
    for session in runtime_sessions:
        filename = f"{_safe_name(str(session.get('session_id') or 'session'))}.json"
        expected_session_files.add(filename)
        writes += _write_json(session_dir / filename, session)
    for stale in session_dir.glob("*.json"):
        if stale.name in expected_session_files:
            continue
        stale.unlink(missing_ok=True)
        writes += 1
    writes += _write_json(control_root / "budgets" / "swarm_dispatch_today.json", swarm_budget)
    writes += _write_json(control_root / "budgets" / "public_hive_quota_today.json", public_hive_budget)
    writes += _write_json(control_root / "metrics" / "proof_of_useful_work.json", proof_of_useful_work)
    writes += _write_json(control_root / "metrics" / "adaptation_proof.json", adaptation_proof)
    writes += _write_json(control_root / "metrics" / "runtime_truth.json", runtime_truth)
    writes += _write_json(control_root / "approvals" / "pending_operator_actions.json", {"generated_at": _utcnow(), "items": pending_actions})
    writes += _write_json(control_root / "approvals" / "pending_runtime_checkpoints.json", {"generated_at": _utcnow(), "items": pending_runtime})
    writes += _write_json(control_root / "deadletters" / "failed_runtime_sessions.json", {"generated_at": _utcnow(), "items": failed_runs})
    writes += _write_json(control_root / "deadletters" / "rejected_or_harmful_results.json", {"generated_at": _utcnow(), "items": rejected_results})
    writes += _write_json(control_root / "metrics" / "overview.json", overview)
    writes += _write_json(control_root / "metrics" / "adaptation.json", adaptation_status)
    writes += _write_json(control_root / "policies" / "budget_caps.json", _budget_caps_policy())
    writes += _write_json(control_root / "policies" / "reviewer_lane.json", reviewer_policy)
    writes += _write_json(control_root / "policies" / "archivist_lane.json", archivist_policy)
    writes += _write_text(control_root / "policies" / "control_plane_policy.md", _control_plane_policy_text())

    for relative_path, payload in _schema_library().items():
        writes += _write_json(control_root / "schemas" / relative_path, payload)

    for template_name, files in _template_library().items():
        template_root = templates_root / template_name
        template_root.mkdir(parents=True, exist_ok=True)
        for filename, payload in files.items():
            target = template_root / filename
            if filename.endswith(".json"):
                writes += _write_json(target, payload)
            else:
                writes += _write_text(target, str(payload))

    return {
        "ok": True,
        "workspace_root": str(target_root),
        "control_root": str(control_root),
        "templates_root": str(templates_root),
        "writes": writes,
        "open_task_count": len(open_offers),
        "runtime_session_count": len(runtime_sessions),
        "pending_approval_count": approval_backlog_count,
        "approval_backlog_count": approval_backlog_count,
        "pending_operator_action_count": pending_operator_action_count,
        "pending_runtime_checkpoint_count": pending_runtime_checkpoint_count,
        "runtime_pending_approval_count": int(runtime_truth.get("session_pending_approval_count") or 0),
        "active_execution_count": int(runtime_truth.get("active_execution_count") or 0),
        "verifier_pending_count": int(runtime_truth.get("verifier_pending_count") or 0),
        "rollback_pending_count": int(runtime_truth.get("rollback_pending_count") or 0),
        "recovery_pending_count": int(runtime_truth.get("recovery_pending_count") or 0),
        "template_count": len(TEMPLATE_NAMES),
    }


def collect_control_plane_status(
    *,
    db_path: str | Path | None = None,
    queue_limit: int = 64,
    run_limit: int = 32,
    event_limit: int = 24,
) -> dict[str, Any]:
    db_target = db_path or DEFAULT_DB_PATH
    run_migrations(db_target)
    useful_output_summary = sync_useful_outputs(db_target)
    conn = get_connection(db_target)
    try:
        open_offers = _load_open_task_offers(conn, limit=queue_limit)
        active_assignments = _load_active_assignments(conn, limit=queue_limit)
        active_hive_claims = _load_active_hive_claims(conn, limit=queue_limit)
        runtime_sessions = _load_runtime_sessions(conn, limit=run_limit, event_limit=event_limit)
        pending_actions = _load_pending_operator_actions(conn, limit=queue_limit)
        pending_runtime = _load_pending_runtime_checkpoints(conn, limit=queue_limit)
        reviewer_lane = _load_reviewer_lane(conn, limit=queue_limit)
        archivist_lane = _load_archivist_lane(conn, limit=queue_limit)
        commons_queue = _load_commons_promotion_queue(conn, limit=queue_limit)
        swarm_budget = _load_swarm_budget_summary(conn)
        public_hive_budget = _load_public_hive_budget_summary(conn)
        proof_of_useful_work = _load_proof_of_useful_work_summary(
            conn,
            limit=min(20, max(5, queue_limit // 2)),
            db_path=db_target,
        )
        adaptation_status = _load_adaptation_status(conn, db_path=db_target)
        adaptation_proof = _load_adaptation_proof_summary(conn, db_path=db_target)
    finally:
        conn.close()
    runtime_truth = summarize_runtime_surface(runtime_sessions)
    pending_operator_action_count = len(pending_actions)
    pending_runtime_checkpoint_count = len(pending_runtime)
    approval_backlog_count = pending_operator_action_count + pending_runtime_checkpoint_count
    return {
        "generated_at": _utcnow(),
        "open_task_count": len(open_offers),
        "active_assignment_count": len(active_assignments),
        "active_hive_claim_count": len(active_hive_claims),
        "runtime_session_count": len(runtime_sessions),
        "pending_approval_count": approval_backlog_count,
        "approval_backlog_count": approval_backlog_count,
        "pending_operator_action_count": pending_operator_action_count,
        "pending_runtime_checkpoint_count": pending_runtime_checkpoint_count,
        "runtime_pending_approval_count": int(runtime_truth.get("session_pending_approval_count") or 0),
        "active_execution_count": int(runtime_truth.get("active_execution_count") or 0),
        "verifier_pending_count": int(runtime_truth.get("verifier_pending_count") or 0),
        "rollback_pending_count": int(runtime_truth.get("rollback_pending_count") or 0),
        "recovery_pending_count": int(runtime_truth.get("recovery_pending_count") or 0),
        "review_pending_count": len(reviewer_lane.get("items") or []),
        "archive_candidate_count": len(archivist_lane.get("items") or []),
        "commons_candidate_count": len(commons_queue.get("items") or []),
        "commons_review_ready_count": sum(1 for item in commons_queue.get("items") or [] if str(item.get("status") or "") == "review_required"),
        "useful_outputs": useful_output_summary,
        "swarm_dispatch_budget_today": swarm_budget,
        "public_hive_budget_today": public_hive_budget,
        "proof_of_useful_work": proof_of_useful_work,
        "adaptation": adaptation_status,
        "adaptation_proof": adaptation_proof,
        "runtime_truth": runtime_truth,
    }


def _workspace_root() -> Path:
    workspace_getter = getattr(runtime_paths, "workspace_path", None)
    if callable(workspace_getter):
        return Path(workspace_getter()).resolve()
    return runtime_paths.project_path("workspace")


def _load_open_task_offers(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    return control_plane_queue_views.load_open_task_offers(
        conn,
        limit=limit,
        table_exists_fn=_table_exists,
    )


def _load_reviewer_lane(conn: Any, *, limit: int) -> dict[str, Any]:
    return control_plane_queue_views.load_reviewer_lane(
        conn,
        limit=limit,
        table_exists_fn=_table_exists,
        utcnow_fn=_utcnow,
    )


def _load_commons_promotion_queue(conn: Any, *, limit: int) -> dict[str, Any]:
    return control_plane_queue_views.load_commons_promotion_queue(
        conn,
        limit=limit,
        table_exists_fn=_table_exists,
        json_loads_fn=_json_loads,
        utcnow_fn=_utcnow,
    )


def _load_archivist_lane(conn: Any, *, limit: int) -> dict[str, Any]:
    return control_plane_queue_views.load_archivist_lane(
        conn,
        limit=limit,
        table_exists_fn=_table_exists,
        utcnow_fn=_utcnow,
    )


def _load_active_assignments(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    return control_plane_queue_views.load_active_assignments(
        conn,
        limit=limit,
        table_exists_fn=_table_exists,
    )


def _load_active_hive_claims(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    return control_plane_queue_views.load_active_hive_claims(
        conn,
        limit=limit,
        table_exists_fn=_table_exists,
        json_loads_fn=_json_loads,
    )


def _load_runtime_sessions(conn: Any, *, limit: int, event_limit: int) -> list[dict[str, Any]]:
    return control_plane_runtime_views.load_runtime_sessions(
        conn,
        limit=limit,
        event_limit=event_limit,
        table_exists_fn=_table_exists,
        runtime_checkpoint_fn=_runtime_checkpoint,
        runtime_events_fn=_runtime_events,
        runtime_receipts_fn=_runtime_receipts,
        paths_from_payload_fn=_paths_from_payload,
    )


def _load_runtime_checkpoints(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    return control_plane_runtime_views.load_runtime_checkpoints(
        conn,
        limit=limit,
        table_exists_fn=_table_exists,
    )


def _load_recent_task_results(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    return control_plane_runtime_views.load_recent_task_results(
        conn,
        limit=limit,
        table_exists_fn=_table_exists,
        json_loads_fn=_json_loads,
    )


def _load_pending_operator_actions(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    return control_plane_queue_views.load_pending_operator_actions(
        conn,
        limit=limit,
        table_exists_fn=_table_exists,
        json_loads_fn=_json_loads,
    )


def _load_pending_runtime_checkpoints(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    return control_plane_queue_views.load_pending_runtime_checkpoints(
        conn,
        limit=limit,
        table_exists_fn=_table_exists,
    )


def _load_failed_runtime_sessions(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    return control_plane_queue_views.load_failed_runtime_sessions(
        conn,
        limit=limit,
        table_exists_fn=_table_exists,
    )


def _load_rejected_results(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    return control_plane_queue_views.load_rejected_results(
        conn,
        limit=limit,
        table_exists_fn=_table_exists,
    )


def _load_swarm_budget_summary(conn: Any) -> dict[str, Any]:
    return control_plane_metrics_views.load_swarm_budget_summary(
        conn,
        table_exists_fn=_table_exists,
        utc_day_bucket_fn=_utc_day_bucket,
        utcnow_fn=_utcnow,
        policy_getter=policy_engine.get,
    )


def _load_public_hive_budget_summary(conn: Any) -> dict[str, Any]:
    return control_plane_metrics_views.load_public_hive_budget_summary(
        conn,
        table_exists_fn=_table_exists,
        utc_day_bucket_fn=_utc_day_bucket,
        utcnow_fn=_utcnow,
        policy_getter=policy_engine.get,
    )


def _load_adaptation_status(conn: Any, *, db_path: str | Path | None = None) -> dict[str, Any]:
    return control_plane_metrics_views.load_adaptation_status(
        conn,
        db_path=db_path,
        table_exists_fn=_table_exists,
        json_loads_fn=_json_loads,
        utcnow_fn=_utcnow,
        summarize_useful_outputs_fn=summarize_useful_outputs,
    )


def _load_proof_of_useful_work_summary(conn: Any, *, limit: int, db_path: str | Path | None = None) -> dict[str, Any]:
    return control_plane_metrics_views.load_proof_of_useful_work_summary(
        conn,
        limit=limit,
        db_path=db_path,
        table_exists_fn=_table_exists,
        utcnow_fn=_utcnow,
    )


def _load_adaptation_proof_summary(conn: Any, *, db_path: str | Path | None = None) -> dict[str, Any]:
    return control_plane_metrics_views.load_adaptation_proof_summary(
        conn,
        db_path=db_path,
        table_exists_fn=_table_exists,
        json_loads_fn=_json_loads,
        utcnow_fn=_utcnow,
    )


def list_useful_outputs_for_workspace(db_path: str | Path | None = None, *, limit: int = 64) -> list[dict[str, Any]]:
    return control_plane_runtime_views.list_useful_outputs_for_workspace(
        db_path,
        limit=limit,
        table_exists_fn=_table_exists,
        json_loads_fn=_json_loads,
    )


def _runtime_checkpoint(conn: Any, checkpoint_id: str) -> dict[str, Any] | None:
    return control_plane_runtime_views.runtime_checkpoint(
        conn,
        checkpoint_id,
        table_exists_fn=_table_exists,
    )


def _runtime_events(conn: Any, session_id: str, *, limit: int) -> list[dict[str, Any]]:
    return control_plane_runtime_views.runtime_events(
        conn,
        session_id,
        limit=limit,
        table_exists_fn=_table_exists,
        json_loads_fn=_json_loads,
    )


def _runtime_receipts(conn: Any, session_id: str, *, limit: int) -> list[dict[str, Any]]:
    return control_plane_runtime_views.runtime_receipts(
        conn,
        session_id,
        limit=limit,
        table_exists_fn=_table_exists,
        json_loads_fn=_json_loads,
    )


def _budget_caps_policy() -> dict[str, Any]:
    return control_plane_policies.budget_caps_policy(
        policy_getter=policy_engine.get,
        utcnow_fn=_utcnow,
    )


def _reviewer_lane_policy() -> dict[str, Any]:
    return control_plane_policies.reviewer_lane_policy()


def _archivist_lane_policy() -> dict[str, Any]:
    return control_plane_policies.archivist_lane_policy()


def _control_plane_policy_text() -> str:
    return control_plane_policies.control_plane_policy_text()


def _schema_library() -> dict[str, dict[str, Any]]:
    return control_plane_schemas.schema_library()


def _template_library() -> dict[str, dict[str, Any]]:
    return control_plane_templates.template_library(spawn_policy_fn=_spawn_policy)


def _spawn_policy(
    *,
    purpose: str,
    allowed_tools: list[str],
    allowed_read_roots: list[str],
    allowed_write_roots: list[str],
    shell_allowed: bool,
    network_allowed: bool,
    credential_use: bool,
    max_steps: int,
    max_lifetime_seconds: int,
    max_retries: int,
    max_requests_per_minute: int,
    review_required: bool,
    archive_behavior: str,
) -> dict[str, Any]:
    return control_plane_templates.spawn_policy(
        purpose=purpose,
        allowed_tools=allowed_tools,
        allowed_read_roots=allowed_read_roots,
        allowed_write_roots=allowed_write_roots,
        shell_allowed=shell_allowed,
        network_allowed=network_allowed,
        credential_use=credential_use,
        max_steps=max_steps,
        max_lifetime_seconds=max_lifetime_seconds,
        max_retries=max_retries,
        max_requests_per_minute=max_requests_per_minute,
        review_required=review_required,
        archive_behavior=archive_behavior,
    )


def _paths_from_payload(payload: dict[str, Any]) -> list[str]:
    return control_plane_templates.paths_from_payload(
        payload,
        path_key_pattern=_PATH_KEY_RE,
    )


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (str(table_name),),
    ).fetchone()
    return bool(row)


def _json_loads(raw: Any, *, fallback: Any) -> Any:
    try:
        loaded = json.loads(str(raw or ""))
    except Exception:
        return fallback
    if isinstance(fallback, dict):
        return loaded if isinstance(loaded, dict) else fallback
    if isinstance(fallback, list):
        return loaded if isinstance(loaded, list) else fallback
    return loaded


def _write_json(path: Path, payload: Any) -> int:
    return _write_text(path, json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n")


def _write_text(path: Path, content: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    if previous == content:
        return 0
    path.write_text(content, encoding="utf-8")
    return 1


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    return cleaned.strip("-._") or "item"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_day_bucket() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
