from __future__ import annotations

from typing import Any


def recover_hive_create_pending_from_history(
    agent: Any,
    *,
    history: list[dict[str, Any]],
    fallback_task_id: str,
) -> dict[str, Any] | None:
    recent_messages = [dict(item) for item in list(history or [])[-8:] if isinstance(item, dict)]
    latest_user_text = ""
    latest_user_draft: dict[str, Any] | None = None
    for message in reversed(recent_messages):
        role = str(message.get("role") or "").strip().lower()
        content = str(message.get("content") or "")
        if not content:
            continue
        if latest_user_draft is None and role == "user":
            draft = agent._extract_hive_topic_create_draft(content)
            if draft is not None and str(draft.get("title") or "").strip():
                latest_user_text = content
                latest_user_draft = draft
                break

    if not latest_user_text or latest_user_draft is None:
        return None
    result = agent._build_hive_create_pending_variants(
        raw_input=latest_user_text,
        draft=latest_user_draft,
        task_id=fallback_task_id,
    )
    if not bool(result.get("ok")):
        return None
    return dict(result.get("pending") or {})
