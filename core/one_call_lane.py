"""The one-call owner-local lane: on the owner's own turn served by a local model, the model decides.

Measured 2026-10-28 (qwen3:8b, same fake home): a plain loop -- one system line, a handful of tools,
``think: false``, one model call per step -- used a tool on 51 of 54 asks; VOOL's daemon used one on
13 of 54. Measured 2026-10-31 on the fresh asks: on 11 of 15 failing tool asks the model was offered
no tool in any round, because word gates and fast paths decided before the model was consulted; a
knowledge ask paid a planner clause split, a helper call, the tool round and a normalization assist
(5.5 min at 1-2.6 tokens/s); a prose reply on a tool round was raised as a malformed call and paid a
second round; and mail and calendar tools were seated with nothing connected.

So on this lane (``owner_local_one_call_lane``, default on; off restores the earlier path exactly):

- the turn goes straight to the model's tool loop holding the owner catalog. No currency contract,
  closed contracts, conductor clause split, demand-owned or live-data plans, planner, ambiguity probe
  or front-door fast path runs first, and no normalization assist runs after. The checks that keep
  the turn safe stay where they are: secret intake, the owner-local and tools-forbidden checks (the
  catalog's own scope), the home roots and approval gates in dispatch, the claim gates at
  finalization. Money tools are never in the catalog.
- every round runs with ``think`` off;
- a prose reply on a tool round is the model's answer. One retry with the tools, and only when that
  prose itself says it read the owner's data (a read claim the claim gate knows) while no tool ran.
  A reply cut by ``done_reason == "length"`` is never passed off as complete;
- a tool whose source is not connected (mail, calendar) is not seated as runnable, and the round is
  told which sources are not connected so the reply can say so;
- the local runner uses every core.

Scope is ``owner_catalog_applies``: the owner's own local session, a local model, and no instruction
to keep tools out of the turn. A remote or phone session and a cloud model are untouched.
"""
from __future__ import annotations

import os
from typing import Any

FLAG = "owner_local_one_call_lane"

#: ModelRequest metadata key: this request is a round of the one-call lane.
ONE_CALL_ROUND_KEY = "one_call_lane_round"
#: ModelRequest metadata key: the retry round asked for after a prose read claim (at most one).
ONE_CALL_RETRY_KEY = "one_call_lane_retry"
#: source-context key caching the lane verdict for one turn.
_APPLIES_KEY = "_one_call_lane_applies"
#: source-context key caching the connection state of each source for one turn.
_CONNECTED_KEY = "_one_call_lane_connected"

#: Tools that read or act on a source the owner connects first. A source whose state is known to be
#: "not connected" seats none of them; an unknown state keeps them (the tool reports its own error).
SOURCE_OF_INTENT_PREFIX: tuple[tuple[str, str], ...] = (
    ("email.", "mail"),
    ("mail.", "mail"),
)
SOURCE_OF_INTENT: dict[str, str] = {
    "operator.show_agenda": "calendar",
    "operator.check_availability": "calendar",
    "operator.inspect_calendar_event": "calendar",
    "operator.list_calendars": "calendar",
    "operator.search_calendar_event": "calendar",
    "operator.update_calendar_event": "calendar",
    "operator.schedule_calendar_event": "calendar",
}

#: What a retry round is told: its own reply said it read owner data no tool read.
RETRY_INSTRUCTION = (
    "Your reply describes the user's own data, but no tool has read it in this turn. Call the tool "
    "that reads it now. If no tool can read it, say plainly that you did not check."
)
#: Appended to a reply the model's output limit cut off.
CUT_NOTE = "(This reply was cut off at the model's output limit; it is not complete.)"


def lane_enabled() -> bool:
    from core.runtime_flags import flag_enabled

    return flag_enabled(FLAG)


def one_call_lane_applies(text: str, source_context: dict[str, Any] | None) -> bool:
    """Whether this turn runs on the one-call lane: the flag, then the owner catalog's own scope."""
    if not lane_enabled():
        return False
    if isinstance(source_context, dict):
        cached = source_context.get(_APPLIES_KEY)
        if isinstance(cached, bool):
            return cached
    try:
        from core.owner_tool_catalog import owner_catalog_applies

        verdict = bool(owner_catalog_applies(str(text or ""), source_context))
    except Exception:
        verdict = False
    if isinstance(source_context, dict):
        source_context[_APPLIES_KEY] = verdict
    return verdict


def source_of_intent(intent: str) -> str:
    name = str(intent or "").strip()
    if name in SOURCE_OF_INTENT:
        return SOURCE_OF_INTENT[name]
    for prefix, source in SOURCE_OF_INTENT_PREFIX:
        if name.startswith(prefix):
            return source
    return ""


def source_connected(source: str, source_context: dict[str, Any] | None = None) -> bool | None:
    """True / False when the connection state of ``source`` is known, None when it is not."""
    cache: dict[str, Any] = {}
    if isinstance(source_context, dict):
        cache = source_context.setdefault(_CONNECTED_KEY, {})
        if not isinstance(cache, dict):
            cache = {}
            source_context[_CONNECTED_KEY] = cache
    if source in cache:
        return cache[source]
    verdict: bool | None = None
    try:
        from core.action_claim_gate import _calendar_connected, _mail_connected

        if source == "mail":
            verdict = _mail_connected()
        elif source == "calendar":
            verdict = _calendar_connected()
    except Exception:
        verdict = None
    cache[source] = verdict
    return verdict


def seat_is_runnable(intent: str, source_context: dict[str, Any] | None) -> bool:
    """False only for a tool whose source is known to be not connected."""
    source = source_of_intent(intent)
    if not source:
        return True
    return source_connected(source, source_context) is not False


def not_connected_sources(source_context: dict[str, Any] | None) -> tuple[str, ...]:
    return tuple(s for s in ("mail", "calendar") if source_connected(s, source_context) is False)


def not_connected_directive(source_context: dict[str, Any] | None) -> str:
    """One line for the round: the sources with nothing connected, so the model says so."""
    names = {"mail": "email", "calendar": "calendar"}
    missing = [names[s] for s in not_connected_sources(source_context)]
    if not missing:
        return ""
    joined = " and ".join(missing)
    return (
        f"Not connected on this computer: {joined}. If the user asks about their {joined}, say it "
        "is not connected yet and can be added in Settings; never guess its contents."
    )


def not_connected_reply(intent: str, user_text: str) -> str | None:
    """The plain line for a call to a tool whose source is not connected, else None."""
    source = source_of_intent(intent)
    if not source:
        return None
    try:
        from core.action_claim_gate import no_action_reply, reply_language

        return no_action_reply(
            [source],
            connected=lambda name: False if name == source else None,
            language=reply_language(str(user_text or "")),
        )
    except Exception:
        return None


def prose_reads_owner_data(text: str) -> bool:
    """Whether a prose reply says it read or acted on the owner's data (the claim gate's own
    claims: "I checked your notes", "I've set the reminder"). On a prose round no tool ran."""
    try:
        from core.action_claim_gate import action_claims

        return bool(action_claims(str(text or "")))
    except Exception:
        return False


def round_carries_tool_evidence(messages: Any) -> bool:
    """Whether a round's messages already carry a tool's result (a role "tool" message or VOOL's
    own tool observation). Prose after a tool ran answers from that evidence; it is never retried."""
    for message in list(messages or []):
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "") == "tool" or "tool_observation_v1" in str(message.get("content") or ""):
            return True
    return False


def prose_round_outcome(text: str, *, finish_reason: str, retried: bool, tool_evidence: bool = False) -> str:
    """What a prose reply on a one-call tool round is.

    ``"cut"``: the model's output limit cut it (``done_reason == "length"``); it is not complete.
    ``"retry"``: it says it read owner data, no tool result is in the round, and the turn's one
    retry is not spent.
    ``"answer"``: the model's direct answer. ``"empty"``: nothing to answer with.
    """
    body = str(text or "").strip()
    if str(finish_reason or "").strip().lower() == "length":
        return "cut" if body else "empty"
    if not body:
        return "empty"
    if not retried and not tool_evidence and prose_reads_owner_data(body):
        return "retry"
    return "answer"


def cut_reply(text: str) -> str:
    body = str(text or "").rstrip()
    return f"{body}\n\n{CUT_NOTE}" if body else CUT_NOTE


def runner_threads() -> int:
    """Local model threads on this lane: the core count (the balanced budget halves it)."""
    return max(1, int(os.cpu_count() or 1))


#: ModelRequest metadata key: the turn-scoped state shared by every round of one turn.
ONE_CALL_TURN_STATE_KEY = "one_call_lane_turn_state"
_TURN_STATE_KEY = "_one_call_lane_turn_state"


def mark_one_call_round(request: Any, text: str, source_context: dict[str, Any] | None) -> bool:
    """Mark a tool round of a one-call turn: the round key, think off, and the turn's shared state
    (so the one retry is per turn, not per round). False, and nothing marked, off the lane."""
    if not one_call_lane_applies(text, source_context):
        return False
    metadata = getattr(request, "metadata", None)
    if not isinstance(metadata, dict):
        return False
    metadata[ONE_CALL_ROUND_KEY] = True
    state: dict[str, Any] = {}
    if isinstance(source_context, dict):
        existing = source_context.get(_TURN_STATE_KEY)
        state = existing if isinstance(existing, dict) else {}
        source_context[_TURN_STATE_KEY] = state
    metadata[ONE_CALL_TURN_STATE_KEY] = state
    try:
        from core.model_request_policy import REASONING_DISABLED

        request.reasoning_mode = REASONING_DISABLED
    except Exception:
        pass
    return True


def retry_spent(request: Any) -> bool:
    metadata = getattr(request, "metadata", None) or {}
    state = metadata.get(ONE_CALL_TURN_STATE_KEY)
    return bool(metadata.get(ONE_CALL_RETRY_KEY)) or (isinstance(state, dict) and bool(state.get(ONE_CALL_RETRY_KEY)))


def spend_retry(request: Any) -> None:
    metadata = getattr(request, "metadata", None)
    if not isinstance(metadata, dict):
        return
    metadata[ONE_CALL_RETRY_KEY] = True
    state = metadata.get(ONE_CALL_TURN_STATE_KEY)
    if isinstance(state, dict):
        state[ONE_CALL_RETRY_KEY] = True


def request_is_one_call_round(request: Any) -> bool:
    return bool((getattr(request, "metadata", None) or {}).get(ONE_CALL_ROUND_KEY))
