"""Session-scoped offer memory and turn-scoped family navigation state.

Two small bounded stores behind the adaptive tool offer:

1. **Family offer memory** — which capability families a session's recent turns were
   offered. A contextual follow-up ("so? audit the skills in there") classifies as
   plain conversation and used to receive ZERO tools; with this memory the follow-up
   inherits the family of the turn it follows (census defect: follow-ups get nothing).

2. **Turn navigation** — when the model calls ``capability.expand_family``, the
   requested family joins its turn's bounded navigation set, and every later model
   round of THAT turn builds its offer with the set. This is the model's legal
   escalation path past the family-navigation set without raising the per-turn
   definition budget. The set is navigation, never permission: each round's offer
   re-reads policy and availability, and execution keeps its own authority checks.

   Reading the set never changes it. A round renders ONE offer built from one read
   (``core.tool_offer_assembly``) for both its prompt catalog and its native tool
   definitions, and a later round of the turn reads the same set again. The set
   belongs to a turn scope: the session, the turn id and the scope token of the loop
   that owns the turn's model rounds. That owner opens the scope with
   ``begin_turn_navigation`` and closes it with ``end_turn_navigation`` when the
   turn completes, pauses, fails or is cancelled; a scope nobody closes expires
   ``_EXPANSION_TTL_SECONDS`` after it was last used.

Both stores are process-local, bounded, TTL'd and never persisted. They carry family
names only — no user text, no tool results, no arguments.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

_LOCK = threading.Lock()

# session_id -> (families, recorded_at)
_FAMILY_OFFERS: dict[str, tuple[tuple[str, ...], float]] = {}
_FAMILY_OFFER_TTL_SECONDS = 30 * 60
_FAMILY_OFFER_MAX_SESSIONS = 512

#: The source-context key holding the scope token of the loop that owns the turn's model rounds.
TURN_SCOPE_KEY = "tool_offer_turn_scope"


@dataclass
class _TurnNavigation:
    families: tuple[str, ...]
    used_at: float


# (session id, turn id, scope token) -> that turn's navigation set
_EXPANSIONS: dict[tuple[str, str, str], _TurnNavigation] = {}
_EXPANSION_TTL_SECONDS = 5 * 60  # since the scope last recorded or read its set
_EXPANSION_MAX_FAMILIES = 2  # per turn: repeated navigator calls cannot flood the offer
_EXPANSION_MAX_TURNS = 512
# Monotonic: a wall-clock change can neither expire a live turn's set nor revive a dead one.
_clock = time.monotonic

# A follow-up is a SHORT message that carries no new task of its own: an anaphoric
# nudge after a tool-bearing turn ("so?", "and the skills?", "what about pdfs?").
# Deliberately narrow — long messages are new demands and resolve their own families.
_MAX_FOLLOWUP_WORDS = 6


def _prune(store: dict[str, tuple[tuple[str, ...], float]], ttl: float) -> None:
    now = time.time()
    stale = [key for key, (_, at) in store.items() if now - at > ttl]
    for key in stale:
        store.pop(key, None)


def _prune_expansions(now: float) -> None:
    stale = [key for key, entry in _EXPANSIONS.items() if now - entry.used_at > _EXPANSION_TTL_SECONDS]
    for key in stale:
        _EXPANSIONS.pop(key, None)


def _turn_id(source_context: dict[str, Any] | None) -> str:
    context = source_context or {}
    return str(
        context.get("turn_id")
        or context.get("cancel_turn_id")
        or context.get("task_id")
        or ""
    ).strip()


def _session_id(source_context: dict[str, Any] | None) -> str:
    context = source_context or {}
    return str(
        context.get("runtime_session_id")
        or context.get("session_id")
        or ""
    ).strip()


def note_family_offer(session_id: str, families: tuple[str, ...] | list[str]) -> None:
    """Record which families a session's turn was just offered."""
    clean = str(session_id or "").strip()
    fams = tuple(str(f).strip() for f in families if str(f).strip())
    if not clean or not fams:
        return
    with _LOCK:
        _prune(_FAMILY_OFFERS, _FAMILY_OFFER_TTL_SECONDS)
        if len(_FAMILY_OFFERS) >= _FAMILY_OFFER_MAX_SESSIONS and clean not in _FAMILY_OFFERS:
            _FAMILY_OFFERS.pop(next(iter(_FAMILY_OFFERS)), None)
        _FAMILY_OFFERS[clean] = (fams[:_EXPANSION_MAX_FAMILIES], time.time())


def last_offered_families(session_id: str) -> tuple[str, ...]:
    """The families most recently offered to this session (TTL'd, empty when none)."""
    clean = str(session_id or "").strip()
    if not clean:
        return ()
    with _LOCK:
        _prune(_FAMILY_OFFERS, _FAMILY_OFFER_TTL_SECONDS)
        entry = _FAMILY_OFFERS.get(clean)
    return entry[0] if entry else ()


def _scope_key(source_context: dict[str, Any] | None) -> tuple[str, str, str] | None:
    """The turn scope a context names: its session, its turn id and its loop's scope token.

    A context that names no turn has no navigation state. The session is part of the key because
    a client chooses its visible turn ids, so two sessions may present the same one; the scope
    token separates two requests that present the same session and turn id (a retry, a resumed
    approval, a reused task id).
    """
    turn = _turn_id(source_context)
    if not turn:
        return None
    context = source_context or {}
    return (_session_id(context), turn, str(context.get(TURN_SCOPE_KEY) or "").strip())


def begin_turn_navigation(source_context: dict[str, Any] | None) -> str:
    """Open the navigation scope of the turn this context carries, and return its token.

    Called by the owner of a turn's model rounds before the first round. A context that already
    carries a scope (a nested loop inside the same turn) keeps it, and "" comes back so that only
    the opener closes it.
    """
    if not isinstance(source_context, dict) or str(source_context.get(TURN_SCOPE_KEY) or "").strip():
        return ""
    token = uuid.uuid4().hex
    source_context[TURN_SCOPE_KEY] = token
    return token


def end_turn_navigation(source_context: dict[str, Any] | None, token: str) -> None:
    """Close the scope ``begin_turn_navigation`` opened: the turn's model rounds are over.

    Drops exactly that scope's navigation state -- another turn's, even one presenting the same
    session and visible turn id, is untouched -- and removes the token from the context.
    """
    clean = str(token or "").strip()
    if not clean:
        return
    with _LOCK:
        for key in [key for key in _EXPANSIONS if key[2] == clean]:
            _EXPANSIONS.pop(key, None)
    if isinstance(source_context, dict) and source_context.get(TURN_SCOPE_KEY) == clean:
        source_context.pop(TURN_SCOPE_KEY, None)


def record_family_expansion(source_context: dict[str, Any] | None, family: str) -> tuple[str, ...]:
    """The model asked to expand a family: add it to THIS turn's navigation set.

    Returns the turn's set after recording. Idempotent: a family already in the set is not added
    again. Bounded: a turn holding ``_EXPANSION_MAX_FAMILIES`` families adds no further family,
    and the returned set shows it is absent, so the caller can say so instead of claiming it was
    seated. A context that names no turn records nothing and gets ().
    """
    key = _scope_key(source_context)
    fam = str(family or "").strip().lower()
    if key is None or not fam:
        return ()
    with _LOCK:
        now = _clock()
        _prune_expansions(now)
        entry = _EXPANSIONS.get(key)
        if entry is None and len(_EXPANSIONS) >= _EXPANSION_MAX_TURNS:
            _EXPANSIONS.pop(min(_EXPANSIONS, key=lambda item: _EXPANSIONS[item].used_at), None)
        families = entry.families if entry is not None else ()
        if fam not in families and len(families) < _EXPANSION_MAX_FAMILIES:
            families = (*families, fam)
        _EXPANSIONS[key] = _TurnNavigation(families=families, used_at=now)
        return families


def turn_family_expansions(source_context: dict[str, Any] | None) -> tuple[str, ...]:
    """The families this turn's model asked to expand, for building a round's offer.

    Never clears the set: the prompt catalog and the native tool definitions of a round render
    one offer built from one read, and every later round of the turn reads the set again. Reading
    counts as use, so a turn that is still running keeps its set; only an abandoned scope expires.
    """
    key = _scope_key(source_context)
    if key is None:
        return ()
    with _LOCK:
        now = _clock()
        _prune_expansions(now)
        entry = _EXPANSIONS.get(key)
        if entry is None:
            return ()
        entry.used_at = now
        return entry.families


def contextually_followup_text(text: str) -> bool:
    """Whether the message is follow-up shaped: short, no new task verb pattern.

    "so?" / "and the skills in there?" are follow-ups; "research the EV market and
    write a file" is a new demand. Word-count bounded keeps this deterministic.
    """
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return False
    return len(cleaned.split()) <= _MAX_FOLLOWUP_WORDS


def followup_inherited_families(
    text: str, source_context: dict[str, Any] | None
) -> tuple[str, ...]:
    """The families a contextual follow-up should inherit, or () when it should not.

    Only a FOLLOW-UP-SHAPED message inherits: a new demand resolves its own
    families and must never be coloured by the previous turn's. The session's
    most recently offered families come back TTL'd and bounded.
    """
    if not contextually_followup_text(text):
        return ()
    return last_offered_families(_session_id(source_context))


def reset_offer_state() -> None:
    """Clear both stores (tests)."""
    with _LOCK:
        _FAMILY_OFFERS.clear()
        _EXPANSIONS.clear()


__all__ = [
    "TURN_SCOPE_KEY",
    "begin_turn_navigation",
    "contextually_followup_text",
    "end_turn_navigation",
    "followup_inherited_families",
    "last_offered_families",
    "note_family_offer",
    "record_family_expansion",
    "reset_offer_state",
    "turn_family_expansions",
]
