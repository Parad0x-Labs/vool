from __future__ import annotations

from typing import Any


def build_hive_topic_delete_failure_result(
    agent: Any,
    *,
    task: Any,
    session_id: str,
    user_input: str,
    source_context: dict[str, object] | None,
    status: str,
) -> dict[str, Any]:
    if status == "route_unavailable":
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response="Live Hive task deletes are not available on the current public deployment yet. The local code supports it, but the public Hive nodes need an update first.",
            confidence=0.9,
            source_context=source_context,
            reason="hive_topic_delete_route_unavailable",
            success=False,
            details={"status": status},
            mode_override="tool_failed",
            task_outcome="failed",
        )
    if status == "not_owner":
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response="I can't delete that Hive task because this agent didn't create it.",
            confidence=0.9,
            source_context=source_context,
            reason="hive_topic_delete_not_owner",
            success=False,
            details={"status": status},
            mode_override="tool_failed",
            task_outcome="failed",
        )
    if status == "already_claimed":
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response="I can't delete that Hive task because another agent already claimed it.",
            confidence=0.9,
            source_context=source_context,
            reason="hive_topic_delete_claimed",
            success=False,
            details={"status": status},
            mode_override="tool_failed",
            task_outcome="failed",
        )
    if status == "not_deletable":
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response="I can't delete that Hive task because only open, unclaimed tasks can be removed.",
            confidence=0.9,
            source_context=source_context,
            reason="hive_topic_delete_not_deletable",
            success=False,
            details={"status": status},
            mode_override="tool_failed",
            task_outcome="failed",
        )
    return agent._action_fast_path_result(
        task_id=task.task_id,
        session_id=session_id,
        user_input=user_input,
        response="I couldn't delete that Hive task.",
        confidence=0.82,
        source_context=source_context,
        reason="hive_topic_delete_failed",
        success=False,
        details={"status": status},
        mode_override="tool_failed",
        task_outcome="failed",
    )
