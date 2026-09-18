"""``vool commands --check`` — the registry lint (CI-able, Omarchy's cultural export).

Fails on:
- ``duplicated``   — two commands claiming one surface spelling (id or alias)
- ``orphaned``     — references pointing at nothing (breadcrumbs, groups,
                     next_actions, remediation command pointers)
- ``unbound``      — handler missing or unresolvable; availability probe
                     unresolvable
- ``contract_divergent`` — raw-dict schemas, empty descriptions, vocabulary
                     violations, mutating commands without a permission gate,
                     constant-true availability on mutating commands
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from core.command_registry.registry import CommandRegistry, resolve_dotted
from core.command_registry.spec import (
    FAULT_CODES,
    LIFECYCLE_STATES,
    SIDE_EFFECT_CLASSES,
    NextAction,
    OpenRead,
)

FINDING_KINDS = ("duplicated", "orphaned", "unbound", "contract_divergent")

_MUTATING = {"idempotent_write", "mutating", "destructive", "spend", "external_send"}


@dataclass(frozen=True)
class Finding:
    kind: str
    command_id: str
    detail: str


@dataclass(frozen=True)
class CheckReport:
    ok: bool
    findings: tuple[Finding, ...] = ()
    command_count: int = 0
    group_count: int = 0

    def summary_lines(self) -> list[str]:
        for kind in FINDING_KINDS:
            count = sum(1 for f in self.findings if f.kind == kind)
            yield f"{kind}: {count}"
        if self.ok:
            yield (
                f"Command registry check passed "
                f"({self.command_count} commands, {self.group_count} groups)"
            )
        else:
            yield f"Command registry check FAILED with {len(self.findings)} findings"


def check_registry(reg: CommandRegistry) -> CheckReport:
    findings: list[Finding] = []
    commands = reg.commands()
    known_ids = {spec.command_id for spec in commands}
    known_groups = {g.group_id for g in reg.groups()}

    def add(kind: str, command_id: str, detail: str) -> None:
        findings.append(Finding(kind=kind, command_id=command_id, detail=detail))

    # Surface duplication (registration also guards; check is the CI truth).
    seen_surface: dict[str, str] = {}
    for spec in commands:
        for surface in (spec.command_id, *spec.aliases):
            owner = seen_surface.setdefault(surface, spec.command_id)
            if owner != spec.command_id:
                add(
                    "duplicated",
                    spec.command_id,
                    f"surface {surface!r} also claimed by {owner!r}",
                )

    for spec in commands:
        cid = spec.command_id

        # -- orphaned references -------------------------------------------
        if spec.group not in known_groups:
            add("orphaned", cid, f"unknown group {spec.group!r}")
        for na in spec.next_actions:
            if isinstance(na, NextAction) and na.command_id not in known_ids:
                add("orphaned", cid, f"next_action points at unknown command {na.command_id!r}")
        for binding in spec.fault_bindings:
            for pointer in binding.remediation:
                first = pointer.split()[0] if pointer.split() else ""
                if "." in first and first not in known_ids and first not in reg.surface_map():
                    add("orphaned", cid, f"remediation points at unknown command {first!r}")

        # -- unbound handlers / probes --------------------------------------
        if spec.handler is None:
            add("unbound", cid, "no handler bound")
        else:
            try:
                fn = resolve_dotted(spec.handler.dotted)
                if not callable(fn):
                    add("unbound", cid, f"handler {spec.handler.dotted!r} is not callable")
            except Exception as exc:
                add("unbound", cid, f"handler {spec.handler.dotted!r} unresolvable: {exc}")
        if spec.availability is not None:
            try:
                probe = resolve_dotted(spec.availability.probe)
                if not callable(probe):
                    add("unbound", cid, f"availability probe {spec.availability.probe!r} not callable")
            except Exception as exc:
                add("unbound", cid, f"availability probe {spec.availability.probe!r} unresolvable: {exc}")

        # -- contract divergence --------------------------------------------
        if not spec.description or not spec.description.strip():
            add("contract_divergent", cid, "empty description")
        if spec.effects not in SIDE_EFFECT_CLASSES:
            add("contract_divergent", cid, f"effects {spec.effects!r} outside vocabulary")
        if spec.lifecycle not in LIFECYCLE_STATES:
            add("contract_divergent", cid, f"lifecycle {spec.lifecycle!r} outside vocabulary")
        for code in (spec.fault_bindings and [b.fault_code for b in spec.fault_bindings]) or []:
            if code not in FAULT_CODES or code == "ok":
                add("contract_divergent", cid, f"fault binding code {code!r} outside vocabulary")
        for exit_code in spec.exit_codes:
            if exit_code not in (0, 2, 3, 10, 20, 21, 22, 30, 40, 41, 42, 43, 44, 45, 50):
                add("contract_divergent", cid, f"exit code {exit_code!r} outside typed vocabulary")
        for schema_role, schema in (("input_schema", spec.input_schema), ("output_schema", spec.output_schema)):
            if schema is not None and not (dataclasses.is_dataclass(schema) and isinstance(schema, type)):
                add("contract_divergent", cid, f"{schema_role} {schema!r} is not a typed dataclass")
        if spec.model_offerable and spec.effects != "read_only":
            add(
                "contract_divergent",
                cid,
                f"model_offerable command must be read_only in this milestone (effects={spec.effects!r})",
            )
        if spec.effects in _MUTATING:
            if isinstance(spec.permission, OpenRead):
                add(
                    "contract_divergent",
                    cid,
                    f"mutating effects {spec.effects!r} with OpenRead permission — gate required",
                )
            if spec.availability is None:
                add(
                    "contract_divergent",
                    cid,
                    "mutating command without availability evidence (constant-true forbidden)",
                )

    return CheckReport(
        ok=not findings,
        findings=tuple(findings),
        command_count=len(commands),
        group_count=len(reg.groups()),
    )
