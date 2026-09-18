"""The operation catalog, generated from the registry rather than maintained beside it.

A planner can only choose an operation it has been told exists. Today that list is built by
`core.conductor.registry.operation_catalog_text()` from the six operations `core.conductor.operations`
registers, so a capability that arrives any other way -- a built-in tool contract, a plugin manifest
loaded at runtime -- is invisible to it no matter how well registered it is. That gap is the concrete
form of "the runtime cannot generalize past the tools someone remembered to type out": the registry
knows about seventy-odd capabilities and the planner is offered six.

This module closes the gap as a **projection**, not as a registration.

**It deliberately does not register anything into the conductor.** Writing seventy operations into
`core.conductor.registry` at import would change `conductor_system_prompt()`, which would change what
the planner model is asked, which would change what it replies -- a behaviour change, and this phase
is instrumentation. So the projection is a library call that measurement and tests make, and Phase 1
is where the planner starts reading it.

**Registration is not authorization, and this is where that is easiest to get wrong.** Everything
here is description: a name, a line of prose, a declared side-effect class, whether the runtime says
it is currently supported. Nothing here consults a permission policy and nothing here can execute.
A capability appearing in this catalog means a model may *name* it; whether it may *run* is
`core.semantic.admission`, which asks `decide_tool_call` every time.
"""
from __future__ import annotations

from typing import Any

from core.conductor.registry import OperationSpec


def _registered_contracts() -> tuple[Any, ...]:
    """Every registered contract, built-in and plugin alike. Empty tuple when the registry is unreadable.

    `core.tool_registry.registered_tools()` rather than `runtime_tool_contract_map()`: the latter
    re-derives the built-in literal list and never sees a runtime registration, so measuring with it
    would report a plugin's vocabulary as absent while the plugin was installed and working.
    """
    try:
        from core.tool_registry import registered_tools

        return tuple(registered_tools())
    except Exception:
        return ()


def catalog_entries(*, include_unsupported: bool = True) -> tuple[dict[str, Any], ...]:
    """One descriptive row per registered capability, sorted by name.

    `include_unsupported` defaults to True and that is the correct default: a capability the runtime
    cannot currently serve still EXISTS, and a catalog that silently omitted it would make
    "unsupported" and "not registered" indistinguishable to anyone reading the list. The row carries
    `supported` and `unsupported_reason` so a caller can filter with the facts in hand.
    """
    rows: list[dict[str, Any]] = []
    for contract in _registered_contracts():
        supported = bool(getattr(contract, "supported", False))
        if not supported and not include_unsupported:
            continue
        rows.append(
            {
                "name": str(getattr(contract, "intent", "") or ""),
                "description": str(getattr(contract, "description", "") or ""),
                "source": str(getattr(contract, "source", "builtin") or "builtin"),
                "side_effect_class": str(getattr(contract, "side_effect_class", "") or ""),
                "approval_requirement": str(getattr(contract, "approval_requirement", "") or ""),
                "supported": supported,
                "unsupported_reason": str(getattr(contract, "unsupported_reason", "") or ""),
                # Stated on every row so nothing downstream has to remember it. A catalog row is a
                # description; the permission policy is the authority.
                "grants_execution": False,
            }
        )
    return tuple(sorted(rows, key=lambda row: row["name"]))


def catalog_names(*, include_unsupported: bool = True) -> frozenset[str]:
    return frozenset(row["name"] for row in catalog_entries(include_unsupported=include_unsupported))


def projected_operations(*, include_unsupported: bool = False) -> tuple[OperationSpec, ...]:
    """Registered contracts as `OperationSpec`s, via `OperationSpec.from_contract`.

    Defaults to supported-only here, unlike `catalog_entries`, because an `OperationSpec` is a thing
    a planner could be handed and run -- and offering a capability the runtime has already said it
    cannot serve invites a node that can only fail. Descriptions are for reading; specs are for
    doing, and the safe default differs between the two.
    """
    specs: list[OperationSpec] = []
    for contract in _registered_contracts():
        if not include_unsupported and not bool(getattr(contract, "supported", False)):
            continue
        try:
            specs.append(OperationSpec.from_contract(contract))
        except Exception:
            # A contract too malformed to project is skipped rather than crashing the catalog, and
            # it is still visible in `catalog_entries` -- so it cannot vanish from measurement.
            continue
    return tuple(sorted(specs, key=lambda spec: spec.name))


def catalog_text(*, include_unsupported: bool = False) -> str:
    """The catalog rendered as prompt lines, in the shape `operation_catalog_text()` already uses."""
    return "\n".join(
        f"  {row['name']} - {row['description']}"
        for row in catalog_entries(include_unsupported=include_unsupported)
        if row["name"]
    )


def catalog_report() -> dict[str, Any]:
    """Counts by provenance and support -- what the measurement quotes."""
    rows = catalog_entries()
    by_source: dict[str, int] = {}
    for row in rows:
        by_source[row["source"]] = by_source.get(row["source"], 0) + 1
    return {
        "schema": "operation_catalog_report_v1",
        "total": len(rows),
        "supported": sum(1 for row in rows if row["supported"]),
        "unsupported": sum(1 for row in rows if not row["supported"]),
        "by_source": dict(sorted(by_source.items())),
        "projected_specs": len(projected_operations()),
    }


__all__ = [
    "catalog_entries",
    "catalog_names",
    "catalog_report",
    "catalog_text",
    "projected_operations",
]
