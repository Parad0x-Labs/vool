from __future__ import annotations

import re
from typing import Any

from core.hive_activity_tracker import session_hive_state

HIVE_CONFIRM_POSITIVE_STRICT = re.compile(
    r"^\s*(?:yes|yea|yeah|yep|yup|ok(?:ay)?|sure|do\s*it|go\s*(?:ahead|for\s*it)|"
    r"lets?\s*(?:go|do\s*it)|for\s*sure|absolutely|confirmed?|lgtm|send\s*it|"
    r"post\s*it|create\s*it|ship\s*it|proceed|affirmative|y)\s*[.!]*\s*$",
    re.IGNORECASE,
)
HIVE_CONFIRM_POSITIVE_LOOSE = re.compile(
    r"^\s*(?:yes|yea|yeah|yep|yup|ok(?:ay)?|sure|do\s*it|go\s*(?:ahead|for\s*it)|"
    r"lets?\s*(?:go|do\s*it)|for\s*sure|absolutely|confirmed?|lgtm|send\s*it|"
    r"post\s*it|create\s*it|ship\s*it|proceed|affirmative)\b",
    re.IGNORECASE,
)
HIVE_CONFIRM_NEGATIVE = re.compile(
    r"^\s*(?:no|nah|nope|not?\s*now|later|meh|cancel|stop|skip|forget\s*it|"
    r"never\s*mind|nevermind|don'?t|nay|negative|n)\s*[.!]*\s*$",
    re.IGNORECASE,
)


def maybe_handle_hive_create_confirmation(
    agent: Any,
    user_input: str,
    *,
    task: Any,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    lowered = user_input.strip()
    variant_choice = agent._parse_hive_create_variant_choice(lowered)
    is_positive = bool(
        HIVE_CONFIRM_POSITIVE_STRICT.match(lowered) or HIVE_CONFIRM_POSITIVE_LOOSE.match(lowered)
    )
    is_negative = bool(HIVE_CONFIRM_NEGATIVE.match(lowered))
    pending = agent._load_pending_hive_create(
        session_id=session_id,
        source_context=source_context,
        fallback_task_id=task.task_id,
        allow_history_recovery=is_positive or is_negative or bool(variant_choice),
    )
    if pending is None:
        if variant_choice:
            return agent._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=user_input,
                response="There isn't a pending Hive draft to confirm. Start with `create hive task: ...` first.",
                confidence=0.94,
                source_context=source_context,
                reason="hive_topic_create_no_pending_confirmation",
                success=False,
                details={"status": "no_pending_confirmation"},
                mode_override="tool_failed",
                task_outcome="failed",
                workflow_summary=agent._action_workflow_summary(
                    operator_kind="hive.create_topic",
                    dispatch_status="no_pending_confirmation",
                    details={"action_id": ""},
                ),
            )
        return None

    if is_positive or bool(variant_choice):
        chosen_variant = variant_choice or str(pending.get("default_variant") or "improved")
        available_variants = {
            key: dict(value)
            for key, value in dict(pending.get("variants") or {}).items()
            if isinstance(value, dict)
        }
        if chosen_variant == "original" and "original" not in available_variants:
            blocked_reason = str(pending.get("original_blocked_reason") or "").strip()
            reply = blocked_reason or "The original Hive draft isn't safe to publish. Use `send improved` instead."
            return agent._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=user_input,
                response=reply,
                confidence=0.92,
                source_context=source_context,
                reason="hive_topic_create_original_blocked",
                success=False,
                details={"status": "original_blocked"},
                mode_override="tool_failed",
                task_outcome="failed",
                workflow_summary=agent._action_workflow_summary(
                    operator_kind="hive.create_topic",
                    dispatch_status="original_blocked",
                    details={"action_id": ""},
                ),
            )
        agent._clear_hive_create_pending(session_id)
        return agent._execute_confirmed_hive_create(
            pending,
            task=task,
            session_id=session_id,
            source_context=source_context,
            user_input=user_input,
            variant=chosen_variant,
        )

    if is_negative:
        agent._clear_hive_create_pending(session_id)
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response="Got it -- Hive task discarded. What's next?",
            confidence=0.95,
            source_context=source_context,
            reason="hive_topic_create_cancelled",
            success=True,
            details={"status": "cancelled"},
            mode_override="tool_executed",
            task_outcome="cancelled",
            workflow_summary=agent._action_workflow_summary(
                operator_kind="hive.create_topic",
                dispatch_status="cancelled",
                details={"action_id": ""},
            ),
        )

    return None


def is_pending_hive_create_confirmation_input(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    hive_state: dict[str, Any] | None = None,
    session_hive_state_fn: Any = session_hive_state,
) -> bool:
    clean = " ".join(str(user_input or "").split()).strip()
    if not clean:
        return False
    if not _looks_like_confirmation_text(clean):
        return False
    state = hive_state or session_hive_state_fn(session_id)
    return agent._has_pending_hive_create_confirmation(
        session_id=session_id,
        hive_state=state,
        source_context=source_context,
    )


def parse_hive_create_variant_choice(text: str) -> str:
    compact = " ".join(str(text or "").split()).strip().lower()
    if re.fullmatch(r"(?:yes\s+)?(?:send\s+)?improved(?:\s+draft)?", compact):
        return "improved"
    if re.fullmatch(r"(?:yes\s+)?(?:send\s+)?original(?:\s+draft)?", compact):
        return "original"
    return ""


def _looks_like_confirmation_text(text: str) -> bool:
    return bool(
        HIVE_CONFIRM_POSITIVE_STRICT.match(text)
        or HIVE_CONFIRM_POSITIVE_LOOSE.match(text)
        or HIVE_CONFIRM_NEGATIVE.match(text)
    )
