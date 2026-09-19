from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.execution.constants import machine_process_sort
from core.reasoning_engine import Plan

from .models import OperatorActionIntent, OperatorActionResult
from .when import format_due_time


def _percent(value: Any) -> float:
    """A process row's percentage as a sortable float; unreadable values rank last, never crash."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1.0


def handle_list_tools(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    operator_capability_ledger_fn: Any,
    audit_log_fn: Any,
) -> OperatorActionResult:
    """THE tool navigator: the real catalog by family, availability, reason and count.

    This report used to list only the eight operator-lane tools, which a model
    could only read as "the operator lane is the whole tool surface" (census
    a6c8e3c4, invariant 8). The operator lane's own ledger still feeds the
    operator family's availability truth; the table itself is the whole runtime.
    """
    del intent, session_id
    from core.tool_navigator import catalog_family_table, render_family_table

    ledger = operator_capability_ledger_fn()
    available = [entry for entry in ledger if entry.get("supported")]
    unavailable = [entry for entry in ledger if not entry.get("supported")]
    families = catalog_family_table()
    audit_log_fn(
        "operator_action_list_tools",
        target_id=task_id,
        target_type="task",
        details={
            "available_tool_ids": [str(entry.get("capability_id") or "").strip() for entry in available],
            "families": [
                {
                    "family": row.get("family"),
                    "available_count": row.get("available_count"),
                    "unavailable_count": row.get("unavailable_count"),
                }
                for row in families
            ],
        },
    )
    return OperatorActionResult(
        ok=True,
        status="reported",
        response_text=render_family_table(families),
        details={
            "families": families,
            # Operator-lane detail preserved for callers that read the ledger
            # shape (Activity attribution, capability truth probes).
            "available_tools": available,
            "unavailable_tools": unavailable,
        },
    )


def handle_inspect_processes(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    evaluate_local_action_fn: Any,
    inspect_processes_fn: Any,
    audit_log_fn: Any,
) -> OperatorActionResult:
    del session_id
    gate = evaluate_local_action_fn(
        "inspect_processes",
        destructive=False,
        user_approved=True,
    )
    if gate.mode not in {"execute", "sandbox"}:
        return OperatorActionResult(
            ok=False,
            status="blocked",
            response_text=f"I can't inspect running processes right now: {gate.reason}",
            details={"gate_mode": gate.mode},
        )
    rows = inspect_processes_fn()
    if not rows:
        return OperatorActionResult(
            ok=False,
            status="unavailable",
            response_text="I couldn't inspect running processes on this host.",
            details={},
        )
    # Rank by what was ASKED for. This lane used to emit one fixed "combined CPU and memory
    # pressure" order for every phrasing, so "give me the top processes by memory" was answered,
    # measured live 2026-07-31, with a list headed by a process using 0.6% of memory while the
    # 30.7% one sat fourth. `machine_process_sort` already reads the requested ranking correctly --
    # it was simply never consulted here, because the handler dropped `intent` on its first line.
    sort_key = machine_process_sort(getattr(intent, "raw_text", "") or "")
    field = "cpu_percent" if sort_key == "cpu" else "mem_percent"
    rows = sorted(rows, key=lambda row: _percent(row.get(field)), reverse=True)
    heading = "CPU" if sort_key == "cpu" else "memory"
    lines = [f"Top running processes by {heading}:"]
    for row in rows[:6]:
        # A row the platform could not measure is reported as unknown rather than formatted. This
        # line raised TypeError on a None percentage, which took down the entire process read over
        # one unreadable field.
        cpu = _percent(row.get("cpu_percent"))
        mem = _percent(row.get("mem_percent"))
        cpu_text = f"{cpu:.1f}%" if cpu >= 0 else "unknown"
        mem_text = f"{mem:.1f}%" if mem >= 0 else "unknown"
        lines.append(f"- PID {row['pid']} {row['name']}: CPU {cpu_text} | MEM {mem_text}")
    audit_log_fn(
        "operator_action_inspect_processes",
        target_id=task_id,
        target_type="task",
        details={"top_processes": rows[:6]},
    )
    return OperatorActionResult(
        ok=True,
        status="reported",
        response_text="\n".join(lines),
        details={"top_processes": rows[:6]},
    )


def handle_inspect_services(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    evaluate_local_action_fn: Any,
    inspect_services_fn: Any,
    audit_log_fn: Any,
) -> OperatorActionResult:
    del intent, session_id
    gate = evaluate_local_action_fn(
        "inspect_services",
        destructive=False,
        user_approved=True,
    )
    if gate.mode not in {"execute", "sandbox"}:
        return OperatorActionResult(
            ok=False,
            status="blocked",
            response_text=f"I can't inspect services right now: {gate.reason}",
            details={"gate_mode": gate.mode},
        )
    rows = inspect_services_fn()
    if not rows:
        return OperatorActionResult(
            ok=False,
            status="unavailable",
            response_text="I couldn't inspect services or startup agents on this host.",
            details={},
        )
    lines = ["Visible services or startup agents:"]
    for row in rows[:8]:
        state = str(row.get("state") or "unknown")
        label = str(row.get("name") or "unknown")
        detail = str(row.get("detail") or "").strip()
        if detail:
            lines.append(f"- {label}: {state} | {detail}")
        else:
            lines.append(f"- {label}: {state}")
    audit_log_fn(
        "operator_action_inspect_services",
        target_id=task_id,
        target_type="task",
        details={"services": rows[:8]},
    )
    return OperatorActionResult(
        ok=True,
        status="reported",
        response_text="\n".join(lines),
        details={"services": rows[:8]},
    )


def handle_inspect_disk_usage(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    evaluate_local_action_fn: Any,
    resolve_target_path_fn: Any,
    inspect_storage_fn: Any,
    candidate_cleanup_roots_fn: Any,
    path_size_fn: Any,
    create_pending_action_fn: Any,
    fmt_bytes_fn: Any,
    monotonic_fn: Any,
    audit_log_fn: Any,
) -> OperatorActionResult:
    gate = evaluate_local_action_fn(
        "inspect_disk_usage",
        destructive=False,
        user_approved=True,
        reads_workspace=True,
    )
    if gate.mode not in {"execute", "sandbox"}:
        return OperatorActionResult(
            ok=False,
            status="blocked",
            response_text=f"I can't inspect storage right now: {gate.reason}",
            details={"gate_mode": gate.mode},
        )

    target = resolve_target_path_fn(intent.target_path)
    if not target.exists():
        return OperatorActionResult(
            ok=False,
            status="missing_path",
            response_text=f"I couldn't inspect storage because this path does not exist: {target}",
            details={"target_path": str(target)},
        )

    summary = inspect_storage_fn(target)
    cleanup_roots = candidate_cleanup_roots_fn(intent.target_path)
    preview_total = int(sum(path_size_fn(path, deadline=monotonic_fn() + 0.8)["bytes"] for path in cleanup_roots))
    pending_action_id = None
    if cleanup_roots:
        pending_action_id = create_pending_action_fn(
            session_id=session_id,
            task_id=task_id,
            action_kind="cleanup_temp_files",
            scope={
                "paths": [str(path) for path in cleanup_roots],
                "target_path": str(target),
                "bytes_preview": preview_total,
            },
        )

    lines = [
        f"Storage scan for {target}",
        f"Free space on volume: {fmt_bytes_fn(summary['disk_free_bytes'])} / {fmt_bytes_fn(summary['disk_total_bytes'])}",
    ]
    if summary["top_entries"]:
        lines.append("Largest entries:")
        for row in summary["top_entries"][:6]:
            marker = " (approx)" if row.get("approximate") else ""
            lines.append(f"- {row['name']}: {fmt_bytes_fn(int(row['bytes']))}{marker}")
    else:
        lines.append("No large entries were found in the requested scope.")

    if cleanup_roots:
        lines.append("")
        lines.append(f"Safe temp cleanup preview: {fmt_bytes_fn(preview_total)} across {len(cleanup_roots)} bounded temp root(s).")
        for path in cleanup_roots[:4]:
            lines.append(f"- {path}")
        lines.append("")
        lines.append(
            f"If you want me to execute it, reply with: clean all temp files. Pending action id: {pending_action_id}"
        )

    audit_log_fn(
        "operator_action_inspect_disk_usage",
        target_id=task_id,
        target_type="task",
        details={
            "target_path": str(target),
            "cleanup_roots": [str(path) for path in cleanup_roots],
            "pending_action_id": pending_action_id,
        },
    )

    return OperatorActionResult(
        ok=True,
        status="reported",
        response_text="\n".join(lines),
        details={
            "target_path": str(target),
            "pending_action_id": pending_action_id,
            "cleanup_roots": [str(path) for path in cleanup_roots],
            "top_entries": summary["top_entries"],
        },
    )


def handle_cleanup_temp_files(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    load_pending_action_fn: Any,
    inspect_disk_usage_handler: Any,
    operator_intent_cls: Any,
    evaluate_local_action_fn: Any,
    path_size_fn: Any,
    delete_children_fn: Any,
    mark_action_executed_fn: Any,
    fmt_bytes_fn: Any,
    monotonic_fn: Any,
    audit_log_fn: Any,
) -> OperatorActionResult:
    pending = load_pending_action_fn(session_id=session_id, action_kind="cleanup_temp_files", action_id=intent.action_id)
    if pending is None:
        preview = inspect_disk_usage_handler(
            operator_intent_cls(kind="inspect_disk_usage", target_path=intent.target_path),
            task_id=task_id,
            session_id=session_id,
        )
        preview.response_text += "\nCleanup was not executed because there was no approved pending cleanup plan yet."
        preview.details["requires_user_approval"] = True
        return preview

    gate = evaluate_local_action_fn(
        "cleanup_temp_files",
        destructive=True,
        user_approved=bool(intent.approval_requested),
        writes_workspace=True,
    )
    if gate.requires_user_approval and not intent.approval_requested:
        return OperatorActionResult(
            ok=False,
            status="approval_required",
            response_text=(
                "Temp cleanup is ready but still needs explicit approval. "
                f"Reply with: approve cleanup {pending['action_id']} or just say clean all temp files."
            ),
            details={"action_id": pending["action_id"]},
        )
    if gate.mode not in {"execute", "sandbox"}:
        return OperatorActionResult(
            ok=False,
            status="blocked",
            response_text=f"I can't run temp cleanup right now: {gate.reason}",
            details={"gate_mode": gate.mode},
        )

    scope = json.loads(str(pending.get("scope_json") or "{}"))
    cleanup_paths = [Path(str(value)).expanduser() for value in scope.get("paths") or []]
    before_total = 0
    after_total = 0
    deleted_files = 0
    deleted_dirs = 0
    errors: list[str] = []
    for root in cleanup_paths:
        if not root.exists():
            continue
        before_info = path_size_fn(root, deadline=monotonic_fn() + 1.2)
        before_total += int(before_info["bytes"])
        counts = delete_children_fn(root)
        deleted_files += int(counts["deleted_files"])
        deleted_dirs += int(counts["deleted_dirs"])
        errors.extend([str(item) for item in counts["errors"]])
        after_info = path_size_fn(root, deadline=monotonic_fn() + 1.2)
        after_total += int(after_info["bytes"])

    reclaimed = max(0, before_total - after_total)
    mark_action_executed_fn(
        pending["action_id"],
        result={
            "before_bytes": before_total,
            "after_bytes": after_total,
            "reclaimed_bytes": reclaimed,
            "deleted_files": deleted_files,
            "deleted_dirs": deleted_dirs,
            "errors": errors,
        },
    )

    learned_plan = Plan(
        summary="Verified user-space temp cleanup workflow with approval and before/after verification.",
        abstract_steps=[
            "inspect bounded temp roots",
            "prepare cleanup preview",
            "require explicit user approval",
            "delete child entries inside temp roots",
            "verify reclaimed space after cleanup",
        ],
        confidence=0.93 if reclaimed > 0 and not errors else 0.82,
        risk_flags=[],
        simulation_steps=[],
        safe_actions=[{"action": "cleanup_temp_files", "paths": [str(path) for path in cleanup_paths]}],
        reads_workspace=True,
        writes_workspace=True,
        requests_network=False,
        requests_subprocess=False,
        evidence_sources=["local_operator:cleanup_temp_files"],
    )

    response = (
        f"Temp cleanup finished. Reclaimed {fmt_bytes_fn(reclaimed)} "
        f"by deleting {deleted_files} files and {deleted_dirs} directories."
    )
    if errors:
        response += f" Some entries could not be removed ({len(errors)} issue(s))."

    audit_log_fn(
        "operator_action_cleanup_temp_files",
        target_id=task_id,
        target_type="task",
        details={
            "action_id": pending["action_id"],
            "paths": [str(path) for path in cleanup_paths],
            "reclaimed_bytes": reclaimed,
            "deleted_files": deleted_files,
            "deleted_dirs": deleted_dirs,
            "error_count": len(errors),
        },
    )

    return OperatorActionResult(
        ok=True,
        status="executed",
        response_text=response,
        details={
            "action_id": pending["action_id"],
            "reclaimed_bytes": reclaimed,
            "deleted_files": deleted_files,
            "deleted_dirs": deleted_dirs,
            "error_count": len(errors),
        },
        learned_plan=learned_plan,
    )


def handle_move_path(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    load_pending_action_fn: Any,
    parse_move_request_fn: Any,
    validate_move_scope_fn: Any,
    resolved_move_target_fn: Any,
    create_pending_action_fn: Any,
    mark_action_executed_fn: Any,
    evaluate_local_action_fn: Any,
    move_fn: Any,
    audit_log_fn: Any,
) -> OperatorActionResult:
    pending = load_pending_action_fn(session_id=session_id, action_kind="move_path", action_id=intent.action_id)
    if pending is None:
        parsed = parse_move_request_fn(
            intent.raw_text,
            fallback_source=intent.target_path,
            fallback_destination=intent.destination_path,
        )
        if not parsed:
            return OperatorActionResult(
                ok=False,
                status="invalid_request",
                response_text=(
                    "I can move or archive a bounded local path, but I need a quoted source path. "
                    'Use a format like: move "/path/to/source" to "/path/to/archive" '
                    'or archive "/path/to/source"'
                ),
                details={},
            )
        source = Path(str(parsed["source_path"])).expanduser()
        destination_dir = Path(str(parsed["destination_dir"])).expanduser()
        validation_error = validate_move_scope_fn(source, destination_dir)
        if validation_error:
            return OperatorActionResult(
                ok=False,
                status="blocked",
                response_text=validation_error,
                details={
                    "source_path": str(source),
                    "destination_dir": str(destination_dir),
                },
            )
        final_path = resolved_move_target_fn(source, destination_dir)
        if final_path.exists():
            return OperatorActionResult(
                ok=False,
                status="conflict",
                response_text=f"I won't move {source} because the destination already exists: {final_path}",
                details={
                    "source_path": str(source),
                    "destination_path": str(final_path),
                },
            )
        action_id = create_pending_action_fn(
            session_id=session_id,
            task_id=task_id,
            action_kind="move_path",
            scope={
                "source_path": str(source),
                "destination_dir": str(destination_dir),
                "destination_path": str(final_path),
            },
        )
        return OperatorActionResult(
            ok=True,
            status="approval_required",
            response_text=(
                f"Move preview ready.\n"
                f"- Source: {source}\n"
                f"- Destination: {final_path}\n\n"
                f"Reply with: approve move {action_id}"
            ),
            details={
                "action_id": action_id,
                "source_path": str(source),
                "destination_dir": str(destination_dir),
                "destination_path": str(final_path),
            },
        )

    gate = evaluate_local_action_fn(
        "move_path",
        destructive=True,
        user_approved=bool(intent.approval_requested),
        writes_workspace=True,
    )
    if gate.requires_user_approval and not intent.approval_requested:
        return OperatorActionResult(
            ok=False,
            status="approval_required",
            response_text=(
                "The move/archive action is ready but still needs explicit approval. "
                f"Reply with: approve move {pending['action_id']}"
            ),
            details={"action_id": pending["action_id"]},
        )
    if gate.mode not in {"execute", "sandbox"}:
        return OperatorActionResult(
            ok=False,
            status="blocked",
            response_text=f"I can't move that path right now: {gate.reason}",
            details={"gate_mode": gate.mode},
        )

    scope = json.loads(str(pending.get("scope_json") or "{}"))
    source = Path(str(scope.get("source_path") or "")).expanduser()
    destination_dir = Path(str(scope.get("destination_dir") or "")).expanduser()
    final_path = Path(str(scope.get("destination_path") or "")).expanduser()
    validation_error = validate_move_scope_fn(source, destination_dir)
    if validation_error:
        return OperatorActionResult(
            ok=False,
            status="blocked",
            response_text=validation_error,
            details={
                "action_id": pending["action_id"],
                "source_path": str(source),
                "destination_dir": str(destination_dir),
            },
        )
    if not source.exists():
        return OperatorActionResult(
            ok=False,
            status="missing_path",
            response_text=f"I can't move this path because it no longer exists: {source}",
            details={"action_id": pending["action_id"], "source_path": str(source)},
        )
    destination_dir.mkdir(parents=True, exist_ok=True)
    if final_path.exists():
        return OperatorActionResult(
            ok=False,
            status="conflict",
            response_text=f"I won't overwrite an existing destination: {final_path}",
            details={"action_id": pending["action_id"], "destination_path": str(final_path)},
        )
    try:
        move_fn(str(source), str(final_path))
    except Exception as exc:
        return OperatorActionResult(
            ok=False,
            status="execution_failed",
            response_text=f"I couldn't move {source} to {final_path}: {exc}",
            details={
                "action_id": pending["action_id"],
                "source_path": str(source),
                "destination_path": str(final_path),
                "error": str(exc),
            },
        )
    verified = final_path.exists() and not source.exists()
    mark_action_executed_fn(
        pending["action_id"],
        result={
            "source_path": str(source),
            "destination_path": str(final_path),
            "verified": verified,
        },
    )
    learned_plan = Plan(
        summary="Verified bounded file move/archive workflow with preview, approval, relocation, and post-move verification.",
        abstract_steps=[
            "validate the requested source and destination paths",
            "prepare a move preview",
            "require explicit user approval",
            "move the source into the approved destination",
            "verify the new path exists and the original path is gone",
        ],
        confidence=0.9 if verified else 0.72,
        risk_flags=[],
        simulation_steps=[],
        safe_actions=[{"action": "move_path", "source_path": str(source), "destination_path": str(final_path)}],
        reads_workspace=True,
        writes_workspace=True,
        requests_network=False,
        requests_subprocess=False,
        evidence_sources=["local_operator:move_path"],
    )
    audit_log_fn(
        "operator_action_move_path",
        target_id=task_id,
        target_type="task",
        details={
            "action_id": pending["action_id"],
            "source_path": str(source),
            "destination_path": str(final_path),
            "verified": verified,
        },
    )
    return OperatorActionResult(
        ok=True,
        status="executed",
        response_text=f"Move finished. {source.name} is now at {final_path}",
        details={
            "action_id": pending["action_id"],
            "source_path": str(source),
            "destination_path": str(final_path),
            "verified": verified,
        },
        learned_plan=learned_plan,
    )


def handle_schedule_calendar_event(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    load_pending_action_fn: Any,
    parse_calendar_request_fn: Any,
    create_pending_action_fn: Any,
    evaluate_local_action_fn: Any,
    operator_intent_cls: Any,
    render_ics_fn: Any,
    mark_action_executed_fn: Any,
    data_path_fn: Any,
    audit_log_fn: Any,
) -> OperatorActionResult:
    pending = load_pending_action_fn(
        session_id=session_id,
        action_kind="schedule_calendar_event",
        action_id=intent.action_id,
    )
    if pending is None:
        parsed = parse_calendar_request_fn(intent.raw_text)
        if not parsed:
            return OperatorActionResult(
                ok=False,
                status="invalid_request",
                response_text=(
                    "I can schedule a meeting, but I need a title and time. "
                    'Use a format like: schedule a meeting "Ops Sync" on 2026-03-08 15:30 for 45m'
                ),
                details={},
            )
        action_id = create_pending_action_fn(
            session_id=session_id,
            task_id=task_id,
            action_kind="schedule_calendar_event",
            scope=parsed,
        )
        gate = evaluate_local_action_fn(
            "schedule_calendar_event",
            destructive=True,
            user_approved=bool(intent.approval_requested),
            writes_workspace=True,
        )
        if gate.mode in {"execute", "sandbox"} and not gate.requires_user_approval:
            return handle_schedule_calendar_event(
                operator_intent_cls(
                    kind="schedule_calendar_event",
                    approval_requested=True,
                    action_id=action_id,
                    raw_text=intent.raw_text,
                ),
                task_id=task_id,
                session_id=session_id,
                load_pending_action_fn=load_pending_action_fn,
                parse_calendar_request_fn=parse_calendar_request_fn,
                create_pending_action_fn=create_pending_action_fn,
                evaluate_local_action_fn=evaluate_local_action_fn,
                operator_intent_cls=operator_intent_cls,
                render_ics_fn=render_ics_fn,
                mark_action_executed_fn=mark_action_executed_fn,
                data_path_fn=data_path_fn,
                audit_log_fn=audit_log_fn,
            )
        return OperatorActionResult(
            ok=True,
            status="approval_required",
            response_text=(
                f"Meeting preview ready.\n"
                f"- Title: {parsed['title']}\n"
                f"- Starts: {parsed['start_iso']}\n"
                f"- Ends: {parsed['end_iso']}\n"
                f"- Calendar outbox: {parsed['outbox_dir']}\n\n"
                f"Reply with: approve calendar {action_id}"
            ),
            details={"action_id": action_id, **parsed},
        )

    gate = evaluate_local_action_fn(
        "schedule_calendar_event",
        destructive=True,
        user_approved=bool(intent.approval_requested),
        writes_workspace=True,
    )
    if gate.requires_user_approval and not intent.approval_requested:
        return OperatorActionResult(
            ok=False,
            status="approval_required",
            response_text=(
                "Calendar event is ready but still needs explicit approval. "
                f"Reply with: approve calendar {pending['action_id']}"
            ),
            details={"action_id": pending["action_id"]},
        )
    if gate.mode not in {"execute", "sandbox"}:
        return OperatorActionResult(
            ok=False,
            status="blocked",
            response_text=f"I can't create that calendar event right now: {gate.reason}",
            details={"gate_mode": gate.mode},
        )

    scope = json.loads(str(pending.get("scope_json") or "{}"))
    title = str(scope.get("title") or "VOOL Meeting")
    start_iso = str(scope.get("start_iso") or "")
    end_iso = str(scope.get("end_iso") or "")
    outbox_dir = Path(str(scope.get("outbox_dir") or data_path_fn("calendar_outbox"))).expanduser()
    outbox_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", title).strip("-").lower() or "meeting"
    filename = f"{slug[:80]}-{pending['action_id']}.ics"
    ics_path = outbox_dir / filename
    try:
        with ics_path.open('x', encoding='utf-8') as handle:
            handle.write(render_ics_fn(title=title, start_iso=start_iso, end_iso=end_iso))
    except OSError as exc:
        return OperatorActionResult(ok=False, status='artifact_error',
                                    response_text=f'The calendar draft could not be created: {exc}. Existing files were not replaced.',
                                    details={'action_id': pending['action_id']})
    mark_action_executed_fn(
        pending["action_id"],
        result={
            "title": title,
            "start_iso": start_iso,
            "end_iso": end_iso,
            "ics_path": str(ics_path),
        },
    )
    learned_plan = Plan(
        summary="Verified calendar-event creation workflow with preview, policy-aware approval, .ics emission, and path verification.",
        abstract_steps=[
            "parse requested meeting title and time",
            "prepare calendar preview",
            "request approval only when the current autonomy mode requires it",
            "emit .ics file into calendar outbox",
            "verify event artifact path exists",
        ],
        confidence=0.91,
        risk_flags=[],
        simulation_steps=[],
        safe_actions=[{"action": "schedule_calendar_event", "title": title, "ics_path": str(ics_path)}],
        reads_workspace=False,
        writes_workspace=True,
        requests_network=False,
        requests_subprocess=False,
        evidence_sources=["local_operator:schedule_calendar_event"],
    )
    audit_log_fn(
        "operator_action_schedule_calendar_event",
        target_id=task_id,
        target_type="task",
        details={"action_id": pending["action_id"], "ics_path": str(ics_path), "title": title},
    )
    return OperatorActionResult(
        ok=True,
        status="executed",
        response_text=f"Calendar event created. ICS written to {ics_path}",
        details={"action_id": pending["action_id"], "ics_path": str(ics_path), "title": title},
        learned_plan=learned_plan,
    )


# ---------------------------------------------------------------------------
# Durable reminders + calendar draft lifecycle (scheduling vertical)
# ---------------------------------------------------------------------------


def _learned_plan(summary: str, steps: list[str], *, confidence: float = 0.9) -> Plan:
    return Plan(
        summary=summary,
        abstract_steps=steps,
        confidence=confidence,
        risk_flags=[],
        simulation_steps=[],
        safe_actions=[],
        reads_workspace=False,
        writes_workspace=False,
        requests_network=False,
        requests_subprocess=False,
        evidence_sources=["local_operator:scheduling"],
    )


def handle_schedule_reminder(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    parse_when_fn: Any,
    schedule_reminder_fn: Any,
    audit_log_fn: Any,
) -> OperatorActionResult:
    when = parse_when_fn(intent.raw_text)
    if not when.ok:
        return OperatorActionResult(
            ok=False,
            status="invalid_request",
            response_text=when.problem,
            details={"kind": "schedule_reminder", "problem": when.problem},
        )
    note = _reminder_note_from_text(intent.raw_text)
    repeat_every = _reminder_repeat_from_text(intent.raw_text)
    record = schedule_reminder_fn(
        session_id=session_id,
        task_id=task_id,
        note=note,
        due_at_utc=when.due_at_utc,
        tz_name=when.tz_name,
        due_wall=when.due_wall,
        **({"repeat_every": repeat_every} if repeat_every else {}),
    )
    audit_log_fn(
        "operator_action_schedule_reminder",
        target_id=task_id,
        target_type="task",
        details={"reminder_id": record["reminder_id"], "due_at_utc": when.due_at_utc, "note": note[:200]},
    )
    wall = format_due_time(when.due_at_utc, when.tz_name, when.due_wall)
    repeat_line = {
        "daily": " It repeats every day at the same local time; each occurrence is its own reminder you can cancel.",
        "weekly": " It repeats every week at the same local time; each occurrence is its own reminder you can cancel.",
    }.get(repeat_every, "")
    return OperatorActionResult(
        ok=True,
        status="executed",
        response_text=(
            f"Reminder scheduled: {note}. Due {wall}. "
            "It will appear in this chat while VOOL is running; overdue reminders appear next time it runs. "
            f"Cancel it any time before then (reminder id {record['reminder_id'][:8]})."
            + repeat_line
        ),
        details={
            "reminder_id": record["reminder_id"],
            "due_at_utc": when.due_at_utc,
            "due_wall": when.due_wall,
            "tz_name": when.tz_name,
            "note": note,
            "effect": "durable_local_reminder_delivered_in_session",
        },
        learned_plan=_learned_plan(
            "Verified reminder scheduling workflow: timezone-aware due-time parse, durable store row, cancellation path.",
            ["parse the requested due time with named-timezone support", "store the reminder durably", "report the exact due instant and the delivery effect"],
        ),
    )


def handle_list_reminders(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    list_reminders_fn: Any,
    now_fn: Any,
    audit_log_fn: Any,
) -> OperatorActionResult:
    del intent
    rows = list_reminders_fn(session_id=session_id, include_delivered=False)
    now = now_fn()
    if not rows:
        return OperatorActionResult(
            ok=True,
            status="reported",
            response_text="You have no pending reminders in this chat.",
            details={"count": 0},
        )
    lines = []
    for row in rows:
        due = str(row.get("due_at_utc") or "")
        overdue = " (OVERDUE)" if due and due <= now else ""
        wall = format_due_time(due, str(row.get("tz_name") or ""), str(row.get("due_wall") or ""))
        status = str(row.get("status") or "")
        flag = " [delivery uncertain -- an attempt was interrupted]" if status == "delivery_uncertain" else overdue
        lines.append(f"- {row.get('note')} — due {wall}{flag} (id {str(row.get('reminder_id'))[:8]})")
    audit_log_fn(
        "operator_action_list_reminders",
        target_id=task_id,
        target_type="task",
        details={"count": len(rows)},
    )
    return OperatorActionResult(
        ok=True,
        status="reported",
        response_text="Pending reminders:\n" + "\n".join(lines),
        details={"count": len(rows), "reminders": rows},
    )


def handle_cancel_reminder(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    load_reminder_fn: Any,
    list_reminders_fn: Any,
    cancel_reminder_fn: Any,
    audit_log_fn: Any,
) -> OperatorActionResult:
    target = _select_reminder_target(
        intent,
        session_id=session_id,
        load_reminder_fn=load_reminder_fn,
        list_reminders_fn=list_reminders_fn,
    )
    if target is None:
        return OperatorActionResult(
            ok=False,
            status="invalid_request",
            response_text="I could not find a pending reminder for that unambiguously. Say 'list my reminders', then specify the reminder id.",
            details={"kind": "cancel_reminder"},
        )
    outcome = cancel_reminder_fn(str(target.get("reminder_id")))
    audit_log_fn(
        "operator_action_cancel_reminder",
        target_id=task_id,
        target_type="task",
        details={"reminder_id": str(target.get("reminder_id")), "outcome": outcome.get("status")},
    )
    if not outcome.get("ok"):
        if outcome.get("status") == "already_delivered":
            return OperatorActionResult(
                ok=False,
                status="invalid_request",
                response_text="That reminder was already delivered, so there is nothing left to cancel.",
                details=outcome,
            )
        return OperatorActionResult(
            ok=False,
            status="failed",
            response_text=f"I could not cancel that reminder: {outcome.get('reason') or outcome.get('status')}.",
            details=outcome,
        )
    return OperatorActionResult(
        ok=True,
        status="executed",
        response_text=f"Reminder cancelled: {target.get('note')} will not be delivered.",
        details={**outcome, "reminder_id": str(target.get("reminder_id")), "note": target.get("note")},
        learned_plan=_learned_plan(
            "Verified reminder cancellation workflow: resolve target by id or most recent, CAS the store row, verify the transition.",
            ["resolve which reminder the user means", "cancel it in the store (idempotent)", "report what will no longer happen"],
        ),
    )


def handle_move_reminder(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    parse_when_fn: Any,
    load_reminder_fn: Any,
    list_reminders_fn: Any,
    move_reminder_fn: Any,
    audit_log_fn: Any,
) -> OperatorActionResult:
    when = parse_when_fn(intent.raw_text)
    if not when.ok:
        return OperatorActionResult(
            ok=False,
            status="invalid_request",
            response_text=when.problem,
            details={"kind": "move_reminder", "problem": when.problem},
        )
    target = _select_reminder_target(
        intent,
        session_id=session_id,
        load_reminder_fn=load_reminder_fn,
        list_reminders_fn=list_reminders_fn,
    )
    if target is None:
        return OperatorActionResult(
            ok=False,
            status="invalid_request",
            response_text="I could not find a pending reminder to move unambiguously. Say 'list my reminders', then specify the reminder id.",
            details={"kind": "move_reminder"},
        )
    outcome = move_reminder_fn(
        str(target.get("reminder_id")),
        due_at_utc=when.due_at_utc,
        tz_name=when.tz_name,
        due_wall=when.due_wall,
    )
    if not outcome.get("ok"):
        return OperatorActionResult(
            ok=False,
            status="failed",
            response_text=f"I could not move that reminder: {outcome.get('reason') or outcome.get('status')}.",
            details=outcome,
        )
    audit_log_fn(
        "operator_action_move_reminder",
        target_id=task_id,
        target_type="task",
        details={"reminder_id": str(target.get("reminder_id")), "new_due_at_utc": when.due_at_utc},
    )
    wall = format_due_time(when.due_at_utc, when.tz_name, when.due_wall)
    return OperatorActionResult(
        ok=True,
        status="executed",
        response_text=f"Reminder moved: {target.get('note')} is now due {wall}.",
        details={**outcome, "reminder_id": str(target.get("reminder_id")), "note": target.get("note")},
        learned_plan=_learned_plan(
            "Verified reminder move workflow: parse the new due time, CAS the store row back to scheduled.",
            ["parse the new due time with named-timezone support", "move the reminder (only while it is still cancellable)", "report the new exact due instant"],
        ),
    )


_REMINDER_NOTE_RE = re.compile(
    r"\bremind\s+me\s+(?:(?:here|in\s+this\s+chat)\s+(?=(?:to|about|that)\s+))?"
    r"(?:to\s+|about\s+|that\s+|on\s+|at\s+)?(?P<note>.+)$", re.IGNORECASE | re.DOTALL
)
_WHEN_TAIL_RE = re.compile(
    r"\s+(?:at|on|in|by)\s+("
    r"(?:20\d{2}-\d{2}-\d{2})(?:[ T]\d{1,2}:\d{2})?"
    r"|(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b.*"
    r"|\d+(?:\.\d+)?\s*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|h|days?|d|weeks?|w)\b.*"
    r"|(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b.*"
    r")$",
    re.IGNORECASE | re.DOTALL,
)


def _reminder_note_from_text(text: str) -> str:
    """The reminder's SUBJECT: the words after 'remind me to/about', with the trailing
    when-clause stripped so the note reads as the thing to do, not the schedule."""
    raw = str(text or "").strip()
    match = _REMINDER_NOTE_RE.search(raw)
    note = (match.group("note") if match else raw).strip()
    tail = _WHEN_TAIL_RE.search(note)
    if tail:
        note = note[: tail.start()].strip()
    note = note.rstrip(".,;:")
    return (note or raw)[:400]


def _reminder_repeat_from_text(text: str) -> str:
    """The basic repeat rule a request names, or ''. Only the two supported shapes claim a word."""
    import re as _re

    lowered = str(text or "").lower()
    if _re.search(r"\bevery day\b|\bdaily\b|\beach day\b", lowered):
        return "daily"
    if _re.search(r"\bevery week\b|\bweekly\b|\beach week\b", lowered):
        return "weekly"
    return ""


def _select_reminder_target(
    intent: OperatorActionIntent,
    *,
    session_id: str,
    load_reminder_fn: Any,
    list_reminders_fn: Any,
) -> dict[str, Any] | None:
    if intent.action_id:
        row = load_reminder_fn(intent.action_id)
        if row is not None and str(row.get("session_id")) == session_id:
            return row
        # The UI displays short ids. Resolve only a unique prefix in this session;
        # a missing/foreign explicit target must never fall back to another reminder.
        rows = list_reminders_fn(session_id=session_id, include_delivered=False)
        matches = [row for row in rows if str(row.get("reminder_id", "")).startswith(intent.action_id)]
        return matches[0] if len(matches) == 1 else None
    rows = list_reminders_fn(session_id=session_id, include_delivered=False)
    pending = [row for row in rows if str(row.get("status")) in {"scheduled", "delivery_uncertain"}]
    if len(pending) == 1:
        return pending[0]
    return None


def handle_complete_reminder(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    load_reminder_fn: Any,
    list_reminders_fn: Any,
    complete_reminder_fn: Any,
    audit_log_fn: Any,
) -> OperatorActionResult:
    """Complete a reminder: pending or already delivered, by id or the one live reminder."""
    rows = list_reminders_fn(session_id=session_id, include_delivered=True)
    completable = [row for row in rows if str(row.get("status")) in
                   {"scheduled", "delivery_uncertain", "delivered", "dispatching"}]
    target = None
    if intent.action_id:
        target = next((row for row in completable if str(row.get("reminder_id")) == intent.action_id
                       or str(row.get("reminder_id", "")).startswith(intent.action_id)), None)
    elif len(completable) == 1:
        target = completable[0]
    elif completable:
        delivered_only = [row for row in completable if str(row.get("status")) == "delivered"]
        if len(delivered_only) == 1:
            target = delivered_only[0]
    if target is None:
        return OperatorActionResult(
            ok=False,
            status="invalid_request",
            response_text="I could not find a reminder to complete unambiguously. Say 'list my reminders', then specify the reminder id.",
            details={"kind": "complete_reminder"},
        )
    outcome = complete_reminder_fn(str(target.get("reminder_id")))
    audit_log_fn(
        "operator_action_complete_reminder",
        target_id=task_id,
        target_type="task",
        details={"reminder_id": str(target.get("reminder_id")), "outcome": outcome.get("status")},
    )
    if not outcome.get("ok"):
        return OperatorActionResult(
            ok=False,
            status="failed",
            response_text=f"I could not complete that reminder: {outcome.get('reason') or outcome.get('status')}.",
            details=outcome,
        )
    note = outcome.get("note") or target.get("note")
    follow_up = ""
    payload_repeat = False
    try:
        import json as _json

        payload_repeat = bool(_json.loads(str(target.get("payload_json") or "{}")).get("repeat_every"))
    except Exception:
        payload_repeat = False
    if payload_repeat:
        follow_up = " The next occurrence of this repeating reminder is untouched; cancel that one to stop the series."
    return OperatorActionResult(
        ok=True,
        status="executed",
        response_text=f"Reminder completed: {note}. It stays in history as done, not deleted.{follow_up}",
        details={**outcome, "reminder_id": str(target.get("reminder_id")), "note": note},
    )


def handle_edit_reminder(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    load_reminder_fn: Any,
    list_reminders_fn: Any,
    edit_reminder_fn: Any,
    audit_log_fn: Any,
) -> OperatorActionResult:
    new_text = str(intent.target_label or "").strip()
    if not new_text:
        return OperatorActionResult(
            ok=False,
            status="invalid_request",
            response_text="What should the reminder say instead? For example: edit my reminder to say \"stretch break\".",
            details={"kind": "edit_reminder"},
        )
    target = _select_reminder_target(
        intent,
        session_id=session_id,
        load_reminder_fn=load_reminder_fn,
        list_reminders_fn=list_reminders_fn,
    )
    if target is None:
        return OperatorActionResult(
            ok=False,
            status="invalid_request",
            response_text="I could not find a pending reminder for that unambiguously. Say 'list my reminders', then specify the reminder id.",
            details={"kind": "edit_reminder"},
        )
    outcome = edit_reminder_fn(str(target.get("reminder_id")), note=new_text)
    audit_log_fn(
        "operator_action_edit_reminder",
        target_id=task_id,
        target_type="task",
        details={"reminder_id": str(target.get("reminder_id")), "outcome": outcome.get("status")},
    )
    if not outcome.get("ok"):
        return OperatorActionResult(
            ok=False,
            status="failed",
            response_text=f"I could not edit that reminder: {outcome.get('reason') or outcome.get('status')}.",
            details=outcome,
        )
    return OperatorActionResult(
        ok=True,
        status="executed",
        response_text=f"Reminder updated. It now says: {new_text} (time unchanged; ask to move it for that).",
        details={**outcome, "reminder_id": str(target.get("reminder_id")), "note": new_text},
    )


def handle_cancel_calendar_event(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    load_executed_calendar_fn: Any,
    cancel_calendar_artifact_fn: Any,
    audit_log_fn: Any,
) -> OperatorActionResult:
    """Cancel a CALENDAR DRAFT (the .ics artifact this runtime writes). The honest effect
    is local: the artifact is removed from the outbox and the action row is closed as
    cancelled. No external calendar service is contacted -- there is none wired."""
    record = load_executed_calendar_fn(session_id=session_id, action_id=intent.action_id, target_label=intent.target_label)
    if record is None or record.get("selection_error"):
        return OperatorActionResult(
            ok=False,
            status="invalid_request",
            response_text=(record or {}).get("selection_error") or "I don't have a calendar draft matching that target in this chat to cancel. Give the exact draft title or ID; only local drafts can be changed here.",
            details={"kind": "cancel_calendar_event"},
        )
    outcome = cancel_calendar_artifact_fn(record)
    audit_log_fn(
        "operator_action_cancel_calendar_event",
        target_id=task_id,
        target_type="task",
        details={"action_id": str(record.get("action_id")), "outcome": str(outcome.get("status"))},
    )
    if not outcome.get("ok"):
        return OperatorActionResult(
            ok=False,
            status="failed",
            response_text=f"I could not cancel that calendar draft: {outcome.get('reason') or outcome.get('status')}.",
            details=outcome,
        )
    return OperatorActionResult(
        ok=True,
        status="executed",
        response_text=(
            f"Calendar draft cancelled ({record.get('title')}). The .ics file was removed from the local outbox; "
            "nothing was ever sent to an external calendar service."
        ),
        details={**outcome, "title": record.get("title")},
        learned_plan=_learned_plan(
            "Verified calendar-draft cancellation workflow: locate the executed draft, remove the local artifact, close the action row.",
            ["resolve one explicitly identified or unambiguous calendar draft in this chat", "remove the local .ics artifact", "report the exact local effect"],
        ),
    )


def handle_move_calendar_event(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    parse_when_fn: Any,
    load_executed_calendar_fn: Any,
    move_calendar_artifact_fn: Any,
    audit_log_fn: Any,
) -> OperatorActionResult:
    when = parse_when_fn(intent.raw_text)
    if not when.ok:
        return OperatorActionResult(
            ok=False,
            status="invalid_request",
            response_text=when.problem,
            details={"kind": "move_calendar_event", "problem": when.problem},
        )
    record = load_executed_calendar_fn(session_id=session_id, action_id=intent.action_id, target_label=intent.target_label)
    if record is None or record.get("selection_error"):
        return OperatorActionResult(
            ok=False,
            status="invalid_request",
            response_text=(record or {}).get("selection_error") or "I don't have a calendar draft matching that target in this chat to move. Give the exact draft title or ID; only local drafts can be changed here.",
            details={"kind": "move_calendar_event"},
        )
    outcome = move_calendar_artifact_fn(record, when)
    audit_log_fn(
        "operator_action_move_calendar_event",
        target_id=task_id,
        target_type="task",
        details={"action_id": str(record.get("action_id")), "new_start": when.due_at_utc},
    )
    if not outcome.get("ok"):
        return OperatorActionResult(
            ok=False,
            status="failed",
            response_text=f"I could not move that calendar draft: {outcome.get('reason') or outcome.get('status')}.",
            details=outcome,
        )
    wall = when.due_wall or when.due_at_utc
    zone_label = f" ({when.tz_name})" if when.tz_name else ""
    return OperatorActionResult(
        ok=True,
        status="executed",
        response_text=(
            f"Calendar draft moved: {record.get('title')} now starts {wall}{zone_label}. "
            f"Updated .ics at {outcome.get('ics_path')}"
        ),
        details={**outcome, "title": record.get("title")},
        learned_plan=_learned_plan(
            "Verified calendar-draft move workflow: parse the new time with zone support, rewrite the local artifact, keep one draft.",
            ["parse the new start time with named-timezone support", "rewrite the local .ics draft in place", "report the new exact start"],
        ),
    )
