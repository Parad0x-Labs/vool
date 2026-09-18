"""The canonical tool navigator: the real catalog, by family, availability and count.

``operator.list_tools`` used to report the eight operator-lane tools and nothing
else — while the runtime carried 72+ catalog intents across 17+ families (census
a6c8e3c4). A model reading that report believed the operator lane WAS the tool
surface. This module is the one place that renders the true map:

- one bounded row per capability family: available count, unavailable count, reasons;
- disabled tools stay visible as UNAVAILABLE METADATA with their reason (they never
  seat into an offer and never execute);
- MCP server families appear when configured; plugin families when the plugin flag
  is explicitly enabled;
- every row is actionable through ``capability.expand_family``.

Rendering is deterministic (sorted families, sorted intents) so the report is
testable and stable across turns.
"""
from __future__ import annotations

from typing import Any

# Keeps the rendered report compact: examples are a hint, not the whole family.
_MAX_EXAMPLE_INTENTS = 3
_MAX_UNAVAILABLE_REASONS = 3


def _family_for_intent(intent: str) -> str:
    from core.capability_graph import (
        _CANONICAL_FAMILIES,
        _intent_to_capability,
        ensure_registry_bootstrap,
    )

    ensure_registry_bootstrap()
    cap = _intent_to_capability.get(intent)
    if cap is not None:
        family = str(cap).split(".", 1)[0]
        if family in _CANONICAL_FAMILIES:
            return family
    prefix = intent.split(".", 1)[0]
    return prefix if prefix in _CANONICAL_FAMILIES else "runtime"


def catalog_family_table() -> list[dict[str, Any]]:
    """The real catalog as one row per family, with availability truth.

    Availability is the capability graph's (implementations marked unavailable
    carry their reason); the live catalog supplies plugin/MCP rows when their
    lanes are on. Unavailable tools are METADATA ONLY — they are never seated
    into an offer and never execute.
    """
    from core.capability_graph import ImplementationId, _implementations, ensure_registry_bootstrap
    from core.runtime_tool_contracts import runtime_tool_contract_map
    from core.tool_intent_executor import runtime_tool_specs

    ensure_registry_bootstrap()

    # The union: live catalog rows first (what can be offered), then contracted
    # tools the catalog omits (policy-disabled — exactly the rows that must stay
    # visible as unavailable metadata).
    intents: dict[str, bool] = {}
    for spec in runtime_tool_specs():
        name = str(spec.get("intent") or "").strip()
        if name:
            intents[name] = True
    contract_map = runtime_tool_contract_map()
    for name in contract_map:
        intents.setdefault(name, False)

    rows: dict[str, dict[str, Any]] = {}
    for intent, offered in intents.items():
        family = _family_for_intent(intent)
        impl = _implementations.get(ImplementationId(intent))
        available = bool(impl.available) if impl is not None else offered
        reason = ""
        if not available:
            reason = str(
                getattr(contract_map.get(intent), "unsupported_reason", "")
                or (impl.availability_reason if impl is not None else "")
                or "not available on this runtime"
            ).strip()
        row = rows.setdefault(
            family,
            {
                "family": family,
                "available_count": 0,
                "unavailable_count": 0,
                "available_intents": [],
                "unavailable": {},
            },
        )
        if available:
            row["available_count"] += 1
            row["available_intents"].append(intent)
        else:
            row["unavailable_count"] += 1
            row["unavailable"][intent] = reason or "not available on this runtime"

    table: list[dict[str, Any]] = []
    for family in sorted(rows):
        row = rows[family]
        table.append(
            {
                "family": family,
                "available_count": row["available_count"],
                "unavailable_count": row["unavailable_count"],
                "example_intents": sorted(row["available_intents"])[:_MAX_EXAMPLE_INTENTS],
                "unavailable_examples": dict(
                    sorted(row["unavailable"].items())[:_MAX_UNAVAILABLE_REASONS]
                ),
            }
        )
    return table


def render_family_table(table: list[dict[str, Any]] | None = None) -> str:
    """Compact navigator text: one line per family plus the escalation instruction."""
    rows = table if table is not None else catalog_family_table()
    lines = ["Tool families on this runtime:"]
    for row in rows:
        family = str(row.get("family") or "").strip()
        available = int(row.get("available_count") or 0)
        unavailable = int(row.get("unavailable_count") or 0)
        examples = ", ".join(str(i) for i in row.get("example_intents") or [])
        line = f"- {family}: {available} available"
        if unavailable:
            line += f", {unavailable} unavailable"
        if examples:
            line += f" (e.g. {examples})"
        lines.append(line)
        for intent, reason in (row.get("unavailable_examples") or {}).items():
            lines.append(f"  - {intent}: unavailable ({reason})")
    lines.append(
        "Ask capability.expand_family with a family name to seat that family's tools "
        "for your next step."
    )
    return "\n".join(lines)


__all__ = [
    "catalog_family_table",
    "render_family_table",
]
