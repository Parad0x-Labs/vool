from __future__ import annotations

from typing import Any

from core.agent_runtime.hive_topic_publish_effects import (
    build_hive_topic_created_response,
    maybe_reserve_hive_credits,
    maybe_start_hive_auto_research,
)
from core.agent_runtime.hive_topic_publish_failures import (
    build_hive_topic_create_failure_result,
)
from core.agent_runtime.hive_topic_publish_failures import (
    hive_topic_create_failure_text as publish_failures_hive_topic_create_failure_text,
)
from core.agent_runtime.hive_topic_publish_transport import publish_topic_with_admission_retry
from core.autonomous_topic_research import research_topic_from_signal
from core.credit_ledger import estimate_hive_task_credit_cost
from core.privacy_guard import text_privacy_risks

hive_topic_create_failure_text = publish_failures_hive_topic_create_failure_text


def execute_confirmed_hive_create(
    agent: Any,
    pending: dict[str, Any],
    *,
    task: Any,
    session_id: str,
    source_context: dict[str, object] | None,
    user_input: str,
    variant: str,
    research_topic_from_signal_fn: Any = research_topic_from_signal,
) -> dict[str, Any]:
    variants = {
        key: dict(value)
        for key, value in dict(pending.get("variants") or {}).items()
        if isinstance(value, dict)
    }
    selected = dict(variants.get(variant or "") or variants.get("improved") or {})
    title = str(selected.get("title") or pending.get("title") or "").strip()
    summary = str(selected.get("summary") or pending.get("summary") or "").strip() or title
    topic_tags = [
        str(item).strip()
        for item in list(selected.get("topic_tags") or pending.get("topic_tags") or [])
        if str(item).strip()
    ][:8]
    linked_task_id = pending.get("task_id") or task.task_id
    auto_start_research = bool(selected.get("auto_start_research") or pending.get("auto_start_research")) or agent._wants_hive_create_auto_start(user_input)
    if variant == "original" and text_privacy_risks(f"{title}\n{summary}"):
        return build_hive_topic_create_failure_result(
            agent,
            task=task,
            session_id=session_id,
            user_input=user_input,
            source_context=source_context,
            status="original_blocked",
            reason="hive_topic_create_original_privacy_blocked",
            details={"status": "original_blocked"},
            response="The original Hive draft still looks private, so I won't post it. Use `send improved` instead.",
            confidence=0.92,
        )

    estimated_cost = estimate_hive_task_credit_cost(
        title,
        summary,
        topic_tags=topic_tags,
        auto_start_research=auto_start_research,
    )

    publish_result = publish_topic_with_admission_retry(
        agent,
        title=title,
        summary=summary,
        topic_tags=topic_tags,
        linked_task_id=str(linked_task_id),
    )
    if not publish_result.get("ok"):
        status = str(publish_result.get("status") or "topic_failed")
        details = dict(publish_result.get("details") or {})
        return build_hive_topic_create_failure_result(
            agent,
            task=task,
            session_id=session_id,
            user_input=user_input,
            source_context=source_context,
            status=status,
            reason=f"hive_topic_create_{status}",
            details=details,
            error=str(publish_result.get("error") or ""),
        )

    topic_id = str(publish_result.get("topic_id") or "").strip()
    title = str(publish_result.get("title") or title).strip()
    response = build_hive_topic_created_response(
        title=title,
        topic_id=topic_id,
        topic_tags=topic_tags,
        variant=variant,
        pending=pending,
        estimated_cost=estimated_cost,
    )
    response = maybe_reserve_hive_credits(
        response=response,
        estimated_cost=estimated_cost,
        topic_id=topic_id,
    )
    response = maybe_start_hive_auto_research(
        agent,
        response=response,
        topic_id=topic_id,
        title=title,
        session_id=session_id,
        source_context=source_context,
        auto_start_research=auto_start_research,
        research_topic_from_signal_fn=research_topic_from_signal_fn,
    )

    return agent._action_fast_path_result(
        task_id=task.task_id,
        session_id=session_id,
        user_input=user_input,
        response=response,
        confidence=0.95,
        source_context=source_context,
        reason="hive_topic_create_created",
        success=True,
        details={"status": "created", "topic_id": topic_id, "topic_tags": topic_tags},
        mode_override="tool_executed",
        task_outcome="success",
        workflow_summary=agent._action_workflow_summary(
            operator_kind="hive.create_topic",
            dispatch_status="created",
            details={"action_id": topic_id},
        ),
    )
