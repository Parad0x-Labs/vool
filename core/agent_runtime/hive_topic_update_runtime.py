from __future__ import annotations

from typing import Any

from core.agent_runtime.hive_topic_update_effects import finalize_hive_topic_update
from core.agent_runtime.hive_topic_update_preflight import prepare_hive_topic_update_request


def handle_hive_topic_update_request(
    agent: Any,
    user_input: str,
    *,
    task: Any,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    preflight = prepare_hive_topic_update_request(
        agent,
        user_input,
        task=task,
        session_id=session_id,
        source_context=source_context,
    )
    if not preflight.get("ok"):
        return dict(preflight["result"])
    return finalize_hive_topic_update(
        agent,
        task=task,
        session_id=session_id,
        source_context=source_context,
        user_input=user_input,
        topic=dict(preflight["topic"]),
        update_draft=dict(preflight["update_draft"]),
        public_copy=dict(preflight["public_copy"]),
        next_title=str(preflight["next_title"]),
    )
