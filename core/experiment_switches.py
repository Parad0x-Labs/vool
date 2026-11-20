"""Switches for the cache-layout experiments, read inline from the environment.

Each is OFF unless its variable holds a truthy value (1, true, yes, on); anything else, a typo included,
leaves it off. They are deliberately not in ``core.runtime_flags``: that module's shipped inventory is
pinned by a test, and these are experiments that ship off. ``override`` is for tests and for a runtime
that wants one turn answered the other way; it restores the previous value on exit.
"""
from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

_ENV = {
    "prompt_layout_context_last": "VOOL_PROMPT_LAYOUT_CONTEXT_LAST",
    "prompt_layout_stable_grounding_first": "VOOL_PROMPT_LAYOUT_STABLE_GROUNDING_FIRST",
    "prompt_cache_lead_run_marker": "VOOL_PROMPT_CACHE_LEAD_RUN_MARKER",
    "router_session_key": "VOOL_ROUTER_SESSION_KEY",
}
_TRUTHY = {"1", "true", "yes", "on"}
_overrides: dict[str, bool] = {}
_lock = threading.RLock()


def names() -> tuple[str, ...]:
    return tuple(_ENV)


def switch_enabled(name: str) -> bool:
    if name not in _ENV:
        raise KeyError(f"unknown switch {name!r}; known: {', '.join(sorted(_ENV))}")
    with _lock:
        if name in _overrides:
            return _overrides[name]
    return str(os.environ.get(_ENV[name]) or "").strip().lower() in _TRUTHY


@contextmanager
def override(name: str, value: bool) -> Iterator[None]:
    if name not in _ENV:
        raise KeyError(name)
    with _lock:
        had = name in _overrides
        prev = _overrides.get(name)
        _overrides[name] = bool(value)
    try:
        yield
    finally:
        with _lock:
            if had:
                _overrides[name] = bool(prev)
            else:
                _overrides.pop(name, None)


__all__ = ["names", "override", "switch_enabled"]
