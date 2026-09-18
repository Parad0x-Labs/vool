"""Legacy-surface forwarding: GENERATED_ADAPTER plumbing.

A legacy route/CLI/REPL surface classified GENERATED_ADAPTER calls
:func:`forward` — the CommandSpec owns the name, schema, permission, effects
and fault semantics; the legacy rendering is preserved byte-for-byte by
returning the authority's own ``(status, payload)`` when the handler filed
``legacy_status`` in the fault detail, and the typed envelope otherwise.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.command_registry.envelope import CommandEnvelope
from core.command_registry.execute import ExecutionContext, execute_command


def fault_status(exit_code: int) -> int:
    """Map typed exit codes onto HTTP statuses for legacy transports."""
    if exit_code == 0:
        return 200
    if exit_code == 2:
        return 400
    if exit_code == 3:
        return 404
    if exit_code in (10, 30):
        return 409
    if exit_code in (20, 21):
        return 403
    return 500


def forward(
    command_id: str,
    input_data: Mapping[str, Any] | None,
    *,
    approval_context: Mapping[str, Any] | None = None,
    projection: str = "api",
    client_host: str = "127.0.0.1",
    headers: Mapping[str, Any] | None = None,
) -> tuple[int, Any]:
    """Execute one registry command for a legacy surface → (status, payload).

    ``client_host`` carries the transport peer so owner-local gates inside the
    lifted authorities evaluate the REAL TCP fact, never a default.
    """
    envelope = execute_command(
        command_id,
        dict(input_data or {}),
        context=ExecutionContext(
            projection=projection,
            approval_context=approval_context,
            extra={"client_host": client_host, "headers": dict(headers or {})},
        ),
    )
    return envelope_status(envelope), envelope_payload(envelope)


def envelope_status(envelope: CommandEnvelope) -> int:
    if envelope.ok:
        return 200
    detail = envelope.fault.detail if envelope.fault else None
    if detail and "legacy_status" in detail:
        return int(detail["legacy_status"])
    return fault_status(envelope.execution.exit_code)


def envelope_payload(envelope: CommandEnvelope) -> Any:
    if envelope.ok:
        return envelope.data
    detail = dict(envelope.fault.detail or {}) if envelope.fault else {}
    if "legacy_status" in detail:
        detail.pop("legacy_status")
        return detail
    return envelope.to_json()
