from __future__ import annotations

from typing import Any

from core import audit_logger
from core.retrieval_observability import begin_web_retrieval, finish_web_retrieval


def live_info_search_notes_with_fallback(
    agent: Any,
    *,
    session_id: str,
    user_input: str,
    query: str,
    live_mode: str,
    interpretation: Any,
    source_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run this lane's web search and RECEIPT it, whichever attempt answers.

    This is the lane a plain "what is X right now" question lands in, and at
    base it was the one lane that reached keyed search providers and published
    no `web_retrieval_receipt` at all — only a `runtime_tool_observation` with a
    bare count. So the surface that actually answers current-information
    questions left nothing any reader could use to prove which provider ran, or
    that a provider ran at all. The typed live-data lane receipts
    (`core.live_data_retrieval_receipts`) and the reasoning fallback receipts
    (`research_tool_loop_facade`); this one now does too, from the same writer,
    so the three cannot describe the same kind of work three different ways.

    The receipt spans BOTH attempts on purpose. They are one retrieval of one
    question — the second is a rephrasing of the first, not a new demand — and
    emitting two receipts would let a turn that searched once report two.

    `source_context` is optional so the seam test that binds this function by
    identity keeps working unchanged; without it there is no turn to record
    onto and the receipt is skipped rather than invented onto a foreign turn.
    """
    receipt = begin_web_retrieval(
        source_context,
        kind="live_info_fast_path",
        query=query or user_input,
        task_id=str(session_id or ""),
        action="live_info_search",
    )
    try:
        notes = agent._live_info_search_notes(
            query=query,
            live_mode=live_mode,
            interpretation=interpretation,
        )
        # For news, the query is already the extracted topic; re-searching the raw message would only
        # re-introduce the conversational filler that polluted the results. Keep the raw-text retry for
        # the other lookup modes, where it can recover a phrasing the normalizer narrowed too far.
        raw_user_input = str(user_input or "").strip()
        if not notes and query != raw_user_input and live_mode != "news":
            notes = agent._live_info_search_notes(
                query=raw_user_input,
                live_mode=live_mode,
                interpretation=interpretation,
            )
        finish_web_retrieval(source_context, receipt, notes=list(notes or []))
        return notes
    except Exception as exc:
        # The lane returns [] on failure so the turn can still answer honestly.
        # The receipt is what stops that [] from being indistinguishable from
        # "searched, found nothing" — the exact conflation that let a failed
        # retrieval be rescued by an unreceipted fallback and reported as work.
        finish_web_retrieval(source_context, receipt, failure=exc)
        audit_logger.log(
            "agent_live_info_fast_path_error",
            target_id=session_id,
            target_type="session",
            details={"error": str(exc), "query": query, "mode": live_mode},
        )
        return []
