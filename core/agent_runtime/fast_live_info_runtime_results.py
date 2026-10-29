from __future__ import annotations

import re
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


#: What the person reads when a live lookup was not made. It names what stopped it and, where the
#: person can change it, where: "disabled on this runtime" left people with no idea how to turn
#: lookups on. "Can't", not "didn't": a sentence denying a web call is one the evidence binder
#: must prove, and inside a planner sub-turn it cannot, so it rewrote this one into nonsense.
WEB_LOOKUPS_OFF_TEXT = (
    "Web lookups are off, so I can't go online {subject}. Live prices, weather and news "
    "need them: turn on Web lookups in Settings > Privacy & Permissions, then ask again."
)
#: Local Only chosen as the model for this chat ("VOOL Auto Local Only").
LOCAL_ONLY_LOOKUP_TEXT = (
    "Local Only is on, so I can't go online {subject}. Live prices, weather and news need "
    "the internet: choose VOOL Auto instead of VOOL Auto Local Only, then ask again."
)
#: "Keep everything on this machine", which holds the whole computer offline. Web lookups stays
#: off and cannot be switched on while it is set, so the switch is not offered as the way out.
MACHINE_LOCAL_ONLY_LOOKUP_TEXT = (
    "This computer is set to keep everything on this machine, so I can't go online {subject}. "
    "Live prices, weather and news need the internet, so they are not available while that is set."
)


# A live-data ask names a reading taken NOW. These words make it one whatever else the text says.
_CURRENT_READING_RE = re.compile(
    r"\b(?:now|right\s+now|today|tonight|tomorrow|current(?:ly)?|latest|live|at\s+the\s+moment|"
    r"this\s+(?:morning|afternoon|evening|minute|hour)|as\s+of|real[\s-]?time)\b",
    re.IGNORECASE,
)
# An explanation, a cause, a history or a trait: what the model can answer without going online.
_GENERAL_KNOWLEDGE_RE = re.compile(
    r"^\s*(?:please\s+)?(?:explain|why|how\s+(?:does|do|did|can|could|would)|"
    r"what\s+(?:determines|affects|drives|causes|moves|influences)|"
    r"tell\s+me\s+(?:about\s+)?(?:why|how))\b"
    r"|^\s*how\s+(?:is|are)\b[^?.]*\b(?:determined|calculated|measured|decided|set|formed|forecast)\b"
    r"|\b(?:history|historical(?:ly)?|over\s+(?:time|the\s+years)|volatil(?:e|ity)|"
    r"in\s+general|usually|typically|tends?\s+to|so\s+often)\b",
    re.IGNORECASE,
)


def asks_only_for_general_knowledge(text: str) -> bool:
    """A question that mentions prices or weather without asking for a reading taken now:
    "explain how the bitcoin price is determined", "why is London so often rainy", "gold price
    history". With Web lookups off these are the model's to answer; refusing them as live lookups
    (as the first cut of the switch gate did) answered nothing anyone asked about."""
    clean = " ".join(str(text or "").split())
    if not clean or _CURRENT_READING_RE.search(clean):
        return False
    return bool(_GENERAL_KNOWLEDGE_RE.search(clean))


def web_lookups_off_text(subject: str = "to check this", *, source_context: dict[str, object] | None = None) -> str:
    """The refusal for what actually stopped the lookup, in the order it binds: the machine-wide
    local-only setting, then Local Only for this chat, then the Web lookups switch."""
    from core import policy_engine
    from core.remote_fetch_policy import local_only_active

    try:
        machine_local_only = bool(policy_engine.local_only_mode())
    except Exception:
        machine_local_only = False
    if machine_local_only:
        return MACHINE_LOCAL_ONLY_LOOKUP_TEXT.format(subject=subject)
    try:
        chat_local_only = bool(local_only_active(source_context))
    except Exception:
        chat_local_only = False
    return (LOCAL_ONLY_LOOKUP_TEXT if chat_local_only else WEB_LOOKUPS_OFF_TEXT).format(subject=subject)


def lookup_refusal_reason(source_context: dict[str, object] | None = None) -> str:
    """The same refusal as a short reason, for a part of a longer message that was not answered:
    "I could not answer this part of your message: - the weather in Kaunas (<this>)"."""
    text = web_lookups_off_text(source_context=source_context)
    if text.startswith("This computer is set"):
        return "this computer is set to keep everything on this machine"
    if text.startswith("Local Only is on"):
        return "Local Only is on for this chat"
    return "Web lookups are off; turn them on in Settings > Privacy & Permissions"


def refusal_reason_text(reason: object, *, source_context: dict[str, object] | None = None) -> str:
    """Plain words for a typed door refusal ("web_lookups_off", the Local Only denial), else ''."""
    from core.remote_fetch_policy import LOCAL_ONLY_EGRESS_DENIAL, WEB_LOOKUPS_OFF_DENIAL

    text = str(reason or "")
    if WEB_LOOKUPS_OFF_DENIAL in text or LOCAL_ONLY_EGRESS_DENIAL in text:
        return web_lookups_off_text(source_context=source_context)
    return ""


def disabled_live_info_result(
    agent: Any,
    *,
    session_id: str,
    user_input: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    disabled_response = web_lookups_off_text(source_context=source_context)
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
    result["live_info_refusal_reason"] = lookup_refusal_reason(source_context)
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
