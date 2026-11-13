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
import re
import threading
import time
from typing import Any

from core.clean_loop import route
from core.clean_loop.loop import NO_REPLY_TEXT, BoundaryStop, run_loop
from core.clean_loop.registry import ToolContext, default_registry
from core.clean_loop.tools import content_absence_claim

_LOG = logging.getLogger(__name__)
REASON = "clean_local_loop"
BRAIN_DOWN_TEXT = (
    "I couldn't run the local model just now (the computer may be short on free memory), so I didn't "
    "look at anything. Try again in a moment, or close a heavy app first."
)
_HISTORY_TURNS = 6
_HISTORY_CHARS = 800
_EVIDENCE_TTL_S = 900.0
_EVIDENCE_CHARS = 1500
_RECORD_EVIDENCE_CHARS = 6000
_EVIDENCE: dict[str, tuple[float, str]] = {}
_EVIDENCE_LOCK = threading.Lock()


def _prior_evidence(session_id: str, text: str, *, owner_local: bool) -> str:
    if not session_id or not owner_local or not route._is_follow_up(text, session_id):
        return ""
    now = time.monotonic()
    with _EVIDENCE_LOCK:
        row = _EVIDENCE.get(session_id)
        if row is None:
            return ""
        if now - row[0] >= _EVIDENCE_TTL_S:
            _EVIDENCE.pop(session_id, None)
            return ""
        return row[1]


def _remember_evidence(session_id: str, evidence: str) -> None:
    if not session_id or not evidence.strip():
        return
    with _EVIDENCE_LOCK:
        _EVIDENCE[session_id] = (time.monotonic(), evidence[:_EVIDENCE_CHARS])
        while len(_EVIDENCE) > 256:
            _EVIDENCE.pop(next(iter(_EVIDENCE)))


def _turn_evidence(seen: list[str]) -> str:
    """What this turn's tools showed (paths and the lines read), latest first, bounded: the read
    that answered usually comes last, after the searches that found it."""
    parts: list[str] = []
    room = _EVIDENCE_CHARS
    for text in reversed([str(item).strip() for item in seen if str(item or "").strip()]):
        if room <= 0:
            break
        piece = text[: min(room, _EVIDENCE_CHARS // 2 if parts == [] and len(seen) > 1 else room)]
        parts.append(piece)
        room -= len(piece) + 1
    return "\n".join(parts)[:_EVIDENCE_CHARS]


def _clear_evidence(session_id: str) -> None:
    if session_id:
        with _EVIDENCE_LOCK:
            _EVIDENCE.pop(session_id, None)


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


def _recorded_folder_label(ctx: ToolContext) -> str:
    """The folder this turn's last listing printed, as `~/Downloads`, or "" (none, or the home folder itself)."""
    for listing in reversed(ctx.listings):
        try:
            from core.clean_loop.tools import scope_path

            folder = scope_path(listing.get("path"), ctx.home)
        except Exception:
            continue
        if folder == ctx.home or not folder.is_dir():
            continue
        return "~/" + folder.relative_to(ctx.home).as_posix()
    return ""


def _history_reply(context: dict[str, Any]) -> str:
    """The last reply the owner saw in this chat (what the chat printed for the previous turn)."""
    for row in reversed(list(context.get("conversation_history") or [])):
        if isinstance(row, dict) and row.get("role") == "assistant" and str(row.get("content") or "").strip():
            return str(row.get("content"))
    return ""


def _claim_evidence(loop_result: Any, ctx: ToolContext, context: dict[str, Any], prior_evidence: str, text: str) -> list[str]:
    """What a file name, size or date in the answer may come from: this turn's tool results, the evidence the chat's
    record and the previous turn hold, the previous reply the owner saw, and the owner's own words."""
    slots = context.get("_conversation_slots") if isinstance(context.get("_conversation_slots"), dict) else {}
    listed = [f"- {item.get('name') or item.get('path') or ''}" for item in (slots.get("listed") or []) if isinstance(item, dict)]
    return [
        *[str(x) for x in getattr(loop_result, "seen", None) or []],
        *[str(r.get("response_preview") or "") for r in ctx.receipts],
        prior_evidence,
        str(slots.get("evidence") or ""),
        "\n".join(listed),
        _history_reply(context),
        text,
    ]


def _merged(first: Any, listing_step: dict[str, Any], second: Any) -> Any:
    from core.clean_loop.loop import LoopResult

    return LoopResult(
        answer=second.answer,
        steps=[*first.steps, listing_step, *second.steps],
        proposals=second.proposals,
        model_calls=first.model_calls + second.model_calls,
        stopped=second.stopped,
        think_ms=first.think_ms + second.think_ms,
        seen=[*first.seen, *second.seen],
    )


def _guard_file_claims(
    loop_result: Any, *, text: str, brain: Any, ctx: ToolContext, context: dict[str, Any], prior_evidence: str,
) -> Any:
    """A file name, size or date in the answer that no tool result of this turn (nor the chat's recorded evidence)
    holds is never published. With the chat's recorded folder the folder is listed again and the model answers from
    that listing; if the answer still names something the evidence does not hold, or there is no folder to list,
    the reply says plainly that it could not check."""
    from core.clean_loop.tools import UNCHECKED_FILE_CLAIM_TEXT, list_dir, tool_listing_answer, unbacked_file_claims

    if loop_result.stopped != "answered" or loop_result.proposals:
        return loop_result
    bad = unbacked_file_claims(loop_result.answer, _claim_evidence(loop_result, ctx, context, prior_evidence, text))
    if not bad:
        return loop_result
    slots = context.get("_conversation_slots") if isinstance(context.get("_conversation_slots"), dict) else {}
    folder = str(slots.get("path") or "").strip()
    if folder:
        started = time.monotonic()
        outcome = list_dir({"path": folder}, ctx)
        if outcome.ok:
            step = {
                "step": len(loop_result.steps), "tool": "list_dir", "args": {"path": folder}, "ok": True, "status": outcome.status,
                "empty": False, "intents": list(outcome.intents), "result_chars": len(outcome.text), "think_ms": 0,
                "tool_ms": int((time.monotonic() - started) * 1000), "by": "claim_guard",
            }
            second = run_loop(
                text, brain=brain, registry=default_registry(), ctx=ctx, history=_history(context, text),
                prior_evidence=outcome.text, looked=True,
            )
            second.seen.insert(0, outcome.text)
            merged = _merged(loop_result, step, second)
            if merged.stopped == "answered" and not merged.proposals and not unbacked_file_claims(
                merged.answer, _claim_evidence(merged, ctx, context, prior_evidence, text)
            ):
                return merged
            merged.answer = tool_listing_answer(merged.seen, ctx) or UNCHECKED_FILE_CLAIM_TEXT
            return merged
    # A tool ran this turn: its own listing is the answer, never a refusal. Only when nothing was listed at all
    # does the reply say it could not check.
    loop_result.answer = tool_listing_answer(loop_result.seen, ctx) or UNCHECKED_FILE_CLAIM_TEXT
    return loop_result


def _force_ordered_answer(loop_result: Any, *, text: str, ctx: ToolContext, context: dict[str, Any]) -> Any:
    """An ordering or counting ask about the folder this chat listed (oldest, newest, largest, smallest, how many) is
    never answered by the model from the old listing text with no tool run: a model comparing dates or sizes it was
    shown picks the wrong file with full confidence. The folder's own listing tool sorts and counts, and that is the
    reply. Applies when the ask holds an ordering word and the answer is about the files in view (it names a file, or
    the ask names nothing but the entries); an ask about anything else ("the biggest country") is left alone."""
    from core.clean_loop.tools import _file_names_in, ordered_folder_answer
    from core.execution.constants import _is_implicit_entry_ask, ordering_reads_of

    if loop_result.stopped != "answered" or loop_result.proposals or loop_result.tools_used:
        return loop_result
    slots = context.get("_conversation_slots") if isinstance(context.get("_conversation_slots"), dict) else {}
    folder = str(slots.get("path") or "").strip()
    if not folder or not ordering_reads_of(text):
        return loop_result
    if not (_file_names_in(loop_result.answer) or _is_implicit_entry_ask(text, allow_kinds=True)):
        return loop_result
    answer = ordered_folder_answer(text, folder, ctx)
    if not answer:
        return loop_result
    loop_result.steps.append({
        "step": len(loop_result.steps), "tool": "list_dir", "args": {"path": folder}, "ok": True, "status": "executed",
        "empty": False, "intents": ["machine.list_directory"], "result_chars": len(answer), "think_ms": 0, "tool_ms": 0,
        "by": "ordering_guard",
    })
    loop_result.seen.append(answer)
    loop_result.answer = answer
    return loop_result


def front_door_needs_web(text: str, context: dict[str, Any]) -> bool:
    """Whether the front door would class this ask as needing fresh or online information (weather, news,
    a lookup, a live value): the same three reads core.agent_runtime.turn_frontdoor makes before it sends an
    ask to the web switch. Used so such an ask meets the switch instead of this loop's file reads."""
    try:
        from core.execution_requirements import requirements_for

        if str(requirements_for(text, source_context=context).answer_mode).rsplit(".", 1)[-1] == "LIVE_DATA":
            return True
    except Exception:
        pass
    try:
        from core.agent_runtime.fast_live_info_mode_classifier import live_info_mode

        if live_info_mode(None, text, interpretation=None) in ("weather", "news", "fresh_lookup"):
            return True
    except Exception:
        pass
    try:
        from core.tool_demand_signals import resolve_demand_signals

        return "web" in tuple(getattr(resolve_demand_signals(text), "required_families", ()) or ())
    except Exception:
        return False


def _is_a_foreign_tool_call(answer: str) -> bool:
    """Whether the loop's answer is a tool-call envelope the loop does not speak, not prose."""
    try:
        from core.model_output_guard import foreign_markers

        return bool(foreign_markers(str(answer or "")))
    except Exception:
        return False


#: Step statuses that are a read which came back with nothing to show.
_EMPTY_STATUSES = frozenset({"repeated_failure", "error", "unsupported", "outside_home"})


def productive_steps(loop_result: Any) -> list[int]:
    """Indexes of the loop's tool steps that returned something: a read that ran, ok, and was not an empty search."""
    return [
        i for i, step in enumerate(getattr(loop_result, "steps", None) or [])
        if step.get("tool") and step.get("tool") != "propose_change" and step.get("ok")
        and not step.get("empty") and str(step.get("status") or "") not in _EMPTY_STATUSES and int(step.get("result_chars") or 0) > 0
    ]


def loop_should_decline(text: str, loop_result: Any, receipts: list[dict[str, Any]] | None = None, *, follow_up: bool = False) -> str:
    """A reason when the loop's tools did not produce evidence for this ask, "" when it may answer.

    Read from what the tools returned: no read ran at all, every search came back empty, or what was read shares
    no word with the ask (names, paths, lines). The model's own wording is only a secondary signal. Only for an
    ask that is not explicitly the owner's own: then "nothing found" is not an answer, the turn is not this
    loop's and the normal turn flow (web lookup, the web-off refusal, the model) gets it."""
    steps = list(getattr(loop_result, "steps", None) or [])
    good = productive_steps(loop_result)
    if not good and str(getattr(loop_result, "staged", "") or "").strip():
        good = [-1]  # a pre-step's staged read ran through the real tools: it is a read, like a model's own step
    if not good:
        # Nothing was read. A follow-up may still be answered from the chat's own earlier evidence (no tool ran).
        if follow_up and not steps:
            return ""
        return "found_nothing_fell_through"
    words = route.own_content_words(text)
    seen = [str(x) for x in getattr(loop_result, "seen", None) or []]
    observed = [str(r.get("response_preview") or "") for r in (receipts or []) if (r.get("execution") or {}).get("ok", True)]
    blob = " ".join([*seen, *observed]).lower()
    if words and not any(w in blob for w in words):
        return "irrelevant_matches_fell_through"
    if content_absence_claim(loop_result.answer) and not follow_up:
        return "found_nothing_fell_through"
    return ""


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
    context_block = route._owner_context_blocks(context, session_id)
    if browser_turn or context_block or not route._is_follow_up(text, session_id):
        _clear_evidence(session_id)
    prior_evidence = _prior_evidence(
        session_id, text, owner_local=not browser_turn and not context_block
    )
    settled = _settle_change(
        agent, text=text, effective_input=effective_input, session_id=session_id, context=context,
        browser_turn=browser_turn,
    )
    if settled is not None:
        return settled
    router = getattr(agent, "memory_router", None)
    brain_kind = {"local": True}

    def _resolve() -> Any:
        from core.clean_loop import brain

        if router is None:
            return None
        local = brain.resolve_local_manifest(router, context)
        if local is not None:
            return local
        brain_kind["local"] = False
        return brain.resolve_cloud_manifest(router, context)

    from core.clean_loop.tools import OutsideHome, home_dir
    try:
        route_ctx = ToolContext(
            home=home_dir(), source_context=context, task_id=str(context.get("turn_id") or ""), ask=text
        )
    except OutsideHome:
        context["clean_local_loop_route"] = "home_is_root"
        return None

    def _file_words() -> set[str]:
        from core.clean_loop.tools import file_words

        return set(file_words(route_ctx.home))

    def _document_match(ask: str) -> bool:
        from core.clean_loop.tools import document_matches, warm_index

        try:
            return document_matches(ask, route_ctx)
        finally:
            # Whatever the route could not index within its short slice is finished off the turn.
            warm_index(route_ctx)

    decision = route.decide(
        text, source_context=context, session_id=session_id, resolve_brain=_resolve, browser_turn=browser_turn,
        file_words=_file_words, document_match=_document_match,
    )
    context["clean_local_loop_route"] = decision.reason
    _LOG.info("clean loop route: %s", decision.reason)
    if not decision.admit:
        return None
    if context.get("unresolved_referent"):
        # The chat's record cannot supply what the message points at ("that folder", "the third one" of a list this
        # chat never printed). The loop would pick something from the folders it can see; the turn's model asks.
        context["clean_local_loop_route"] = "unresolved_referent"
        return None
    # A short follow-up to a loop turn that left evidence stays with the loop: its answer may come from that
    # evidence alone, so "no new read" is not a reason to decline it.
    needs_web = front_door_needs_web(effective_input or text, context)
    # A follow-up only when the previous loop turn left evidence (a follow-up to a found-nothing turn has nothing
    # to follow up on) (the front door's fresh-ask reading does not override that: it misreads elliptical follow-ups).
    follow_up = bool(prior_evidence) or route._is_follow_up(text, session_id)
    explicit_own = route.asks_explicitly_about_own_things(text) or follow_up
    if not explicit_own and needs_web:
        # Order: an ask the front door classes as fresh/online meets the web switch (or its refusal)
        # before this loop; only an ask that says it is the owner's own keeps the loop's first turn.
        context["clean_local_loop_route"] = "fresh_ask_to_front_door"
        return None
    exact = _exact_read_first(agent, effective_input or text, session_id=session_id, context=context)
    if exact is not None:
        context["clean_local_loop_route"] = "exact_read_lane"
        route.note_clean_turn(session_id)
        return exact
    from core.clean_loop.brain import make_brain, model_label
    ctx = route_ctx
    provider_id, model_id = model_label(decision.brain)
    loop_brain = make_brain(router, decision.brain, context)
    loop_ask = text
    changed_slots = context.get("_conversation_slots") if isinstance(context.get("_conversation_slots"), dict) else {}
    carried = ""
    if context.get("_conversation_changed") and str(changed_slots.get("path") or "").strip():
        # The owner moved the last ask to another folder ("try the documents folder instead"): the search keeps to it,
        # and looks for what the last ask asked for: the redirect's own words name only the place (Chat R8 C6, C7, M6).
        ctx.scope = str(changed_slots["path"]).strip()
        carried = re.sub(r"\s*\(change:.*\)\s*$", "", str(context.get("_conversation_request") or "")).strip()
        loop_ask = f"{text}\n[Look only in {ctx.scope}.]"
        if carried and carried != text:
            loop_ask = f"{text}\n[The earlier ask, now in {ctx.scope}: {carried}]\n[Look only in {ctx.scope}.]"
    try:
        loop_result = run_loop(
            loop_ask,
            brain=loop_brain,
            registry=default_registry(),
            ctx=ctx,
            history=_history(context, text),
            prior_evidence=prior_evidence,
        )
    except BoundaryStop as stop:
        return _boundary_stopped(
            agent, stop, context=context, session_id=session_id, effective_input=effective_input, ctx=ctx,
            provider_id=provider_id, model_id=model_id,
        )
    if loop_result.stopped == "answered" and not loop_result.proposals:
        loop_result = _guard_file_claims(
            loop_result, text=text, brain=loop_brain, ctx=ctx, context=context, prior_evidence=prior_evidence
        )
        loop_result = _force_ordered_answer(loop_result, text=text, ctx=ctx, context=context)
    _LOG.info(
        # A pre-step's reads ran before the first model call; they are named apart (staged=) so a one-call answer is
        # never read as an answer with no tool behind it (Chat R8 on f50e52eb: C1, C2, C11, M5 showed "tools=").
        "clean loop: stopped=%s model_calls=%d tools=%s staged=%s think_ms=%d tool_ms=%d",
        loop_result.stopped, loop_result.model_calls, ",".join(loop_result.tools_used),
        ",".join(getattr(loop_result, "staged_tools", None) or []),
        loop_result.think_ms,
        sum(int(step.get("tool_ms") or 0) for step in loop_result.steps),
    )
    if (
        loop_result.stopped == "brain_error"
        and not loop_result.tools_used
        and not loop_result.proposals
        and not brain_kind["local"]
    ):
        # The owner's cloud model did not answer: the normal pipeline owns its own provider errors
        # and wording, so nothing here claims a local-memory cause.
        context["clean_local_loop_route"] = "cloud_brain_error_fell_through"
        return None
    if loop_result.stopped == "brain_error" and not loop_result.tools_used and not loop_result.proposals:
        # The local model never answered (VOOL refuses to load it on low memory). This ask is about
        # the owner's own things, and the old pipeline answered such asks on the Mac by listing
        # real-home and drive paths and by asking for account details (demo 2026-10-28, T3/T4), so
        # it is answered here, plainly, and nothing was looked at.
        context["clean_local_loop_route"] = "brain_error_answered"
        loop_result.answer = BRAIN_DOWN_TEXT
    if loop_result.answer == NO_REPLY_TEXT and not loop_result.proposals:
        # No answer came out of the loop (the audit's "mac or windows" and "what is a dmg" turns):
        # an apology is never better than the normal pipeline's own try. Only reads ran so far.
        context["clean_local_loop_route"] = "no_answer_fell_through"
        return None
    if not loop_result.proposals and _is_a_foreign_tool_call(loop_result.answer):
        # The brain's last word was a tool call in its own format (`<tool_call>{"name": ...}`), not
        # an answer: shown, it is only replaced by "I asked to run a tool and the runtime did not
        # execute it", and nothing records which check set it aside (measured 2026-11-12 on the
        # v0.8 candidate, served, with the loop on). The normal pipeline gets the turn; a reply it
        # refuses carries a receipt naming the check. Only reads ran so far.
        context["clean_local_loop_route"] = "foreign_tool_call_fell_through"
        return None
    if decision.by_documents and not loop_result.proposals and content_absence_claim(loop_result.answer):
        # Only the owner's documents brought this turn here, and they did not answer it. It may be
        # something the owner told VOOL in chat, which lives in memory the loop cannot read: the
        # normal pipeline (and its memory lane) gets the turn. Only reads ran so far.
        context["clean_local_loop_route"] = "documents_found_nothing_fell_through"
        _clear_evidence(session_id)
        return None
    if not explicit_own and not loop_result.proposals:
        declined = loop_should_decline(carried or text, loop_result, ctx.receipts, follow_up=follow_up)
        if declined:
            # Admitted on a word, not on the owner's own things, and the reads found nothing for it: not this
            # loop's turn. Only reads ran; none of them is recorded as this turn's observation.
            context["clean_local_loop_route"] = declined
            _clear_evidence(session_id)
            return None
    # A listing a pre-step staged is a read that ran too (its receipts are this turn's): the model answered from it in
    # one call, and the chat's record must hold its folder and text like a listing the model asked for, or "and the
    # newest?" next has no folder (Chat R8 C1, C2, C11, M5 on the Mac: follow-ups went to the model on its own).
    productive = bool(productive_steps(loop_result)) or bool(str(getattr(loop_result, "staged", "") or "").strip())
    if productive:
        evidence = _turn_evidence(loop_result.seen) or prior_evidence
    else:
        evidence = "" if loop_result.steps else prior_evidence
    if evidence:
        _remember_evidence(session_id, evidence)
    else:
        _clear_evidence(session_id)
    if productive:
        # What this turn printed rides on the chat's record: the folder it listed (so "the largest one" next has a folder)
        # and the tool text a later file name, size or date must be backed by.
        recorded: dict[str, Any] = {"evidence": "\n".join(str(x) for x in loop_result.seen if str(x).strip())[:_RECORD_EVIDENCE_CHARS]}
        folder_label = _recorded_folder_label(ctx)
        if folder_label:
            recorded["path"] = folder_label
        context["_conversation_kind_listing"] = {**dict(context.get("_conversation_kind_listing") or {}), **recorded}
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
    if productive or evidence or loop_result.proposals:
        route.note_clean_turn(session_id)
    else:
        route.forget_clean_turn(session_id)
    return result


def _exact_read_first(agent: Any, text: str, *, session_id: str, context: dict[str, Any]) -> dict[str, Any] | None:
    """An admitted ask that an exact read lane answers in full (a kind listing, a count or the biggest
    file of the folder just listed, a folder named by name, a typed path, the picked file) is that
    lane's: its answer is measured, the loop's model would be guessing it. The route has already
    kept private, RED, write and remote turns out (core.agent_runtime.fast_paths_machine)."""
    try:
        from core.agent_runtime.fast_paths_machine import maybe_answer_exact_read_first

        return maybe_answer_exact_read_first(
            agent, text, session_id=session_id,
            source_surface=str(context.get("surface", "cli")).lower(), source_context=context,
        )
    except Exception:
        _LOG.exception("exact read lane failed; the clean loop answers")
        return None


def _boundary_stopped(
    agent: Any, stop: BoundaryStop, *, context: dict[str, Any], session_id: str, effective_input: str,
    ctx: ToolContext, provider_id: str, model_id: str,
) -> dict[str, Any] | None:
    """A step stopped at the owner's cloud disclosure boundary, and nothing of theirs was sent.

    A preview this caller cannot show (only the chat page answers one while its turn waits) is the
    turn's answer: it says where the preview is, and the served door returns the preview as pending.
    Any other stop (the owner chose not to send, or a local model instead, or the chat's privacy
    could not be checked) is the normal pipeline's to word: its own request meets the same boundary,
    which stays closed for the rest of this turn, so it sends nothing either.
    """
    context["clean_local_loop_route"] = "disclosure_stopped"
    _clear_evidence(session_id)
    if stop.kind != "DisclosurePreviewNeeded" or not stop.message:
        return None
    _record_observations(context, ctx.receipts)
    context["tool_receipts"] = list(context.get("tool_receipts") or []) + ctx.receipts
    _emit_tool_events(agent, context, ctx.receipts)
    result = agent._fast_path_result(
        session_id=session_id,
        user_input=effective_input,
        response=stop.message,
        confidence=0.8,
        source_context=context,
        reason=REASON,
    )
    result["model_execution"] = {
        "source": REASON,
        "used_model": stop.model_calls > 0,
        "provider_id": provider_id,
        "model_id": model_id,
        "model_calls": stop.model_calls,
        "stopped": "disclosure_preview_needed",
    }
    result["model_selected"] = model_id
    result["fast_path_hit"] = False
    result["tool_receipts"] = list(ctx.receipts)
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
