from __future__ import annotations

from typing import Any

from core.turn_contract import LaneProposal


def _frontdoor_live_info_proposal(source_context: dict[str, object] | None) -> LaneProposal | None:
    """The typed claim this turn's live-info fast path recorded, if any (M3)."""
    from core.turn_contract import TURN_PROPOSALS_KEY

    items = list((source_context or {}).get(TURN_PROPOSALS_KEY) or [])
    for item in reversed(items):
        if isinstance(item, LaneProposal) and item.lane_id == "live_info_fast_path":
            return item
    return None


def _route_metadata(agent: Any, source_context: dict[str, object] | None, default_confidence: float) -> tuple[str, float]:
    """The proposal is the single home of this lane's route identity (M3 slice 2
    displacement): the literals the result builders scattered now come from the
    recorded claim when one exists, and from the module constants otherwise."""
    proposal = _frontdoor_live_info_proposal(source_context)
    if proposal is not None:
        return proposal.lane_id, proposal.confidence
    return "live_info_fast_path", default_confidence


#: What the person reads when a live lookup was not made. It names the switch and where it is,
#: because "disabled on this runtime" left people with no idea how to turn lookups on.
WEB_LOOKUPS_OFF_TEXT = (
    "Web lookups are off, so I didn't go online {subject}. Live prices, weather and news "
    "need them: turn on Web lookups in Settings > Privacy & Permissions, then ask again."
)
LOCAL_ONLY_LOOKUP_TEXT = (
    "Local Only is on, so I didn't go online {subject}. Live prices, weather and news need "
    "the internet: choose VOOL Auto instead of VOOL Auto Local Only, then ask again."
)


def web_lookups_off_text(subject: str = "to check this") -> str:
    """The refusal for the switch that actually stopped the lookup: Web lookups, or Local Only."""
    from core import policy_engine

    try:
        web_on = bool(policy_engine.allow_web_fallback())
    except Exception:
        web_on = False
    return (LOCAL_ONLY_LOOKUP_TEXT if web_on else WEB_LOOKUPS_OFF_TEXT).format(subject=subject)


def disabled_live_info_result(
    agent: Any,
    *,
    session_id: str,
    user_input: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    disabled_response = web_lookups_off_text()
    _reason, confidence = _route_metadata(agent, source_context, 0.82)
    result = agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=disabled_response,
        confidence=confidence,
        source_context=source_context,
        reason=_reason,
    )
    # A typed refusal marker: this lane did NOT serve the demand, so a caller arbitration
    # (the frontdoor's whole-turn claim) must not let this text finalize sibling demands
    # it never touched -- a refusal is recorded as a slice answer and the turn continues.
    result["live_info_refusal"] = True
    return result


def live_info_result(
    agent: Any,
    *,
    session_id: str,
    user_input: str,
    response: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    _reason, confidence = _route_metadata(agent, source_context, 0.84)
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=response,
        confidence=confidence,
        source_context=source_context,
        reason=_reason,
    )
