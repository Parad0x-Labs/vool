from __future__ import annotations

from typing import Any

from core.turn_contract import LaneProposal


#: What the user is told when a live question (weather, prices, news) cannot be looked up this
#: turn. One sentence per cause, each naming the way out, so the reply never reads as an outage.
#: The opening words are kept stable ("Live web lookup is ...") for log and test readers.
WEB_OFF_TEXT = (
    "Live web lookup is off: Web lookups are switched off, so I can't check current weather, "
    "prices or news. You can turn them on in Settings, Privacy & Permissions."
)
OFFLINE_TEXT = (
    "Live web lookup is off because this chat is Offline: nothing leaves this computer, so I "
    "can't check current weather, prices or news. To let me look it up, choose Computer + web "
    "or Local first in the model menu."
)
MACHINE_OFFLINE_TEXT = (
    "Live web lookup is off because VOOL is set to keep everything on this computer, so I can't "
    "check current weather, prices or news."
)


def web_unavailable_text(source_context: dict[str, object] | None) -> str:
    """The refusal for THIS turn's reason: machine-wide offline, an Offline chat, or the switch."""
    try:
        from core.policy_engine import local_only_mode

        if local_only_mode():
            return MACHINE_OFFLINE_TEXT
    except Exception:
        pass
    try:
        from core.auto_local_only_mode import turn_is_offline

        if turn_is_offline(dict(source_context or {})):
            return OFFLINE_TEXT
    except Exception:
        pass
    return WEB_OFF_TEXT


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


def disabled_live_info_result(
    agent: Any,
    *,
    session_id: str,
    user_input: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    disabled_response = web_unavailable_text(source_context)
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
