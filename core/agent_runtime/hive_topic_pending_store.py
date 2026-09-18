from __future__ import annotations

from typing import Any

from core.agent_runtime.hive_topic_pending_history import (
    recover_hive_create_pending_from_history as pending_history_recover_hive_create_pending_from_history,
)
from core.agent_runtime.hive_topic_pending_payloads import (
    build_hive_create_pending_payload,
)
from core.hive_activity_tracker import clear_hive_interaction_state, session_hive_state, set_hive_interaction_state


def has_pending_hive_create_confirmation(
    agent: Any,
    *,
    session_id: str,
    hive_state: dict[str, Any],
    source_context: dict[str, object] | None,
) -> bool:
    pending = agent._hive_create_pending.get(session_id)
    if pending and str(pending.get("title") or "").strip():
        return True

    payload = dict(hive_state.get("interaction_payload") or {})
    stored = dict(payload.get("pending_hive_create") or {})
    if str(stored.get("title") or "").strip():
        return True

    recovered = agent._recover_hive_create_pending_from_history(
        history=list((source_context or {}).get("conversation_history") or []),
        fallback_task_id="",
    )
    return recovered is not None


def remember_hive_create_pending(
    agent: Any,
    session_id: str,
    pending: dict[str, Any],
    *,
    set_hive_interaction_state_fn: Any = set_hive_interaction_state,
) -> None:
    payload = build_hive_create_pending_payload(agent, pending)
    agent._hive_create_pending[session_id] = dict(payload)
    set_hive_interaction_state_fn(
        session_id,
        mode="hive_topic_create_pending",
        payload={"pending_hive_create": payload},
    )


def clear_hive_create_pending(
    agent: Any,
    session_id: str,
    *,
    session_hive_state_fn: Any = session_hive_state,
    clear_hive_interaction_state_fn: Any = clear_hive_interaction_state,
) -> None:
    agent._hive_create_pending.pop(session_id, None)
    hive_state = session_hive_state_fn(session_id)
    if str(hive_state.get("interaction_mode") or "").strip().lower() == "hive_topic_create_pending":
        clear_hive_interaction_state_fn(session_id)


def load_pending_hive_create(
    agent: Any,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    fallback_task_id: str,
    allow_history_recovery: bool,
    session_hive_state_fn: Any = session_hive_state,
) -> dict[str, Any] | None:
    pending = agent._hive_create_pending.get(session_id)
    if pending:
        return dict(pending)

    hive_state = session_hive_state_fn(session_id)
    payload = dict(hive_state.get("interaction_payload") or {})
    stored = dict(payload.get("pending_hive_create") or {})
    if stored and (str(stored.get("title") or "").strip() or dict(stored.get("variants") or {})):
        recovered = build_hive_create_pending_payload(
            agent,
            stored,
            fallback_task_id=fallback_task_id,
        )
        agent._hive_create_pending[session_id] = dict(recovered)
        return recovered

    if not allow_history_recovery:
        return None
    recovered = agent._recover_hive_create_pending_from_history(
        history=list((source_context or {}).get("conversation_history") or []),
        fallback_task_id=fallback_task_id,
    )
    if recovered is not None:
        agent._remember_hive_create_pending(session_id, recovered)
    return recovered


recover_hive_create_pending_from_history = pending_history_recover_hive_create_pending_from_history
