from __future__ import annotations

import re
import uuid
from typing import Any

from core.agent_runtime import hive_research_followup as agent_hive_research_followup

_HIVE_REVIEW_ACTION_RE = re.compile(
    r"(?:please\s+)?(?:(?:public\s+)?hive[,:]?\s+)?(?P<decision>approve|approved|reject|rejected|needs?\s+more\s+evidence|needs?\s+improvement|send\s+back|quarantine|void)\b"
    r"(?:\s+(?:the\s+)?)?"
    r"(?:(?P<object_type>post|topic)\s+)?"
    r"(?:#)?(?P<object_id>[a-z0-9][a-z0-9-]{5,255})\b",
    re.IGNORECASE,
)


def maybe_handle_hive_frontdoor(
    agent: Any,
    *,
    raw_user_input: str,
    effective_input: str,
    session_id: str,
    source_context: dict[str, object] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool]:
    from core.inline_payload import turn_supplies_its_own_content

    if turn_supplies_its_own_content(raw_user_input):
        return None, None, False
    hive_review_result = agent._maybe_handle_hive_review_command(
        effective_input,
        session_id=session_id,
        source_context=source_context,
    )
    if hive_review_result is not None:
        return hive_review_result, None, False

    pending_hive_create_confirmation = agent._is_pending_hive_create_confirmation_input(
        effective_input,
        session_id=session_id,
        source_context=source_context,
    )
    if not pending_hive_create_confirmation:
        hive_followup = agent._maybe_handle_hive_research_followup(
            effective_input,
            session_id=session_id,
            source_context=source_context,
        )
        if hive_followup is not None:
            return hive_followup, None, False

    raw_hive_create_draft = agent._extract_hive_topic_create_draft(raw_user_input)
    effective_hive_create_draft = raw_hive_create_draft or agent._extract_hive_topic_create_draft(effective_input)
    if effective_hive_create_draft is None and not pending_hive_create_confirmation:
        handled, response, model_wording_candidate, hive_command_details = agent._maybe_handle_hive_runtime_command(
            effective_input,
            session_id=session_id,
        )
        if not handled:
            recovered_hive_input = agent._recover_hive_runtime_command_input(effective_input)
            if recovered_hive_input:
                handled, response, model_wording_candidate, hive_command_details = agent._maybe_handle_hive_runtime_command(
                    recovered_hive_input,
                    session_id=session_id,
                )
        if handled:
            topic_rows = [
                dict(item)
                for item in list((hive_command_details or {}).get("topics") or [])
                if isinstance(item, dict) and str(item.get("topic_id") or "").strip()
            ]
            if topic_rows:
                agent._store_hive_topic_selection_state(session_id, topic_rows)
            response_class = agent._classify_hive_command_details(hive_command_details or {}, response=response)
            if model_wording_candidate and agent._is_chat_truth_surface(source_context):
                return (
                    agent._chat_surface_hive_wording_result(
                        session_id=session_id,
                        user_input=effective_input,
                        source_context=source_context,
                        response_class=response_class,
                        reason="hive_activity_model_wording",
                        observations=agent._chat_surface_hive_command_observations(hive_command_details or {}),
                        fallback_response=agent._chat_surface_hive_degraded_response(hive_command_details or {}),
                    ),
                    effective_hive_create_draft,
                    pending_hive_create_confirmation,
                )
            return (
                agent._fast_path_result(
                    session_id=session_id,
                    user_input=effective_input,
                    response=response,
                    confidence=0.89,
                    source_context=source_context,
                    reason="hive_activity_command",
                    classification_details=hive_command_details,
                ),
                effective_hive_create_draft,
                pending_hive_create_confirmation,
            )

    if not pending_hive_create_confirmation:
        hive_status = agent._maybe_handle_hive_status_followup(
            effective_input,
            session_id=session_id,
            source_context=source_context,
        )
        if hive_status is not None:
            return hive_status, effective_hive_create_draft, False

    return None, effective_hive_create_draft, pending_hive_create_confirmation


def maybe_handle_hive_review_command(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    clean = " ".join(str(user_input or "").split()).strip()
    lowered = clean.lower()
    if not clean:
        return None
    if agent._looks_like_hive_review_queue_command(lowered):
        return agent._handle_hive_review_queue_command(
            clean,
            session_id=session_id,
            source_context=source_context,
        )
    review_action = agent._parse_hive_review_action(clean)
    if review_action is not None:
        return agent._handle_hive_review_action(
            clean,
            session_id=session_id,
            source_context=source_context,
            review_action=review_action,
        )
    if agent._looks_like_hive_cleanup_command(lowered):
        return agent._handle_hive_cleanup_command(
            clean,
            session_id=session_id,
            source_context=source_context,
        )
    return None


def looks_like_hive_review_queue_command(lowered: str) -> bool:
    compact = " ".join(str(lowered or "").split()).strip().lower()
    if not compact:
        return False
    if "hive" not in compact and "review" not in compact:
        return False
    return any(
        marker in compact
        for marker in (
            "review queue",
            "what needs review",
            "what is in review",
            "show review queue",
            "check review queue",
            "moderation queue",
            "review items",
            "pending reviews",
        )
    )


def parse_hive_review_action(user_input: str) -> dict[str, str] | None:
    # PRECEDENCE: the operator lane's approval grammar is exact and owns its shape.
    # "approve calendar <uuid>" reads to this regex as decision="approve",
    # object_id="calendar" — a moderation review of an object that does not exist,
    # intercepting the user's actual approval turn. When the operator approval idiom
    # matches, it is not a Hive review, whatever this regex would do with the prefix.
    try:
        from core.operator.parser import _APPROVAL_ID_RE

        if _APPROVAL_ID_RE.search(str(user_input or "")):
            return None
    except Exception:
        pass
    match = _HIVE_REVIEW_ACTION_RE.search(user_input)
    # A moderation action must own the whole command, never a clause inside code,
    # quoted examples, requirements, or a negated request.
    command = str(user_input or "").strip().rstrip(".! ")
    match = _HIVE_REVIEW_ACTION_RE.fullmatch(command)
    if match is None:
        return None
    decision_phrase = " ".join(str(match.group("decision") or "").split()).strip().lower()
    object_type = str(match.group("object_type") or "").strip().lower()
    object_id = str(match.group("object_id") or "").strip()
    if not object_id:
        return None
    if not object_type and not object_id.lower().startswith(("post-", "topic-")):
        return None
    decision = {
        "approve": "approve",
        "approved": "approve",
        "reject": "void",
        "rejected": "void",
        "needs more evidence": "review_required",
        "needs improvement": "review_required",
        "send back": "review_required",
        "quarantine": "quarantine",
        "void": "void",
    }.get(decision_phrase)
    if not decision:
        return None
    if object_type not in {"post", "topic"}:
        object_type = "post" if object_id.startswith("post-") else "topic" if object_id.startswith("topic-") else "post"
    return {
        "decision": decision,
        "decision_phrase": decision_phrase,
        "object_type": object_type,
        "object_id": object_id,
    }


def looks_like_hive_cleanup_command(lowered: str) -> bool:
    compact = " ".join(str(lowered or "").split()).strip().lower()
    if "hive" not in compact and "vool_smoke" not in compact and "smoke topic" not in compact:
        return False
    if "cleanup" not in compact and "clean up" not in compact and "remove" not in compact and "close" not in compact:
        return False
    return any(marker in compact for marker in ("smoke", "junk", "test artifact", "test topic", "noise"))


def handle_hive_review_queue_command(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    if not agent.public_hive_bridge.enabled():
        return agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response="Public Hive is not enabled on this runtime, so I can't inspect the live review queue.",
            confidence=0.9,
            source_context=source_context,
            reason="hive_review_queue_disabled",
        )
    rows = agent.public_hive_bridge.list_public_review_queue(limit=8)
    if not rows:
        response = "Hive review queue is empty right now."
    else:
        lines = ["Hive review queue:"]
        for row in rows[:6]:
            object_type = str(row.get("object_type") or "object").strip()
            object_id = str(row.get("object_id") or "").strip()
            preview = " ".join(str(row.get("preview") or "").split()).strip()
            moderation_state = str(row.get("moderation_state") or "review_required").strip()
            summary = dict(row.get("review_summary") or {})
            total_reviews = int(summary.get("total_reviews") or 0)
            current_state = str(summary.get("current_state") or moderation_state).strip()
            applied_state = str(summary.get("applied_state") or "").strip()
            state_suffix = f" -> {applied_state}" if applied_state and applied_state != current_state else ""
            snippet = preview[:120] + ("..." if len(preview) > 120 else "")
            lines.append(
                f"- [{object_type}] {object_id}: {current_state}{state_suffix}; reviews={total_reviews}; {snippet or 'No preview'}"
            )
        response = "\n".join(lines)
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=response,
        confidence=0.93,
        source_context=source_context,
        reason="hive_review_queue",
    )


def handle_hive_review_action(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    review_action: dict[str, str],
) -> dict[str, Any]:
    if not agent.public_hive_bridge.enabled():
        return agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response="Public Hive is not enabled on this runtime, so I can't submit a moderation review.",
            confidence=0.9,
            source_context=source_context,
            reason="hive_review_action_disabled",
        )
    if not agent.public_hive_bridge.write_enabled():
        return agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response="Public Hive moderation writes are disabled here because live write auth is not configured.",
            confidence=0.9,
            source_context=source_context,
            reason="hive_review_action_write_disabled",
        )
    result = agent.public_hive_bridge.submit_public_moderation_review(
        object_type=review_action["object_type"],
        object_id=review_action["object_id"],
        decision=review_action["decision"],
        note=f"VOOL operator review via chat: {review_action['decision_phrase']}",
    )
    if not result.get("ok"):
        response = f"Failed to submit Hive moderation review for {review_action['object_type']} `{review_action['object_id']}`."
        return agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=response,
            confidence=0.82,
            source_context=source_context,
            reason="hive_review_action_failed",
        )
    current_state = str(result.get("current_state") or "").strip() or review_action["decision"]
    quorum_reached = bool(result.get("quorum_reached"))
    response = (
        f"Submitted Hive moderation review for {review_action['object_type']} `{review_action['object_id']}`: "
        f"{review_action['decision']}. Current state `{current_state}`."
    )
    if quorum_reached:
        response = f"{response} Review quorum is reached."
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=response,
        confidence=0.95,
        source_context=source_context,
        reason="hive_review_action",
    )


def handle_hive_cleanup_command(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    if not agent.public_hive_bridge.enabled():
        return agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response="Public Hive is not enabled on this runtime, so I can't clean live smoke topics.",
            confidence=0.9,
            source_context=source_context,
            reason="hive_cleanup_disabled",
        )
    if not agent.public_hive_bridge.write_enabled():
        return agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response="Public Hive cleanup writes are disabled here because live write auth is not configured.",
            confidence=0.9,
            source_context=source_context,
            reason="hive_cleanup_write_disabled",
        )
    topics = agent.public_hive_bridge.list_public_topics(
        limit=64,
        statuses=("open", "researching", "disputed", "partial", "needs_improvement", "solved", "closed"),
    )
    candidates = [
        topic
        for topic in topics
        if agent._looks_like_disposable_hive_cleanup_topic(topic)
        and str(topic.get("status") or "").strip().lower() != "closed"
    ]
    if not candidates:
        return agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response="I didn't find any live disposable smoke topics to close.",
            confidence=0.92,
            source_context=source_context,
            reason="hive_cleanup_noop",
        )
    closed_count = 0
    failed_ids: list[str] = []
    for topic in candidates[:16]:
        topic_id = str(topic.get("topic_id") or "").strip()
        if not topic_id:
            continue
        result = agent.public_hive_bridge.update_public_topic_status(
            topic_id=topic_id,
            status="closed",
            note="Disposable smoke cleanup from VOOL operator surface.",
            idempotency_key=f"{topic_id}:cleanup:{uuid.uuid4().hex[:8]}",
        )
        if result.get("ok"):
            closed_count += 1
        else:
            failed_ids.append(topic_id[:8])
    response = f"Closed {closed_count} disposable Hive smoke topic{'s' if closed_count != 1 else ''}."
    if failed_ids:
        response = f"{response} Failed: {', '.join(failed_ids[:6])}."
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=response,
        confidence=0.94,
        source_context=source_context,
        reason="hive_cleanup_smoke_topics",
    )


def looks_like_disposable_hive_cleanup_topic(topic: dict[str, Any]) -> bool:
    title = str(topic.get("title") or "").strip()
    summary = str(topic.get("summary") or "").strip()
    tags = {
        str(item or "").strip().lower()
        for item in list(topic.get("topic_tags") or [])
        if str(item or "").strip()
    }
    combined = f"{title} {summary}".lower()
    return (
        "[vool_smoke:" in combined
        or title.startswith("[VOOL_SMOKE]")
        or "vool_smoke" in combined
        or ("smoke" in tags and any(marker in combined for marker in ("cleanup", "smoke", "test artifact", "disposable")))
    )

maybe_handle_hive_research_followup = agent_hive_research_followup.maybe_handle_hive_research_followup
maybe_resume_active_hive_task = agent_hive_research_followup.maybe_resume_active_hive_task
extract_hive_topic_hint = agent_hive_research_followup.extract_hive_topic_hint
maybe_handle_hive_status_followup = agent_hive_research_followup.maybe_handle_hive_status_followup
resolve_hive_status_topic_id = agent_hive_research_followup.resolve_hive_status_topic_id
looks_like_hive_status_followup = agent_hive_research_followup.looks_like_hive_status_followup
history_hive_topic_hints = agent_hive_research_followup.history_hive_topic_hints
looks_like_hive_research_followup = agent_hive_research_followup.looks_like_hive_research_followup
