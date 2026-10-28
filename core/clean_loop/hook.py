"""The one runtime entry: run an admitted turn through the clean loop and shape a normal result.

`maybe_answer` returns None for every turn the route does not admit (and whenever anything needed to
run the loop is missing), so the caller falls through to the existing pipeline unchanged. An
admitted turn returns the agent's own fast-path result envelope with reason `clean_local_loop`,
plus what actually happened: the model calls, the tools that ran (as `tool_receipts`, the shape the
honesty validator and the machine fast paths already use, and as `tool_executed`/`tool_failed`
runtime events, which the execution-truth ledger records), and any pending proposals.
"""
from __future__ import annotations

from typing import Any

from core.clean_loop import route
from core.clean_loop.loop import NO_REPLY_TEXT, run_loop
from core.clean_loop.registry import ToolContext, default_registry

REASON = "clean_local_loop"
_HISTORY_TURNS = 6
_HISTORY_CHARS = 800


def _history(source_context: dict[str, Any], current: str) -> list[dict[str, str]]:
    rows = [
        {"role": str(row.get("role")), "content": str(row.get("content") or "")[:_HISTORY_CHARS]}
        for row in list(source_context.get("conversation_history") or [])
        if isinstance(row, dict) and row.get("role") in {"user", "assistant"} and str(row.get("content") or "").strip()
    ]
    if rows and rows[-1]["role"] == "user" and rows[-1]["content"].strip() == current.strip():
        rows = rows[:-1]
    return rows[-_HISTORY_TURNS:]


def _emit_tool_events(agent: Any, source_context: dict[str, Any], receipts: list[dict[str, Any]]) -> None:
    for receipt in receipts:
        name = str(receipt.get("tool_name") or "")
        if not name or name == "propose_change":
            continue
        ok = bool((receipt.get("execution") or {}).get("ok"))
        try:
            agent._emit_runtime_event(
                source_context,
                event_type="tool_executed" if ok else "tool_failed",
                message=f"{name} {'ran' if ok else 'did not run'} (clean local loop)",
                tool_name=name,
                status=str(receipt.get("status") or ""),
                lane=REASON,
            )
        except Exception:
            continue


def _record_observations(source_context: dict[str, Any], receipts: list[dict[str, Any]]) -> None:
    """Each read that ran is this turn's own typed observation, the support a grounded answer is
    matched against. Without it the publication gate saw a model answer with nothing retrieved
    and withheld it whenever the ask sounded current ("any new photos from this week?")."""
    rows = [
        {
            "ok": True,
            "tool_name": str(receipt.get("tool_name") or ""),
            "summary": str(receipt.get("response_preview") or ""),
            "lane": REASON,
        }
        for receipt in receipts
        if (receipt.get("execution") or {}).get("executed") and str(receipt.get("response_preview") or "").strip()
    ]
    if not rows:
        return
    try:
        from core.grounding_lifecycle import record_typed_observations

        record_typed_observations(source_context, entries=rows)
    except Exception:
        return


def maybe_answer(
    agent: Any,
    *,
    text: str,
    effective_input: str,
    session_id: str,
    source_context: dict[str, Any] | None,
    browser_turn: bool = False,
) -> dict[str, Any] | None:
    if not route.flag_on():
        return None
    context = source_context if isinstance(source_context, dict) else {}
    router = getattr(agent, "memory_router", None)

    def _resolve() -> Any:
        from core.clean_loop.brain import resolve_local_manifest

        return resolve_local_manifest(router, context) if router is not None else None

    decision = route.decide(
        text, source_context=context, session_id=session_id, resolve_brain=_resolve, browser_turn=browser_turn
    )
    context["clean_local_loop_route"] = decision.reason
    if not decision.admit:
        return None
    from core.clean_loop.brain import make_brain, model_label
    from core.clean_loop.tools import OutsideHome, home_dir

    try:
        ctx = ToolContext(home=home_dir(), source_context=context, task_id=str(context.get("turn_id") or ""))
    except OutsideHome:
        context["clean_local_loop_route"] = "home_is_root"
        return None
    provider_id, model_id = model_label(decision.brain)
    loop_result = run_loop(
        text,
        brain=make_brain(router, decision.brain, context),
        registry=default_registry(),
        ctx=ctx,
        history=_history(context, text),
    )
    if loop_result.stopped == "brain_error" and not loop_result.tools_used:
        # (`steps` also holds the error entry itself, so "no steps" never matched; the Mac audit
        # saw this when VOOL refused to load the model on low memory.)
        # The local model never answered: let the normal pipeline (and its own failover) try.
        context["clean_local_loop_route"] = "brain_error_fell_through"
        return None
    if loop_result.answer == NO_REPLY_TEXT and not loop_result.proposals:
        # No answer came out of the loop (the audit's "mac or windows" and "what is a dmg" turns):
        # an apology is never better than the normal pipeline's own try. Only reads ran so far.
        context["clean_local_loop_route"] = "no_answer_fell_through"
        return None
    _record_observations(context, ctx.receipts)
    trace = {**loop_result.trace(), "provider_id": provider_id, "model_id": model_id}
    context["tool_receipts"] = list(context.get("tool_receipts") or []) + ctx.receipts
    context[REASON] = trace
    _emit_tool_events(agent, context, ctx.receipts)
    result = agent._fast_path_result(
        session_id=session_id,
        user_input=effective_input,
        response=loop_result.answer,
        confidence=0.8,
        source_context=context,
        reason=REASON,
    )
    result["model_execution"] = {
        "source": REASON,
        "used_model": loop_result.model_calls > 0,
        "provider_id": provider_id,
        "model_id": model_id,
        "model_calls": loop_result.model_calls,
    }
    result["model_selected"] = model_id
    result["route_skips"] = [skip for skip in list(result.get("route_skips") or []) if skip not in {"model", "tool_loop"}]
    result["fast_path_hit"] = False
    result["tool_receipts"] = list(ctx.receipts)
    result[REASON] = trace
    if loop_result.proposals:
        result["pending_proposals"] = list(loop_result.proposals)
    route.note_clean_turn(session_id)
    return result


__all__ = ["REASON", "maybe_answer"]
