from __future__ import annotations

from typing import Any

from core.runtime_task_events import emit_runtime_event

from .task_envelope import TaskEnvelopeV1


def build_envelope_event_context(
    *,
    source_context: dict[str, Any] | None,
    session_id: str | None,
) -> dict[str, Any] | None:
    base = dict(source_context or {})
    runtime_session_id = str(base.get("runtime_session_id") or base.get("session_id") or session_id or "").strip()
    if not runtime_session_id:
        return None
    base["runtime_session_id"] = runtime_session_id
    return base


def emit_task_envelope_event(
    event_context: dict[str, Any] | None,
    envelope: TaskEnvelopeV1,
    *,
    event_type: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    if event_context is None:
        return
    payload = {
        "task_id": envelope.task_id,
        "parent_task_id": envelope.parent_task_id,
        "task_role": envelope.role,
        "task_goal": envelope.goal,
        "task_class": str(envelope.inputs.get("task_class") or "").strip(),
        "privacy_class": envelope.privacy_class,
        "request_preview": envelope.goal,
    }
    payload.update(dict(details or {}))
    emit_runtime_event(
        event_context,
        event_type=event_type,
        message=message,
        details=payload,
    )


def receipt_type_list(receipts: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in list(receipts or []):
        receipt_type = str((item or {}).get("receipt_type") or "").strip()
        if not receipt_type or receipt_type in seen:
            continue
        seen.add(receipt_type)
        out.append(receipt_type)
    return out
