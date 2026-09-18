"""C13 context/privacy controls over the turn-context authority.

Inspect / edit / forget for the scoped turn-context pages real turns admit —
the operator-facing half of "context you can see, correct, and take back".

**This module is a TRANSPORT ADAPTER, not an authority.** Since C13 closed the
census gap, the owner of every one of these actions is the ``context.pages.*``
command family in the one Command Registry
(``core.command_registry.groups.context_pages_group``). What lives here is only
what is genuinely transport: query-string and JSON-body parsing, the legacy
status/body rendering, and the action→command routing table. Every operation
that actually runs traverses ``execute_command`` exactly once, so the permission
gate, the typed effects class and the effect-budget door apply identically
whether the caller arrived over HTTP, the CLI or chat.

Privacy laws this surface serves:

    OWNER-LOCAL ONLY — the whole surface is loopback-owner; a foreign channel
    gets a typed 403 and never learns whether an admission exists. The check
    below renders that 403; the AUTHORITY enforces the same law independently
    in ``_gate_owner_local``, so deleting this transport check does not open
    the surface.
    PRINCIPAL-BOUNDED READS — inspection is SQL-bounded to the caller's
    principal, so isolation is structural, not a filter applied after the fact.
    CONTENT IS OPT-IN — records carry provenance (origin, scope, status,
    hashes); bytes only ride along when the operator explicitly asks.
    WITHHELD/ERASED STOPS SERVING EVERYWHERE — the same authority the compile
    path consults is the one these actions mutate, so a withhold or erasure
    performed here is visible to the very next turn.
    HASHES ARE NOT CONTENT — page hashes identify; they never re-create.
"""

from __future__ import annotations

from typing import Any

from core.web.api.service import (
    RuntimeServices,
    apply_runtime_headers,
    is_loopback_host,
    json_response,
)

_KNOWN_ACTIONS = (
    "withhold",
    "erase",
    "supersede",
    "pin",
    "release_pin",
    "archive",
    "recall",
    "bump_generation",
)

_ACTION_FIELDS: dict[str, set[str]] = {
    "withhold": {"admission_id", "reason"},
    "erase": {"admission_id", "reason"},
    "supersede": {"admission_id", "content", "reason", "title"},
    "pin": {"admission_id", "reason"},
    "release_pin": {"admission_id", "pin_id"},
    "archive": {"session_id"},
    "recall": {"admission_id"},
    "bump_generation": {"session_id", "reason"},
}


def _query_value(query: dict[str, Any] | None, name: str) -> str:
    values = (query or {}).get(name) or [""]
    return str(values[0] if isinstance(values, list) else values or "").strip()


def _refuse(status: int, payload: dict[str, Any], runtime: RuntimeServices):
    return apply_runtime_headers(json_response(status, payload), runtime)


def handle_context_pages_get(
    *,
    query: dict[str, Any] | None,
    runtime: RuntimeServices,
    client_host: str,
):
    """Inspect scoped turn-context admissions (provenance; content opt-in).

    GENERATED_ADAPTER: the command registry owns this action
    (``context.pages.list``); this function parses the query string and renders
    the answer.
    """
    if not is_loopback_host(client_host):
        return _refuse(403, {"ok": False, "error": "owner_local_required"}, runtime)

    session_id = _query_value(query, "session")
    include_content = _query_value(query, "include_content") in {"1", "true", "yes"}
    try:
        limit = int(_query_value(query, "limit") or "200")
    except ValueError:
        return _refuse(400, {"ok": False, "error": "invalid limit"}, runtime)

    from core.command_registry.legacy import forward as _cr_forward

    status, payload = _cr_forward(
        "context.pages.list",
        {
            "session_id": session_id,
            "include_content": include_content,
            "limit": limit,
        },
        client_host=client_host,
    )
    return apply_runtime_headers(json_response(status, payload), runtime)


def _command_input(action: str, body: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Typed input for one action, or the legacy 400 payload that refuses it."""
    admission_id = str(body.get("admission_id") or "").strip()
    reason = str(body.get("reason") or "").strip()
    session_id = str(body.get("session_id") or "").strip()

    if action in {"withhold", "erase", "supersede", "pin", "recall"} and not admission_id:
        return None, {"ok": False, "error": f"{action} requires admission_id"}

    if action in {"withhold", "erase", "pin"}:
        return {"admission_id": admission_id, "reason": reason}, None
    if action == "recall":
        return {"admission_id": admission_id}, None
    if action == "supersede":
        content = str(body.get("content") or "")
        if not content.strip():
            return None, {"ok": False, "error": "supersede requires non-empty content"}
        return {
            "admission_id": admission_id,
            "content": content,
            "reason": reason,
            "title": str(body.get("title") or "").strip(),
        }, None
    if action == "release_pin":
        pin_id = str(body.get("pin_id") or "").strip()
        if not pin_id:
            return None, {"ok": False, "error": "release_pin requires pin_id"}
        return {"admission_id": admission_id, "pin_id": pin_id}, None
    if action == "archive":
        if not session_id:
            return None, {"ok": False, "error": "archive requires session_id"}
        return {"session_id": session_id}, None
    # bump_generation
    if not session_id:
        return None, {"ok": False, "error": "bump_generation requires session_id"}
    return {"session_id": session_id, "reason": reason}, None


def handle_context_pages_post(
    *,
    body: dict[str, Any],
    runtime: RuntimeServices,
    client_host: str,
):
    """One typed control surface: withhold / erase / supersede / pin / archive / recall / bump_generation.

    GENERATED_ADAPTER: each action is owned by its own registry command
    (``core.command_registry.groups.context_pages_group.ACTION_COMMANDS``);
    this function validates the transport shape and forwards.
    """
    if not is_loopback_host(client_host):
        return _refuse(403, {"ok": False, "error": "owner_local_required"}, runtime)

    action = str(body.get("action") or "").strip()
    if action not in _KNOWN_ACTIONS:
        return _refuse(
            400,
            {
                "ok": False,
                "error": "unknown action",
                "known_actions": list(_KNOWN_ACTIONS),
            },
            runtime,
        )
    unknown = set(body.keys()) - {"action", *_ACTION_FIELDS[action]}
    if unknown:
        return _refuse(400, {"ok": False, "error": f"unknown fields: {sorted(unknown)}"}, runtime)

    payload_in, refusal = _command_input(action, body)
    if refusal is not None:
        return _refuse(400, refusal, runtime)

    from core.command_registry.groups.context_pages_group import ACTION_COMMANDS
    from core.command_registry.legacy import forward as _cr_forward

    status, payload = _cr_forward(
        ACTION_COMMANDS[action], payload_in, client_host=client_host
    )
    return apply_runtime_headers(json_response(status, payload), runtime)
