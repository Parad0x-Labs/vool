"""Typed command envelope + typed exit codes.

Laws (Blueprint §5):
- ``ok`` is true ⇔ ``exit_code == 0`` — the ONLY success signals;
- ``--json`` output is exactly this envelope, no banners;
- fault codes map 1:1 onto typed exit codes;
- no consumer parses prose: ``summary`` carries no success guarantees.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


class ExitCodes:
    OK = 0
    USAGE = 2
    UNKNOWN_COMMAND = 3
    UNAVAILABLE = 10
    PERMISSION_REQUIRED = 20
    PERMISSION_DENIED = 21
    BUDGET_REFUSED = 22
    CONFLICT = 30
    FAULT_TOOL = 40
    FAULT_PROVIDER = 41
    FAULT_VALIDATION = 42
    FAULT_SYNTHESIS = 43
    FAULT_PARTIAL = 44
    FAULT_CANCELLED = 45
    INTERNAL = 50


FAULT_CODE_TO_EXIT = {
    "ok": ExitCodes.OK,
    "usage": ExitCodes.USAGE,
    "unknown_command": ExitCodes.UNKNOWN_COMMAND,
    "unavailable": ExitCodes.UNAVAILABLE,
    "permission_required": ExitCodes.PERMISSION_REQUIRED,
    "permission_denied": ExitCodes.PERMISSION_DENIED,
    "budget_refused": ExitCodes.BUDGET_REFUSED,
    "conflict": ExitCodes.CONFLICT,
    "fault_tool": ExitCodes.FAULT_TOOL,
    "fault_provider": ExitCodes.FAULT_PROVIDER,
    "fault_validation": ExitCodes.FAULT_VALIDATION,
    "fault_synthesis": ExitCodes.FAULT_SYNTHESIS,
    "fault_partial": ExitCodes.FAULT_PARTIAL,
    "fault_cancelled": ExitCodes.FAULT_CANCELLED,
    "internal": ExitCodes.INTERNAL,
}

SPEC_VERSION = "1"


@dataclass(frozen=True)
class Breadcrumb:
    command_id: str
    label: str
    invocation: str              # literally typeable next command

    def to_json(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "label": self.label,
            "invocation": self.invocation,
        }


@dataclass(frozen=True)
class FaultBlock:
    code: str                    # FAULT_CODES member, never "ok"
    root_cause_state: str | None = None
    remediation: tuple[str, ...] = ()
    detail: Mapping[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "root_cause_state": self.root_cause_state,
            "remediation": list(self.remediation),
        }
        if self.detail is not None:
            out["detail"] = dict(self.detail)
        return out


@dataclass(frozen=True)
class ExecutionBlock:
    command_id: str
    spec_version: str = SPEC_VERSION
    platform: str = ""
    started: str = ""
    duration_ms: int = 0
    cancelled: bool = False
    retry_policy: str = "none"
    attempts: int = 1
    exit_code: int = ExitCodes.OK
    projection: str = "core"     # cli | chat | api | core — provenance of execution

    def to_json(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "spec_version": self.spec_version,
            "platform": self.platform,
            "started": self.started,
            "duration_ms": self.duration_ms,
            "cancelled": self.cancelled,
            "retry": {"policy": self.retry_policy, "attempts": self.attempts},
            "exit_code": self.exit_code,
            "projection": self.projection,
        }


@dataclass(frozen=True)
class CommandEnvelope:
    ok: bool
    data: Any = None
    summary: str = ""
    breadcrumbs: tuple[Breadcrumb, ...] = ()
    fault: FaultBlock | None = None
    receipts: tuple[dict[str, Any], ...] = ()
    execution: ExecutionBlock = field(default_factory=ExecutionBlock)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": self.ok,
            "data": _jsonify(self.data),
            "summary": self.summary,
            "breadcrumbs": [b.to_json() for b in self.breadcrumbs],
            "fault": self.fault.to_json() if self.fault is not None else None,
            "receipts": [dict(r) for r in self.receipts],
            "execution": self.execution.to_json(),
        }
        return out


def _jsonify(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "to_json") and callable(value.to_json):
        return value.to_json()
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    if isinstance(value, Mapping):
        return {str(k): _jsonify(v) for k, v in value.items()}
    if hasattr(value, "__dict__") or hasattr(value, "__dataclass_fields__"):
        fields: dict[str, Any] = {}
        for name in dir(value):
            if name.startswith("_"):
                continue
            attr = getattr(value, name)
            if callable(attr):
                continue
            fields[name] = _jsonify(attr)
        return fields
    return str(value)
