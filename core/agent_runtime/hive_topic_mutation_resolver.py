from __future__ import annotations

from typing import Any

from core.hive_activity_tracker import session_hive_state


def resolve_hive_topic_for_mutation(
    agent: Any,
    *,
    session_id: str,
    topic_hint: str,
    session_hive_state_fn: Any = session_hive_state,
) -> dict[str, Any] | None:
    clean_hint = str(topic_hint or "").strip().lower()
    if clean_hint:
        topic = agent.public_hive_bridge.get_public_topic(clean_hint, include_flagged=True)
        if topic:
            return topic
        for row in agent.public_hive_bridge.list_public_topics(
            limit=64,
            statuses=("open", "researching", "disputed", "partial", "needs_improvement", "solved", "closed"),
        ):
            topic_id = str(row.get("topic_id") or "").strip().lower()
            if topic_id.startswith(clean_hint):
                return row
    hive_state = session_hive_state_fn(session_id)
    payload = dict(hive_state.get("interaction_payload") or {})
    candidate_ids: list[str] = []
    active_topic_id = str(payload.get("active_topic_id") or "").strip()
    if active_topic_id:
        candidate_ids.append(active_topic_id)
    candidate_ids.extend(
        str(item).strip()
        for item in reversed(list(hive_state.get("watched_topic_ids") or []))
        if str(item).strip()
    )
    seen: set[str] = set()
    for candidate_id in candidate_ids:
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        topic = agent.public_hive_bridge.get_public_topic(candidate_id, include_flagged=True)
        if topic:
            return topic
    return None
