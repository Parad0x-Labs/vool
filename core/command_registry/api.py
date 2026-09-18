"""API projection — registry-backed routes for the main API.

Seam (service.py adds exactly two dispatch entries):

    GET  /api/commands            → palette snapshot (live availability)
    GET  /api/commands/schema     → typed API command schema
    GET  /api/commands/chat       → chat command discovery
    POST /api/commands/dispatch   → the same execution seam the CLI uses

Owner-local transport is a network fact, never authorization: mutating
commands still pass the registry's permission gate.
"""
from __future__ import annotations

from typing import Any

from core.command_registry.execute import ExecutionContext, execute_command
from core.command_registry.projections import api_schema, chat_discovery, commands_json, palette_data
from core.command_registry.registry import registry as get_registry


def handle_commands_get(path: str, query: dict[str, list[str]]):
    """Return an (status, payload) tuple; service.py wraps it with json_response."""
    reg = get_registry()
    if path in {"/api/commands", "/api/commands/"}:
        return 200, commands_json(reg, live_availability=True)
    if path in {"/api/commands/schema", "/api/commands/schema/"}:
        return 200, api_schema(reg)
    if path in {"/api/commands/palette", "/api/commands/palette/"}:
        return 200, palette_data(reg)
    if path in {"/api/commands/chat", "/api/commands/chat/"}:
        return 200, chat_discovery(reg)
    return 404, {"error": "unknown commands route", "path": path}


def handle_commands_dispatch(body: dict[str, Any]):
    """POST /api/commands/dispatch {command_id, input, approval_context?}."""
    command_id = str(body.get("command_id") or body.get("command") or "").strip()
    if not command_id:
        return 400, {"ok": False, "fault": {"code": "usage", "detail": {"reason": "command_id is required"}}}
    input_data = body.get("input") or {}
    if not isinstance(input_data, dict):
        return 400, {"ok": False, "fault": {"code": "usage", "detail": {"reason": "input must be an object"}}}
    approval_context = body.get("approval_context")
    if approval_context is not None and not isinstance(approval_context, dict):
        return 400, {"ok": False, "fault": {"code": "usage", "detail": {"reason": "approval_context must be an object"}}}
    envelope = execute_command(
        command_id,
        input_data,
        context=ExecutionContext(projection="api", approval_context=approval_context),
    )
    status = 200 if envelope.ok else _fault_status(envelope.execution.exit_code)
    return status, envelope.to_json()


def _fault_status(exit_code: int) -> int:
    if exit_code == 0:
        return 200
    if exit_code in (2, 3):
        return 400 if exit_code == 2 else 404
    if exit_code == 10:
        return 409
    if exit_code in (20, 21):
        return 403
    if exit_code == 30:
        return 409
    return 500
