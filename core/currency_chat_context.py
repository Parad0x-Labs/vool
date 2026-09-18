"""What EARLIER turns in this chat settled about currency — the breadcrumb the fast path reads.

QA-050-027. `core/currency_intent.py` decides what ONE message asks for and knows nothing about
sessions; this module is the only place that looks backwards, and it returns a plain tuple of codes
so the recognition layer stays free of any store access. Nothing here answers anything.

Why a chat breadcrumb is needed at all
---------------------------------------
Measured on 03b04b39, the live sequence a tester drove:

    "what is  ALL?"     -> currency_definition_fast_path, no model. Correct.
    "WHAT IS all?"      -> cloud Nemotron 550B, 1,512 tokens

The second message is the same question. It missed because "all" is lowercase and the homograph
rule (rightly) refuses a bare lowercase English word — but the chat had JUST established, on the
runtime's own answer, that ALL is the Albanian lek. That answer is evidence, and it was being
thrown away one message later.

Why it reads the chat's ANSWERS rather than a session flag
------------------------------------------------------------
Two reasons, both learned from this runtime.

* A flag would have to be written somewhere. The obvious slot, the hive interaction state, is a
  single mode field that `_apply_interaction_transition` rewrites on the way out of every turn —
  so a currency breadcrumb written there is clobbered by the very turn that set it, and keeping it
  alive would mean a currency special case inside the shared transition table. That is coupling in
  the shared state machine to fix a currency defect, which is the blast radius this project bans.
* The answers are already durable, in two independent places: the surface's own
  `conversation_history`, and the conversation event log every fast path writes through
  `append_conversation_event`. Either one alone carries the breadcrumb, so an operator running with
  ephemeral memory still gets the fix, and a fresh surface with no history still gets it from the
  log.

Reading an answer back is not prose guessing: `codes_from_currency_answer` gates on marker
sentences THIS runtime emits, and the round-trip is pinned by a test that renders the whole family
and parses it back.

What is deliberately NOT context
---------------------------------
A code is enrolled only from a turn whose ANSWER was a currency answer. A user message that merely
contained a capitalised three-letter token — "check the API and the CPU" — enrols nothing, because
no currency answer was produced for it. And enrolment is per CODE, never a "currency mode" flag: a
chat that discussed TRY does not license reading a later bare "all" as the Albanian lek.
"""

from __future__ import annotations

from typing import Any

from core.currency_intent import codes_from_currency_answer, codes_named

#: How many recent turns still count as context. Long enough for a real back-and-forth about a
#: currency, short enough that a code mentioned much earlier does not silently govern a later
#: unrelated question.
_CONTEXT_TURNS = 6

#: A chat message carries one turn's half, so a window of turns is twice as many messages.
_CONTEXT_MESSAGES = _CONTEXT_TURNS * 2


def _extend(found: list[str], codes: Any) -> None:
    for code in codes or ():
        text = str(code or "").strip().upper()
        if text and text not in found:
            found.append(text)


def _codes_from_turn(user_text: str, assistant_text: str) -> list[str]:
    """The codes one completed turn settled, or [] when that turn was not a currency turn."""

    settled = list(codes_from_currency_answer(assistant_text))
    if not settled:
        return []
    # The answer names what it resolved; the question that produced it may name more of the same
    # domain ("1000 TRY to USD" is answered naming both). The question is read ONLY because the
    # answer already proved the turn was a currency turn, so an unrelated capitalised token in some
    # other message can never enter this way.
    found = list(settled)
    _extend(found, codes_named(user_text))
    return found


def _codes_from_history(history: Any) -> list[str]:
    """Codes settled by currency answers in the surface's own conversation history."""

    messages = [item for item in list(history or []) if isinstance(item, dict)]
    if not messages:
        return []
    window = messages[-_CONTEXT_MESSAGES:]
    found: list[str] = []
    for index, message in enumerate(window):
        if str(message.get("role") or "").strip().lower() != "assistant":
            continue
        settled = codes_from_currency_answer(str(message.get("content") or ""))
        if not settled:
            continue
        asked = ""
        for earlier in reversed(window[:index]):
            if str(earlier.get("role") or "").strip().lower() == "user":
                asked = str(earlier.get("content") or "")
                break
        _extend(found, _codes_from_turn(asked, str(message.get("content") or "")))
    return found


def _codes_from_events(session_id: str) -> list[str]:
    """Codes settled by currency answers in this session's conversation event log."""

    if not str(session_id or "").strip():
        return []
    try:
        from core.memory.entries import recent_conversation_events

        rows = recent_conversation_events(str(session_id), limit=_CONTEXT_TURNS)
    except Exception:
        # A missing or unreadable log means no breadcrumb, never a failed turn: the fast path still
        # answers everything it could answer without any context at all.
        return []
    found: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        _extend(
            found,
            _codes_from_turn(str(row.get("user") or ""), str(row.get("assistant") or "")),
        )
    return found


def chat_currency_codes(
    *,
    session_id: str = "",
    source_context: dict[str, Any] | None = None,
) -> tuple[str, ...]:
    """Every currency code THIS chat has already settled, oldest first, deduplicated.

    Both sources are read and merged rather than one being preferred: they cover different failure
    modes (a surface that sends no history; an operator running with memory persistence off), and
    a code found in either is equally settled.
    """

    found: list[str] = []
    _extend(found, _codes_from_events(str(session_id or "")))
    _extend(found, _codes_from_history((source_context or {}).get("conversation_history")))
    return tuple(found)


__all__ = ["chat_currency_codes"]
