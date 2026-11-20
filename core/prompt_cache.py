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

Placement
---------
One breakpoint, at the end of the leading system message: tools and the stable system text are
cached, the history is not. (A history breakpoint was tried and removed: when a route folds every
system message into one top-level block, it writes every turn and never reads.)

Billing facts
-------------
Settlement never asks today's flag what a past call did. It prices a cache write from what the
provider reported, and holds the worst case when a markable model's response reports no write
count (:func:`cache_write_surcharge_tokens`).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

#: Anthropic's 5-minute-TTL write price as a multiple of the input price.
ANTHROPIC_CACHE_WRITE_MULTIPLIER = 1.25

_EPHEMERAL = {"type": "ephemeral"}


@dataclass(frozen=True)
class CachePlan:
    """Whether a request carries explicit breakpoints, and what that may cost."""

    enabled: bool
    reason: str
    write_multiplier: float = 1.0


def _is_anthropic_model(model_id: str) -> bool:
    model = str(model_id or "").strip().lower()
    return model.startswith("anthropic/") or model.startswith("~anthropic/")


def cache_breakpoint_plan(*, openrouter_lane: bool, model_id: str, local_runtime: bool = False) -> CachePlan:
    """Decide whether this call gets explicit breakpoints. Pure: no network, no catalog read.

    The same answer feeds the wire (:func:`apply_cache_breakpoints`) and the reservation
    (``core.paid_call_reservation``), so a call that may write the cache is always reserved at the
    write price.

    The flag is read when the call is made. Settlement does not read it again: it prices from what
    the provider reported (:func:`cache_write_surcharge_tokens`), so flipping the flag between
    reserve and settle cannot change what a past call is charged.
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
    return CachePlan(True, "anthropic_on_openrouter", ANTHROPIC_CACHE_WRITE_MULTIPLIER)


def reservation_cache_plan(model_id: str) -> CachePlan:
    """The plan a RESERVATION holds: any lane that could mark this model, so never less than the wire.

    The adapter decides the wire from the manifest (OpenRouter lane or an ``openrouter.ai`` base
    URL); the reservation only sees a provider string, which cannot always tell those routes
    apart. Holding the write price for every route of a markable model keeps the reservation at or
    above what the call can cost.
    """
    return cache_breakpoint_plan(openrouter_lane=True, model_id=model_id)


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


#: The cheapest prefix a Claude model caches (Haiku 5.5). A breakpoint under it is ignored by the provider.
MIN_CACHEABLE_TOKENS = 512
#: Characters per token used to decide whether a prefix reaches the minimum. Measured on the logged
#: Haiku requests (prompt tokens against characters): 3.0 to 3.4, so 3.0 never over-counts.
_CHARS_PER_TOKEN_FLOOR = 3.0


def _message_text(message: Any) -> str:
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
    return ""


def stable_lead_run_end(messages: list[dict[str, Any]]) -> int | None:
    """Index of the last message of the stable leading run, or None when the run is too short to cache.

    The run is the unbroken stretch of system messages at the front that say the same thing every turn:
    everything up to the first message that opens with the per-turn context header, a retrieved-records
    capsule, or any non-system message. A run under the provider's minimum (512 tokens, counted at
    3 characters per token so it is never over-counted) is not marked: the provider would ignore the
    marker, and marking a prefix too short to be cached buys nothing.
    """
    from core.internal_message_schema import TURN_DIRECTIVES_HEADER

    end = -1
    chars = 0
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or str(message.get("role") or "").lower() != "system":
            break
        text = _message_text(message)
        if text.startswith(TURN_DIRECTIVES_HEADER):
            break
        if text.startswith("<retrieved_context>") and not text.startswith(
            "<retrieved_context>\nGrounding observations for this turn."
        ):
            break
        end = index
        chars += len(text)
    if end < 0 or chars / _CHARS_PER_TOKEN_FLOOR < MIN_CACHEABLE_TOKENS:
        return None
    return end


def apply_cache_breakpoints(messages: list[dict[str, Any]], plan: CachePlan) -> tuple[list[dict[str, Any]], int]:
    """Return (messages with breakpoints, breakpoints placed). The input list is never mutated.

    Only ``cache_control`` is added and string content becomes one text part: the text the model
    reads is byte-identical. A plan that is not enabled returns the messages untouched.

    Default placement is the end of the leading system message. With ``prompt_cache_lead_run_marker``
    on, it is the end of the stable leading run (:func:`stable_lead_run_end`) instead, and nothing is
    marked when that run is under the provider's minimum.
    """
    if not plan.enabled or not messages:
        return messages, 0
    from core.experiment_switches import switch_enabled

    if switch_enabled("prompt_cache_lead_run_marker"):
        target = stable_lead_run_end(messages)
        if target is None:
            return messages, 0
        marked = copy.deepcopy(messages)
        parts = _marked_parts(marked[target].get("content"))
        if parts is None:
            return messages, 0
        marked[target]["content"] = parts
        return marked, 1
    marked = copy.deepcopy(messages)
    placed = 0
    first = marked[0]
    if isinstance(first, dict) and first.get("role") == "system":
        parts = _marked_parts(first.get("content"))
        if parts is not None:
            first["content"] = parts
            placed += 1
    return (marked, placed) if placed else (messages, 0)


def unmarked_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The messages as they were before :func:`apply_cache_breakpoints` marked them.

    A message whose content is one text part carrying ``cache_control`` goes back to a plain string.
    Every other message is returned as it is, so a request without markers comes back unchanged (the
    same list object). The evidence seal digests the request as the model reads it, and a marker
    changes how the content is written, not what it says.
    """
    changed = False
    out: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if (
            isinstance(content, list)
            and len(content) == 1
            and isinstance(content[0], dict)
            and content[0].get("type") == "text"
            and set(content[0]) == {"type", "text", "cache_control"}
        ):
            restored = dict(message)
            restored["content"] = content[0]["text"]
            out.append(restored)
            changed = True
        else:
            out.append(message)
    return out if changed else messages


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


def bills_cache_writes_at_premium(model_id: str) -> bool:
    """Anthropic bills the prompt tokens it writes to its cache above the input rate, on every route."""
    model = str(model_id or "").strip().lower().lstrip("~")
    return model.startswith("anthropic/") or model.startswith("claude")


def cache_write_surcharge_tokens(usage: Any, *, model_id: str, prompt_tokens: int) -> int:
    """Prompt tokens a settlement must price at the cache-write rate, from facts that cannot change.

    Never from the flag as it stands at settlement: a past call is charged for what happened.

    * The provider reported what it wrote: those tokens, for a model billed a write premium. A
      reported write is billed whatever VOOL's flag says now, and a reported 0 is a real zero.
    * Nothing reported, for a model VOOL can mark: every prompt token, the worst case. Whether
      this call carried a breakpoint is not known here, and an unknown write is a liability.
    * Anything else: no surcharge.
    """
    tokens = max(0, int(prompt_tokens))
    if tokens <= 0 or not bills_cache_writes_at_premium(model_id):
        return 0
    written = cache_write_prompt_tokens(usage)
    if written is not None:
        return min(written, tokens)
    return tokens if _is_anthropic_model(model_id) else 0


__all__ = [
    "ANTHROPIC_CACHE_WRITE_MULTIPLIER",
    "CachePlan",
    "MIN_CACHEABLE_TOKENS",
    "apply_cache_breakpoints",
    "bills_cache_writes_at_premium",
    "cache_breakpoint_plan",
    "cache_write_prompt_tokens",
    "cache_write_surcharge_tokens",
    "reservation_cache_plan",
    "stable_lead_run_end",
    "unmarked_messages",
]
