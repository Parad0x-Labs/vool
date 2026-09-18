from __future__ import annotations

import contextlib
import uuid
from typing import Any

from core.agent_runtime.hive_topic_update_failures import build_hive_topic_update_failure_result


def finalize_hive_topic_update(
    agent: Any,
    *,
    task: Any,
    session_id: str,
    source_context: dict[str, object] | None,
    user_input: str,
    topic: dict[str, Any],
    update_draft: dict[str, Any],
    public_copy: dict[str, Any],
    next_title: str,
) -> dict[str, Any]:
    result = agent.public_hive_bridge.update_public_topic(
        topic_id=str(topic.get("topic_id") or "").strip(),
        title=str(public_copy.get("title") or "").strip(),
        summary=str(public_copy.get("summary") or "").strip(),
        topic_tags=[
            str(item).strip()
            for item in list(update_draft.get("topic_tags") or topic.get("topic_tags") or [])
            if str(item).strip()
        ][:8],
        idempotency_key=f"{str(topic.get('topic_id') or '').strip()}:update:{uuid.uuid4().hex[:8]}",
    )
    if not result.get("ok"):
        return build_hive_topic_update_failure_result(
            agent,
            task=task,
            session_id=session_id,
            user_input=user_input,
            source_context=source_context,
            status=str(result.get("status") or "failed"),
        )
    topic_id = str(result.get("topic_id") or topic.get("topic_id") or "").strip()
    with contextlib.suppress(Exception):
        agent.hive_activity_tracker.note_watched_topic(session_id=session_id, topic_id=topic_id)
    updated = dict(result.get("topic_result") or {})
    updated_title = str(updated.get("title") or next_title).strip()
    return agent._action_fast_path_result(
        task_id=task.task_id,
        session_id=session_id,
        user_input=user_input,
        response=f"Updated Hive task `{updated_title}` (#{topic_id[:8]}).",
        confidence=0.95,
        source_context=source_context,
        reason="hive_topic_updated",
        success=True,
        details={"status": "updated", "topic_id": topic_id},
        mode_override="tool_executed",
        task_outcome="success",
    )
