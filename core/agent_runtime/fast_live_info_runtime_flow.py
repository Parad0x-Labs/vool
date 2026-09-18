from __future__ import annotations

from typing import Any

from .fast_live_info_runtime_dispatch import build_live_info_response_result
from .fast_live_info_runtime_preflight import prepare_live_info_request
from .fast_live_info_runtime_results import disabled_live_info_result, live_info_result
from .fast_live_info_runtime_search import live_info_search_notes_with_fallback
from .fast_live_info_runtime_truth import (
    chat_truth_live_info_result,
    should_use_chat_truth_wording,
)

__all__ = [
    "build_live_info_response_result",
    "chat_truth_live_info_result",
    "disabled_live_info_result",
    "live_info_result",
    "live_info_search_notes_with_fallback",
    "maybe_handle_live_info_fast_path",
    "prepare_live_info_request",
    "record_live_info_observation",
    "should_use_chat_truth_wording",
]

#: Matches the cap `core.agent_runtime.fast_paths_currency` applies to the same shared channel.
_OBSERVATION_LIMIT = 12


def record_live_info_observation(
    source_context: dict[str, object] | None, notes: list[dict[str, Any]]
) -> bool:
    """Put this lane's retrieval on the shared same-turn observation channel. True when recorded.

    This lane really reaches the network -- `lookup_live_quote` on the price road, the search
    fallback on the news road -- and composes an answer that names its source and its timestamp.
    Until eca76ff9 it recorded that nowhere any evidence-sufficiency authority could see: not a
    receipt, not an observation, nothing. `core.model_output_guard.turn_ran_observations` therefore
    read a turn that HAD observed as a turn that had not, which is only harmless for as long as some
    other exemption happens to cover the answer's shape.

    It stopped being harmless the moment the attribution exemption came out: `BTC PRICE?` fetched a
    real quote, rendered `Bitcoin: USD 63,791.00 ... Source: CoinGecko`, and the seam replaced it
    with "I didn't run any live lookup on this turn" -- a lane calling its own real work a
    fabrication. Caught by `tests/test_every_asset_asked_for_is_answered.py` in the full gate.

    The fix is the one `core.live_data_retrieval_receipts` and
    `core.agent_runtime.fast_paths_currency.attach_currency_grounding` already apply: the lane that
    executed records itself, so the truth lives in the channel every reader already consults instead
    of in a second inference engine per guard. A failed lookup is recorded too, as `ok=False` --
    truthful, and correctly worth nothing as evidence (`core.observation_evidence`).
    """

    if not isinstance(source_context, dict):
        return False
    clean = [note for note in list(notes or []) if isinstance(note, dict)]
    observation = {
        "schema": "tool_observation_v1",
        "intent": "live_info.lookup",
        "tool_surface": "web",
        "ok": bool(clean),
        "status": "executed" if clean else "no_results",
        "response_preview": (
            f"{len(clean)} live-info result(s) retrieved"
            if clean
            else "live-info lookup returned no results"
        ),
    }
    existing = [
        dict(item)
        for item in list(source_context.get("runtime_tool_observations") or [])
        if isinstance(item, dict)
    ]
    existing.append(observation)
    source_context["runtime_tool_observations"] = existing[-_OBSERVATION_LIMIT:]
    # M3 requirement 7: this lane's own successful observation mints support on the turn's
    # grounding lifecycle, and the rows it observed travel with it. The observation entry
    # proves the lookup succeeded; the rows are what any claim in the rendered answer is
    # matched against, so a lane that really fetched is never asked to prove it twice and a
    # lane that failed mints nothing (`core.observation_evidence` owns that question).
    try:
        from core.grounding_lifecycle import record_typed_observations

        record_typed_observations(source_context, entries=[observation, *clean])
    except Exception:
        pass
    return True


def maybe_handle_live_info_fast_path(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    interpretation: Any,
    response_class: Any,
) -> dict[str, Any] | None:
    live_mode, query, preflight_result = prepare_live_info_request(
        agent,
        user_input,
        session_id=session_id,
        source_context=source_context,
        interpretation=interpretation,
    )
    if preflight_result is not None:
        # M3 slice 2: a disabled/unresolvable runtime is still a typed DECISION by
        # this lane — recorded as a decline, never a silent pre-answer exit (the
        # pre-agent-exit shape M4 converges).
        _record_frontdoor_live_info_proposal(
            source_context,
            user_input=user_input,
            claimed_unit_ids=(),
            refusal_reason="live-info preflight declined (disabled or unresolved content)",
        )
        return preflight_result
    if not live_mode:
        # M3 slice 2: the frontdoor live-info fast path CONSUMES the canonical set.
        # A decline is typed with its reason (no live mode for this text) — the
        # shape M4's decline recording builds on; nothing is claimed, so the
        # conservation record is empty by construction, never guessed.
        _record_frontdoor_live_info_proposal(
            source_context,
            user_input=user_input,
            claimed_unit_ids=(),
            refusal_reason="no live-info mode for this text",
        )
        return None

    # M3 slice 2: claiming. The fast path answers the WHOLE text it was handed
    # (the frontdoor scopes it to the live-info clauses before calling), so the
    # claim is every canonical unit inside that text, and the conservation record
    # names anything the set holds beyond it. Same derivation the live-data lane
    # uses — needle binding over the canonical units, not a re-mint.
    _fd_claims = _frontdoor_claimed_units(user_input)
    _record_frontdoor_live_info_proposal(
        source_context,
        user_input=user_input,
        claimed_unit_ids=_fd_claims,
        refusal_reason="",
    )
    # M4 SLICE 4 — the frontdoor serve consults too (completing registry-wide
    # coverage). Registry rank 0 means nothing can supersede it in a healthy
    # registry; this is the structural law (every serving lane consults) plus
    # defense in depth should the registry ever be reordered. Fail-open.
    try:
        from core.lane_registry import mediate
        from core.turn_contract import TURN_PROPOSALS_KEY

        _fd_verdict = mediate(
            [
                item
                for item in (source_context or {}).get(TURN_PROPOSALS_KEY) or []
                if hasattr(item, "lane_id")
            ],
            "live_info_fast_path",
            _fd_claims,
        )
        if _fd_verdict is not None and not _fd_verdict.allowed:
            _record_frontdoor_live_info_proposal(
                source_context,
                user_input=user_input,
                claimed_unit_ids=(),
                refusal_reason=(
                    f"superseded by {_fd_verdict.superseded_by} (consult-before-serve)"
                ),
            )
            return None
    except Exception:
        pass
    # M1 -- the retrieval door.  This lane no longer decides freshness alone: its claim
    # is a typed signal the canonical authority consumes.  The door reads the turn's
    # frozen requirement and, when the authority's own reading did not know this
    # vocabulary, escalates THROUGH it (recorded, reason-coded) before any retrieval.
    # Post-synthesis the decision is closed and this fails closed: retrieving behind the
    # authority's back is how every grounding guard downstream was disarmed.
    from core.execution_requirements import require_current_information_for_retrieval

    if not require_current_information_for_retrieval(
        source_context, user_input, lane="live_info_fast_path"
    ):
        _record_frontdoor_live_info_proposal(
            source_context,
            user_input=user_input,
            claimed_unit_ids=(),
            refusal_reason="current-information requirement not established at the canonical authority",
        )
        return None
    notes = live_info_search_notes_with_fallback(
        agent,
        session_id=session_id,
        user_input=user_input,
        query=query,
        live_mode=live_mode,
        interpretation=interpretation,
        # The turn's context, so this lane's retrieval leaves the same durable,
        # provider-attributed receipt every other retrieving lane leaves. Until
        # this was passed, the lane that answers "what is X right now" was the
        # one lane whose search nothing could prove.
        source_context=source_context if isinstance(source_context, dict) else None,
    )
    # Recorded HERE, between the retrieval and every exit that renders from it, so no downstream
    # branch (`chat_truth_live_info_result`, the unresolved-price road, the failure text) can be the
    # one that forgets.
    record_live_info_observation(source_context, notes)
    return build_live_info_response_result(
        agent,
        session_id=session_id,
        user_input=user_input,
        query=query,
        live_mode=live_mode,
        notes=notes,
        source_context=source_context,
        interpretation=interpretation,
        response_class=response_class,
    )


def _frontdoor_claimed_units(user_input: str) -> tuple[str, ...]:
    """The FULL-TEXT canonical units whose text the handed text contains.

    The frontdoor scopes live-info turns to their live-info clauses BEFORE this
    handler runs, so the claim is the subset of the TURN's units the handed
    text covers. IDs are resolved against the turn's own set (the spine's
    basis): minting fresh ids from the slice text alone put `u1`-of-the-slice
    in the claim while the turn's `u1` was a different demand — two unit
    namespaces colliding, which M4's shared claim space cannot tolerate.
    Matching is by exact unit text (the minted unit text of a covered clause
    is stable across both bases), never by wording similarity.
    """
    from core.agent_runtime.answer_coverage import demand_units

    slice_texts = {unit.text for unit in demand_units(user_input)}
    full_text = str(user_input or "")
    return tuple(
        unit.unit_id
        for unit in demand_units(full_text)
        if unit.text in slice_texts
    )


def _record_frontdoor_live_info_proposal(
    source_context: dict[str, object] | None,
    *,
    user_input: str,
    claimed_unit_ids: tuple[str, ...],
    refusal_reason: str,
) -> None:
    """Record the frontdoor live-info fast path's typed claim/decline (M3 slice 2).

    The conservation record: against the canonical set minted from the FULL
    effective text the turn carries (the spine's authority), the units the handed
    text does not contain are NAMED as unclaimed. Fail-soft: accounting may never
    break the lane it accounts for.
    """
    try:
        from core.agent_runtime.answer_coverage import demand_units
        from core.turn_contract import TURN_PROPOSALS_KEY, LaneProposal

        full_units = demand_units(str((source_context or {}).get("effective_input") or user_input))
        claimed = tuple(dict.fromkeys(claimed_unit_ids))
        minted = tuple(unit.unit_id for unit in full_units)
        unclaimed = tuple(u for u in minted if u not in claimed)
        items = list((source_context or {}).get(TURN_PROPOSALS_KEY) or [])
        items.append(
            LaneProposal(
                lane_id="live_info_fast_path",
                obligations_claimed=claimed,
                unclaimed_obligations=unclaimed,
                required_capabilities=("live_info",),
                confidence=0.84,
                refusal_reason=refusal_reason,
            )
        )
        if isinstance(source_context, dict):
            source_context[TURN_PROPOSALS_KEY] = items
    except Exception:
        pass
