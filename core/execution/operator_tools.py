from __future__ import annotations

from typing import Any

from core.execution.models import ToolIntentExecution, _tool_observation
from core.local_operator_actions import OperatorActionIntent, dispatch_operator_action
from core.operator.models import operator_step_executed
from core.tool_argument_aliases import side_effect_class_for_intent


def execute_operator_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    task_id: str,
    session_id: str,
    dispatch_operator_action_fn=dispatch_operator_action,
    source_context: dict[str, Any] | None = None,
) -> ToolIntentExecution:
    operator_kind = intent.split(".", 1)[1]
    operator_intent = build_operator_action_intent(operator_kind, arguments)
    dispatch = dispatch_operator_action_fn(
        operator_intent,
        task_id=task_id,
        session_id=session_id,
    )
    # The turn-keyed execution TRUTH: an operator action that ran is a tool that ran, so
    # the signed honesty ledger and the completion-claim guards must read it from the one
    # store built for that question. Without this fact a mixed-demand turn whose
    # note/calendar unit really executed still answers with the honest-looking "cannot
    # verify" replacement, because no other path records the operator lane's execution
    # against the turn. Best-effort, never raises, and no fact without a real turn
    # identity (resolve_turn_key refuses to invent one).
    try:
        from core.execution_truth import KIND_TOOL, record_execution, resolve_turn_key

        turn_key = resolve_turn_key(dict(source_context or {}), None)
        if turn_key:
            record_execution(
                session_id=str(session_id or ""),
                turn_key=turn_key,
                kind=KIND_TOOL,
                name=str(intent or ""),
                ok=operator_step_executed(dispatch),
                status=str(dispatch.status or ""),
                dedupe=f"{intent}:{dispatch.status}",
            )
    except Exception:
        pass
    if dispatch.status == "reported" and side_effect_class_for_intent(intent) == "read_only":
        # A completed catalog/inspection report is evidence, not a proposed effect.
        # Calling it a preview creates a pending approval with nothing to approve.
        mode = "tool_executed" if dispatch.ok else "tool_failed"
    elif dispatch.status == "executed":
        mode = "tool_executed"
    elif dispatch.status in {"reported", "approval_required"}:
        mode = "tool_preview"
    else:
        mode = "tool_failed"
    return ToolIntentExecution(
        handled=True,
        ok=bool(dispatch.ok),
        status=str(dispatch.status),
        response_text=str(dispatch.response_text or ""),
        mode=mode,
        tool_name=intent,
        details={
            **dict(dispatch.details or {}),
            "observation": _tool_observation(
                intent=intent,
                tool_surface="local_operator",
                ok=bool(dispatch.ok),
                status=str(dispatch.status),
                details=dict(dispatch.details or {}),
                response_preview=str(dispatch.response_text or "")[:280],
            ),
        },
        learned_plan=dispatch.learned_plan,
    )


def build_operator_action_intent(operator_kind: str, arguments: dict[str, Any]) -> OperatorActionIntent:
    target_path = str(arguments.get("target_path") or arguments.get("path") or "").strip() or None
    destination_path = str(arguments.get("destination_path") or arguments.get("destination_dir") or "").strip() or None
    raw_text = ""
    if operator_kind == "move_path":
        source = str(arguments.get("source_path") or target_path or "").strip()
        dest = str(destination_path or "").strip()
        raw_text = f'move "{source}" to "{dest}"'.strip()
        target_path = source or None
    elif operator_kind == "schedule_calendar_event":
        title = str(arguments.get("title") or "VOOL Meeting").strip()
        start_iso = str(arguments.get("start_iso") or "").strip()
        duration_minutes = max(15, int(arguments.get("duration_minutes") or 30))
        raw_text = f'schedule a meeting "{title}" on {start_iso} for {duration_minutes}m'.strip()
    elif operator_kind == "cleanup_temp_files" and target_path:
        raw_text = f'clean temp files in "{target_path}"'
    elif operator_kind == "inspect_disk_usage" and target_path:
        raw_text = f'find disk bloat in "{target_path}"'
    return OperatorActionIntent(
        kind=operator_kind,
        target_path=target_path,
        destination_path=destination_path,
        approval_requested=False,
        action_id=str(arguments.get("action_id") or "").strip() or None,
        raw_text=raw_text,
    )
