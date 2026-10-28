"""The one runtime entry: run an admitted turn through the clean loop and shape a normal result.

`maybe_answer` returns None for every turn the route does not admit (and whenever anything needed to
run the loop is missing), so the caller falls through to the existing pipeline unchanged. An
admitted turn returns the agent's own fast-path result envelope with reason `clean_local_loop`,
plus what actually happened: the model calls, the tools that ran (as `tool_receipts`, the shape the
honesty validator and the machine fast paths already use, and as `tool_executed`/`tool_failed`
runtime events, which the execution-truth ledger records), and any pending proposals.
"""
from __future__ import annotations

import logging

from typing import Any

from core.clean_loop import route
from core.clean_loop.loop import NO_REPLY_TEXT, run_loop
from core.clean_loop.registry import ToolContext, default_registry

_LOG = logging.getLogger(__name__)
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


def _record_observations(context: dict[str, Any], receipts: list[dict[str, Any]]) -> None:
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
    # Also on the turn's own observation channel: the lifecycle row is often opened later, at
    # publication, and a direct record before it exists is silently dropped (Mac clean-4: five
    # answers built from real reads were refused as "nothing retrieved"). The seal harvests it.
    channel = context.get("runtime_tool_observations")
    context["runtime_tool_observations"] = [*(channel if isinstance(channel, list) else []), *rows]
    try:
        from core.grounding_lifecycle import record_typed_observations

        record_typed_observations(context, entries=rows)
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
    settled = _settle_change(
        agent, text=text, effective_input=effective_input, session_id=session_id, context=context,
        browser_turn=browser_turn,
    )
    if settled is not None:
        return settled
    router = getattr(agent, "memory_router", None)

    def _resolve() -> Any:
        from core.clean_loop.brain import resolve_local_manifest

        return resolve_local_manifest(router, context) if router is not None else None

    def _file_words() -> set[str]:
        from core.clean_loop.tools import file_words, home_dir

        return set(file_words(home_dir()))

    decision = route.decide(
        text, source_context=context, session_id=session_id, resolve_brain=_resolve, browser_turn=browser_turn,
        file_words=_file_words,
    )
    context["clean_local_loop_route"] = decision.reason
    _LOG.info("clean loop route: %s", decision.reason)
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
    _LOG.info(
        "clean loop: stopped=%s model_calls=%d tools=%s think_ms=%d tool_ms=%d",
        loop_result.stopped, loop_result.model_calls, ",".join(loop_result.tools_used),
        sum(int(step.get("think_ms") or 0) for step in loop_result.steps),
        sum(int(step.get("tool_ms") or 0) for step in loop_result.steps),
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
    if ctx.plans and session_id:
        from core.clean_loop import changes

        changes.remember_pending(session_id, ctx.plans[-1])
    route.note_clean_turn(session_id)
    return result


def _settle_change(
    agent: Any, *, text: str, effective_input: str, session_id: str, context: dict[str, Any],
    browser_turn: bool = False,
) -> dict[str, Any] | None:
    """The owner's answer to a change card ("yes", "no"), or "undo" after one: no model, no new plan.

    Only with the changes flag on, only on the owner's own local turn (never a browser turn), and
    only a bare reply counts. A card answers the very next turn and nothing later: any other turn
    ends it, so a "yes" meant for something else never applies it. "undo" right after a change puts
    it back; after other turns it asks first, and only the next turn's "yes" puts it back."""
    if not session_id or not route.changes_enabled():
        return None
    from core.clean_loop import changes
    from core.clean_loop.tools import OutsideHome, home_dir

    verdict = route.change_reply(text)
    if browser_turn or route._owner_context_blocks(context, session_id) or route.is_red(text):
        changes.forget_session_turn(session_id)
        return None
    undo_asked = changes.take_undo_asked(session_id) if verdict in {"yes", "no"} else None
    fresh_done = changes.last_done(session_id)
    fresh = fresh_done is not None and fresh_done.fresh
    if verdict not in {"yes", "no"}:
        changes.forget_session_turn(session_id)

    def _ctx() -> ToolContext | None:
        try:
            return ToolContext(home=home_dir(), source_context=context, task_id=str(context.get("turn_id") or ""),
                               withdraw_pending_approvals=True)
        except OutsideHome:
            return None

    def _put_back(done: Any) -> dict[str, Any] | None:
        ctx = _ctx()
        if ctx is None:
            return None
        seen = len(done.direct)
        problems = changes.undo(done, _boundary_call(ctx))
        _note_direct(ctx, done.direct[seen:])
        answer = "Put back: everything is where it was before." if not problems else (
            "I put back what I could. " + " ".join(f"{p}." for p in problems[:5])
            + ' Say "undo" again once that is sorted, and I will try the rest.'
        )
        return _settled(agent, context, session_id, effective_input, answer, ctx.receipts, "change_undone")

    if undo_asked is not None:
        if verdict == "no":
            return _settled(agent, context, session_id, effective_input, "Okay, I left it as it is.", [], "undo_declined")
        return _put_back(undo_asked)
    if verdict in {"yes", "no"} and changes.has_pending(session_id):
        plan = changes.take_pending(session_id)
        if plan is None:
            return None
        if verdict == "no":
            return _settled(agent, context, session_id, effective_input, "Okay, I left everything as it was.", [], "change_declined")
        ctx = _ctx()
        if ctx is None:
            return None
        try:
            done = changes.apply(plan, _boundary_call(ctx))
        except changes.NeedsPermission as exc:
            _note_direct(ctx, getattr(exc, "direct", []))
            # Keep the card: after switching the setting, the owner's next yes applies it.
            changes.remember_pending(session_id, plan)
            return _settled(agent, context, session_id, effective_input, f"I didn't change anything: {exc}.", ctx.receipts, "change_needs_permission")
        except changes.ChangeRefused as exc:
            _note_direct(ctx, getattr(exc, "direct", []))
            left = list(getattr(exc, "left", []) or [])
            if left:
                # Part of it is still out: keep it as a change "undo" can finish.
                changes.remember_done(session_id, changes.Done(plan=plan, inverse=left, matches_preview=False,
                                                               direct=list(getattr(exc, "direct", []))))
                answer = f"Not all of it went back: {exc}. Say \"undo\" to try the rest again."
                return _settled(agent, context, session_id, effective_input, answer, ctx.receipts, "change_partly_back")
            return _settled(agent, context, session_id, effective_input, f"I didn't change anything: {exc}", ctx.receipts, "change_refused")
        changes.remember_done(session_id, done)
        count = len(plan.ops)
        if done.matches_preview:
            answer = f"Done: {count} change{'s' if count != 1 else ''}, exactly as shown. Say \"undo\" to put it all back."
        else:
            answer = (f"Done: {count} change{'s' if count != 1 else ''}. The result differs a little from what I showed "
                      "you, so please check the folder. Say \"undo\" to put it all back.")
        return _settled(agent, context, session_id, effective_input, answer, ctx.receipts, "change_applied")
    if verdict == "undo":
        done = fresh_done
        if done is None:
            return None
        if not fresh:
            changes.ask_undo(session_id, done)
            first = done.plan.lines[0] if done.plan.lines else "the last change"
            count = len(done.plan.ops)
            answer = (f"The last change I made was {count} step{'s' if count != 1 else ''}, starting with \"{first}\". "
                      "Put all of it back? Say yes to undo it.")
            return _settled(agent, context, session_id, effective_input, answer, [], "undo_confirm")
        return _put_back(done)
    return None


def _note_direct(ctx: ToolContext, direct: list[str]) -> None:
    """Removals the change made itself (an empty folder it created, a file it wrote, unedited) are
    receipts like any other step, so the turn's record says exactly what was taken away."""
    for line in direct or []:
        ctx.receipts.append(
            {
                "tool_name": "clean_loop.change_put_back",
                "mode": "tool_executed",
                "status": "executed",
                "execution": {"executed": True, "ok": True, "status": "executed"},
                "lane": REASON,
                "response_preview": str(line),
            }
        )


def _boundary_call(ctx: ToolContext):
    from core.clean_loop.tools import _call

    def call(intent: str, arguments: dict[str, Any]):
        return _call(ctx, intent, arguments)

    return call


def _settled(
    agent: Any, context: dict[str, Any], session_id: str, effective_input: str, answer: str,
    receipts: list[dict[str, Any]], how: str,
) -> dict[str, Any]:
    context["clean_local_loop_route"] = how
    _record_observations(context, receipts)
    context["tool_receipts"] = list(context.get("tool_receipts") or []) + list(receipts)
    _emit_tool_events(agent, context, receipts)
    result = agent._fast_path_result(
        session_id=session_id, user_input=effective_input, response=answer, confidence=0.95,
        source_context=context, reason=REASON,
    )
    result["tool_receipts"] = list(receipts)
    result[REASON] = {"lane": REASON, "settled": how, "model_calls": 0}
    route.note_clean_turn(session_id)
    return result


__all__ = ["REASON", "maybe_answer"]
