"""One place a tool becomes real, whether it ships with VOOL or a user installed it.

Today a tool's metadata is authored in several places that each derive what they need by matching
on the intent string, and a third-party tool cannot participate in any of them. This module is the
seam that changes that: a contract registered here is the same kind of object whether it came from
`runtime_tool_contracts()` or from a plugin manifest, so everything downstream — the model-facing
catalog, permissions, receipts, the answer binder — reads one record.

Two rules it enforces, both loudly:

**No duplicate intent.** A duplicate is not a merge conflict to be resolved by whoever ran last.
Measured 2026-07-28, `web.fetch` was declared by two sources with different argument names; the
native-schema builder unioned them, a strict schema then required both spellings, and the executor
read one. Nobody noticed because nothing refused the collision.

**No namespace theft.** A plugin's intents must live under its own name, so an installed pack can
never shadow `machine.*` or `workspace.*` and quietly receive calls meant for a built-in tool.

Registration is additive and process-local. `reset()` exists for tests and for a reload after a
plugin is installed or removed.

**The registry is the declaration authority; every projection follows its epoch.** Each mutation
(register, unregister, reset) advances `registry_epoch()`. The capability graph indexes the
registry and remembers the epoch it indexed, so a contract registered after boot — a plugin the
API server loaded, an MCP server whose listing changed — seats in the next discovery without any
caller having to know a refresh exists. There is deliberately no second registry and no side
channel into the graph.
"""
from __future__ import annotations

import threading
from collections.abc import Iterable

from core.runtime_tool_contracts import RuntimeToolContract, runtime_tool_contracts

# Prefixes owned by the runtime. A registered tool whose source is not "builtin" may not claim one.
_RESERVED_NAMESPACES = frozenset(
    {
        "browser",
        "hive",
        "machine",
        "marketplace",
        "operator",
        "pay",
        "respond",
        "sandbox",
        "sell",
        "set",
        "wallet",
        "web",
        "web0",
        "workspace",
    }
)

_lock = threading.RLock()
_extra: dict[str, RuntimeToolContract] = {}
# Monotonic per process. Bumped under `_lock` by every mutation; read by projections (the
# capability graph) to decide whether their index is current.
_epoch = 0


def _bump_locked() -> None:
    global _epoch
    _epoch += 1


def registry_epoch() -> int:
    """How many times the registered set has changed this process. Projections key on it."""

    with _lock:
        return _epoch


class ToolRegistrationError(ValueError):
    """A tool could not be registered. Always raised at registration, never at call time."""


def _namespace_of(intent: str) -> str:
    return str(intent or "").split(".", 1)[0].strip().lower()


def validate_contract(contract: RuntimeToolContract, *, known: Iterable[str] = ()) -> None:
    """Reject a contract that cannot be safely offered to a model.

    Separated from `register` so a plugin loader can check a manifest and report every problem to
    its author before anything is installed.
    """

    intent = str(getattr(contract, "intent", "") or "").strip()
    if not intent:
        raise ToolRegistrationError("a tool contract must declare an intent")
    if "." not in intent:
        raise ToolRegistrationError(
            f"{intent!r}: an intent must be namespaced as '<owner>.<tool>' so two packs cannot collide"
        )
    namespace = _namespace_of(intent)
    source = str(getattr(contract, "source", "builtin") or "builtin")
    if source != "builtin" and namespace in _RESERVED_NAMESPACES:
        raise ToolRegistrationError(
            f"{intent!r}: {namespace!r} is a runtime namespace; a {source} tool must use its own "
            "plugin name as the prefix"
        )
    if intent in set(known):
        raise ToolRegistrationError(
            f"{intent!r}: is already registered. Two declarations of one intent are merged by the "
            "native-schema builder into a single function with the union of their arguments, so a "
            "collision silently changes what the model is asked to send."
        )
    # Blackbox coverage: a REGISTERED tool (plugin, MCP) capable of LOCAL mutation must declare
    # its mutation capability -- scope, reversibility, snapshot strategy, receipt lifecycle,
    # rollback support, recorder -- or it does not register at all. An undeclared mutation path
    # is not an installed tool; it is a hole in the flight recorder.
    _validate_mutation_declaration(contract)


def _validate_mutation_declaration(contract: RuntimeToolContract) -> None:
    from core.blackbox.coverage.capability import LOCAL_MUTATION_CLASSES, MutationCapability

    intent = str(getattr(contract, "intent", "") or "")
    source = str(getattr(contract, "source", "builtin") or "builtin")
    side_effect_class = str(getattr(contract, "side_effect_class", "") or "")
    declared = getattr(contract, "mutation", None)
    if source == "builtin" or side_effect_class not in LOCAL_MUTATION_CLASSES:
        return
    if not isinstance(declared, dict) or not declared:
        raise ToolRegistrationError(
            f"{intent!r}: its side_effect_class {side_effect_class!r} can mutate local state, and a "
            "mutation-capable tool must declare its Blackbox coverage (`mutation=`: scope, "
            "effect_class, snapshot_strategy, receipt_lifecycle, rollback_support, recorder). "
            "A tool with no recorder declaration does not register."
        )
    capability = MutationCapability.from_dict(declared)
    if capability is None or capability.tool != intent:
        raise ToolRegistrationError(
            f"{intent!r}: its mutation declaration is malformed or names another tool "
            f"({declared.get('tool') if isinstance(declared, dict) else declared!r})."
        )
    problems = capability.problems()
    if problems:
        raise ToolRegistrationError(f"{intent!r}: {('; '.join(problems)).capitalize()}")


def _seat_coverage_capability(contract: RuntimeToolContract) -> None:
    """Seat the contract's mutation declaration in the coverage registry beside it."""
    from core.blackbox.coverage.capability import MutationCapability
    from core.blackbox.coverage import registry as coverage_registry

    declared = getattr(contract, "mutation", None)
    capability = MutationCapability.from_dict(declared) if isinstance(declared, dict) else None
    if capability is not None and not coverage_registry.capability_for(str(contract.intent)):
        coverage_registry.register_capability(capability)


def register(contract: RuntimeToolContract) -> RuntimeToolContract:
    """Add a tool. Raises rather than overwriting an existing intent."""

    with _lock:
        validate_contract(contract, known=_all_intents_locked())
        _extra[str(contract.intent)] = contract
        _seat_coverage_capability(contract)
        _bump_locked()
        return contract


def register_all(contracts: Iterable[RuntimeToolContract]) -> tuple[RuntimeToolContract, ...]:
    """Register several tools atomically: if any is rejected, none are added.

    A half-installed plugin is worse than one that failed to install, because the model is offered
    part of a pack whose other half it will be told does not exist.
    """

    items = list(contracts)
    with _lock:
        known = set(_all_intents_locked())
        for item in items:
            validate_contract(item, known=known)
            known.add(str(item.intent))
        for item in items:
            _extra[str(item.intent)] = item
            _seat_coverage_capability(item)
        if items:
            _bump_locked()
    return tuple(items)


def unregister(intent: str) -> bool:
    """Remove a previously registered non-builtin tool. Builtins cannot be removed."""

    with _lock:
        removed = _extra.pop(str(intent), None) is not None
        if removed:
            try:
                from core.blackbox.coverage import registry as coverage_registry

                coverage_registry.unregister_capability(str(intent))
            except Exception:
                pass
            _bump_locked()
        return removed


def reset() -> None:
    """Drop every registered non-builtin tool. For tests and for a post-install reload."""

    with _lock:
        _extra.clear()
        try:
            from core.blackbox.coverage import registry as coverage_registry

            coverage_registry.reset_registered()
        except Exception:
            pass
        _bump_locked()


def _all_intents_locked() -> set[str]:
    return {str(item.intent) for item in runtime_tool_contracts()} | set(_extra)


def registered_tools() -> tuple[RuntimeToolContract, ...]:
    """Every tool the runtime knows about: builtins first, then registered ones."""

    with _lock:
        extra = tuple(_extra.values())
    return tuple(runtime_tool_contracts()) + extra


def registry_map() -> dict[str, RuntimeToolContract]:
    return {str(item.intent): item for item in registered_tools()}


def contracts_from_source(prefix: str) -> tuple[RuntimeToolContract, ...]:
    """Registered (non-builtin) contracts whose `source` starts with `prefix` (e.g. "mcp:")."""

    clean = str(prefix or "")
    with _lock:
        return tuple(
            item for item in _extra.values() if str(getattr(item, "source", "") or "").startswith(clean)
        )


def tool_for_intent(intent: str) -> RuntimeToolContract | None:
    return registry_map().get(str(intent or "").strip())


def claim_for_intent(intent: str):
    """The binder's entry point: how to check an answer against this tool's own output."""

    contract = tool_for_intent(intent)
    return contract.claim if contract is not None else None


__all__ = [
    "ToolRegistrationError",
    "claim_for_intent",
    "contracts_from_source",
    "register",
    "register_all",
    "registered_tools",
    "registry_epoch",
    "registry_map",
    "reset",
    "tool_for_intent",
    "unregister",
    "validate_contract",
]
