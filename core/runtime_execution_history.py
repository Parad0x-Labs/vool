from __future__ import annotations

import re
from typing import Any

_PATH_KEY_RE = re.compile(r"(?:^|_)(?:path|paths|root|roots|dir|directory|file|target|destination)$", re.IGNORECASE)
_MUTATING_TOOL_INTENTS = {
    "workspace.ensure_directory",
    "workspace.write_file",
    "workspace.replace_in_file",
    "workspace.apply_unified_diff",
    "workspace.rollback_last_change",
    "workspace.run_formatter",
    "sandbox.run_command",
    "hive.research_topic",
    "hive.create_topic",
    "hive.claim_task",
    "hive.post_progress",
    "hive.submit_result",
    "operator.cleanup_temp_files",
    "operator.move_path",
    "operator.schedule_calendar_event",
}
_VALIDATION_TOOL_INTENTS = {
    "workspace.run_tests",
    "workspace.run_lint",
    "workspace.run_formatter",
}
_ROLLBACK_EVENT_TYPES = {"task_envelope_rollback_completed", "task_envelope_rollback_failed"}
_RESTORE_EVENT_TYPES = {"task_envelope_restore_completed", "task_envelope_restore_failed"}
_MODEL_REVIEW_EVENT_STATES = {
    "model_lane_verifier_started": "running",
    "model_lane_verifier_completed": "passed",
    "model_lane_verifier_flagged": "flagged",
    "model_lane_verifier_failed": "runtime_failed",
    "model_lane_verifier_blocked": "blocked",
    "model_lane_verifier_degraded": "degraded",
}
_FAILED_EVIDENCE_EVENT_TYPES = {
    "task_envelope_failed",
    "task_envelope_step_failed",
    "tool_failed",
}
_PASSED_EVIDENCE_EVENT_TYPES = {
    "task_envelope_completed",
    "task_envelope_step_completed",
    "tool_executed",
}


def build_runtime_execution_history(
    *,
    session: dict[str, Any],
    checkpoint: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    receipts: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    session_row = dict(session or {})
    checkpoint_row = dict(checkpoint or {})
    normalized_events = [_normalize_event(item) for item in list(events or []) if isinstance(item, dict)]
    normalized_receipts = [_normalize_receipt(item) for item in list(receipts or []) if isinstance(item, dict)]

    artifact_ids: list[str] = []
    packet_artifact_ids: list[str] = []
    bundle_artifact_ids: list[str] = []
    artifact_rows: list[dict[str, str]] = []
    candidate_ids: list[str] = []
    changed_paths: list[str] = []
    touched_paths: list[str] = []
    failure_items: list[dict[str, str]] = []
    retry_history: list[dict[str, Any]] = []
    query_runs: list[dict[str, Any]] = []
    query_run_labels: set[str] = set()
    started_queries: set[str] = set()
    completed_queries: set[str] = set()
    seen_tool_attempts: dict[str, int] = {}
    mutating_tools: list[str] = []
    mutating_tool_seen: set[str] = set()
    receipt_types: list[str] = []
    receipt_type_seen: set[str] = set()

    request_status = _string(session_row.get("status") or checkpoint_row.get("status") or "running").lower() or "running"
    last_message = _string(session_row.get("last_message"))
    latest_tool = _string(checkpoint_row.get("last_tool_name"))
    stop_reason = ""
    topic_id = ""
    topic_title = ""
    claim_id = ""
    result_status = ""
    post_id = ""
    query_count = 0
    artifact_count = 0
    candidate_count = 0
    approval_seen = False
    approval_state = "not_required"
    rollback_state = "not_triggered"
    restore_state = "not_triggered"
    model_review_state = "not_run"
    # The reviewer's OWN reason for a review that produced no verdict ("Verifier was required,
    # but no verifier lane was available."). The typed state alone says only "blocked"; the
    # message says which of the four blocked shapes it was, and that is the fact the operator
    # can act on (configure a verifier lane vs wait vs switch models).
    model_review_reason = ""
    validation_state = "not_run"
    stages = {
        "received": False,
        "claimed": False,
        "packet": False,
        "queries": False,
        "bundle": False,
        "result": False,
    }

    for receipt in normalized_receipts:
        tool_name = _string(receipt.get("tool_name"))
        if _is_mutating_tool_intent(tool_name) and tool_name not in mutating_tool_seen:
            mutating_tool_seen.add(tool_name)
            mutating_tools.append(tool_name)
        for path in _paths_from_payload(receipt):
            _append_unique(changed_paths, path)
            _append_unique(touched_paths, path)

    for event in normalized_events:
        event_type = _string(event.get("event_type")).lower()
        event_status = _string(event.get("status")).lower()
        event_tool = _string(event.get("tool_name") or event.get("intent"))

        if event.get("topic_id") and not topic_id:
            topic_id = _string(event.get("topic_id"))
        if event.get("topic_title") and not topic_title:
            topic_title = _string(event.get("topic_title"))
        if event.get("claim_id") and not claim_id:
            claim_id = _string(event.get("claim_id"))
        if event.get("result_status") and not result_status:
            result_status = _string(event.get("result_status")).lower()
        if event.get("post_id") and not post_id:
            post_id = _string(event.get("post_id"))
        if event_tool:
            latest_tool = event_tool
            seen_tool_attempts[event_tool] = int(seen_tool_attempts.get(event_tool) or 0) + 1
            if _is_mutating_tool_intent(event_tool) and event_tool not in mutating_tool_seen:
                mutating_tool_seen.add(event_tool)
                mutating_tools.append(event_tool)
        if event.get("message"):
            last_message = _string(event.get("message"))
        if not stop_reason:
            stop_reason = _string(event.get("stop_reason") or event.get("loop_stop_reason") or event.get("final_stop_reason"))

        if event_type in {"task_received", "task_envelope_started"}:
            stages["received"] = True
        if claim_id or event_tool == "hive.claim_task":
            stages["claimed"] = True

        artifact_id = _string(event.get("artifact_id"))
        if artifact_id:
            _append_unique(artifact_ids, artifact_id)
            artifact_rows.append(
                {
                    "artifact_id": artifact_id,
                    "role": _string(event.get("artifact_role") or event.get("artifact_kind") or "artifact"),
                    "path": _string(event.get("path") or event.get("file_path") or event.get("target_path")),
                    "tool_name": event_tool,
                }
            )
            if _string(event.get("artifact_role")) == "packet" or event_tool == "liquefy.pack_research_packet":
                _append_unique(packet_artifact_ids, artifact_id)
            if _string(event.get("artifact_role")) == "bundle" or event_tool == "liquefy.pack_research_bundle":
                _append_unique(bundle_artifact_ids, artifact_id)
        if event_tool == "liquefy.pack_research_packet":
            stages["packet"] = True
        if event_tool == "liquefy.pack_research_bundle":
            stages["bundle"] = True
        if event_tool == "hive.submit_result" or event_type in {"task_completed", "task_envelope_completed", "task_envelope_merge_completed"}:
            stages["result"] = True

        candidate_id = _string(event.get("candidate_id"))
        if candidate_id:
            _append_unique(candidate_ids, candidate_id)
        if event.get("candidate_count") is not None:
            candidate_count = max(candidate_count, _safe_int(event.get("candidate_count")))
        if event.get("query_count") is not None:
            query_count = max(query_count, _safe_int(event.get("query_count")))
        if event.get("artifact_count") is not None:
            artifact_count = max(artifact_count, _safe_int(event.get("artifact_count")))

        for path in _paths_from_payload(event):
            _append_unique(changed_paths, path)
            _append_unique(touched_paths, path)

        if _event_failed(event_type, event_status, _string(event.get("result_status")).lower()):
            failure_items.append(
                {
                    "type": event_type or "failed",
                    "tool": event_tool,
                    "message": _string(event.get("message")),
                }
            )

        if event.get("retry_count") is not None:
            retry_history.append(
                {
                    "tool": event_tool or "runtime.step",
                    "retry_count": _safe_int(event.get("retry_count")),
                    "reason": _string(event.get("retry_reason") or event.get("message")),
                }
            )

        if event_type == "tool_preview" or event_type == "task_pending_approval" or event_status == "pending_approval":
            approval_seen = True
            approval_state = "pending"

        if event_type in _ROLLBACK_EVENT_TYPES:
            rollback_state = "completed" if event_type.endswith("_completed") else "failed"
        if event_type in _RESTORE_EVENT_TYPES:
            restore_state = "completed" if event_type.endswith("_completed") else "failed"

        # The event type is the authority for reviewer-model evidence. The legacy
        # task_role="verifier" is not: verifier envelopes often run pytest/lint and therefore
        # belong to validation. Exact validation tools/receipts are classified independently.
        review_observation = _MODEL_REVIEW_EVENT_STATES.get(event_type)
        if review_observation is not None:
            model_review_state = _advance_review_state(model_review_state, review_observation)
            if review_observation in {"blocked", "degraded", "runtime_failed"}:
                # The event's own message names the concrete cause ("no verifier lane was
                # available", "selected the same model as the primary lane", ...). The typed
                # state collapses all of those to one word; the reason is the actionable fact.
                reason = _string(event.get("message"))
                if reason:
                    model_review_reason = reason[:200]
        elif event_tool in _VALIDATION_TOOL_INTENTS or _has_receipt_type(event, "validation_result"):
            validation_state = _advance_evidence_state(
                validation_state,
                _typed_evidence_observation(event_type=event_type, event_status=event_status),
            )

        if event_tool == "curiosity.run_external_topic":
            q_index = _safe_int(event.get("query_index"))
            q_total = _safe_int(event.get("query_total"))
            label = _string(event.get("query") or event.get("message"))
            key = label or f"{q_index}/{q_total}"
            if event_type == "tool_started":
                started_queries.add(key)
            if event_type == "tool_executed":
                completed_queries.add(key)
            if label and label not in query_run_labels:
                query_run_labels.add(label)
                query_runs.append(
                    {
                        "label": label,
                        "index": q_index,
                        "total": q_total,
                        "state": "completed" if event_type == "tool_executed" else "running",
                    }
                )

        for receipt_type in list(event.get("receipt_types") or []):
            normalized_type = _string(receipt_type)
            if normalized_type and normalized_type not in receipt_type_seen:
                receipt_type_seen.add(normalized_type)
                receipt_types.append(normalized_type)

    for tool_name, attempts in seen_tool_attempts.items():
        if attempts > 1 and not any(item.get("tool") == tool_name for item in retry_history):
            retry_history.append(
                {
                    "tool": tool_name,
                    "retry_count": attempts - 1,
                    "reason": "repeated execution in the same session",
                }
            )

    query_completed_count = len(completed_queries) or query_count
    query_started_count = max(len(started_queries), len(query_runs), query_completed_count)
    if query_started_count > 0 or query_completed_count > 0:
        stages["queries"] = True
    artifact_count = max(artifact_count, len(artifact_ids))
    candidate_count = max(candidate_count, len(candidate_ids))

    checkpoint_status = _string(checkpoint_row.get("status") or session_row.get("checkpoint_status")).lower()
    if request_status == "pending_approval" or checkpoint_status == "pending_approval":
        approval_state = "pending"
    elif approval_seen:
        approval_state = "cleared"
    if rollback_state == "not_triggered" and checkpoint_row.get("last_tool_name") == "workspace.rollback_last_change":
        rollback_state = "running" if request_status == "running" else rollback_state
    if restore_state == "not_triggered" and any(item.get("restore_session_id") for item in normalized_events):
        restore_state = "running"
    # Retain the merged field for old consumers, but derive it from explicit typed dimensions.
    # Ambiguous legacy verifier provenance leaves every state at not_run instead of becoming a
    # fabricated pass in either dimension.
    verifier_state = _legacy_verifier_state(model_review_state, validation_state)

    checkpoint_step_count = _safe_int(checkpoint_row.get("step_count") or session_row.get("checkpoint_step_count"))
    checkpoint_resume_count = _safe_int(checkpoint_row.get("resume_count"))
    pending_intent = dict(checkpoint_row.get("pending_intent") or {})
    pending_intent_name = _string(pending_intent.get("intent"))
    if not latest_tool:
        latest_tool = _string(pending_intent_name or checkpoint_row.get("last_tool_name"))

    title = topic_title or _string(session_row.get("request_preview")) or _string(session_row.get("session_id")) or "Recent runtime session"
    topic_status = result_status
    display_status = topic_status or (
        "request_done"
        if _string(session_row.get("task_class")).lower() == "autonomous_research" and request_status == "completed"
        else request_status
    )
    request_state_label = (
        "request finished; topic still active"
        if request_status == "completed" and topic_status and topic_status not in {"solved", "completed"}
        else "request finished after the first bounded pass"
        if request_status == "completed" and _string(session_row.get("task_class")).lower() == "autonomous_research"
        else request_status
    )

    timeline = [
        {
            "key": "request",
            "label": "Request",
            "state": "done" if stages["received"] else "waiting",
            "value": "accepted" if stages["received"] else "waiting",
            "detail": _string(session_row.get("request_preview")) or title,
        },
        {
            "key": "approval",
            "label": "Approval",
            "state": "active" if approval_state == "pending" else "done" if approval_state == "cleared" else "waiting",
            "value": approval_state,
            "detail": pending_intent_name or "no approval gate recorded",
        },
        {
            "key": "execution",
            "label": "Execution",
            "state": "done" if latest_tool else "waiting",
            "value": latest_tool or "not started",
            "detail": f"{len(seen_tool_attempts)} unique tool(s), {sum(seen_tool_attempts.values())} total attempt(s)",
        },
        {
            "key": "model_review",
            "label": "Model review",
            "state": _timeline_evidence_state(model_review_state),
            "value": model_review_state,
            "detail": model_review_reason or _model_review_detail(model_review_state),
        },
        {
            "key": "validation",
            "label": "Validation",
            "state": _timeline_evidence_state(validation_state),
            "value": validation_state,
            "detail": "test, lint, or format event state" if validation_state != "not_run" else "no validation event recorded",
        },
        {
            "key": "recovery",
            "label": "Recovery",
            "state": "failed" if "failed" in {rollback_state, restore_state} else "done" if "completed" in {rollback_state, restore_state} else "waiting",
            "value": rollback_state if rollback_state != "not_triggered" else restore_state,
            "detail": _recovery_detail(rollback_state=rollback_state, restore_state=restore_state),
        },
        {
            "key": "result",
            "label": "Result",
            "state": "failed" if display_status == "failed" else "done" if stages["result"] or display_status in {"completed", "request_done", "solved"} else "active" if display_status in {"running", "researching"} else "waiting",
            "value": topic_status or display_status or request_status,
            "detail": post_id or last_message or "no final result yet",
        },
    ]

    return {
        "history_version": "vool.runtime.execution_history.v1",
        "session_id": _string(session_row.get("session_id")),
        "title": title,
        "request_preview": _string(session_row.get("request_preview")),
        "task_class": _string(session_row.get("task_class") or "unknown"),
        "status": display_status,
        "request_status": request_status,
        "request_state_label": request_state_label,
        "topic_status": topic_status,
        "last_message": last_message,
        "updated_at": _string(session_row.get("updated_at")),
        "topic_id": topic_id,
        "claim_id": claim_id,
        "result_status": topic_status or request_status,
        "post_id": post_id,
        "latest_tool": latest_tool,
        "artifact_ids": artifact_ids,
        "packet_artifact_ids": packet_artifact_ids,
        "bundle_artifact_ids": bundle_artifact_ids,
        "candidate_ids": candidate_ids,
        "artifact_rows": artifact_rows,
        "changed_paths": changed_paths,
        "touched_paths": touched_paths,
        "failure_items": failure_items,
        "retry_history": retry_history,
        "stop_reason": stop_reason or ("bounded loop finished" if request_status == "completed" else ""),
        "query_runs": query_runs,
        "query_started_count": query_started_count,
        "query_completed_count": query_completed_count,
        "artifact_count": artifact_count,
        "candidate_count": candidate_count,
        "stages": stages,
        "timeline": timeline,
        "receipt_types": receipt_types,
        "mutating_tools": mutating_tools,
        "bounded_execution": {
            "approval_state": approval_state,
            "rollback_state": rollback_state,
            "restore_state": restore_state,
            "verifier_state": verifier_state,
            "model_review_state": model_review_state,
            "model_review_reason": model_review_reason,
            "validation_state": validation_state,
            "retry_count": sum(_safe_int(item.get("retry_count")) for item in retry_history),
            "failure_count": len(failure_items),
            "tool_attempt_count": sum(seen_tool_attempts.values()),
            "tool_receipt_count": len(normalized_receipts),
            "mutating_tool_count": len(mutating_tools),
            "resume_available": bool(session_row.get("resume_available")),
            "checkpoint_status": checkpoint_status,
            "checkpoint_step_count": checkpoint_step_count,
            "checkpoint_resume_count": checkpoint_resume_count,
            "pending_intent": pending_intent_name,
        },
        "checkpoint": {
            "checkpoint_id": _string(checkpoint_row.get("checkpoint_id") or session_row.get("last_checkpoint_id")),
            "status": checkpoint_status,
            "step_count": checkpoint_step_count,
            "resume_count": checkpoint_resume_count,
            "pending_intent": pending_intent_name,
            "last_tool_name": _string(checkpoint_row.get("last_tool_name")),
        },
    }


def summarize_runtime_surface(sessions: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> dict[str, Any]:
    items = [dict(item) for item in list(sessions or []) if isinstance(item, dict)]
    status_counts: dict[str, int] = {}
    resume_ready_count = 0
    approval_pending_count = 0
    active_execution_count = 0
    verifier_pending_count = 0
    model_review_pending_count = 0
    validation_pending_count = 0
    rollback_pending_count = 0
    recovery_pending_count = 0
    failure_count = 0
    latest_session: dict[str, Any] | None = None
    for item in items:
        history = dict(item.get("execution_history") or {})
        status = _string(history.get("status") or item.get("status") or "unknown").lower() or "unknown"
        request_status = _string(history.get("request_status") or item.get("status") or status).lower() or status
        status_counts[status] = int(status_counts.get(status) or 0) + 1
        bounded = dict(history.get("bounded_execution") or {})
        verifier_state = _string(bounded.get("verifier_state")).lower()
        model_review_state = _string(bounded.get("model_review_state")).lower()
        validation_state = _string(bounded.get("validation_state")).lower()
        rollback_state = _string(bounded.get("rollback_state")).lower()
        restore_state = _string(bounded.get("restore_state")).lower()
        if bool(bounded.get("resume_available") or item.get("resume_available")):
            resume_ready_count += 1
        if _string(bounded.get("approval_state")) == "pending":
            approval_pending_count += 1
        if request_status in {"running", "researching"} or verifier_state == "running" or rollback_state == "running" or restore_state == "running":
            active_execution_count += 1
        if verifier_state == "running":
            verifier_pending_count += 1
        if model_review_state == "running":
            model_review_pending_count += 1
        if validation_state == "running":
            validation_pending_count += 1
        if rollback_state == "running":
            rollback_pending_count += 1
        if rollback_state == "running" or restore_state == "running":
            recovery_pending_count += 1
        if status == "failed" or _safe_int(bounded.get("failure_count")) > 0:
            failure_count += 1
        if latest_session is None:
            latest_session = {
                "session_id": _string(item.get("session_id")),
                "title": _string(history.get("title") or item.get("request_preview") or item.get("last_message")),
                "status": status,
                "latest_tool": _string(history.get("latest_tool")),
                "updated_at": _string(item.get("updated_at")),
            }
    return {
        "history_version": "vool.runtime.surface_summary.v1",
        "session_count": len(items),
        "status_counts": status_counts,
        "active_execution_count": active_execution_count,
        "resume_ready_count": resume_ready_count,
        "session_pending_approval_count": approval_pending_count,
        "approval_pending_count": approval_pending_count,
        "verifier_pending_count": verifier_pending_count,
        "model_review_pending_count": model_review_pending_count,
        "validation_pending_count": validation_pending_count,
        "rollback_pending_count": rollback_pending_count,
        "recovery_pending_count": recovery_pending_count,
        "failure_count": failure_count,
        "latest_session": latest_session or {},
    }


def _append_unique(items: list[str], value: str) -> None:
    normalized = _string(value)
    if normalized and normalized not in items:
        items.append(normalized)


def _normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    flattened = dict(event)
    details = flattened.pop("details", None)
    if isinstance(details, dict):
        for key, value in details.items():
            flattened.setdefault(str(key), value)
    return flattened


def _normalize_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(receipt)
    normalized["arguments"] = dict(normalized.get("arguments") or {})
    normalized["execution"] = dict(normalized.get("execution") or {})
    return normalized


def _paths_from_payload(payload: dict[str, Any]) -> list[str]:
    out: list[str] = []
    _collect_paths(payload, out)
    return out


def _collect_paths(value: Any, out: list[str], *, parent_key: str = "") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = _string(key)
            if _PATH_KEY_RE.search(key_text):
                if isinstance(nested, str):
                    _append_unique(out, nested)
                elif isinstance(nested, (list, tuple)):
                    for item in nested:
                        if isinstance(item, str):
                            _append_unique(out, item)
            _collect_paths(nested, out, parent_key=key_text)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_paths(item, out, parent_key=parent_key)


def _has_receipt_type(event: dict[str, Any], receipt_type: str) -> bool:
    target = _string(receipt_type)
    return any(_string(item) == target for item in list(event.get("receipt_types") or []))


def _typed_evidence_observation(*, event_type: str, event_status: str) -> str:
    if event_type in _FAILED_EVIDENCE_EVENT_TYPES or event_status == "failed":
        return "failed"
    if event_type in _PASSED_EVIDENCE_EVENT_TYPES or event_status in {"completed", "executed", "passed"}:
        return "passed"
    return "running"


def _advance_evidence_state(current: str, observation: str) -> str:
    if current == "failed" or observation == "failed":
        return "failed"
    if observation == "passed":
        return "passed"
    return "running" if current == "not_run" else current


def _advance_review_state(current: str, observation: str) -> str:
    # Review events are an ordered execution-state machine, not a severity rollup. A later retry
    # may legitimately move blocked -> running -> passed, while flagged must remain distinguishable
    # from a reviewer call that never produced a verdict.
    known = {"running", "passed", "flagged", "blocked", "degraded", "runtime_failed"}
    return observation if observation in known else current


def _legacy_verifier_state(model_review_state: str, validation_state: str) -> str:
    # Old consumers receive a lossy aggregate, but infrastructure failure must not masquerade as a
    # negative reviewer judgment even there. Only an actual flagged verdict or failed validation
    # maps to the legacy failed state.
    if validation_state == "failed" or model_review_state == "flagged":
        return "failed"
    if model_review_state in {"blocked", "degraded", "runtime_failed"}:
        return "unavailable"
    states = {model_review_state, validation_state}
    if "running" in states:
        return "running"
    if "passed" in states:
        return "passed"
    return "not_run"


def _timeline_evidence_state(value: str) -> str:
    if value in {"failed", "flagged", "runtime_failed"}:
        return "failed"
    if value == "passed":
        return "done"
    if value == "running":
        return "active"
    return "waiting"


def _model_review_detail(value: str) -> str:
    return {
        "not_run": "no model-review event recorded",
        "running": "reviewer model is still running; no verdict recorded yet",
        "passed": "reviewer model completed and returned a pass verdict",
        "flagged": "reviewer model completed and returned a flagged verdict",
        "blocked": "model review was required but could not start; no verdict recorded",
        "degraded": "model-review path was degraded or unavailable; no verdict recorded",
        "runtime_failed": "reviewer execution failed before a valid verdict was recorded",
    }.get(value, "model-review outcome is unavailable")


def _event_failed(event_type: str, event_status: str, result_status: str) -> bool:
    return "failed" in event_type or event_status == "failed" or result_status == "failed"


def _recovery_detail(*, rollback_state: str, restore_state: str) -> str:
    if rollback_state != "not_triggered":
        return f"rollback {rollback_state}"
    if restore_state != "not_triggered":
        return f"restore {restore_state}"
    return "no recovery path triggered"


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _string(value: Any) -> str:
    return str(value or "").strip()


def _is_mutating_tool_intent(intent: str) -> bool:
    return _string(intent) in _MUTATING_TOOL_INTENTS


__all__ = [
    "build_runtime_execution_history",
    "summarize_runtime_surface",
]
