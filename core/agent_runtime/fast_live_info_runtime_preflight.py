from __future__ import annotations

from typing import Any

from core import policy_engine
from core.retrieval_constraints import analyze_retrieval_constraints
from core.stipulated_frame import stipulated_frame_active

from .fast_live_info_runtime_results import disabled_live_info_result


def prepare_live_info_request(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    interpretation: Any,
) -> tuple[str, str, dict[str, Any] | None]:
    from core.agent_runtime.fast_live_info_price import price_request_leaves_unresolved_content

    if stipulated_frame_active(user_input, source_context=source_context):
        return "", "", None
    constraints = analyze_retrieval_constraints(user_input)
    candidate_input = constraints.eligible_text
    if not candidate_input or constraints.forbids_candidate(candidate_input):
        return "", "", None

    if price_request_leaves_unresolved_content(candidate_input):
        # A price-shaped request that names something the deterministic alias table cannot
        # resolve must not be answered as if the extra name never existed (the silent drop that
        # produced the original incident) and must not fall through to the tools-less "unknown"
        # chat lane either -- that lane is exactly what rendered an unexecuted tool-call as the
        # final answer on 2026-08-03. Decline the fast path outright and flag the turn so
        # `should_keep_ai_first_chat_lane` and `should_attempt_tool_intent` route it to a lane
        # where a real tool is actually offered, instead of a regex guessing at the missing name.
        if isinstance(source_context, dict):
            source_context["live_info_partial_unresolved"] = True
        return "", "", None

    live_mode = agent._live_info_mode(candidate_input, interpretation=interpretation)
    recovered_query = agent._recover_price_lookup_query(
        candidate_input,
        source_context=source_context,
    )
    if not live_mode and recovered_query:
        live_mode = "fresh_lookup"
    if not live_mode:
        return "", "", None
    if _explicit_remote_fetch_disabled(source_context):
        return "", "", None
    if not policy_engine.allow_web_fallback():
        return live_mode, "", disabled_live_info_result(
            agent,
            session_id=session_id,
            user_input=user_input,
            source_context=source_context,
        )

    query = recovered_query or agent._normalize_live_info_query(candidate_input, mode=live_mode)
    if agent._requires_ultra_fresh_insufficient_evidence(candidate_input):
        response = agent._ultra_fresh_insufficient_evidence_response(query=query)
        return live_mode, query, agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=response,
            confidence=0.9,
            source_context=source_context,
            reason="live_info_insufficient_evidence",
        )
    return live_mode, query, None


def _explicit_remote_fetch_disabled(source_context: dict[str, object] | None) -> bool:
    if not isinstance(source_context, dict) or "allow_remote_fetch" not in source_context:
        return False
    # Explicit false means the caller is forcing a local-only turn. Do not let
    # the live-info fast path reinterpret a trusted surface as permission to browse.
    return not bool(source_context.get("allow_remote_fetch"))
