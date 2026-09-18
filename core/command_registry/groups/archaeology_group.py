"""Archaeology group — the typed, read-only project/job archaeology door (C21).

Binds to ``core.project_archaeology`` (locate / search / trace / compare /
recovery_plan). The group is a thin adapter: every handler calls the one
authority, every command is ``read_only`` and ``logs.read``-capped, and no
command here can restore, mutate, check out, rewind or clean anything —
executing an approved recovery plan is separate permission/effect/ledger
work. Every result envelope carries source/object id, hash, timestamp,
authority, truncation truth and confidence; surfaced history content is
quarantined as untrusted data and gated through the A8 availability verdicts.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.command_registry.spec import (
    Availability,
    CommandSpec,
    FaultBinding,
    GroupSpec,
    Handler,
    HandlerFault,
    HandlerOk,
    NextAction,
)
from core.project_archaeology import (
    ArchaeologyInputError,
    ReferenceNotFound,
    ScopeRefused,
)
from core.project_archaeology import (
    compare as archaeology_compare,
)
from core.project_archaeology import (
    locate as archaeology_locate,
)
from core.project_archaeology import (
    recovery_plan as archaeology_recovery_plan,
)
from core.project_archaeology import (
    search as archaeology_search,
)
from core.project_archaeology import (
    trace as archaeology_trace,
)


@dataclass(frozen=True)
class LocateInput:
    reference: str = ""
    workspace_root: str = ""
    limit: int = 25


@dataclass(frozen=True)
class SearchInput:
    text: str = ""
    store: str = ""
    kind: str = ""
    session_id: str = ""
    turn_id: str = ""
    path_prefix: str = ""
    since: str = ""
    until: str = ""
    workspace_root: str = ""
    limit: int = 50


@dataclass(frozen=True)
class TraceInput:
    turn_id: str = ""
    session_id: str = ""
    effect_id: str = ""
    attempt_id: str = ""
    path: str = ""
    since: str = ""
    until: str = ""
    workspace_root: str = ""
    limit: int = 100


@dataclass(frozen=True)
class CompareInput:
    kind: str = "git_commits"
    a: str = ""
    b: str = ""
    workspace_root: str = ""


@dataclass(frozen=True)
class RecoveryPlanInput:
    reference: str = ""
    path: str = ""
    turn_id: str = ""
    workspace_root: str = ""
    limit: int = 25


# -- availability probe (machine evidence, never a constant) -----------------


def _probe_archaeology_sources(context: dict) -> tuple[bool, str]:
    from core.project_archaeology import default_store_roots

    roots = default_store_roots()
    if roots:
        return True, f"archaeology sources present: {', '.join(sorted(roots))}"
    return False, "no archaeology sources resolved (no store roots under this home)"


# -- typed error mapping -------------------------------------------------------


def _fault_for(exc: Exception, command: str) -> HandlerFault:
    if isinstance(exc, ReferenceNotFound):
        return HandlerFault(
            fault_code="fault_validation",
            summary=f"not found: {exc}",
            detail={"reason": str(exc), "not_found": True, "command": command},
        )
    if isinstance(exc, ScopeRefused):
        return HandlerFault(
            fault_code="fault_validation",
            summary=f"scope refused: {exc.reason}",
            detail={
                "reason": exc.reason,
                "path": exc.path,
                "scope_refused": True,
                "command": command,
            },
        )
    if isinstance(exc, ArchaeologyInputError):
        return HandlerFault(
            fault_code="fault_validation",
            summary=str(exc),
            detail={"reason": str(exc), "command": command},
        )
    return HandlerFault(
        fault_code="internal",
        summary=f"{command} failed: {exc}",
        detail={"error": str(exc), "command": command},
    )


def _identity_summary(results: list[dict], *, cap: int = 8) -> str:
    parts = []
    for item in results[:cap]:
        label = str(item.get("object_id") or "?")
        store = str(item.get("store") or "")
        parts.append(f"{label}[{store}]" if store else label)
    tail = f" +{len(results) - cap} more" if len(results) > cap else ""
    return ", ".join(parts) + tail


# -- handlers (thin adapters over the authority) -------------------------------


def _handle_locate(inp, ctx):
    try:
        payload = archaeology_locate(
            inp.reference,
            workspace_root=inp.workspace_root,
            limit=inp.limit,
            requester=f"command:{ctx.projection}",
        )
    except (ReferenceNotFound, ArchaeologyInputError, ScopeRefused) as exc:
        return _fault_for(exc, "archaeology.locate")
    return HandlerOk(
        data=payload,
        summary=(
            f"located {payload['returned']} object(s) for {inp.reference!r}"
            + (f" (TRUNCATED, limit {payload['limits']['limit']})" if payload["truncated"] else "")
            + f": {_identity_summary(payload['results'])}"
        ),
    )


def _handle_search(inp, ctx):
    try:
        payload = archaeology_search(
            text=inp.text,
            store=inp.store,
            kind=inp.kind,
            session_id=inp.session_id,
            turn_id=inp.turn_id,
            path_prefix=inp.path_prefix,
            since=inp.since,
            until=inp.until,
            workspace_root=inp.workspace_root,
            limit=inp.limit,
            requester=f"command:{ctx.projection}",
        )
    except (ArchaeologyInputError, ScopeRefused) as exc:
        return _fault_for(exc, "archaeology.search")
    truth = "TRUNCATED" if payload["truncated"] else "complete"
    return HandlerOk(
        data=payload,
        summary=(
            f"{payload['returned']} object(s) ({truth}, limit {payload['limits']['limit']})"
            f": {_identity_summary(payload['results'])}"
        ),
    )


def _handle_trace(inp, ctx):
    try:
        payload = archaeology_trace(
            turn_id=inp.turn_id,
            session_id=inp.session_id,
            effect_id=inp.effect_id,
            attempt_id=inp.attempt_id,
            path=inp.path,
            since=inp.since,
            until=inp.until,
            workspace_root=inp.workspace_root,
            limit=inp.limit,
            requester=f"command:{ctx.projection}",
        )
    except (ArchaeologyInputError, ScopeRefused) as exc:
        return _fault_for(exc, "archaeology.trace")
    joined = ", ".join(str(name) for name in payload["lineage"]["stores_joined"])
    return HandlerOk(
        data=payload,
        summary=(
            f"trace joined {payload['returned']} object(s) across [{joined}]"
            f" (timeline {len(payload['timeline'])})"
        ),
    )


def _handle_compare(inp, ctx):
    try:
        payload = archaeology_compare(
            kind=inp.kind,
            a=inp.a,
            b=inp.b,
            workspace_root=inp.workspace_root,
            requester=f"command:{ctx.projection}",
        )
    except (ArchaeologyInputError, ScopeRefused, ReferenceNotFound, LookupError) as exc:
        return _fault_for(exc, "archaeology.compare")
    comparison = payload["comparison"]
    return HandlerOk(
        data=payload,
        summary=(
            f"{comparison['summary']}; changed: "
            f"{', '.join(comparison['changed_paths'][:8]) or 'nothing'}"
        ),
    )


def _handle_recovery_plan(inp, ctx):
    try:
        payload = archaeology_recovery_plan(
            reference=inp.reference,
            path=inp.path,
            turn_id=inp.turn_id,
            workspace_root=inp.workspace_root,
            limit=inp.limit,
            requester=f"command:{ctx.projection}",
        )
    except (ArchaeologyInputError, ScopeRefused) as exc:
        return _fault_for(exc, "archaeology.recovery_plan")
    plan = payload["plan"]
    verdict = "RECOVERABLE" if plan["recoverable"] else "NOT RECOVERABLE from in-scope evidence"
    return HandlerOk(
        data=payload,
        summary=(
            f"recovery plan ({verdict}): {len(plan['steps'])} step(s); "
            f"execution: {plan['execution']}"
        ),
    )


def register(reg) -> None:
    reg.add_group(
        GroupSpec(
            group_id="archaeology",
            description=(
                "read-only project/job archaeology over the authorities VOOL already "
                "has (Blackbox journal, runtime ledger, receipts, honesty chain, "
                "approved memory, governed finalizations, git objects, workspace): "
                "locate, search, trace, compare and recovery_plan — never restores, "
                "mutates, checks out, rewinds or cleans anything"
            ),
        )
    )
    reg.add(
        CommandSpec(
            command_id="archaeology.locate",
            group="archaeology",
            description="Where is that report/commit/receipt/effect? One reference, every in-scope store probed, full provenance per hit",
            input_schema=LocateInput,
            effects="read_only",
            capabilities=frozenset({"logs.read"}),
            handler=Handler("core.command_registry.groups.archaeology_group:_handle_locate"),
            availability=Availability("core.command_registry.groups.archaeology_group:_probe_archaeology_sources"),
            fault_bindings=(
                FaultBinding(when="reference_missing", fault_code="fault_validation", remediation=("archaeology.search",)),
            ),
            exit_codes=(0, 2, 10, 42),
            model_offerable=True,
        )
    )
    reg.add(
        CommandSpec(
            command_id="archaeology.search",
            group="archaeology",
            description="Typed bounded search across the in-scope stores: text/store/kind/session/turn/path/time filters with truthful truncation",
            input_schema=SearchInput,
            effects="read_only",
            capabilities=frozenset({"logs.read"}),
            handler=Handler("core.command_registry.groups.archaeology_group:_handle_search"),
            availability=Availability("core.command_registry.groups.archaeology_group:_probe_archaeology_sources"),
            fault_bindings=(
                FaultBinding(when="bad_time_window", fault_code="fault_validation", remediation=()),
            ),
            exit_codes=(0, 2, 10, 42),
            next_actions=(NextAction(command_id="archaeology.trace", label="Trace one hit across stores"),),
            model_offerable=True,
        )
    )
    reg.add(
        CommandSpec(
            command_id="archaeology.trace",
            group="archaeology",
            description="Cross-store lineage for one turn/session/effect/path/window: what happened, which task changed this file, why did it fail",
            input_schema=TraceInput,
            effects="read_only",
            capabilities=frozenset({"logs.read"}),
            handler=Handler("core.command_registry.groups.archaeology_group:_handle_trace"),
            availability=Availability("core.command_registry.groups.archaeology_group:_probe_archaeology_sources"),
            exit_codes=(0, 2, 10, 42),
            model_offerable=True,
        )
    )
    reg.add(
        CommandSpec(
            command_id="archaeology.compare",
            group="archaeology",
            description="Bounded read-only diff between two git commits, two turns or two workspace files",
            input_schema=CompareInput,
            effects="read_only",
            capabilities=frozenset({"logs.read"}),
            handler=Handler("core.command_registry.groups.archaeology_group:_handle_compare"),
            availability=Availability("core.command_registry.groups.archaeology_group:_probe_archaeology_sources"),
            exit_codes=(0, 2, 10, 42),
            model_offerable=True,
        )
    )
    reg.add(
        CommandSpec(
            command_id="archaeology.recovery_plan",
            group="archaeology",
            description="Can this lost work be recovered? Locates recoverable copies and names the required permission; produces a plan only — execution is separate approval/effect work",
            input_schema=RecoveryPlanInput,
            effects="read_only",
            capabilities=frozenset({"logs.read"}),
            handler=Handler("core.command_registry.groups.archaeology_group:_handle_recovery_plan"),
            availability=Availability("core.command_registry.groups.archaeology_group:_probe_archaeology_sources"),
            exit_codes=(0, 2, 10, 42),
            model_offerable=True,
        )
    )
