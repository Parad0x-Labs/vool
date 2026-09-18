"""Opt-in "think harder" routing.

When VOOL_SLM_MUX_MODE is on, a turn is answered by self-consistency across several small models
(core/slm_mux.slm_mux_select) instead of a single sample. Off by default -> exactly one call, so
behaviour is identical to today. This is the *decision wrapper* only: the sampler (the real model
call) is injected by the caller, so nothing here talks to a model directly, and wiring this into the
live turn path is a separate, supervised step.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from core.slm_mux import MuxResult, slm_mux_select

_MODE_FLAG = "VOOL_SLM_MUX_MODE"
_MODELS_ENV = "VOOL_SLM_MUX_MODELS"
_COUNT_ENV = "VOOL_SLM_MUX_N"
_DEFAULT_MODELS = ("qwen2.5:7b", "qwen2.5:3b")
_TRUTHY = {"1", "true", "yes", "on"}


@dataclass
class ThinkResult:
    answer: str
    model: str
    used_mux: bool
    confidence: float = 1.0
    reason: str = ""
    mux: MuxResult | None = None


def think_harder_enabled() -> bool:
    """True only when the opt-in env flag is set. Off by default."""
    return str(os.environ.get(_MODE_FLAG, "")).strip().lower() in _TRUTHY


def mux_models(default: Sequence[str] | None = None) -> tuple[str, ...]:
    """Model list for the mux, from VOOL_SLM_MUX_MODELS (comma-separated) or a sane default."""
    raw = str(os.environ.get(_MODELS_ENV, "")).strip()
    if raw:
        models = tuple(m.strip() for m in raw.split(",") if m.strip())
        if models:
            return models
    return tuple(default or _DEFAULT_MODELS)


def mux_sample_count(default: int = 3) -> int:
    """How many distinct models the live mux votes across, from VOOL_SLM_MUX_N (>=2) or the default."""
    raw = str(os.environ.get(_COUNT_ENV, "")).strip()
    if raw.isdigit() and int(raw) >= 2:
        return int(raw)
    return max(2, int(default))


def think_harder_answer(
    prompt: str,
    sampler: Callable[[str, str], str],
    *,
    models: Sequence[str] | None = None,
    k: int = 3,
) -> ThinkResult:
    """Answer `prompt` via a caller-supplied `sampler(model, prompt) -> str`.

    Off (default): a single sampler call on the first model — behaviour-identical to a normal turn.
    On: route through slm_mux_select for a self-consistency pick across `models` (k samples each).
    """
    chosen = mux_models(models)
    primary = chosen[0] if chosen else ""
    if not think_harder_enabled():
        answer = str(sampler(primary, prompt) or "")
        return ThinkResult(answer=answer, model=primary, used_mux=False, reason="mux_disabled")
    result = slm_mux_select(prompt, chosen, sampler, k=k)
    return ThinkResult(
        answer=result.answer, model=result.model, used_mux=True,
        confidence=result.confidence, reason=result.reason, mux=result,
    )


__all__ = [
    "ThinkResult",
    "mux_models",
    "mux_sample_count",
    "think_harder_answer",
    "think_harder_enabled",
]
