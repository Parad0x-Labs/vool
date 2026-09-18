"""Deep-reasoning ("thinking") mode for the local chat model — OFF by default.

A thinking model (qwen3) reasons before it answers. That yields cleaner answers on hard problems
but is SLOW: on modest hardware it over-thinks a one-word message ("wat?", "a coffee pls?") for
tens of seconds and blows the turn timeout, so the user gets a non-answer for trivial chat. For a
local-first chat assistant the right default is thinking OFF (fast, direct replies); the user can
switch it ON when they want the model to reason harder.

This is read at model-call time (by the adapter) and at manifest-registration time, so toggling the
preference takes effect on the next turn without a restart. An env override wins for tests/CI.
"""
from __future__ import annotations

import os

_ENV = "VOOL_DEEP_REASONING"
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def deep_reasoning_enabled() -> bool:
    """True when deep reasoning (model `think`) should be on. Env `VOOL_DEEP_REASONING` wins;
    otherwise the saved user preference; default False."""
    raw = os.environ.get(_ENV)
    if raw is not None:
        val = raw.strip().lower()
        if val in _TRUE:
            return True
        if val in _FALSE:
            return False
    try:
        from core.user_preferences import load_preferences

        return bool(getattr(load_preferences(), "deep_reasoning", False))
    except Exception:
        return False


__all__ = ["deep_reasoning_enabled"]
