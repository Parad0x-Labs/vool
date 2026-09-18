from __future__ import annotations

from typing import Any

from core.identity_manager import load_active_persona


def should_use_chat_truth_wording(
    agent: Any,
    *,
    source_context: dict[str, object] | None,
    live_mode: str,
    notes: list[dict[str, Any]],
    wants_prose: bool = False,
) -> bool:
    # News/weather normally render as a deterministic structured list. When the user explicitly asked
    # to summarize ("...latest news! summary", "not links"), drop "news" from the structured set so the
    # already-fetched notes are synthesized into prose instead of returned as a link list.
    structured_modes = {"weather"} if wants_prose else {"weather", "news"}
    return (
        agent._is_chat_truth_surface(source_context)
        and live_mode not in structured_modes
        and agent._first_live_quote(notes) is None
    )


def chat_truth_live_info_result(
    agent: Any,
    *,
    session_id: str,
    user_input: str,
    query: str,
    live_mode: str,
    notes: list[dict[str, Any]],
    source_context: dict[str, object] | None,
    interpretation: Any,
    response_class: Any,
    response: str,
) -> dict[str, Any]:
    return agent._chat_surface_model_wording_result(
        session_id=session_id,
        user_input=user_input,
        source_context=source_context,
        persona=load_active_persona(agent.persona_id),
        interpretation=interpretation,
        task_class="research",
        response_class=response_class,
        reason="live_info_model_wording",
        model_input=agent._chat_surface_live_info_model_input(
            user_input=user_input,
            query=query,
            mode=live_mode,
            notes=notes,
            runtime_note="" if notes else response,
        ),
        fallback_response=(
            "I pulled live evidence for this turn, but I couldn't produce a clean final synthesis in this run."
            if notes
            else response
        ),
        tool_backing_sources=["web_lookup"] if notes else [],
    )
