from __future__ import annotations

from typing import Any


def prepare_hive_topic_delete_request(
    agent: Any,
    user_input: str,
    *,
    task: Any,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    if not agent.public_hive_bridge.enabled():
        return {
            "ok": False,
            "result": agent._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=user_input,
                response="Public Hive is not enabled on this runtime, so I can't delete a live Hive task.",
                confidence=0.9,
                source_context=source_context,
                reason="hive_topic_delete_disabled",
                success=False,
                details={"status": "disabled"},
                mode_override="tool_failed",
                task_outcome="failed",
            ),
        }
    if not agent.public_hive_bridge.write_enabled():
        return {
            "ok": False,
            "result": agent._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=user_input,
                response="Hive task deletes are disabled here because public Hive auth is not configured for writes.",
                confidence=0.9,
                source_context=source_context,
                reason="hive_topic_delete_missing_auth",
                success=False,
                details={"status": "missing_auth"},
                mode_override="tool_failed",
                task_outcome="failed",
            ),
        }
    topic = agent._resolve_hive_topic_for_mutation(
        session_id=session_id,
        topic_hint=agent._extract_hive_topic_hint(user_input),
    )
    if topic is None:
        return {
            "ok": False,
            "result": agent._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=user_input,
                response="I couldn't resolve which Hive task to delete. Give me the task id or ask right after creating/listing it.",
                confidence=0.82,
                source_context=source_context,
                reason="hive_topic_delete_missing_target",
                success=False,
                details={"status": "missing_topic"},
                mode_override="tool_failed",
                task_outcome="failed",
            ),
        }
    return {"ok": True, "topic": topic}
