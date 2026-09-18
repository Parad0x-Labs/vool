"""Size one turn's output budget against the lane that will actually serve it.

Pure policy: nothing here imports the router, an adapter or a manifest, so it can be exercised
standalone the way ``core/prompt_budget.py`` is.

The budget policy this is groundwork for takes ONE argument -- the output mode -- so a 550B cloud
model and qwen3:0.6b are handed the same 240 tokens. Measured 2026-07-28 against
``nvidia/nemotron-3-ultra-550b-a55b:free``: plain_text turns went out at ``max_tokens`` 284 ("Hey"),
440 and 356; the model spent them reasoning and returned EMPTY ``content``, which reached the user
as "I couldn't get a live model response". Same shape on a tool turn against
``nvidia/nemotron-3-nano-30b-a3b:free``: at 512 the turn ended ``finish_reason: "length"`` with no
call, at 3000 both phrasings returned ``finish_reason: "tool_calls"``.

``core/prompt_normalizer._max_output_tokens`` calls ``resolve_output_budget`` whenever it is handed
a ``LaneCapability``; its production callers still pass ``capability=None`` and get the fixed mode
table. The first real lane is ``core.cloud_broker.lane_output_budget`` (2026-09-06): the broker is
where the serving model is actually known, so it resolves the prompt layer's number against that
model's window, completion cap, cost class and catalog ``reasoning`` flag before every cloud call,
and re-checks a paid model's estimate against the lifted number. Measured cause: a grounded
comparison on ``nvidia/nemotron-3.5-lightning:free`` at the chat table's 520 tokens ended
``finish_reason: "length"`` with the model's half-written reasoning as its whole reply; at 2048 the
same request returned a sourced 1063-char answer with the reasoning kept in its own field.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

FREE_LOCAL = "free_local"
FREE_CLOUD = "free_cloud"
PAID_CLOUD = "paid_cloud"
REMOTE_UNKNOWN = "remote_unknown"

# 3000 is not a new number. It is `_CLOUD_TOOL_CALL_FLOOR_TOKENS` from
# adapters/openai_compatible_adapter.py, measured against nvidia/nemotron-3-nano-30b-a3b:free --
# 512 tokens produced `finish_reason: "length"` and no call, 3000 produced the call on both
# phrasings tried. The measurement describes how much room a model needs to reach the call, not
# what the room costs, so the paid lane carries the same number: a tool turn that stops short is
# billed for the whole input and returns nothing usable.
_TOOL_INTENT_TARGET_TOKENS = 3000

# The room this runtime has already decided a turn needs when it has something long to write: the
# creative-director branch in core/prompt_normalizer.py raises `max_output_tokens` to 1800. On a
# lane whose output tokens cost nothing that is the ceiling of usefulness rather than a spend
# decision, so a free cloud turn starts from it instead of from a 240-token table sized for a
# 0.6B local model.
_FREE_CLOUD_TARGET_TOKENS = 1800

# The largest budget this runtime's own adaptive chat path ever asks for is 760
# (`_adaptive_chat_max_output_tokens`, research ceiling). Going above what the prompt layer ever
# requests on its own would be inventing spend. Going below it is the expensive failure: `max_tokens`
# is a ceiling and only emitted tokens are billed, so a budget too small to finish the answer pays
# for the FULL input and delivers a truncated reply that has to be asked for again.
_PAID_CLOUD_TARGET_TOKENS = 760

# Output and prompt share one window. Half each is the split this codebase already applies --
# `_thinking_aware_output_budget` in adapters/openai_compatible_adapter.py bounds its reserve by
# `context_window // 2`. A larger output share starves the protected system prompt, and
# core/prompt_budget.py then fails the turn closed instead of answering it.
_CONTEXT_WINDOW_OUTPUT_DIVISOR = 2

# Headroom a reasoning model needs before it writes the first word of the answer. Same value and
# same measurement as `_THINKING_RESERVE_TOKENS` in adapters/openai_compatible_adapter.py: qwen3:4b
# spent ~800-2000 tokens reasoning on a one-sentence question, and at a 200-token budget produced
# 802 characters of reasoning and an EMPTY `content`.
_THINKING_RESERVE_TOKENS = 2048

# The Ollama lane already adds the reserve itself in `_thinking_aware_output_budget`. Adding it here
# as well is the reserve twice, which takes from the prompt what the answer never needed.
_SELF_RESERVING_RUNTIME_FAMILIES = frozenset({"ollama"})

_CAP_NONE = "none"
_CAP_INTENT_CEILING = "intent_ceiling"
_CAP_PROVIDER_MAX_OUTPUT = "capability_max_output_tokens"
_CAP_CONTEXT_WINDOW_SHARE = "context_window_share"

_SOURCE_INTENT_BASE = "intent_base"
_SOURCE_INTENT_FLOOR = "intent_floor"


@dataclass(frozen=True)
class OutputBudgetIntent:
    """What the prompt layer wants from the ANSWER, before any lane is known.

    ``base_tokens`` is the number the generation profile already computes today. ``floor`` is the
    length below which the answer shape stops being usable; ``ceiling`` is the length above which
    more room buys nothing (an exact-string turn is the clear case -- it is pinned to one line by a
    stop sequence, so handing it 3000 tokens only invites a reasoning model to ramble). A ``ceiling``
    of 0 means the prompt layer states no upper bound and the lane decides.
    """

    output_mode: str = "plain_text"
    base_tokens: int = 0
    floor: int = 0
    ceiling: int = 0
    reason: str = ""


@dataclass(frozen=True)
class LaneCapability:
    """What the lane that will serve the turn can actually do.

    Every field already exists upstream and is currently discarded: ``context_window`` is
    ``OpenRouterModel.context_length`` (core/openrouter_catalog.py) and
    ``CloudModelMetadata.context_window``; ``max_output_tokens`` is
    ``CloudModelMetadata.max_output_tokens``, taken from ``top_provider.max_completion_tokens``;
    ``cost_class`` is ``provider_cost_class`` / ``reported_cost_class`` from
    core/model_selection_policy.py. 0 means UNKNOWN for both token counts -- never "zero allowed".
    """

    context_window: int = 0
    max_output_tokens: int = 0
    cost_class: str = REMOTE_UNKNOWN
    thinking_capable: bool = False
    runtime_family: str = ""


@dataclass(frozen=True)
class ResolvedOutputBudget:
    """The budget and the rule that produced it.

    ``capped_by`` names the rule that bound the result so a surprising budget is explainable from a
    log line rather than from a debugger, and ``intent_base`` keeps the number the prompt layer
    asked for so the lift is visible next to it.

    ``tool_call_floor_shortfall`` is how far a ``tool_intent`` budget lands BELOW the room a model
    was measured to need to reach the call -- 0 when the lane can serve a tool turn. It is not
    advice: a caller that ignores it spends the whole input, gets ``finish_reason: "length"`` with
    no ``tool_calls``, and reports "malformed provider response: required native tool call is
    missing", which names the model for a budget this policy set.
    """

    tokens: int
    source: str
    capped_by: str
    reserve_applied: int
    intent_base: int
    tool_call_floor_shortfall: int = 0


def _cost_class_target(cost_class: str, output_mode: str) -> int:
    """The budget this cost class wants for this mode, or 0 when the class lifts nothing."""
    if cost_class == FREE_CLOUD:
        if output_mode == "tool_intent":
            return _TOOL_INTENT_TARGET_TOKENS
        return _FREE_CLOUD_TARGET_TOKENS
    if cost_class == PAID_CLOUD:
        if output_mode == "tool_intent":
            return _TOOL_INTENT_TARGET_TOKENS
        return _PAID_CLOUD_TARGET_TOKENS
    # free_local pays nothing but owns the machine's RAM, and an unpriced remote lane must never be
    # inflated on a guess -- both keep exactly what the prompt layer asked for.
    return 0


def _context_window_share(context_window: int) -> int:
    window = max(0, int(context_window or 0))
    if window <= 0:
        return 0
    return window // _CONTEXT_WINDOW_OUTPUT_DIVISOR


def resolve_output_budget(
    intent: OutputBudgetIntent,
    capability: LaneCapability,
) -> ResolvedOutputBudget:
    """Resolve the answer budget for one turn on one lane.

    Order matters and is deliberate: lift by what the lane's tokens cost, then apply the two hard
    limits the lane physically has, then pay for reasoning on top -- because the reserve is not part
    of the answer the caller asked for, it is the price of getting to it.
    """
    base = max(0, int(intent.base_tokens or 0))
    if base <= 0:
        # A base of 0 is the caller declining to pin `max_tokens` at all. Inventing a ceiling where
        # the turn had none is this module's own failure mode in reverse.
        return ResolvedOutputBudget(
            tokens=0,
            source=_SOURCE_INTENT_BASE,
            capped_by=_CAP_NONE,
            reserve_applied=0,
            intent_base=0,
        )

    output_mode = str(intent.output_mode or "").strip().lower()
    cost_class = str(capability.cost_class or "").strip().lower()
    ceiling = max(0, int(intent.ceiling or 0))
    floor = max(0, int(intent.floor or 0))

    tokens = base
    source = _SOURCE_INTENT_BASE
    capped_by = _CAP_NONE

    target = _cost_class_target(cost_class, output_mode)
    if target > tokens:
        allowed = min(target, ceiling) if ceiling > 0 else target
        if ceiling > 0 and target > ceiling:
            # Recorded even when the ceiling equals the base and the number does not move: the lane
            # offered more and the answer shape refused it, which is the whole reason the resolved
            # budget looks small on a lane that could afford a large one.
            capped_by = _CAP_INTENT_CEILING
        if allowed > tokens:
            tokens = allowed
            source = f"{cost_class}_target"

    if floor > tokens:
        tokens = floor
        source = _SOURCE_INTENT_FLOOR
        capped_by = _CAP_NONE

    hard_caps: list[tuple[int, str]] = []
    provider_cap = max(0, int(capability.max_output_tokens or 0))
    if provider_cap > 0:
        hard_caps.append((provider_cap, _CAP_PROVIDER_MAX_OUTPUT))
    window_cap = _context_window_share(capability.context_window)
    if window_cap > 0:
        hard_caps.append((window_cap, _CAP_CONTEXT_WINDOW_SHARE))

    for limit, name in hard_caps:
        if tokens > limit:
            tokens = limit
            capped_by = name

    reserve = 0
    if capability.thinking_capable and str(capability.runtime_family or "").strip().lower() not in _SELF_RESERVING_RUNTIME_FAMILIES:
        reserve = _THINKING_RESERVE_TOKENS
        if hard_caps:
            # A provider-declared `max_completion_tokens` is a 400 from the provider, not a
            # preference, and taking more than the window's output share fails the turn closed in
            # core/prompt_budget.py. Reasoning gets whatever room is left under those, which is what
            # `_thinking_aware_output_budget` already does with `context_window // 2`.
            limit, name = min(hard_caps)
            room = max(0, limit - tokens)
            if room < reserve:
                reserve = room
                capped_by = name
        tokens += reserve

    # The hard caps above are physical: a provider's `max_completion_tokens` is a 400 when exceeded,
    # and taking more than the window share fails the turn closed in core/prompt_budget.py. Neither
    # can be argued up to reach the tool-call floor, so this does NOT raise the budget back. It
    # reports the gap, because the alternative is the caller discovering it as an empty `tool_calls`
    # and blaming the model. Measured shapes that land here: a 4k-window lane resolves to 2048 and a
    # provider declaring `max_completion_tokens: 1024` resolves to 1024, both under the 3000 that
    # produced the call on `nvidia/nemotron-3-nano-30b-a3b:free` where 512 produced none.
    shortfall = 0
    if output_mode == "tool_intent" and tokens < _TOOL_INTENT_TARGET_TOKENS:
        shortfall = _TOOL_INTENT_TARGET_TOKENS - tokens

    return ResolvedOutputBudget(
        tokens=tokens,
        source=source,
        capped_by=capped_by,
        reserve_applied=reserve,
        intent_base=base,
        tool_call_floor_shortfall=shortfall,
    )


#: Models OBSERVED to reason, from the provider's own terminal facts (a length-finish with
#: reasoning tokens present, or the endpoint's typed "reasoning is mandatory" refusal). A lane
#: whose feed publishes no per-model capability -- the UsePod marketplace lists pricing only --
#: still hands the runtime this evidence on the first call that exhausts; recording it lets
#: every later sizing and retry decision for THAT model read a fact the provider itself
#: reported, instead of a name guess. Process-local and never persisted: it is an observation
#: about a serving lane, not a configuration change.
_OBSERVED_REASONING: set[tuple[str, str]] = set()


def note_observed_reasoning(provider_id: str, model_id: str) -> None:
    """Record that this exact (provider, model) was observed to emit reasoning before its answer."""
    key = (str(provider_id or "").strip().lower(), str(model_id or "").strip().lower())
    if key[0] and key[1]:
        _OBSERVED_REASONING.add(key)


def observed_reasoning(manifest: Any) -> bool:
    """Whether this exact manifest's lane/model was observed to reason (terminal facts)."""
    key = (
        str(getattr(manifest, "provider_id", "") or "").strip().lower(),
        str(getattr(manifest, "model_name", "") or "").strip().lower(),
    )
    return key in _OBSERVED_REASONING


def reset_observed_reasoning() -> None:
    """Drop the observation set (test isolation)."""
    _OBSERVED_REASONING.clear()


def manifest_declares_reasoning(manifest: Any) -> bool:
    """Whether the manifest's own declarations say this model emits reasoning before its answer.

    Three sources, most to least authoritative, and none a model-name guess about a family a
    marker list happens to know: (1) the manifest's ``supported_parameters`` declaring
    ``reasoning``/``include_reasoning`` — stamped from the provider catalog at registration;
    (2) for the OpenRouter byok lane, the cached catalog row's own ``supported_parameters``
    (OpenRouter publishes the ``reasoning`` parameter there), read cache-only so a turn never
    blocks on a refresh; (3) the legacy name markers, kept for local tags no catalog describes.
    An explicit no-think tag still opts out. Source (4): the provider's own TERMINAL FACTS --
    a length-finish with reasoning present, or the endpoint's typed reasoning-mandate refusal --
    recorded by the invoke loop the moment it observes one. This is the source for lanes whose
    feeds publish no capability at all (the UsePod marketplace lists pricing only), and it is
    how the FIRST exhausted call teaches the SECOND sizing without anyone inventing a capability.
    """
    name = str(getattr(manifest, "model_name", "") or "").strip().lower()
    if "nothink" in name or "no-think" in name:
        return False
    if observed_reasoning(manifest):
        return True
    declared = {
        str(item).strip().lower()
        for item in (dict(getattr(manifest, "metadata", None) or {}).get("supported_parameters") or ())
    }
    if {"reasoning", "include_reasoning"} & declared:
        return True
    if str(getattr(manifest, "provider_name", "") or "").strip().lower() == "openrouter-byok":
        try:
            from core.openrouter_catalog import cached_catalog_row

            row = cached_catalog_row(name)
        except Exception:
            row = None
        if row is not None:
            catalog_parameters = {str(item).strip().lower() for item in row.supported_parameters}
            return bool({"reasoning", "include_reasoning"} & catalog_parameters)
    from core.agent_runtime.builder.controller import THINKING_MODEL_MARKERS

    return any(marker in name for marker in THINKING_MODEL_MARKERS)


def manifest_lane_capability(manifest: Any) -> LaneCapability:
    """The serving lane a manifest describes, as one ``LaneCapability``.

    The broker builds this from its own ``CloudModelMetadata`` at its send point; an explicit
    owner-pinned byok model dispatched straight through the adapter has only the manifest, so
    this reads the same facts off it: the context window and completion cap stamped at
    registration, the lane's cost class, and whether the model reasons — from the same
    ``manifest_declares_reasoning`` the adapter consults, so the two owners cannot drift.
    """
    metadata = dict(getattr(manifest, "metadata", None) or {})
    try:
        context_window = max(0, int(metadata.get("context_window") or 0))
    except (TypeError, ValueError):
        context_window = 0
    try:
        max_output = max(0, int(metadata.get("max_output_tokens") or 0))
    except (TypeError, ValueError):
        max_output = 0
    cost_class = REMOTE_UNKNOWN
    try:
        from core.model_selection_policy import is_verified_free_cloud_manifest, provider_cost_class

        if is_verified_free_cloud_manifest(manifest):
            cost_class = FREE_CLOUD
        else:
            cost_class = str(provider_cost_class(manifest) or "").strip().lower()
    except Exception:
        cost_class = REMOTE_UNKNOWN
    return LaneCapability(
        context_window=context_window,
        max_output_tokens=max_output,
        cost_class=cost_class,
        thinking_capable=manifest_declares_reasoning(manifest),
        runtime_family=str(metadata.get("runtime_family") or "").strip().lower(),
    )


def lane_resolved_output_tokens(manifest: Any, *, base_tokens: int, output_mode: str = "plain_text") -> int:
    """The output ceiling one turn on one manifest-backed lane should carry on the wire.

    The ONE resolution both sides of the money seam use: the adapter sizes the request's
    ``max_tokens`` from it, and the paid reservation sizes the completion tokens it holds from
    it, so the funds reserved before dispatch always cover the ceiling actually sent.
    ``base_tokens`` is the prompt layer's number (what the answer was sized to before any lane
    was known); a base of 0 means the caller declined to pin a ceiling and stays 0.
    """
    asked = max(0, int(base_tokens or 0))
    if asked <= 0:
        return 0
    resolved = resolve_output_budget(
        OutputBudgetIntent(
            output_mode=str(output_mode or "plain_text").strip().lower(),
            base_tokens=asked,
            floor=asked,
            ceiling=0,
            reason="manifest_lane_resolution",
        ),
        manifest_lane_capability(manifest),
    )
    return int(resolved.tokens)


__all__ = [
    "FREE_CLOUD",
    "note_observed_reasoning",
    "observed_reasoning",
    "reset_observed_reasoning",
    "FREE_LOCAL",
    "PAID_CLOUD",
    "REMOTE_UNKNOWN",
    "LaneCapability",
    "OutputBudgetIntent",
    "ResolvedOutputBudget",
    "lane_resolved_output_tokens",
    "manifest_declares_reasoning",
    "manifest_lane_capability",
    "resolve_output_budget",
]
