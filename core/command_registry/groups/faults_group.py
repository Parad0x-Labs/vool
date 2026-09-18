"""Faults group — fault diagnostics from the fault security plane.

Binds to ``core.faults.catalog`` (the one fault vocabulary) and
``core.faults.recorder`` (observations). Read-only diagnostics.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.command_registry.spec import (
    CommandSpec,
    FaultBinding,
    GroupSpec,
    Handler,
    HandlerFault,
    HandlerOk,
    NextAction,
)


@dataclass(frozen=True)
class ListInput:
    code: str = ""
    limit: int = 50


@dataclass(frozen=True)
class ShowInput:
    fault_id: str


def _handle_list(inp, ctx):
    from core.faults.recorder import list_faults

    limit = max(1, min(int(inp.limit or 50), 200))
    records = list_faults(code=inp.code or "", limit=limit)
    rows = [
        {
            "fault_id": r.fault_id,
            "code": r.code,
            "component": getattr(r, "component", ""),
            "summary": getattr(r, "summary", ""),
            "lifecycle": getattr(r, "lifecycle", ""),
            "observed_at": getattr(r, "observed_at", ""),
        }
        for r in records
    ]
    return HandlerOk(
        data={"count": len(rows), "faults": rows},
        summary=f"{len(rows)} fault observation(s)" + (f" for code {inp.code!r}" if inp.code else ""),
    )


def _handle_show(inp, ctx):
    from core.faults.recorder import fault_by_id

    record = fault_by_id(inp.fault_id)
    if record is None:
        return HandlerFault(
            fault_code="fault_validation",
            summary=f"No fault with id {inp.fault_id!r}",
            detail={"fault_id": inp.fault_id},
        )
    return HandlerOk(
        data={k: getattr(record, k) for k in vars(record) if not k.startswith("_")} if vars(record) else str(record),
        summary=f"Fault {inp.fault_id}",
    )


def _handle_catalog(inp, ctx):
    from core.faults.catalog import all_codes, catalog_digest, export_catalog

    return HandlerOk(
        data={"codes": list(all_codes()), "digest": catalog_digest(), "catalog": export_catalog()},
        summary=f"Fault catalog: {len(all_codes())} codes",
    )


def register(reg) -> None:
    reg.add_group(GroupSpec(group_id="faults", description="fault diagnostics: observations and the one fault vocabulary"))
    reg.add(
        CommandSpec(
            command_id="faults.list",
            group="faults",
            description="List recorded fault observations (optionally by code)",
            aliases=("faults",),
            input_schema=ListInput,
            effects="read_only",
            capabilities=frozenset({"faults.read"}),
            handler=Handler("core.command_registry.groups.faults_group:_handle_list"),
            fault_bindings=(FaultBinding(when="store_missing", fault_code="fault_validation", remediation=("vool faults catalog",)),),
            exit_codes=(0, 2, 42),
            model_offerable=True
        )
    )
    reg.add(
        CommandSpec(
            command_id="faults.show",
            group="faults",
            description="Show one fault observation by id",
            input_schema=ShowInput,
            effects="read_only",
            capabilities=frozenset({"faults.read"}),
            handler=Handler("core.command_registry.groups.faults_group:_handle_show"),
            exit_codes=(0, 2, 42),
        )
    )
    reg.add(
        CommandSpec(
            command_id="faults.catalog",
            group="faults",
            description="Show the fault vocabulary (codes, lifecycle, digest)",
            effects="read_only",
            capabilities=frozenset({"faults.read"}),
            handler=Handler("core.command_registry.groups.faults_group:_handle_catalog"),
            exit_codes=(0,),
            next_actions=(NextAction(command_id="faults.list", label="List fault observations"),),
            model_offerable=True
        )
    )
