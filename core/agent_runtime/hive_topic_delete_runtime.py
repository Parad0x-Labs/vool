from __future__ import annotations

from typing import Any

from core.agent_runtime.hive_topic_delete_effects import finalize_hive_topic_delete
from core.agent_runtime.hive_topic_delete_preflight import prepare_hive_topic_delete_request


def handle_hive_topic_delete_request(
    agent: Any,
    user_input: str,
    *,
    task: Any,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    preflight = prepare_hive_topic_delete_request(
        agent,
        user_input,
        task=task,
        session_id=session_id,
        source_context=source_context,
    )
    if not preflight.get("ok"):
        return dict(preflight["result"])
    return finalize_hive_topic_delete(
        agent,
        task=task,
        session_id=session_id,
        source_context=source_context,
        user_input=user_input,
        topic=dict(preflight["topic"]),
    )
