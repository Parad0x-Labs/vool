"""The Operator Profile's seat in a chat turn.

One function is called at the turn front door for every user turn -- BEFORE the memory command
lane, so a profile-shaped "remember ..." lands on the typed authority instead of the free-text
memory store -- and it does exactly three things:

1. binds the turn's principal + chat for every later profile reader (a ContextVar, so the
   identity authority and the prompt assembler read the SAME resolution this turn used);
2. interprets the turn (:mod:`core.operator_profile_interpretation`) and applies the memory law
   through :mod:`core.operator_profile`: explicit -> persist and report; strong -> candidate;
   weak -> chat-local; contradiction -> conflict candidate; secrets -> refused;
3. leaves the outcome on ``source_context["profile_observation"]`` so the transport door can put
   a ``vool_profile`` frame on the wire (chip / confirmation / "Used N preferences").

It claims the turn only when the turn is ENTIRELY profile-shaped and explicit (or a forget /
undo / show command). A mixed turn ("remember my name is X and I live in Vilnius") persists the
profile part, reports it, and hands the remainder on so the memory lane still sees its clause.
A turn with no profile content costs one interpretation pass and changes nothing.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from core import operator_profile as profile
from core.operator_profile_interpretation import ProfileProposal, interpret_profile_turn

__all__ = [
    "bind_turn_scope",
    "clear_turn_scope",
    "current_turn_scope",
    "observe_profile_turn",
    "profile_frame_for_result",
]

_TURN_SCOPE: ContextVar[tuple[str, str, str]] = ContextVar("operator_profile_turn_scope", default=("", "", ""))


def bind_turn_scope(principal: str, session_id: str, project_id: str = ""):
    return _TURN_SCOPE.set((str(principal or ""), str(session_id or ""), str(project_id or "")))


def clear_turn_scope() -> None:
    """End-of-turn reset, called where the agent clears its execution context, so an off-turn
    reader never resolves against the previous turn's principal."""
    _TURN_SCOPE.set(("", "", ""))


def current_turn_scope() -> tuple[str, str, str]:
    """``(principal, session_id, project_id)`` of the turn in flight, or blanks off-turn."""
    return _TURN_SCOPE.get()


def _observation(source_context: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(source_context, dict):
        return {"saved": [], "candidates": [], "conflicts": [], "used": [], "notices": []}
    obs = source_context.get("profile_observation")
    if not isinstance(obs, dict):
        obs = {"saved": [], "candidates": [], "conflicts": [], "used": [], "notices": []}
        source_context["profile_observation"] = obs
    return obs


def _candidate_payload(change: profile.ProfileChange) -> dict[str, Any]:
    item = change.item
    return {
        "candidate_id": item.item_id if item else "",
        "text": change.report,
        "category": item.category if item else "",
        "value": item.value_text if item else "",
        "actions": ["save", "edit", "only_this_chat"],
    }


def _conflict_payload(change: profile.ProfileChange) -> dict[str, Any]:
    item = change.item
    return {
        "candidate_id": item.item_id if item else "",
        "text": change.report,
        "category": item.category if item else "",
        "value": item.value_text if item else "",
        "current": change.previous.value_text if change.previous else "",
        "actions": ["replace", "scope", "keep"],
    }


def _apply(proposal: ProfileProposal, *, principal: str, session_id: str, turn_id: str) -> profile.ProfileChange:
    if proposal.action == "forget":
        return _forget(proposal, principal=principal, session_id=session_id)
    if proposal.action == "undo":
        return _undo(principal, session_id)
    if proposal.action == "show":
        return _show(principal, session_id)
    if proposal.strength == "explicit":
        return profile.remember(
            principal, proposal.category, proposal.value, scope=proposal.scope,
            session_id=session_id, turn_id=turn_id, origin="explicit", confidence=1.0, actor="chat",
            reason=f"explicit: {proposal.clause[:120]}",
        )
    if proposal.strength == "strong":
        return profile.propose_candidate(
            principal, proposal.category, proposal.value, session_id=session_id, turn_id=turn_id,
            confidence=0.85, reason=f"stated: {proposal.clause[:120]}",
        )
    return profile.note_chat_local(
        principal, proposal.category, proposal.value, session_id=session_id, turn_id=turn_id, confidence=0.5,
    )


def _forget(proposal: ProfileProposal, *, principal: str, session_id: str) -> profile.ProfileChange:
    items = profile.list_items(principal, session_id=session_id)
    if proposal.category != "*":
        items = [item for item in items if item.category == proposal.category]
    if not items:
        label = "anything" if proposal.category == "*" else profile.CATEGORIES.get(proposal.category, {}).get("label", proposal.category)
        return profile.ProfileChange("unchanged", f"Nothing saved for {label}, so there is nothing to forget.")
    reports = []
    last: profile.ProfileChange | None = None
    for item in items:
        last = profile.forget_item(item.item_id, actor="chat", reason=f"forget: {proposal.clause[:120]}")
        reports.append(last.report.split(". Say 'undo")[0])
    return profile.ProfileChange("forgotten", "; ".join(reports) + ". Say 'undo that' to restore.", item=last.item if last else None, previous=last.previous if last else None)


def _undo(principal: str, session_id: str) -> profile.ProfileChange:
    from storage.db import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT item_id FROM operator_profile_history WHERE principal = ? AND action IN ('create','update','forget','move_scope','restore','confirm_chat') "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (principal,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return profile.ProfileChange("unchanged", "There is no profile change to undo.")
    return profile.restore_previous(str(row["item_id"]), actor="chat")


def _show(principal: str, session_id: str) -> profile.ProfileChange:
    items = profile.list_items(principal, session_id=session_id)
    if not items:
        return profile.ProfileChange("unchanged", "I don't have anything saved about you yet. Tell me what to remember, or open What VOOL remembers about you in Settings.")
    lines = [f"- {item.label}: {item.value_text} ({item.scope if item.scope != 'chat' else 'this chat only'}; {item.origin})" for item in items]
    return profile.ProfileChange("unchanged", "What I remember about you:\n" + "\n".join(lines))


def observe_profile_turn(
    agent: Any,
    raw_user_input: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
    access_policy: Any | None = None,
) -> dict[str, Any] | None:
    """Apply the memory law to one turn. Returns ``{"result": fast_path_result | None,
    "remaining_text": str}`` or None when the turn carries no profile content."""
    principal = profile.principal_for_request(source_context)
    project_id = str(getattr(access_policy, "project_id", "") or (source_context or {}).get("_trusted_project_id") or "")
    bind_turn_scope(principal, session_id, project_id)
    try:
        proposals = interpret_profile_turn(raw_user_input)
    except Exception:
        proposals = []
    used_lines: list[str] = []
    used: list[dict[str, str]] = []
    if principal:
        try:
            used_lines, used = profile.hydration_for_turn(principal, session_id=session_id, project_id=project_id)
        except Exception:
            used_lines, used = [], []
    # The turn context is touched ONLY when the profile did something: an ordinary turn carries
    # no profile keys at all (byte-equivalence law, and the frozen front-door tests pin the
    # context they hand each lane).
    if used and isinstance(source_context, dict):
        source_context["profile_context_lines"] = used_lines
        source_context["profile_used"] = used
        _observation(source_context)["used"] = used
    if not proposals:
        return None
    if not principal:
        _observation(source_context)["notices"].append("Profile memory is unavailable on this surface.")
        return None
    obs = _observation(source_context)
    turn_id = str((source_context or {}).get("cancel_turn_id") or (source_context or {}).get("_canonical_user_turn_id") or "")
    claim_lines: list[str] = []
    consumed: list[str] = []
    claims_turn = True
    for proposal in proposals:
        change = _apply(proposal, principal=principal, session_id=session_id, turn_id=turn_id)
        kind = change.kind
        if kind == "candidate":
            obs["candidates"].append(_candidate_payload(change))
            claims_turn = False
            continue
        if kind == "conflict":
            obs["conflicts"].append(_conflict_payload(change))
            claim_lines.append(change.report)
            consumed.append(proposal.clause)
            continue
        if kind == "chat_local":
            claims_turn = False
            continue
        if kind in {"saved", "updated", "forgotten"}:
            obs["saved"].append({**change.as_dict(), "category": change.item.category if change.item else proposal.category, "item_id": change.item.item_id if change.item else ""})
            _record_receipt(change, proposal, session_id=session_id, source_context=source_context)
            claim_lines.append(change.report)
            consumed.append(proposal.clause)
            continue
        if kind in {"refused_secret", "refused_sensitive", "refused_privacy", "refused_binding", "refused_invalid", "paused", "unchanged"}:
            if proposal.strength == "explicit" or proposal.action in {"forget", "undo", "show"}:
                claim_lines.append(change.report)
                consumed.append(proposal.clause)
            else:
                obs["notices"].append(change.report)
                claims_turn = False
            continue
        claims_turn = False
    remaining = _remaining_text(raw_user_input, consumed)
    if not claim_lines:
        return {"result": None, "remaining_text": raw_user_input}
    if remaining.strip() and remaining.strip() != raw_user_input.strip():
        # A mixed turn: the profile part is done and reported; the rest continues on its own lane
        # and the report rides the response frame.
        obs["notices"].extend(claim_lines)
        return {"result": None, "remaining_text": remaining}
    if not claims_turn and remaining.strip():
        obs["notices"].extend(claim_lines)
        return {"result": None, "remaining_text": raw_user_input}
    response = "\n".join(claim_lines)
    return {
        "result": agent._fast_path_result(
            session_id=session_id,
            user_input=raw_user_input,
            response=response,
            confidence=0.97,
            source_context=source_context,
            reason="operator_profile_command",
        ),
        "remaining_text": "",
    }


def _record_receipt(change: profile.ProfileChange, proposal: ProfileProposal, *, session_id: str, source_context: dict[str, Any] | None) -> None:
    """A profile write is real work with a durable record, so it carries the same executed
    receipt every other action carries: the final-action honesty gate judges "Saved to your
    profile" against this receipt, in the turn context AND in the durable receipt store."""
    item = change.item
    execution = {
        "executed": True,
        "ok": True,
        "status": "executed",
        "tool": "profile.remember" if change.kind != "forgotten" else "profile.forget",
        "item_id": item.item_id if item else "",
        "category": item.category if item else proposal.category,
        "revision": item.revision if item else 0,
        "kind": change.kind,
    }
    receipt = {"tool_name": execution["tool"], "intent": execution["tool"], "execution": execution, "status": "executed"}
    if isinstance(source_context, dict):
        receipts = source_context.get("tool_receipts")
        if not isinstance(receipts, list):
            receipts = []
            source_context["tool_receipts"] = receipts
        receipts.append(receipt)
    try:
        from core.runtime_continuity import build_tool_receipt_key, store_tool_receipt

        key = build_tool_receipt_key(
            checkpoint_id=str((source_context or {}).get("runtime_checkpoint_id") or session_id),
            step_index=int(item.revision if item else 0),
            intent=execution["tool"],
            arguments={"item_id": execution["item_id"], "kind": change.kind},
        )
        store_tool_receipt(
            receipt_key=key,
            session_id=session_id,
            checkpoint_id=str((source_context or {}).get("runtime_checkpoint_id") or ""),
            tool_name=execution["tool"],
            idempotency_key=f"{execution['item_id']}:{execution['revision']}:{change.kind}",
            arguments={"category": execution["category"], "scope": item.scope if item else proposal.scope},
            execution=execution,
        )
    except Exception:
        pass


def _remaining_text(raw: str, consumed: list[str]) -> str:
    text = str(raw or "")
    for clause in consumed:
        clause = str(clause or "").strip()
        if clause and clause in text:
            text = text.replace(clause, " ", 1)
    text = " ".join(text.split())
    # a leftover directive fragment ("Remember that", "and") is not content
    import re

    text = re.sub(r"^(?:(?:and|also|please|remember|note|save|store|that|this|too)\b[\s,.!]*)+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:[\s,.!]*\b(?:and|also|too|please|remember that|remember)\b)+\s*[.!]?$", "", text, flags=re.IGNORECASE)
    return text.strip(" ,.!;")


def profile_frame_for_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """The ``vool_profile`` wire frame for a finished turn, or None when nothing happened --
    an ordinary turn puts no profile key on the wire at all."""
    if not isinstance(result, dict):
        return None
    ctx = result.get("source_context") if isinstance(result.get("source_context"), dict) else {}
    obs = ctx.get("profile_observation") if isinstance(ctx.get("profile_observation"), dict) else None
    if not obs:
        return None
    frame: dict[str, Any] = {}
    for key in ("saved", "candidates", "conflicts", "used", "notices"):
        values = [v for v in list(obs.get(key) or []) if v]
        if values:
            frame[key] = values
    return frame or None
