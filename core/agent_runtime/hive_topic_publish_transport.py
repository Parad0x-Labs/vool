from __future__ import annotations

from typing import Any


def publish_topic_with_admission_retry(
    agent: Any,
    *,
    title: str,
    summary: str,
    topic_tags: list[str],
    linked_task_id: str,
) -> dict[str, Any]:
    result: dict[str, Any] | None = None
    error_text = ""
    try:
        result = agent.public_hive_bridge.create_public_topic(
            title=title,
            summary=summary,
            topic_tags=topic_tags,
            linked_task_id=linked_task_id,
            idempotency_key=f"{linked_task_id}:hive_create",
        )
    except Exception as exc:
        error_text = str(exc or "").strip()
        lowered_error = error_text.lower()
        if "user command instead of agent analysis" in lowered_error:
            retry_title, retry_summary, _ = agent._shape_public_hive_admission_safe_copy(
                title=title,
                summary=summary,
                force=True,
            )
            if retry_title != title or retry_summary != summary:
                try:
                    result = agent.public_hive_bridge.create_public_topic(
                        title=retry_title,
                        summary=retry_summary,
                        topic_tags=topic_tags,
                        linked_task_id=linked_task_id,
                        idempotency_key=f"{linked_task_id}:hive_create",
                    )
                except Exception as retry_exc:
                    error_text = str(retry_exc or error_text).strip()
                else:
                    if result.get("ok") and str(result.get("topic_id") or "").strip():
                        title = retry_title
                        error_text = ""
                    else:
                        status = str(result.get("status") or "admission_blocked").strip() or "admission_blocked"
                        return {
                            "ok": False,
                            "status": status,
                            "details": {"status": status, **dict(result)},
                        }
    if error_text:
        lowered_error = error_text.lower()
        status = (
            "invalid_auth"
            if "unauthorized" in lowered_error
            else "admission_blocked"
            if "brain hive admission blocked" in lowered_error
            else "topic_failed"
        )
        return {"ok": False, "status": status, "details": {"status": status}, "error": error_text}
    result = dict(result or {})
    topic_id = str(result.get("topic_id") or "").strip()
    if not result.get("ok") or not topic_id:
        status = str(result.get("status") or "topic_failed").strip() or "topic_failed"
        return {"ok": False, "status": status, "details": {"status": status, **result}}
    return {"ok": True, "topic_id": topic_id, "title": title}
