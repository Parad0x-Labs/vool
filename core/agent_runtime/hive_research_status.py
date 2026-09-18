from __future__ import annotations

from typing import Any


def maybe_handle_hive_status_followup(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    session_hive_state_fn: Any,
) -> dict[str, Any] | None:
    clean = " ".join(str(user_input or "").split()).strip()
    lowered = clean.lower()
    if not agent._looks_like_hive_status_followup(lowered):
        return None
    if not agent.public_hive_bridge.enabled():
        return None

    hive_state = session_hive_state_fn(session_id)
    history = list((source_context or {}).get("conversation_history") or [])
    topic_hint = agent._extract_hive_topic_hint(clean)
    watched_topic_ids = [
        str(item).strip()
        for item in list(hive_state.get("watched_topic_ids") or [])
        if str(item).strip()
    ]
    resolved_topic_id = agent._resolve_hive_status_topic_id(
        topic_hint=topic_hint,
        watched_topic_ids=watched_topic_ids,
        history=history,
        interaction_state=hive_state,
    )
    if not resolved_topic_id:
        return None

    packet = agent.public_hive_bridge.get_public_research_packet(resolved_topic_id)
    topic = dict(packet.get("topic") or {})
    state = dict(packet.get("execution_state") or {})
    counts = dict(packet.get("counts") or {})
    posts = [dict(item) for item in list(packet.get("posts") or [])]
    title = str(topic.get("title") or resolved_topic_id).strip()
    status = str(topic.get("status") or state.get("topic_status") or "").strip().lower()
    execution_state = str(state.get("execution_state") or "").strip().lower()
    active_claim_count = int(state.get("active_claim_count") or counts.get("active_claim_count") or 0)
    artifact_count = int(state.get("artifact_count") or 0)
    post_count = int(counts.get("post_count") or len(posts))

    if status in {"solved", "closed"}:
        lead = f"Yes. `{title}` (#{resolved_topic_id[:8]}) is `{status}`."
    elif status == "partial":
        lead = f"No. `{title}` (#{resolved_topic_id[:8]}) is `partial` and still needs follow-up work."
    elif status == "needs_improvement":
        lead = f"No. `{title}` (#{resolved_topic_id[:8]}) is `needs_improvement` and has been sent back for more work."
    elif status:
        lead = f"No. `{title}` (#{resolved_topic_id[:8]}) is still `{status}`."
    else:
        lead = f"`{title}` (#{resolved_topic_id[:8]}) is still in progress."

    summary: list[str] = [lead]
    if execution_state == "claimed" or active_claim_count > 0:
        summary.append(f"Active claims: {active_claim_count}.")
    if post_count:
        summary.append(f"Posts: {post_count}.")
    if artifact_count:
        summary.append(f"Artifacts: {artifact_count}.")
    if status == "researching" and artifact_count > 0:
        summary.append("The first bounded pass landed, but the topic did not clear the solve threshold yet.")
    latest_post = posts[0] if posts else {}
    latest_post_kind = str(latest_post.get("post_kind") or "").strip().lower()
    latest_post_body = " ".join(str(latest_post.get("body") or "").split()).strip()
    if latest_post_kind or latest_post_body:
        label = latest_post_kind or "post"
        if latest_post_body:
            summary.append(f"Latest {label}: {latest_post_body[:220]}.")
    response = " ".join(part for part in summary if part)
    deterministic_review_statuses = {"partial", "needs_improvement"}
    if agent._is_chat_truth_surface(source_context) and status not in deterministic_review_statuses:
        return agent._chat_surface_hive_wording_result(
            session_id=session_id,
            user_input=clean,
            source_context=source_context,
            response_class=agent.ResponseClass.TASK_STATUS,
            reason="hive_status_model_wording",
            observations=agent._chat_surface_hive_status_observations(
                topic_id=resolved_topic_id,
                title=title,
                status=status,
                execution_state=execution_state,
                active_claim_count=active_claim_count,
                artifact_count=artifact_count,
                post_count=post_count,
                latest_post_kind=latest_post_kind,
                latest_post_body=latest_post_body,
                truth_payload=packet,
            ),
            fallback_response=response,
        )
    return agent._fast_path_result(
        session_id=session_id,
        user_input=clean,
        response=response,
        confidence=0.92,
        source_context=source_context,
        reason="hive_status_followup",
    )


def resolve_hive_status_topic_id(
    agent: Any,
    *,
    topic_hint: str,
    watched_topic_ids: list[str],
    history: list[dict[str, Any]],
    interaction_state: dict[str, Any] | None = None,
) -> str:
    interaction_payload = dict((interaction_state or {}).get("interaction_payload") or {})
    active_topic = str(interaction_payload.get("active_topic_id") or "").strip().lower()
    if active_topic and (not topic_hint or active_topic == topic_hint or active_topic.startswith(topic_hint)):
        return active_topic
    watched = [str(item).strip().lower() for item in list(watched_topic_ids or []) if str(item).strip()]
    if topic_hint:
        for topic_id in reversed(watched):
            if topic_id == topic_hint or topic_id.startswith(topic_hint):
                return topic_id
    history_hints = agent._history_hive_topic_hints(history)
    for hint in [topic_hint, *history_hints]:
        clean_hint = str(hint or "").strip().lower()
        if not clean_hint:
            continue
        for topic_id in reversed(watched):
            if topic_id == clean_hint or topic_id.startswith(clean_hint):
                return topic_id
    if watched:
        return watched[-1]

    lookup_rows = agent.public_hive_bridge.list_public_topics(
        limit=32,
        statuses=("open", "researching", "disputed", "partial", "needs_improvement", "solved", "closed"),
    )
    for hint in [topic_hint, *history_hints]:
        clean_hint = str(hint or "").strip().lower()
        if not clean_hint:
            continue
        for row in lookup_rows:
            topic_id = str(row.get("topic_id") or "").strip().lower()
            if topic_id == clean_hint or topic_id.startswith(clean_hint):
                return topic_id
    return ""


def looks_like_hive_status_followup(lowered: str) -> bool:
    text = str(lowered or "").strip().lower()
    if not text:
        return False
    if not any(marker in text for marker in ("research", "hive", "topic", "task", "done", "complete", "status", "finish", "finished")):
        return False
    for phrase in (
        "is research complete",
        "is the research complete",
        "is it complete",
        "is it done",
        "is research done",
        "did it finish",
        "did research finish",
        "is the task complete",
        "what is the status",
        "status?",
        "what's the status",
        "is that solved",
        "is it solved",
    ):
        if phrase in text:
            return True
    return False
