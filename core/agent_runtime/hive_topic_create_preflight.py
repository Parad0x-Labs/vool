from __future__ import annotations

from typing import Any

from core.credit_ledger import estimate_hive_task_credit_cost


def maybe_handle_hive_topic_create_request(
    agent: Any,
    user_input: str,
    *,
    task: Any,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    draft = agent._extract_hive_topic_create_draft(user_input)
    if draft is None:
        return None

    if not agent.public_hive_bridge.enabled():
        return _create_preview_result(
            agent,
            task=task,
            session_id=session_id,
            user_input=user_input,
            source_context=source_context,
            response="Public Hive is not enabled on this runtime, so I can't create a live Hive task. Hive truth: future/unsupported.",
            reason="hive_topic_create_disabled",
            success=False,
            details={"status": "disabled"},
            dispatch_status="disabled",
            mode_override="tool_failed",
            task_outcome="failed",
        )
    if not agent.public_hive_bridge.write_enabled():
        return _create_preview_result(
            agent,
            task=task,
            session_id=session_id,
            user_input=user_input,
            source_context=source_context,
            response="Hive task creation is disabled here because public Hive auth is not configured for writes. Hive truth: future/unsupported.",
            reason="hive_topic_create_missing_auth",
            success=False,
            details={"status": "missing_auth"},
            dispatch_status="missing_auth",
            mode_override="tool_failed",
            task_outcome="failed",
        )

    variant_result = agent._build_hive_create_pending_variants(
        raw_input=user_input,
        draft=draft,
        task_id=task.task_id,
    )
    if not bool(variant_result.get("ok")):
        return _create_preview_result(
            agent,
            task=task,
            session_id=session_id,
            user_input=user_input,
            source_context=source_context,
            response=str(variant_result.get("response") or "I won't create that Hive task."),
            reason=str(variant_result.get("reason") or "hive_topic_create_privacy_blocked"),
            success=False,
            details={
                "status": "privacy_blocked",
                "privacy_risks": list(variant_result.get("privacy_risks") or []),
            },
            dispatch_status="privacy_blocked",
            mode_override="tool_failed",
            task_outcome="failed",
        )

    pending = dict(variant_result.get("pending") or {})
    improved_variant = dict((pending.get("variants") or {}).get("improved") or {})
    title = str(improved_variant.get("title") or "").strip()
    summary = str(improved_variant.get("summary") or "").strip() or title
    preview_note = str(improved_variant.get("preview_note") or "")
    topic_tags = [
        str(item).strip()
        for item in list(improved_variant.get("topic_tags") or [])
        if str(item).strip()
    ][:8]
    if len(title) < 4:
        return _create_preview_result(
            agent,
            task=task,
            session_id=session_id,
            user_input=user_input,
            source_context=source_context,
            response=(
                "I can create the Hive task, but I still need a concrete title. "
                'Use a format like: create new task in Hive: "better watcher task UX".'
            ),
            reason="hive_topic_create_missing_title",
            success=False,
            details={"status": "missing_title"},
            dispatch_status="missing_title",
            mode_override="tool_failed",
            task_outcome="failed",
            confidence=0.42,
        )

    dup = agent._check_hive_duplicate(title, summary)
    agent._remember_hive_create_pending(session_id, pending)
    estimated_cost = estimate_hive_task_credit_cost(
        title,
        summary,
        topic_tags=topic_tags,
        auto_start_research=bool(improved_variant.get("auto_start_research")),
    )
    dup_warning = ""
    if dup:
        dup_title = dup.get("title", "")
        dup_id = str(dup.get("topic_id") or "")[:8]
        dup_warning = (
            f"\n\nHeads up -- a similar topic already exists: "
            f"**{dup_title}** (#{dup_id}). Still want to create a new one?"
        )
    preview = agent._format_hive_create_preview(
        pending=pending,
        estimated_cost=estimated_cost,
        dup_warning=dup_warning,
        preview_note=preview_note,
    )
    return _create_preview_result(
        agent,
        task=task,
        session_id=session_id,
        user_input=user_input,
        source_context=source_context,
        response=preview,
        reason="hive_topic_create_awaiting_confirmation",
        success=True,
        details={
            "status": "awaiting_confirmation",
            "title": title,
            "topic_tags": topic_tags,
            "default_variant": str(pending.get("default_variant") or "improved"),
        },
        dispatch_status="awaiting_confirmation",
        mode_override="tool_preview",
        task_outcome="pending_approval",
        confidence=0.95,
    )


def _create_preview_result(
    agent: Any,
    *,
    task: Any,
    session_id: str,
    user_input: str,
    source_context: dict[str, object] | None,
    response: str,
    reason: str,
    success: bool,
    details: dict[str, Any],
    dispatch_status: str,
    mode_override: str,
    task_outcome: str,
    confidence: float = 0.9,
) -> dict[str, Any]:
    return agent._action_fast_path_result(
        task_id=task.task_id,
        session_id=session_id,
        user_input=user_input,
        response=response,
        confidence=confidence,
        source_context=source_context,
        reason=reason,
        success=success,
        details=details,
        mode_override=mode_override,
        task_outcome=task_outcome,
        workflow_summary=agent._action_workflow_summary(
            operator_kind="hive.create_topic",
            dispatch_status=dispatch_status,
            details={"action_id": ""},
        ),
    )
