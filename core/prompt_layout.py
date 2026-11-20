"""
core/prompt_layout.py
=====================
Wire-level request layout for provider prefix caches: the parts of a request that do not change from
turn to turn go first, the parts that do go last, and nothing the model reads is rewritten.

Why this exists
---------------
A provider prefix cache serves a request from the longest byte-identical opening it has seen before.
Measured on VOOL's own request logs (novelty/layout/prefix_diff.py): the turn-directives message
("Context for this turn:", which opens with the minute clock) sits in third place, ahead of the
evidence message and the grounding capsule, so every minute the match ended there and about 1,900
characters of bytes that did not change were billed again.

What it does (each step behind its own runtime flag, all OFF by default)
------------------------------------------------------------------------
* ``prompt_layout_context_last``: the turn-directives message moves down past the run of evidence and
  grounding messages that follows it, to sit right before the current turn. Whole messages move; no
  message is edited. Only that exact shape is touched: any other message in the way (an assistant
  turn, a tool result, a second turn-directives message) leaves the request as it came.
* ``prompt_layout_stable_grounding_first``: the grounding messages whose text is the same from turn to
  turn (the absence marker and the no-evidence grounding capsule) move up to sit right behind the
  leading system messages, in front of the chat history and the evidence message (whose last line
  carries the current turn).

With every flag off :func:`apply_prompt_layout` returns its input unchanged (the same list object).

What it never does
------------------
It never moves text across an assistant message, never merges, splits or rewrites a message, never
moves a message with retrieved records ahead of anything, and never adds content. Nothing here is
stored: a forgotten fact that no longer appears in the request cannot match any prefix, and the
function keeps no state.
"""
from __future__ import annotations

from typing import Any

from core.experiment_switches import switch_enabled
from core.internal_message_schema import is_turn_directives_message

EVIDENCE_OPENER = "Relevant context and evidence:"
#: Grounding messages whose text does not change from turn to turn when nothing was looked up.
ABSENCE_OPENER = "<records_absence"
NO_EVIDENCE_CAPSULE_OPENER = "<retrieved_context>\nGrounding observations for this turn."
#: A capsule that carries retrieved records. It is volatile and never moves ahead of anything.
RECORDS_CAPSULE_OPENER = "<retrieved_context>"


def _text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
    return ""


def _role(message: Any) -> str:
    return str(message.get("role") or "").strip().lower() if isinstance(message, dict) else ""


def _is_evidence_message(message: Any) -> bool:
    return _role(message) == "user" and _text(message).startswith(EVIDENCE_OPENER)


def _is_grounding_message(message: Any) -> bool:
    if _role(message) != "system":
        return False
    text = _text(message)
    return text.startswith(ABSENCE_OPENER) or text.startswith(RECORDS_CAPSULE_OPENER)


def _is_stable_grounding(message: Any) -> bool:
    if _role(message) != "system":
        return False
    text = _text(message)
    return text.startswith(ABSENCE_OPENER) or text.startswith(NO_EVIDENCE_CAPSULE_OPENER)


def _context_run(messages: list[dict[str, Any]]) -> tuple[int, int] | None:
    """(index of the turn-directives message, index just past the run behind it), or None.

    The run is the unbroken stretch of evidence and grounding messages right behind the
    turn-directives message. It must be followed by another message (the current turn).
    """
    found = [i for i, m in enumerate(messages) if is_turn_directives_message(m)]
    if len(found) != 1:
        return None
    start = found[0]
    end = start + 1
    while end < len(messages) and (_is_evidence_message(messages[end]) or _is_grounding_message(messages[end])):
        end += 1
    if end == start + 1 or end >= len(messages):
        return None
    return start, end


def context_last(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Move the turn-directives message behind the evidence and grounding run that follows it."""
    span = _context_run(messages)
    if span is None:
        return messages
    start, end = span
    return [*messages[:start], *messages[start + 1:end], messages[start], *messages[end:]]


def stable_grounding_first(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable grounding messages move up to sit right behind the leading system messages.

    The absence marker and the no-evidence capsule are the same text on every turn, so in front of
    the chat history (which differs from chat to chat) they extend the opening every request of every
    chat shares. Only messages of those two shapes move, only in front of the current turn, and
    never across an assistant message that carries a tool call (a tool loop is left as it is).
    """
    n = len(messages)
    if n < 3 or _role(messages[0]) != "system":
        return messages
    lead = 0
    while lead < n and _role(messages[lead]) == "system" and not _is_grounding_message(messages[lead]) \
            and not is_turn_directives_message(messages[lead]):
        lead += 1
    movable = [i for i in range(lead, n - 1) if _is_stable_grounding(messages[i])]
    if not movable or movable == list(range(lead, lead + len(movable))):
        return messages
    if any(_looks_like_tool_exchange(m) for m in messages[lead:n - 1]):
        return messages
    moved = [messages[i] for i in movable]
    rest = [m for i, m in enumerate(messages) if i >= lead and i not in set(movable)]
    return [*messages[:lead], *moved, *rest]


def _looks_like_tool_exchange(message: Any) -> bool:
    """A tool call or tool result inside the request: such a loop keeps its own order."""
    if not isinstance(message, dict):
        return False
    if message.get("tool_calls") or str(message.get("role") or "").lower() == "tool":
        return True
    text = _text(message)
    return text.startswith("[tool result]") or text.startswith('{"tool"')


def apply_prompt_layout(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The request's messages in cache-friendly order. The input list is never mutated.

    With every layout flag off this is the identity: the same list object comes back.
    """
    if not messages:
        return messages
    out = messages
    if switch_enabled("prompt_layout_context_last"):
        out = context_last(out)
    if switch_enabled("prompt_layout_stable_grounding_first"):
        out = stable_grounding_first(out)
    return out


__all__ = ["apply_prompt_layout", "context_last", "stable_grounding_first"]
