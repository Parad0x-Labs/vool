"""One alias contract, read by the permission gate and by the handler.

The binding used to live only inside the runtime dispatcher, below the permission gate. The gate
therefore classified a DIFFERENT call from the one that ran: `core/mode_permission_policy.py` reads
`arguments["path"]` to decide whether a write lands on an existing file, so a model that named the
argument `file_path` -- the spelling most models emit -- left the gate seeing no path at all.

Measured on `main` before this moved, in `auto` mode against an existing file:

    path=secrets.txt       -> ['overwrite_existing_files'] -> require_approval
    file_path=secrets.txt  -> ['create_files']             -> allow
    handler then rebinds to {'path': 'secrets.txt'} and writes the same real file.

Permission and execution have to decide over the same arguments, so the binding belongs where both
can reach it rather than under one of them.

Note what this deliberately does NOT do: it does not strip `_`-prefixed keys. Those carry
server-owned grants such as `_trusted_local_only`, which the runtime stamps and the model cannot
forge from the wire. Dropping them here would revoke a grant the server had already made.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

ARGUMENT_ALIASES: dict[str, str] = {
    "directory": "path",
    "dir": "path",
    "folder": "path",
    "folder_path": "path",
    "dir_path": "path",
    "directory_path": "path",
    "file": "path",
    "filename": "path",
    "file_path": "path",
    "filepath": "path",
    "cmd": "command",
    "command_line": "command",
    "shell_command": "command",
    "q": "query",
    "search": "query",
    "search_query": "query",
    "search_term": "query",
    "term": "query",
    "text": "content",
    "contents": "content",
    "body": "content",
    "uri": "url",
    "link": "url",
    "max_results": "limit",
    "count": "limit",
}


def bind_known_argument_aliases(
    arguments: Mapping[str, Any] | None,
    *,
    input_schema: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Rename a model's near-miss argument names onto the fields this contract declares."""

    schema = dict(input_schema or {})
    bound = dict(arguments or {})
    if not bound or not schema:
        return bound
    for alias, canonical in ARGUMENT_ALIASES.items():
        if alias not in bound:
            continue
        if alias in schema:
            continue  # this contract means something specific by that name -- leave it alone
        if canonical not in schema:
            continue  # the contract has no such field; the unknown-argument refusal still applies
        if str(bound.get(canonical) or "").strip():
            bound.pop(alias, None)  # an explicit value always wins over an alias
            continue
        bound[canonical] = bound.pop(alias)
    return bound


def input_schema_for_intent(intent: str) -> dict[str, Any]:
    """This intent's declared input schema, or ``{}`` when no registry knows it.

    Imports are deferred and each is guarded: the permission policy is imported early and by
    callers that have no tool registry loaded, and a permission check must never fail because a
    registry could not be reached. An empty schema binds no aliases, which leaves the caller
    exactly where it was before this lookup existed.
    """

    clean = str(intent or "").strip()
    if not clean:
        return {}
    try:
        from core.tool_registry import tool_for_intent

        contract = tool_for_intent(clean)
        if contract is not None:
            return dict(getattr(contract, "input_schema", None) or {})
    except Exception:
        pass
    try:
        from core.runtime_tool_contracts import runtime_tool_contract_map

        contract = runtime_tool_contract_map().get(clean)
        if contract is not None:
            return dict(getattr(contract, "input_schema", None) or {})
    except Exception:
        pass
    return {}


def side_effect_class_for_intent(intent: str) -> str:
    """This intent's declared side-effect class, or `""` when no registry knows it.

    Same deferred, guarded lookup as `input_schema_for_intent`, and for the same reason: a
    permission decision must never fail because a registry could not be reached. An unknown intent
    returns `""`, which leaves every caller exactly where it was before this existed.
    """

    clean = str(intent or "").strip()
    if not clean:
        return ""
    for module, getter in (
        ("core.tool_registry", "tool_for_intent"),
        ("core.runtime_tool_contracts", "runtime_tool_contract_map"),
    ):
        try:
            loaded = __import__(module, fromlist=[getter])
            resolver = getattr(loaded, getter)
            contract = resolver(clean) if getter == "tool_for_intent" else resolver().get(clean)
        except Exception:
            continue
        if contract is not None:
            return str(getattr(contract, "side_effect_class", "") or "")
    return ""


__all__ = [
    "ARGUMENT_ALIASES",
    "bind_known_argument_aliases",
    "input_schema_for_intent",
    "side_effect_class_for_intent",
]
