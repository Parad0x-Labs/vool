from __future__ import annotations

from typing import Any


def hive_topic_create_failure_text(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == "privacy_blocked_topic":
        return "I won't create that Hive task because it looks like it contains private or secret material."
    if normalized == "missing_target":
        return "Hive topic creation is configured incompletely on this runtime, so I can't post the task yet. Hive truth: future/unsupported."
    if normalized == "disabled":
        return "Public Hive is not enabled on this runtime, so I can't create a live Hive task. Hive truth: future/unsupported."
    if normalized == "missing_auth":
        return "Hive task creation is disabled here because public Hive auth is not configured for writes. Hive truth: future/unsupported."
    if normalized == "invalid_auth":
        return "Hive task creation is configured, but the live Hive rejected this runtime's write auth. I need to refresh public Hive auth before posting."
    if normalized == "admission_blocked":
        return "The live Hive rejected that task draft as too command-like or low-substance. I need to frame it as agent analysis before posting."
    if normalized == "empty_topic":
        return "I can create the Hive task, but I still need a concrete title and summary."
    return "I couldn't create that Hive task."


def build_hive_topic_create_failure_result(
    agent: Any,
    *,
    task: Any,
    session_id: str,
    user_input: str,
    source_context: dict[str, object] | None,
    status: str,
    reason: str,
    details: dict[str, Any],
    response: str | None = None,
    confidence: float = 0.46,
    error: str = "",
) -> dict[str, Any]:
    payload = dict(details)
    if error:
        payload["error"] = error
    return agent._action_fast_path_result(
        task_id=task.task_id,
        session_id=session_id,
        user_input=user_input,
        response=response or agent._hive_topic_create_failure_text(status),
        confidence=confidence,
        source_context=source_context,
        reason=reason,
        success=False,
        details=payload,
        mode_override="tool_failed",
        task_outcome="failed",
        workflow_summary=agent._action_workflow_summary(
            operator_kind="hive.create_topic",
            dispatch_status=status,
            details={"action_id": ""},
        ),
    )
