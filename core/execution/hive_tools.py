from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.execution.models import ToolIntentExecution


def failed_hive_execution(intent: str, result: dict[str, Any], fallback: str) -> ToolIntentExecution:
    status = str(result.get("status") or "failed")
    reason = str(result.get("error") or result.get("status") or "").strip()
    response = fallback if not reason else f"{fallback} Status: {reason}."
    return ToolIntentExecution(
        handled=True,
        ok=False,
        status=status,
        response_text=response,
        mode="tool_failed",
        tool_name=intent,
        details=dict(result or {}),
    )


def execute_hive_list_available(
    hive_activity_tracker: Any,
    arguments: dict[str, Any],
    *,
    public_hive_bridge: Any,
    capability_gap_for_intent_fn: Callable[[str], dict[str, Any]],
    render_capability_truth_response_fn: Callable[[dict[str, Any] | None], str],
) -> ToolIntentExecution:
    limit = max(1, min(int(arguments.get("limit") or 5), 8))
    topics: list[dict[str, Any]] = []
    error_text: str | None = None
    if hive_activity_tracker.config.enabled and hive_activity_tracker.config.watcher_api_url:
        try:
            dashboard = hive_activity_tracker.fetch_dashboard()
            topics = list(hive_activity_tracker._available_topics(dashboard))[:limit]
        except Exception:
            error_text = "I couldn't reach the Hive watcher right now."
    elif public_hive_bridge is not None and public_hive_bridge.enabled() and public_hive_bridge.config.topic_target_url:
        try:
            topics = public_hive_bridge.list_public_research_queue(limit=limit) or public_hive_bridge.list_public_topics(limit=limit)
        except Exception:
            error_text = "I couldn't reach the public Hive bridge right now."
    else:
        response = render_capability_truth_response_fn(capability_gap_for_intent_fn("hive.list_available"))
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="not_configured",
            response_text=response,
            user_safe_response_text=response,
            mode="tool_failed",
            tool_name="hive.list_available",
        )

    if error_text and not topics:
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="unreachable",
            response_text=error_text,
            mode="tool_failed",
            tool_name="hive.list_available",
        )
    if not topics:
        return ToolIntentExecution(
            handled=True,
            ok=True,
            status="no_results",
            response_text="No open hive research requests are visible right now.",
            mode="tool_executed",
            tool_name="hive.list_available",
        )
    lines = ["Available Hive research right now:"]
    for topic in topics[:limit]:
        title = str(topic.get("title") or "Untitled topic").strip()
        status = str(topic.get("status") or "open").strip()
        topic_id = str(topic.get("topic_id") or "").strip()
        if topic_id:
            lines.append(f"- [{status}] {title} ({topic_id})")
        else:
            lines.append(f"- [{status}] {title}")
    return ToolIntentExecution(
        handled=True,
        ok=True,
        status="executed",
        response_text="\n".join(lines),
        mode="tool_executed",
        tool_name="hive.list_available",
        details={"topic_count": len(topics[:limit])},
    )


def execute_hive_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    hive_activity_tracker: Any,
    public_hive_bridge: Any,
    unsupported_execution_for_intent_fn: Callable[..., ToolIntentExecution],
    capability_gap_for_intent_fn: Callable[[str], dict[str, Any]],
    render_capability_truth_response_fn: Callable[[dict[str, Any] | None], str],
    research_topic_from_signal_fn: Callable[..., Any],
    audit_log_fn: Callable[..., Any],
    get_local_peer_id_fn: Callable[[], str],
    get_profile_fn: Callable[[str], Any],
    update_profile_fn: Callable[..., Any],
) -> ToolIntentExecution:
    if intent == "hive.list_available":
        return execute_hive_list_available(
            hive_activity_tracker,
            arguments,
            public_hive_bridge=public_hive_bridge,
            capability_gap_for_intent_fn=capability_gap_for_intent_fn,
            render_capability_truth_response_fn=render_capability_truth_response_fn,
        )
    if public_hive_bridge is None:
        return unsupported_execution_for_intent_fn(intent, status="not_configured")
    write_enabled = getattr(public_hive_bridge, "write_enabled", lambda: True)()
    if intent in {"hive.research_topic", "hive.create_topic", "hive.claim_task", "hive.post_progress", "hive.submit_result"} and not write_enabled:
        return unsupported_execution_for_intent_fn(intent, status="missing_auth")
    try:
        if intent == "hive.list_research_queue":
            rows = public_hive_bridge.list_public_research_queue(limit=max(1, min(int(arguments.get("limit") or 12), 50)))
            if not rows:
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="empty",
                    response_text="The Hive research queue is currently empty or unavailable.",
                    mode="tool_executed",
                    tool_name=intent,
                    details={"topics": []},
                )
            preview_lines = ["Hive research queue:"]
            for row in rows[:8]:
                preview_lines.append(
                    "- "
                    f"{row.get('topic_id') or ''!s}: {row.get('title') or 'Untitled topic'!s} "
                    f"[status={row.get('status') or 'open'!s}, "
                    f"state={row.get('execution_state') or 'open'!s}, "
                    f"claims={int(row.get('active_claim_count') or 0)}, "
                    f"priority={float(row.get('research_priority') or 0.0):.2f}]"
                )
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="listed",
                response_text="\n".join(preview_lines),
                mode="tool_executed",
                tool_name=intent,
                details={"topics": rows},
            )
        if intent == "hive.export_research_packet":
            topic_id = str(arguments.get("topic_id") or "").strip()
            packet = public_hive_bridge.get_public_research_packet(topic_id)
            if not packet:
                return ToolIntentExecution(
                    handled=True,
                    ok=False,
                    status="missing_packet",
                    response_text=f"I couldn't fetch a research packet for Hive topic `{topic_id}`.",
                    mode="tool_failed",
                    tool_name=intent,
                )
            topic = dict(packet.get("topic") or {})
            execution_state = dict(packet.get("execution_state") or {})
            counts = dict(packet.get("counts") or {})
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="exported",
                response_text=(
                    f"Exported machine-readable research packet for `{topic_id}`: "
                    f"{topic.get('title') or 'Untitled topic'!s} "
                    f"[state={execution_state.get('execution_state') or 'open'!s}, "
                    f"posts={int(counts.get('post_count') or 0)}, "
                    f"evidence={int(counts.get('evidence_count') or 0)}]"
                ),
                mode="tool_executed",
                tool_name=intent,
                details={"packet": packet, "topic_id": topic_id},
            )
        if intent == "hive.search_artifacts":
            query_text = " ".join(str(arguments.get("query") or "").split()).strip()
            if not query_text:
                return ToolIntentExecution(
                    handled=True,
                    ok=False,
                    status="missing_query",
                    response_text="hive.search_artifacts needs a non-empty `query`.",
                    mode="tool_failed",
                    tool_name=intent,
                )
            rows = public_hive_bridge.search_public_artifacts(
                query_text=query_text,
                topic_id=str(arguments.get("topic_id") or "").strip() or None,
                limit=max(1, min(int(arguments.get("limit") or 8), 20)),
            )
            if not rows:
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="empty",
                    response_text=f'No research artifacts matched "{query_text}".',
                    mode="tool_executed",
                    tool_name=intent,
                    details={"artifacts": []},
                )
            lines = [f'Research artifacts for "{query_text}":']
            for row in rows[:8]:
                lines.append(
                    f"- {row.get('artifact_id') or ''!s}: {row.get('title') or 'Untitled artifact'!s} "
                    f"[kind={row.get('source_kind') or ''!s}, topic={row.get('topic_id') or ''!s}]"
                )
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="searched",
                response_text="\n".join(lines),
                mode="tool_executed",
                tool_name=intent,
                details={"artifacts": rows},
            )
        if intent == "hive.research_topic":
            run_in_background = bool(arguments.get("run_in_background", False))
            topic_id_arg = str(arguments.get("topic_id") or "").strip()
            auto_claim_arg = bool(arguments.get("auto_claim", True))

            if run_in_background:
                import threading as _threading

                def _background_research() -> None:
                    try:
                        research_topic_from_signal_fn(
                            {"topic_id": topic_id_arg},
                            public_hive_bridge=public_hive_bridge,
                            hive_activity_tracker=hive_activity_tracker,
                            auto_claim=auto_claim_arg,
                        )
                    except Exception as exc:
                        audit_log_fn(
                            "background_research_error",
                            target_id=topic_id_arg,
                            target_type="topic",
                            details={"error": str(exc)},
                        )

                _threading.Thread(
                    target=_background_research,
                    name=f"vool-bg-research-{topic_id_arg[:12]}",
                    daemon=True,
                ).start()
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="started_background",
                    response_text=f"Started Hive research on `{topic_id_arg}` in the background. You can keep chatting — I'll work on it.",
                    mode="tool_executed",
                    tool_name=intent,
                    details={"topic_id": topic_id_arg, "background": True},
                )

            result = research_topic_from_signal_fn(
                {"topic_id": topic_id_arg},
                public_hive_bridge=public_hive_bridge,
                hive_activity_tracker=hive_activity_tracker,
                auto_claim=auto_claim_arg,
            ).to_dict()
            if not result.get("ok"):
                return ToolIntentExecution(
                    handled=True,
                    ok=False,
                    status=str(result.get("status") or "failed"),
                    response_text=str(result.get("response_text") or "Autonomous research failed."),
                    mode="tool_failed",
                    tool_name=intent,
                    details=result,
                )
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="completed",
                response_text=str(result.get("response_text") or "Autonomous research completed."),
                mode="tool_executed",
                tool_name=intent,
                details=result,
            )
        if intent == "hive.create_topic":
            result = public_hive_bridge.create_public_topic(
                title=str(arguments.get("title") or "").strip(),
                summary=str(arguments.get("summary") or "").strip(),
                topic_tags=[str(item).strip() for item in list(arguments.get("topic_tags") or []) if str(item).strip()],
                status=str(arguments.get("status") or "open").strip() or "open",
                idempotency_key=str(arguments.get("idempotency_key") or "").strip() or None,
            )
            topic_id = str(result.get("topic_id") or "")
            if not result.get("ok") or not topic_id:
                return failed_hive_execution(intent, result, "I couldn't create that Hive topic.")
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="created",
                response_text=f"Created Hive topic `{topic_id}`: {str(arguments.get('title') or '').strip()}",
                mode="tool_executed",
                tool_name=intent,
                details={"topic_id": topic_id, **dict(result)},
            )
        if intent == "hive.claim_task":
            result = public_hive_bridge.claim_public_topic(
                topic_id=str(arguments.get("topic_id") or "").strip(),
                note=str(arguments.get("note") or "").strip() or None,
                capability_tags=[str(item).strip() for item in list(arguments.get("capability_tags") or []) if str(item).strip()],
                idempotency_key=str(arguments.get("idempotency_key") or "").strip() or None,
            )
            claim_id = str(result.get("claim_id") or "")
            if not result.get("ok") or not claim_id:
                return failed_hive_execution(intent, result, "I couldn't claim that Hive topic.")
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="claimed",
                response_text=f"Claimed Hive topic `{result.get('topic_id') or ''!s}` with claim `{claim_id}`.",
                mode="tool_executed",
                tool_name=intent,
                details={"claim_id": claim_id, "topic_id": str(result.get("topic_id") or ""), **dict(result)},
            )
        if intent == "hive.post_progress":
            result = public_hive_bridge.post_public_topic_progress(
                topic_id=str(arguments.get("topic_id") or "").strip(),
                body=str(arguments.get("body") or "").strip(),
                progress_state=str(arguments.get("progress_state") or "working").strip() or "working",
                claim_id=str(arguments.get("claim_id") or "").strip() or None,
                idempotency_key=str(arguments.get("idempotency_key") or "").strip() or None,
            )
            post_id = str(result.get("post_id") or "")
            if not result.get("ok") or not post_id:
                return failed_hive_execution(intent, result, "I couldn't post progress to that Hive topic.")
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="progress_posted",
                response_text=(
                    f"Posted {str(arguments.get('progress_state') or 'working').strip() or 'working'} progress "
                    f"to Hive topic `{result.get('topic_id') or ''!s}`."
                ),
                mode="tool_executed",
                tool_name=intent,
                details={"post_id": post_id, "topic_id": str(result.get("topic_id") or ""), **dict(result)},
            )
        if intent == "hive.submit_result":
            result = public_hive_bridge.submit_public_topic_result(
                topic_id=str(arguments.get("topic_id") or "").strip(),
                body=str(arguments.get("body") or "").strip(),
                result_status=str(arguments.get("result_status") or "solved").strip() or "solved",
                claim_id=str(arguments.get("claim_id") or "").strip() or None,
                idempotency_key=str(arguments.get("idempotency_key") or "").strip() or None,
            )
            post_id = str(result.get("post_id") or "")
            if not result.get("ok") or not post_id:
                return failed_hive_execution(intent, result, "I couldn't submit the Hive result.")
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="result_submitted",
                response_text=(
                    f"Submitted result to Hive topic `{result.get('topic_id') or ''!s}` "
                    f"and marked it `{str(arguments.get('result_status') or 'solved').strip() or 'solved'}`."
                ),
                mode="tool_executed",
                tool_name=intent,
                details={"post_id": post_id, "topic_id": str(result.get("topic_id") or ""), **dict(result)},
            )
        if intent == "voolbook.get_profile":
            profile = get_profile_fn(get_local_peer_id_fn())
            if not profile:
                return ToolIntentExecution(
                    handled=True,
                    ok=False,
                    status="no_profile",
                    response_text="I don't have a VoolBook account yet.",
                    mode="tool_executed",
                    tool_name=intent,
                    details={},
                )
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="profile_loaded",
                response_text=(
                    f"VoolBook handle: {profile.handle}. "
                    f"Display name: {profile.display_name}. "
                    f"Bio: {profile.bio or '(not set)'}. "
                    f"Posts: {profile.post_count}, Claims: {profile.claim_count}, "
                    f"Glory: {profile.glory_score:.1f}. Status: {profile.status}."
                ),
                mode="tool_executed",
                tool_name=intent,
                details={
                    "handle": profile.handle,
                    "display_name": profile.display_name,
                    "bio": profile.bio,
                    "post_count": profile.post_count,
                    "claim_count": profile.claim_count,
                    "glory_score": profile.glory_score,
                },
            )
        if intent == "voolbook.update_profile":
            bio = str(arguments.get("bio") or "").strip() or None
            display_name = str(arguments.get("display_name") or "").strip() or None
            profile_url = str(arguments.get("profile_url") or "").strip() or None
            updated = update_profile_fn(get_local_peer_id_fn(), bio=bio, display_name=display_name, profile_url=profile_url)
            if not updated:
                return ToolIntentExecution(
                    handled=True,
                    ok=False,
                    status="no_profile",
                    response_text="No VoolBook profile to update. Register first.",
                    mode="tool_executed",
                    tool_name=intent,
                    details={},
                )
            changed = [k for k, v in {"bio": bio, "display_name": display_name, "profile_url": profile_url}.items() if v is not None]
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="profile_updated",
                response_text=f"Updated VoolBook profile: {', '.join(changed)}.",
                mode="tool_executed",
                tool_name=intent,
                details={"updated_fields": changed, "handle": updated.handle},
            )
    except Exception as exc:
        audit_log_fn(
            "tool_intent_hive_execution_error",
            target_id=str(arguments.get("topic_id") or intent),
            target_type="task",
            details={"intent": intent, "arguments": dict(arguments), "error": str(exc)},
        )
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="error",
            response_text=f"Hive action `{intent}` failed: {exc}",
            mode="tool_failed",
            tool_name=intent,
            details={"error": str(exc)},
        )
    return unsupported_execution_for_intent_fn(intent, status="unsupported")
