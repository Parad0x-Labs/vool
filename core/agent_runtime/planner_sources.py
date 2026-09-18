"""Bind planner references to the existing turn's structural clauses."""
from __future__ import annotations

import copy
import json

from core.turn_ir import parse_turn_ir


def source_contract(text: str, schema: dict) -> tuple[dict, str]:
    clauses = parse_turn_ir(text).clauses
    if len(clauses) < 2 or len(clauses) > int(schema["maxItems"]):
        return schema, ""
    contract = copy.deepcopy(schema)
    contract["items"]["properties"]["source_clause_ids"] = {
        "type": "array", "minItems": 1, "maxItems": len(clauses), "uniqueItems": True,
        "items": {"type": "string", "enum": [c.clause_id for c in clauses]},
    }
    contract["items"]["properties"]["request"] = {"type": "string", "enum": [""]}
    contract["items"]["properties"]["depends_on"] = {
        "type": "array", "uniqueItems": True,
        "items": {"type": "string", "enum": [c.clause_id for c in clauses]},
    }
    contract["items"]["required"].append("source_clause_ids")
    supplement = (
        "\nSource-reference contract for this turn (replaces request-text instructions above):\n"
        "The runtime has already located the source clauses below. Name their IDs in "
        "source_clause_ids instead of rewriting them; set request to an empty string for all "
        "entries. The runtime substitutes their original text. Every listed ID must occur once "
        "across the plan. For depends_on use SOURCE CLAUSE IDs, never array indices or numbers. "
        "Name the clauses whose computed answers this entry needs, not its own clauses. "
        "Entries may be in any order; the runtime orders them by these dependencies. "
        "You still choose operation; references do not authorize "
        "an operation or prove it answered anything. Context and assumptions remain available "
        "from the original message. Group the source clauses of one connected calculation into "
        "one quantitative_reasoning entry. Style alone stays with the work it modifies; explicit "
        "presentation of computed results belongs to result_presentation, depending on the "
        "calculation's source IDs. Do not invent intermediate request text. "
        'Each entry has exactly request:"", source_clause_ids:[IDs], operation, depends_on.\n'
        + json.dumps([{"id": c.clause_id, "text": c.request_text} for c in clauses], ensure_ascii=True)
    )
    return contract, supplement


def bind_sources(raw: str, text: str, *, required: bool = False) -> str:
    """Expand a complete reference plan; legacy text proposals keep their old guards."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return ""
    if not isinstance(payload, list) or not payload:
        return ""
    if not any(isinstance(item, dict) and "source_clause_ids" in item for item in payload):
        return "" if required else raw
    sources = {c.clause_id: c.request_text for c in parse_turn_ir(text).clauses}
    seen: set[str] = set()
    owners: dict[str, int] = {}
    result = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            return ""
        refs = item.get("source_clause_ids")
        if not isinstance(refs, list) or not refs or str(item.get("request") or "").strip():
            return ""
        texts = []
        for ref in refs:
            if not isinstance(ref, str) or ref not in sources or ref in seen:
                return ""
            seen.add(ref)
            owners[ref] = index
            texts.append(sources[ref])
        result.append({"request": "\n".join(texts), "operation": item.get("operation"),
                       "depends_on": item.get("depends_on", [])})
    if seen != set(sources):
        return ""
    # Compile source identity to execution position only after every clause has an owner.
    # Reordering or grouping source clauses must not change which answer an edge names.
    edges: list[set[int]] = []
    for index, item in enumerate(payload):
        dependencies = item.get("depends_on", [])
        if not isinstance(dependencies, list):
            return ""
        resolved: set[int] = set()
        for ref in dependencies:
            if isinstance(ref, str) and ref in owners:
                owner = owners[ref]
            elif not required and type(ref) is int and 0 <= ref < index:
                owner = ref  # Historical proposals retain their existing index contract.
            else:
                return ""
            if owner == index:
                return ""
            resolved.add(owner)
        edges.append(resolved)
    order: list[int] = []
    pending = set(range(len(result)))
    while pending:
        ready = sorted(index for index in pending if not (edges[index] & pending))
        if not ready:
            return ""
        order.append(ready[0])
        pending.remove(ready[0])
    positions = {old: new for new, old in enumerate(order)}
    result = [{**result[index], "depends_on": sorted(positions[dep] for dep in edges[index])}
              for index in order]
    return json.dumps(result, ensure_ascii=True)
