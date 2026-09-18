"""Projections — every surface renders from the registry; none contains names.

- ``commands_json``    → ``vool commands --json``
- ``check_output``     → ``vool commands --check``
- ``cli_help``         → CLI help (grouped, generated)
- ``chat_discovery``   → chat command discovery table (typed literals only)
- ``palette_data``     → Cmd+K command palette rows with live availability truth
- ``api_schema``       → API command schema (typed input/output, exit codes)
"""
from __future__ import annotations

import dataclasses
from typing import Any

from core.command_registry.check import CheckReport, check_registry
from core.command_registry.registry import CommandRegistry, resolve_dotted
from core.command_registry.spec import ApprovalGate, CommandSpec, OpenRead, OperatorAuthority


def _permission_label(spec: CommandSpec) -> str:
    if isinstance(spec.permission, OpenRead):
        return "open_read"
    if isinstance(spec.permission, ApprovalGate):
        return f"approval:{spec.permission.kind}"
    if isinstance(spec.permission, OperatorAuthority):
        return f"operator_authority:{spec.permission.kind}"
    return "unknown"


def _schema_fields(schema: type | None) -> dict[str, Any] | None:
    if schema is None:
        return None
    fields: dict[str, Any] = {}
    for f in dataclasses.fields(schema):
        required = f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
        fields[f.name] = {
            "type": getattr(f.type, "__name__", str(f.type)),
            "required": required,
        }
    return fields


def _evaluate_availability(spec: CommandSpec, context: dict[str, Any] | None = None) -> tuple[bool, str | None]:
    if spec.availability is None:
        return True, None
    try:
        probe = resolve_dotted(spec.availability.probe)
        return probe(context or {})
    except Exception as exc:
        return False, f"availability probe failed: {exc}"


def command_row(spec: CommandSpec, *, live_availability: bool = False) -> dict[str, Any]:
    row: dict[str, Any] = {
        "command_id": spec.command_id,
        "aliases": list(spec.aliases),
        "group": spec.group,
        "description": spec.description,
        "effects": spec.effects,
        "permission": _permission_label(spec),
        "capabilities": sorted(spec.capabilities),
        "lifecycle": spec.lifecycle,
        "platforms": sorted(spec.platforms),
        "input_schema": _schema_fields(spec.input_schema),
        "output_schema": _schema_fields(spec.output_schema),
        "exit_codes": list(spec.exit_codes),
        "fault_codes": [b.fault_code for b in spec.fault_bindings],
        "handler": spec.handler.dotted if spec.handler else None,
    }
    if live_availability:
        available, reason = _evaluate_availability(spec)
        row["available"] = available
        if not available:
            row["unavailable_reason"] = reason
    return row


def commands_json(reg: CommandRegistry, *, live_availability: bool = False) -> dict[str, Any]:
    return {
        "commands": [command_row(s, live_availability=live_availability) for s in reg.commands()],
        "groups": [
            {
                "group_id": g.group_id,
                "description": g.description,
                "aliases": list(g.aliases),
            }
            for g in reg.groups()
        ],
    }


def check_output(reg: CommandRegistry, *, include_census: bool = True) -> tuple[CheckReport, list[str]]:
    report = check_registry(reg)
    lines: list[str] = []
    for finding in report.findings:
        lines.append(f"{finding.kind}: {finding.command_id}: {finding.detail}")
    census_failed = False
    if include_census:
        from core.command_registry.census import census_check

        census = census_check({s.command_id for s in reg.commands()})
        for finding in census.findings:
            lines.append(f"census: {finding}")
            census_failed = True
        if not census_failed:
            totals = census.totals()
            lines.append(
                "census: ok "
                f"({census_report_line(totals)})"
            )
    lines.extend(report.summary_lines())
    if census_failed and report.ok:
        report = CheckReport(
            ok=False,
            findings=report.findings
            + tuple(
                __import__("core.command_registry.check", fromlist=["Finding"]).Finding(
                    kind="census", command_id="-", detail=f
                )
                for f in ("census findings present",)
            ),
            command_count=report.command_count,
            group_count=report.group_count,
        )
    return report, lines


def census_report_line(totals: dict[str, int]) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(totals.items()))


def cli_help(reg: CommandRegistry) -> str:
    lines: list[str] = ["vool — the VOOL operator command centre", ""]
    for group in sorted(reg.groups(), key=lambda g: g.group_id):
        lines.append(f"{group.group_id} — {group.description}")
        members = [s for s in reg.commands() if s.group == group.group_id]
        for spec in sorted(members, key=lambda s: s.command_id):
            alias_note = f" (aliases: {', '.join(spec.aliases)})" if spec.aliases else ""
            lines.append(f"  {spec.command_id}{alias_note} — {spec.description}")
        lines.append("")
    lines.append("Run `vool <command_id> [--json]` to execute; `vool commands --json` for machine truth.")
    return "\n".join(lines)


def chat_discovery(reg: CommandRegistry) -> dict[str, Any]:
    """Chat command discovery — typed literals the chat surface may offer."""
    rows = []
    for spec in reg.commands():
        available, reason = _evaluate_availability(spec)
        rows.append(
            {
                "literal": spec.command_id,
                "aliases": list(spec.aliases),
                "group": spec.group,
                "description": spec.description,
                "effects": spec.effects,
                "available": available,
                "unavailable_reason": reason if not available else None,
                "invocation": f"vool {spec.command_id}",
            }
        )
    return {"surface": "chat", "commands": rows}


def palette_data(reg: CommandRegistry) -> dict[str, Any]:
    """Cmd+K palette rows — same registry, same availability truth, never hidden."""
    rows = []
    for spec in reg.commands():
        available, reason = _evaluate_availability(spec)
        rows.append(
            {
                "command_id": spec.command_id,
                "aliases": list(spec.aliases),
                "group": spec.group,
                "label": spec.description,
                "effects": spec.effects,
                "available": available,
                "unavailable_reason": reason if not available else None,
                "requires_input": spec.input_schema is not None,
            }
        )
    return {"surface": "palette", "commands": rows}


def api_schema(reg: CommandRegistry) -> dict[str, Any]:
    """API command schema — the dispatch contract for POST /api/commands/dispatch."""
    return {
        "spec_version": "1",
        "dispatch": {
            "method": "POST",
            "path": "/api/commands/dispatch",
            "request": {"command_id": "str", "input": "object (validated against the command's input_schema)"},
            "response": "CommandEnvelope",
        },
        "commands": [
            {
                "command_id": s.command_id,
                "aliases": list(s.aliases),
                "input_schema": _schema_fields(s.input_schema),
                "output_schema": _schema_fields(s.output_schema),
                "exit_codes": list(s.exit_codes),
                "effects": s.effects,
                "permission": _permission_label(s),
            }
            for s in reg.commands()
        ],
    }
