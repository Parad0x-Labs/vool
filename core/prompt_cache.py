"""
core/prompt_cache.py
====================
Explicit prompt-cache breakpoints for the routes that need them, and the money they move.

Why this exists
---------------
Providers bill a repeated request PREFIX at a discount. OpenAI, DeepSeek, Moonshot (Kimi), Grok
and Gemini 2.5 do it on their own once the prefix is stable (VOOL keeps the leading system
message stable since the per-turn segments moved behind the history, see
``core.prompt_normalizer._PER_TURN_SYSTEM_SEGMENTS``). Anthropic models do NOT: nothing is cached
unless the request marks a breakpoint with ``cache_control``, so every Claude turn VOOL sent was
billed as fresh input, the stable opening included.

A cache WRITE is not free. Anthropic bills the tokens it writes at 1.25x the input rate (5-minute
TTL) and the tokens it later reads at 0.1x. A breakpoint whose prefix changes every turn is
therefore a pure 25% surcharge, and the money law (nothing bypasses VOOL's wallet, caps, approval
or receipts) means the reservation has to hold that worst case before the call is sent. This
module owns both halves, so the wire and the wallet cannot disagree:

* :func:`cache_breakpoint_plan` decides, from the lane and the model alone, whether a request will
  carry breakpoints and at which write multiplier;
* :func:`apply_cache_breakpoints` marks the final OpenAI-shaped messages accordingly;
* :func:`cache_write_prompt_tokens` reads what the provider says it wrote, across dialects.

Scope
-----
Only the OpenRouter lane with an ``anthropic/`` model gets explicit breakpoints: OpenRouter
documents ``cache_control`` on content parts for those models and passes it through. Anthropic's
own OpenAI-compatibility endpoint does not take it, and the automatic providers need nothing on
the wire. Everything is behind the ``prompt_cache_breakpoints`` runtime flag, OFF by default: with
the flag off the payload is byte-identical to the one sent before this module existed.

Placement (``VOOL_PROMPT_CACHE_PLACEMENT``)
-------------------------------------------
* ``system`` (default): one breakpoint at the end of the leading system message. Tools and the
  stable system text are cached; history is not.
* ``system_history``: also a breakpoint on the last message before the per-turn directives, so the
  chat history is cached too. That only pays when the route keeps the per-turn directives out of
  the cached prefix; if the route folds every system message into one top-level system block, the
  history breakpoint writes every turn and never reads. Measure before choosing it.
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from typing import Any

#: Anthropic's 5-minute-TTL write price as a multiple of the input price.
ANTHROPIC_CACHE_WRITE_MULTIPLIER = 1.25

PLACEMENT_SYSTEM = "system"
PLACEMENT_SYSTEM_HISTORY = "system_history"
_PLACEMENTS = (PLACEMENT_SYSTEM, PLACEMENT_SYSTEM_HISTORY)
PLACEMENT_ENV = "VOOL_PROMPT_CACHE_PLACEMENT"

_EPHEMERAL = {"type": "ephemeral"}


@dataclass(frozen=True)
class CachePlan:
    """Whether a request carries explicit breakpoints, and what that may cost."""

    enabled: bool
    reason: str
    placement: str = ""
    write_multiplier: float = 1.0


def cache_placement() -> str:
    """The configured placement; an unknown value falls back to the narrower ``system``."""
    raw = str(os.environ.get(PLACEMENT_ENV) or "").strip().lower()
    return raw if raw in _PLACEMENTS else PLACEMENT_SYSTEM


def _is_anthropic_model(model_id: str) -> bool:
    model = str(model_id or "").strip().lower()
    return model.startswith("anthropic/") or model.startswith("~anthropic/")


def cache_breakpoint_plan(*, openrouter_lane: bool, model_id: str, local_runtime: bool = False) -> CachePlan:
    """Decide whether this call gets explicit breakpoints. Pure: no network, no catalog read.

    The same answer feeds the wire (:func:`apply_cache_breakpoints`) and the reservation
    (``core.paid_call_reservation``), so a call that may write the cache is always reserved at the
    write price.
    """
    from core.runtime_flags import flag_enabled

    if not flag_enabled("prompt_cache_breakpoints"):
        return CachePlan(False, "flag_off")
    if local_runtime:
        return CachePlan(False, "local_runtime")
    if not openrouter_lane:
        return CachePlan(False, "route_without_cache_control")
    if not _is_anthropic_model(model_id):
        return CachePlan(False, "model_caches_automatically_or_not_at_all")
    return CachePlan(True, "anthropic_on_openrouter", cache_placement(), ANTHROPIC_CACHE_WRITE_MULTIPLIER)


def _marked_parts(content: Any) -> list[dict[str, Any]] | None:
    """The content as text parts with a breakpoint on the last text part, or None if not markable."""
    if isinstance(content, str):
        if not content.strip():
            return None
        return [{"type": "text", "text": content, "cache_control": dict(_EPHEMERAL)}]
    if isinstance(content, list) and content:
        parts = [dict(part) if isinstance(part, dict) else part for part in content]
        for index in range(len(parts) - 1, -1, -1):
            part = parts[index]
            if isinstance(part, dict) and part.get("type") == "text" and str(part.get("text") or "").strip():
                part["cache_control"] = dict(_EPHEMERAL)
                return parts
    return None


def _history_breakpoint_index(messages: list[dict[str, Any]]) -> int | None:
    """The plain user/assistant message right before the per-turn directives, if there is one.

    A tool exchange or another system message at the history's end gets no mark.
    """
    from core.internal_message_schema import is_turn_directives_message

    directives = next((i for i, m in enumerate(messages) if i > 0 and is_turn_directives_message(m)), None)
    if directives is None or directives < 2:
        return None
    message = messages[directives - 1]
    if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"} or message.get("tool_calls"):
        return None
    return directives - 1 if _marked_parts(message.get("content")) is not None else None


def apply_cache_breakpoints(messages: list[dict[str, Any]], plan: CachePlan) -> tuple[list[dict[str, Any]], int]:
    """Return (messages with breakpoints, breakpoints placed). The input list is never mutated.

    Only ``cache_control`` is added and string content becomes one text part: the text the model
    reads is byte-identical. A plan that is not enabled returns the messages untouched.
    """
    if not plan.enabled or not messages:
        return messages, 0
    marked = copy.deepcopy(messages)
    placed = 0
    first = marked[0]
    if isinstance(first, dict) and first.get("role") == "system":
        parts = _marked_parts(first.get("content"))
        if parts is not None:
            first["content"] = parts
            placed += 1
    if plan.placement == PLACEMENT_SYSTEM_HISTORY:
        index = _history_breakpoint_index(messages)
        if index is not None:
            parts = _marked_parts(marked[index].get("content"))
            if parts is not None:
                marked[index]["content"] = parts
                placed += 1
    return (marked, placed) if placed else (messages, 0)


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(value)


def cache_write_prompt_tokens(usage: Any) -> int | None:
    """Prompt tokens the provider says it WROTE to its cache, or None when it reported none.

    OpenRouter says ``prompt_tokens_details.cache_write_tokens``; Anthropic's native usage says
    ``cache_creation_input_tokens``. A reported 0 is a real zero; an absent field stays unknown,
    never an assumed zero.
    """
    try:
        block = dict(usage or {})
    except Exception:
        return None
    details = block.get("prompt_tokens_details")
    candidates = (
        details.get("cache_write_tokens") if isinstance(details, dict) else None,
        block.get("cache_creation_input_tokens"),
    )
    for value in candidates:
        parsed = _nonnegative_int(value)
        if parsed is not None:
            return parsed
    return None


__all__ = [
    "ANTHROPIC_CACHE_WRITE_MULTIPLIER",
    "PLACEMENT_ENV",
    "PLACEMENT_SYSTEM",
    "PLACEMENT_SYSTEM_HISTORY",
    "CachePlan",
    "apply_cache_breakpoints",
    "cache_breakpoint_plan",
    "cache_placement",
    "cache_write_prompt_tokens",
]
