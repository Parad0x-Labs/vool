from __future__ import annotations

import contextlib
from typing import Any


def maybe_handle_hive_research_followup(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    session_hive_state_fn: Any,
    clear_hive_interaction_state_fn: Any,
    set_hive_interaction_state_fn: Any,
    research_topic_from_signal_fn: Any,
) -> dict[str, Any] | None:
    clean = " ".join(str(user_input or "").split()).strip()
    lowered = clean.lower()
    hive_state = session_hive_state_fn(session_id)
    if agent._is_pending_hive_create_confirmation_input(
        clean,
        session_id=session_id,
        source_context=source_context,
        hive_state=hive_state,
    ):
        return None

    active_resume = agent._maybe_resume_active_hive_task(
        lowered, session_id=session_id, source_context=source_context, hive_state=hive_state,
    )
    if active_resume is not None:
        return active_resume

    topic_hint = agent._extract_hive_topic_hint(clean)
    history = list((source_context or {}).get("conversation_history") or [])
    pending_topic_ids = [
        str(item).strip()
        for item in list(hive_state.get("pending_topic_ids") or [])
        if str(item).strip()
    ]
    shown_titles = agent._interaction_shown_titles(hive_state)
    if not agent._looks_like_hive_research_followup(
        lowered,
        topic_hint=topic_hint,
        has_pending_topics=bool(pending_topic_ids),
        shown_titles=shown_titles,
        history_has_task_list=agent._history_mentions_hive_task_list(history)
        or str(hive_state.get("interaction_mode") or "") == "hive_task_selection_pending",
    ):
        return None
    if not agent.public_hive_bridge.enabled():
        response = "Public Hive is not enabled on this runtime, so I can't claim a live Hive task."
        if agent._is_chat_truth_surface(source_context):
            return agent._chat_surface_hive_wording_result(
                session_id=session_id,
                user_input=clean,
                source_context=source_context,
                response_class=agent.ResponseClass.TASK_FAILED_USER_SAFE,
                reason="hive_research_followup_model_wording",
                observations={
                    "channel": "hive",
                    "kind": "unsupported",
                    "truth_source": "future_or_unsupported",
                    "truth_label": "future/unsupported",
                    "truth_status": "disabled",
                    "presence_claim_state": "unsupported",
                    "presence_truth_label": "future/unsupported",
                    "presence_note": "public Hive is not enabled on this runtime",
                },
                fallback_response=response,
            )
        return agent._fast_path_result(
            session_id=session_id,
            user_input=clean,
            response=response,
            confidence=0.9,
            source_context=source_context,
            reason="hive_research_followup",
        )
    if not agent.public_hive_bridge.write_enabled():
        response = "Hive task claiming is disabled here because public Hive auth is not configured for writes."
        if agent._is_chat_truth_surface(source_context):
            return agent._chat_surface_hive_wording_result(
                session_id=session_id,
                user_input=clean,
                source_context=source_context,
                response_class=agent.ResponseClass.TASK_FAILED_USER_SAFE,
                reason="hive_research_followup_model_wording",
                observations={
                    "channel": "hive",
                    "kind": "unsupported",
                    "truth_source": "future_or_unsupported",
                    "truth_label": "future/unsupported",
                    "truth_status": "write_disabled",
                    "presence_claim_state": "unsupported",
                    "presence_truth_label": "future/unsupported",
                    "presence_note": "public Hive writes are not configured on this runtime",
                },
                fallback_response=response,
            )
        return agent._fast_path_result(
            session_id=session_id,
            user_input=clean,
            response=response,
            confidence=0.9,
            source_context=source_context,
            reason="hive_research_followup",
        )

    queue_rows = agent.public_hive_bridge.list_public_research_queue(limit=12)
    ambiguous_selection = agent._looks_like_ambiguous_hive_selection_followup(
        lowered,
        has_pending_topics=bool(pending_topic_ids),
        history_has_task_list=agent._history_mentions_hive_task_list(history)
        or str(hive_state.get("interaction_mode") or "") == "hive_task_selection_pending",
    )
    selection_scope = agent._interaction_scoped_queue_rows(queue_rows, hive_state) or queue_rows
    allow_default_pick = not ambiguous_selection or len(selection_scope) <= 1
    signal = agent._select_hive_research_signal(
        queue_rows,
        lowered=lowered,
        topic_hint=topic_hint,
        pending_topic_ids=agent._interaction_pending_topic_ids(hive_state) or pending_topic_ids,
        allow_default_pick=allow_default_pick,
    )
    if signal is None:
        if queue_rows and ambiguous_selection:
            response = agent._render_hive_research_queue_choices(
                selection_scope,
                lead="I still have multiple real Hive tasks open. Pick one by name or short `#id` and I’ll start there.",
            )
            if agent._is_chat_truth_surface(source_context):
                return agent._chat_surface_hive_wording_result(
                    session_id=session_id,
                    user_input=clean,
                    source_context=source_context,
                    response_class=agent.ResponseClass.TASK_SELECTION_CLARIFICATION,
                    reason="hive_research_followup_model_wording",
                    observations=agent._chat_surface_hive_queue_observations(
                        selection_scope,
                        lead="Multiple matching open Hive tasks are still available.",
                        truth_payload=agent._bridge_hive_truth_from_rows(selection_scope),
                    ),
                    fallback_response=response,
                )
            return agent._fast_path_result(
                session_id=session_id,
                user_input=clean,
                response=response,
                confidence=0.9,
                source_context=source_context,
                reason="hive_research_followup",
            )
        if topic_hint:
            response = f"I couldn't find an open Hive task matching `#{topic_hint}`."
        else:
            response = "I couldn't map that follow-up to a concrete open Hive task."
        if agent._is_chat_truth_surface(source_context):
            return agent._chat_surface_hive_wording_result(
                session_id=session_id,
                user_input=clean,
                source_context=source_context,
                response_class=agent.ResponseClass.TASK_SELECTION_CLARIFICATION,
                reason="hive_research_followup_model_wording",
                observations={
                    "channel": "hive",
                    "kind": "selection_clarification",
                    **agent._hive_truth_observation_fields(agent._bridge_hive_truth_from_rows(queue_rows)),
                },
                fallback_response=response,
            )
        return agent._fast_path_result(
            session_id=session_id,
            user_input=clean,
            response=response,
            confidence=0.84,
            source_context=source_context,
            reason="hive_research_followup",
        )

    topic_id = str(signal.get("topic_id") or "").strip()
    title = str(signal.get("title") or topic_id or "Hive topic").strip()
    clear_hive_interaction_state_fn(session_id)

    wants_background = any(
        marker in lowered
        for marker in ("background", "in the background", "while we chat", "while i chat", "keep chatting")
    )
    if wants_background:
        import threading as _threading

        _signal = dict(signal)
        _bridge = agent.public_hive_bridge
        _curiosity = agent.curiosity
        _tracker = agent.hive_activity_tracker

        def _bg_research() -> None:
            with contextlib.suppress(Exception):
                research_topic_from_signal_fn(
                    _signal,
                    public_hive_bridge=_bridge,
                    curiosity=_curiosity,
                    hive_activity_tracker=_tracker,
                    session_id=session_id,
                    auto_claim=True,
                )

        _threading.Thread(target=_bg_research, name=f"bg-research-{topic_id[:12]}", daemon=True).start()
        response = f"Started Hive research on `{title}` in the background. We can keep chatting — I'll work on it."
        return agent._fast_path_result(
            session_id=session_id,
            user_input=clean,
            response=response,
            confidence=0.92,
            source_context=source_context,
            reason="hive_research_background",
        )

    agent._sync_public_presence(status="busy", source_context=source_context)
    result = research_topic_from_signal_fn(
        signal,
        public_hive_bridge=agent.public_hive_bridge,
        curiosity=agent.curiosity,
        hive_activity_tracker=agent.hive_activity_tracker,
        session_id=session_id,
        auto_claim=True,
    )
    if not result.ok:
        response = str(result.response_text or f"Failed to start Hive research for `{topic_id}`.").strip()
        if agent._is_chat_truth_surface(source_context):
            return agent._chat_surface_hive_wording_result(
                session_id=session_id,
                user_input=clean,
                source_context=source_context,
                response_class=agent.ResponseClass.TASK_FAILED_USER_SAFE,
                reason="hive_research_followup_model_wording",
                observations=agent._chat_surface_hive_research_result_observations(
                    topic_id=topic_id,
                    title=title,
                    result=result,
                ),
                fallback_response=response,
            )
        return agent._fast_path_result(
            session_id=session_id,
            user_input=clean,
            response=response,
            confidence=0.84,
            source_context=source_context,
            reason="hive_research_followup",
        )

    set_hive_interaction_state_fn(
        session_id,
        mode="hive_task_active",
        payload={
            "active_topic_id": topic_id,
            "active_title": title,
            "claim_id": str(result.claim_id or "").strip(),
        },
    )

    summary = [
        f"Started Hive research on `{title}` (#{topic_id[:8]}).",
    ]
    if result.claim_id:
        summary.append(f"Claim `{result.claim_id[:8]}` is active.")
    query_count = len(list((result.details or {}).get("query_results") or []))
    if result.status == "completed":
        summary.append("The first bounded research pass already ran and posted its result.")
    else:
        summary.append("The research lane is active.")
    if query_count:
        summary.append(f"Bounded queries run: {query_count}.")
    if result.artifact_ids:
        summary.append(f"Artifacts packed: {len(result.artifact_ids)}.")
    if result.candidate_ids:
        summary.append(f"Candidate notes: {len(result.candidate_ids)}.")
    if str(result.result_status or "").strip().lower() == "researching":
        summary.append(
            "This fast reply only means the first bounded research pass finished."
        )
        summary.append(
            "Topic stays `researching` because VOOL still needs more evidence before it can honestly mark the task solved."
        )
    response = " ".join(summary)
    return agent._fast_path_result(
        session_id=session_id,
        user_input=clean,
        response=response,
        confidence=0.9,
        source_context=source_context,
        reason="hive_research_followup",
    )


def maybe_resume_active_hive_task(
    agent: Any,
    lowered: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    hive_state: dict[str, Any],
    set_hive_interaction_state_fn: Any,
    research_topic_from_signal_fn: Any,
) -> dict[str, Any] | None:
    interaction_mode = str(hive_state.get("interaction_mode") or "").strip().lower()
    if interaction_mode != "hive_task_active":
        return None
    if not agent._is_proceed_message(lowered):
        return None
    payload = dict(hive_state.get("interaction_payload") or {})
    topic_id = str(payload.get("active_topic_id") or "").strip()
    title = str(payload.get("active_title") or topic_id or "Hive topic").strip()
    if not topic_id:
        return None
    if not agent.public_hive_bridge.enabled():
        return None

    agent._sync_public_presence(status="busy", source_context=source_context)
    result = research_topic_from_signal_fn(
        {"topic_id": topic_id},
        public_hive_bridge=agent.public_hive_bridge,
        curiosity=agent.curiosity,
        hive_activity_tracker=agent.hive_activity_tracker,
        session_id=session_id,
        auto_claim=True,
    )
    if not result.ok:
        response = str(result.response_text or f"Research on `{title}` didn't complete cleanly.").strip()
        return agent._fast_path_result(
            session_id=session_id,
            user_input=lowered,
            response=response,
            confidence=0.84,
            source_context=source_context,
            reason="hive_research_active_resume",
        )

    set_hive_interaction_state_fn(
        session_id,
        mode="hive_task_active",
        payload={
            "active_topic_id": topic_id,
            "active_title": title,
            "claim_id": str(result.claim_id or "").strip(),
        },
    )

    quality = dict((result.details or {}).get("quality_summary") or {})
    q_status = str(quality.get("research_quality_status") or result.result_status or "researching").strip()
    query_count = len(list((result.details or {}).get("query_results") or []))
    nonempty = int(quality.get("nonempty_query_count") or 0)
    promoted = int(quality.get("promoted_finding_count") or 0)
    domains = int(quality.get("source_domain_count") or 0)

    summary_parts = [f"Research on `{title}` (#{topic_id[:8]}) completed."]
    if result.claim_id:
        summary_parts.append(f"Claim `{result.claim_id[:8]}` is active.")
    summary_parts.append(f"Quality: {q_status}.")
    if query_count:
        summary_parts.append(f"Queries: {nonempty}/{query_count} returned evidence.")
    if domains:
        summary_parts.append(f"Source domains: {domains}.")
    if promoted:
        summary_parts.append(f"Promoted findings: {promoted}.")
    if result.artifact_ids:
        summary_parts.append(f"Artifacts: {len(result.artifact_ids)}.")
    if q_status not in ("grounded", "solved"):
        summary_parts.append("Topic stays open — more evidence needed for grounded status.")
    response = " ".join(summary_parts)
    return agent._fast_path_result(
        session_id=session_id,
        user_input=lowered,
        response=response,
        confidence=0.92,
        source_context=source_context,
        reason="hive_research_active_resume",
    )
