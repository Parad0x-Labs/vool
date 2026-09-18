"""One door every current-answer retrieval scheduler passes through, and one refusal record.

WHY THIS EXISTS
---------------
M1 gave the runtime a canonical authority for "does this turn need current information"
(`core.execution_requirements.require_current_information_for_retrieval`) and wired exactly
ONE production scheduler to it -- the live-info fast path. The others kept deciding
privately: `ResearchToolLoopFacade._collect_live_web_notes` asked its own
`_wants_fresh_info` and went straight to `begin_web_retrieval`, and the adaptive-research
roamer did the same behind its own enable decision. A private yes that never reaches the
canonical record is invisible to every downstream grounding guard, which is the mechanism
that let a turn retrieve and still be judged as though it had not.

WHAT THE DOOR ACTUALLY DOES, STATED PLAINLY
-------------------------------------------
It does NOT overrule a scheduler's recognizer. Before the synthesis freeze the canonical
authority ESCALATES on the lane's claim: the frozen record widens to
`current_information_required=True` and gains a `current_info_signal:lane:<lane>` reason
code naming the contributor. So the lane still proposes; what changes is that its proposal
is now RECORDED as the turn's decision instead of being made off the books.

The teeth are on the other side of synthesis. Once `begin_synthesis_freeze` has run the
door fails closed, so a retrieval attempted after the answering model call cannot start --
which is the post-answer retrieval that made a fabrication look sourced.

A refusal is a typed, durable fact, not a silent `return []`. A lane that wanted to search
and was told no leaves a row saying so, naming itself and the reason; otherwise "the
provider had nothing" and "this lane was not allowed to ask" are indistinguishable in the
record, and they are opposite facts about the turn.
"""

from __future__ import annotations

import contextlib
from typing import Any

from core.runtime_task_events import emit_runtime_event

#: Schema for the refusal record. Distinct from a retrieval receipt on purpose: no retrieval
#: began, so nothing here may be read as one that did.
DECLINED_SCHEMA = "vool.retrieval_authority_refusal.v1"

REASON_FROZEN = "decision_closed_by_synthesis"
REASON_NOT_CURRENT = "not_current_information_at_authority"
#: The user supplied the turn's material (a pasted document, a picked file); a lane's claim may
#: not send the turn to the web behind it. See `execution_requirements.escalate_current_requirement`.
REASON_MATERIAL_SUPPLIED = "user_material_supplied"
#: A retrieval of the SAME query already ran this turn and its whole provider chain ended with
#: nothing reachable. Measured on the final pack (turn 19, 127.6 s): the live-info lane searched
#: the user's text for 24 s (every keyless engine unavailable), then adaptive research asked to
#: search the identical text again and spent 25 s on the identical chain. The second ask cannot
#: reach a provider the first did not, so it is declined here with the first outcome named.
REASON_CHAIN_UNAVAILABLE = "same_query_chain_unavailable_this_turn"
_TERMINAL_EMPTY_STATUSES = frozenset({"unavailable", "failed"})


def _same_query_chain_unavailable(source_context: dict[str, Any] | None, user_input: str) -> str:
    """The earlier receipt's `kind:status` when this turn already exhausted the chain on this query."""
    if not isinstance(source_context, dict):
        return ""
    receipts = source_context.get("web_retrieval_receipts")
    if not isinstance(receipts, list) or not receipts:
        return ""
    import hashlib

    clean = " ".join(str(user_input or "").split()).strip()
    if not clean:
        return ""
    digest = hashlib.sha256(clean.encode("utf-8")).hexdigest()
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        if str(receipt.get("query_hash") or "") != digest:
            continue
        status = str(receipt.get("status") or "")
        if status in _TERMINAL_EMPTY_STATUSES and int(receipt.get("source_count") or 0) == 0:
            return f"{receipt.get('kind') or 'retrieval'}:{status}"
    return ""


def _requirement_state(source_context: dict[str, Any] | None) -> tuple[bool, bool]:
    """(current_required, synthesis_started) as the canonical record holds them right now."""
    try:
        from core.execution_requirements import current_requirement_record

        record = current_requirement_record(source_context)
    except Exception:
        return (False, False)
    if record is None:
        return (False, False)
    return (
        bool(getattr(record.requirements, "current_information_required", False)),
        bool(getattr(record, "synthesis_started", False)),
    )


def record_retrieval_refusal(
    source_context: dict[str, Any] | None,
    *,
    lane: str,
    user_input: str = "",
    proposal_reason: str = "",
    reason_code: str = "",
    detail: str = "",
) -> dict[str, Any]:
    """The typed row a declined scheduler leaves behind. Never raises.

    `reason_code` overrides the requirement-derived code for refusals the requirement record
    does not express (the same-query chain exhaustion); `detail` names what was seen.
    """
    current_required, frozen = _requirement_state(source_context)
    # A third place a reader may be sent: the turn's material was supplied by the user (a
    # pasted document), and the authority declined the lane's claim for that reason.
    supplied = False
    try:
        from core.execution_requirements import current_requirement_record

        found = current_requirement_record(source_context)
        supplied = bool(found is not None and found.requirements.user_material_supplied and not current_required)
    except Exception:
        supplied = False
    record = {
        "schema": DECLINED_SCHEMA,
        "lane": str(lane or "")[:120],
        # WHY it was refused, from the record's own state rather than a guess: a closed
        # decision and a decision that was never current send a reader to different places.
        "reason_code": str(reason_code or "")
        or (REASON_FROZEN if frozen else (REASON_MATERIAL_SUPPLIED if supplied else REASON_NOT_CURRENT)),
        "detail": str(detail or "")[:200],
        "synthesis_started": frozen,
        "current_information_required": current_required,
        # What the lane's private recognizer thought, kept so the disagreement is legible.
        "proposal_reason": str(proposal_reason or "")[:200],
        "retrieval_started": False,
    }
    with contextlib.suppress(Exception):
        emit_runtime_event(
            source_context,
            event_type="retrieval_declined_by_authority",
            message=f"Retrieval declined for lane '{record['lane']}': {record['reason_code']}.",
            details=dict(record),
        )
    if isinstance(source_context, dict):
        with contextlib.suppress(Exception):
            refusals = source_context.get("retrieval_authority_refusals")
            if not isinstance(refusals, list):
                refusals = []
                source_context["retrieval_authority_refusals"] = refusals
            refusals.append(dict(record))
    return record


def authorize_retrieval(
    source_context: dict[str, Any] | None,
    user_input: str,
    *,
    lane: str,
    proposal_reason: str = "",
    query: str = "",
) -> bool:
    """Ask the canonical authority whether THIS lane may retrieve on THIS turn.

    Call it immediately before the retrieval starts -- after the lane's own recognizer has
    proposed, and before `begin_web_retrieval`. Returns True to proceed. On False the
    refusal has already been recorded and the caller must not retrieve.

    Fail-closed on its own errors: an authority that cannot be consulted is not an
    authority that said yes.
    """
    # TWO conditions, and they are different questions asked of the same record.
    #
    # M1's door answers "is this turn current-information required", and it answers YES for an
    # already-current record whether or not synthesis has begun -- correct for the question it
    # asks, and not enough for this one. A turn that legitimately needed current information at
    # 09:00 does not still need a NEW search after its answer has been written; that retrieval
    # can only decorate bytes that never saw it. So the freeze is checked HERE, first, and M1's
    # door is left exactly as M1 shipped it.
    # The same-query check reads the lane's ACTUAL query, never the user's text: adaptive
    # research narrows and broadens inside one turn, and every one of those asks carries the
    # same user text. A lane that names no query is not checked -- nothing is guessed.
    exhausted = _same_query_chain_unavailable(source_context, query) if str(query or "").strip() else ""
    if exhausted:
        record_retrieval_refusal(
            source_context,
            lane=lane,
            user_input=user_input,
            proposal_reason=proposal_reason,
            reason_code=REASON_CHAIN_UNAVAILABLE,
            detail=exhausted,
        )
        return False
    _current, frozen = _requirement_state(source_context)
    if frozen:
        record_retrieval_refusal(
            source_context, lane=lane, user_input=user_input, proposal_reason=proposal_reason
        )
        return False
    try:
        from core.execution_requirements import (
            escalate_current_requirement,
            require_current_information_for_retrieval,
        )

        allowed = bool(
            require_current_information_for_retrieval(source_context, user_input, lane=lane)
        )
        if allowed:
            # ATTRIBUTION, and not a redundant second call. M1's door SHORT-CIRCUITS on a record
            # that is already current -- correct for the question it asks ("may this lane
            # retrieve") and it means the record never learns WHICH lane scheduled the search.
            # That is not cosmetic: with no `current_info_signal:lane:<lane>` code the frozen
            # record is byte-identical whether this door ran or was deleted, so nothing can tell a
            # gated scheduler from an ungated one on an already-current turn. Escalation is
            # monotone and idempotent -- on an already-current record it only appends the code --
            # so asking twice costs one tuple append and buys the evidence.
            escalate_current_requirement(
                source_context,
                text=str(user_input or ""),
                source=lane,
                reason_code=f"lane:{lane}",
            )
    except Exception:
        allowed = False
    if not allowed:
        record_retrieval_refusal(
            source_context,
            lane=lane,
            user_input=user_input,
            proposal_reason=proposal_reason,
        )
    return allowed


__all__ = [
    "DECLINED_SCHEMA",
    "REASON_CHAIN_UNAVAILABLE",
    "REASON_FROZEN",
    "REASON_NOT_CURRENT",
    "authorize_retrieval",
    "record_retrieval_refusal",
]
