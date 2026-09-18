"""The semantic-routing authority ladder — how much the model-first resolver is allowed to decide.

There is no flag-day switch from "heuristics decide" to "the model decides". Authority climbs one
evidence-gated rung at a time, and every rung above the one that is actually built clamps DOWN to
the highest implemented rung rather than being taken on trust. That clamp is the whole safety of the
ladder: a config that asks for AUTHORITATIVE in a build where only SHADOW is implemented gets SHADOW,
never a resolver silently steering real turns.

    OFF                          the resolver does not run; the runtime behaves exactly as today
    SHADOW                       runs observe-only beside the heuristic; records; never dispatches
    DUAL_RUN                     runs and is diffed live; the heuristic is still authoritative
    AUTHORITATIVE_WITH_FALLBACK  resolver authoritative; heuristic serves on abstain/error
    AUTHORITATIVE                resolver authoritative; heuristics proven-superseded are retired

Resolved from ``VOOL_SEMANTIC_RESOLVER``. Default is OFF: a freshly-landed resolver adds no per-turn
cost and no behaviour change until an operator opts in, and shadow evidence is gathered offline by
the differential harness rather than by forcing a model call on every production turn. Two hard
floors, both fail-closed: a build with no available model backend cannot leave OFF, and local models
being disabled cannot leave OFF (the resolver would have nothing to call).
"""
from __future__ import annotations

import os
from enum import Enum

#: The highest rung whose behaviour is actually implemented. Higher requested modes clamp to this.
#: Raised deliberately, in the same change that lands the rung's behaviour and its evidence — never
#: ahead of it. Rung 1 (this milestone) implements SHADOW.
HIGHEST_IMPLEMENTED_MODE_NAME = "shadow"

_FLAG_ENV = "VOOL_SEMANTIC_RESOLVER"


class SemanticAuthorityMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"
    DUAL_RUN = "dual_run"
    AUTHORITATIVE_WITH_FALLBACK = "authoritative_with_fallback"
    AUTHORITATIVE = "authoritative"


#: Rung order, low to high. Index in this tuple is the rung height.
_LADDER: tuple[SemanticAuthorityMode, ...] = (
    SemanticAuthorityMode.OFF,
    SemanticAuthorityMode.SHADOW,
    SemanticAuthorityMode.DUAL_RUN,
    SemanticAuthorityMode.AUTHORITATIVE_WITH_FALLBACK,
    SemanticAuthorityMode.AUTHORITATIVE,
)

#: Accepted spellings for the env value. Unknown values fall to OFF (fail-closed), never to a guess.
_ALIASES: dict[str, SemanticAuthorityMode] = {
    "": SemanticAuthorityMode.OFF,
    "0": SemanticAuthorityMode.OFF,
    "off": SemanticAuthorityMode.OFF,
    "false": SemanticAuthorityMode.OFF,
    "no": SemanticAuthorityMode.OFF,
    "shadow": SemanticAuthorityMode.SHADOW,
    "1": SemanticAuthorityMode.SHADOW,
    "on": SemanticAuthorityMode.SHADOW,
    "dual": SemanticAuthorityMode.DUAL_RUN,
    "dual_run": SemanticAuthorityMode.DUAL_RUN,
    "authoritative_with_fallback": SemanticAuthorityMode.AUTHORITATIVE_WITH_FALLBACK,
    "fallback": SemanticAuthorityMode.AUTHORITATIVE_WITH_FALLBACK,
    "authoritative": SemanticAuthorityMode.AUTHORITATIVE,
}


def _rung(mode: SemanticAuthorityMode) -> int:
    return _LADDER.index(mode)


def highest_implemented_mode() -> SemanticAuthorityMode:
    return _ALIASES.get(HIGHEST_IMPLEMENTED_MODE_NAME, SemanticAuthorityMode.SHADOW)


def clamp_to_implemented(mode: SemanticAuthorityMode) -> SemanticAuthorityMode:
    """Never return a rung higher than what is built. This is the fail-closed heart of the ladder."""
    ceiling = highest_implemented_mode()
    return mode if _rung(mode) <= _rung(ceiling) else ceiling


def resolve_semantic_authority_mode(
    env: dict[str, str] | None = None,
    *,
    backend_available: bool = True,
    local_models_disabled: bool = False,
) -> SemanticAuthorityMode:
    """The effective mode for this process, after aliasing, clamping, and the two hard floors.

    A requested mode above the highest implemented rung is clamped down (fail-closed). With no
    backend, or with local models disabled, the resolver has nothing to call, so the mode floors to
    OFF regardless of what was asked — the resolver never becomes a no-op that pretends to run.
    """
    raw = str((env or os.environ).get(_FLAG_ENV) or "").strip().lower()
    requested = _ALIASES.get(raw, SemanticAuthorityMode.OFF)
    if not backend_available or local_models_disabled:
        return SemanticAuthorityMode.OFF
    return clamp_to_implemented(requested)


def mode_runs_resolver(mode: SemanticAuthorityMode) -> bool:
    """Whether the resolver executes at all in this mode (SHADOW and above)."""
    return _rung(mode) >= _rung(SemanticAuthorityMode.SHADOW)


def mode_is_authoritative(mode: SemanticAuthorityMode) -> bool:
    """Whether the resolver's reading may DECIDE the turn (AUTHORITATIVE_WITH_FALLBACK and above)."""
    return _rung(mode) >= _rung(SemanticAuthorityMode.AUTHORITATIVE_WITH_FALLBACK)


__all__ = [
    "HIGHEST_IMPLEMENTED_MODE_NAME",
    "SemanticAuthorityMode",
    "clamp_to_implemented",
    "highest_implemented_mode",
    "mode_is_authoritative",
    "mode_runs_resolver",
    "resolve_semantic_authority_mode",
]
