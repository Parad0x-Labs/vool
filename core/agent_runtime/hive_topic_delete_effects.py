from __future__ import annotations

import uuid
from typing import Any

from core.agent_runtime.hive_topic_delete_failures import build_hive_topic_delete_failure_result


def finalize_hive_topic_delete(
    agent: Any,
    *,
    task: Any,
    session_id: str,
    source_context: dict[str, object] | None,
    user_input: str,
    topic: dict[str, Any],
) -> dict[str, Any]:
    topic_id = str(topic.get("topic_id") or "").strip()
    result = agent.public_hive_bridge.delete_public_topic(
        topic_id=topic_id,
        note="Deleted from VOOL operator chat before the task was claimed.",
        idempotency_key=f"{topic_id}:delete:{uuid.uuid4().hex[:8]}",
    )
    if not result.get("ok"):
        return build_hive_topic_delete_failure_result(
            agent,
            task=task,
            session_id=session_id,
            user_input=user_input,
            source_context=source_context,
            status=str(result.get("status") or "failed"),
        )
    return agent._action_fast_path_result(
        task_id=task.task_id,
        session_id=session_id,
        user_input=user_input,
        response=f"Deleted Hive task `{str(topic.get('title') or '').strip()}` (#{topic_id[:8]}) from the active queue.",
        confidence=0.95,
        source_context=source_context,
        reason="hive_topic_deleted",
        success=True,
        details={"status": "deleted", "topic_id": topic_id},
        mode_override="tool_executed",
        task_outcome="success",
    )
