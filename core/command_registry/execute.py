"""The one execution seam.

CLI, chat and API projections all call :func:`execute_command`; identical
semantics are structural because there is exactly one dispatch path:

lookup → typed input construction → availability probe → permission gate →
handler → typed envelope with typed exit code.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import difflib
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from core.command_registry.envelope import (
    FAULT_CODE_TO_EXIT,
    Breadcrumb,
    CommandEnvelope,
    ExecutionBlock,
    FaultBlock,
)
from core.command_registry.registry import CommandRegistry, resolve_dotted
from core.command_registry.spec import (
    ApprovalDecision,
    ApprovalGate,
    AuthorityDecision,
    CommandSpec,
    HandlerFault,
    HandlerOk,
    OperatorAuthority,
)

PLATFORM_NAMES = {"darwin": "macos", "win32": "windows", "linux": "linux"}


@dataclass(frozen=True)
class ExecutionContext:
    projection: str = "core"          # cli | chat | api | core
    approval_context: Mapping[str, Any] | None = None
    principal: str = "operator"
    extra: Mapping[str, Any] = field(default_factory=dict)


def _platform() -> str:
    return PLATFORM_NAMES.get(sys.platform, sys.platform)


def _breadcrumbs(spec: CommandSpec, extra_remediation: tuple[str, ...] = ()) -> tuple[Breadcrumb, ...]:
    out: list[Breadcrumb] = []
    for na in spec.next_actions:
        out.append(Breadcrumb(command_id=na.command_id, label=na.label, invocation=f"vool {na.command_id}"))
    for pointer in extra_remediation:
        first = pointer.split()[0] if pointer.split() else ""
        if "." in first:
            out.append(Breadcrumb(command_id=first, label=pointer, invocation=pointer))
    return tuple(out)


def _did_you_mean(surface: str, reg: CommandRegistry) -> str | None:
    candidates = list(reg.surface_map().keys())
    matches = difflib.get_close_matches(surface, candidates, n=1, cutoff=0.6)
    return matches[0] if matches else None


def _build_input(spec: CommandSpec, input_data: Mapping[str, Any] | None) -> tuple[Any, str | None]:
    """Construct the typed input; returns (instance, error_detail)."""
    schema = spec.input_schema
    if schema is None:
        if input_data:
            return None, f"command takes no input; got keys {sorted(input_data)}"
        return None, None
    if input_data is None:
        input_data = {}
    field_names = {f.name for f in dataclasses.fields(schema)}
    unknown = sorted(set(input_data) - field_names)
    if unknown:
        return None, f"unknown input keys: {unknown}"
    required = [
        f.name
        for f in dataclasses.fields(schema)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    ]
    missing = [name for name in required if name not in input_data]
    if missing:
        return None, f"missing required input keys: {missing}"
    coerced: dict[str, Any] = {}
    for f in dataclasses.fields(schema):
        if f.name not in input_data:
            continue
        value = input_data[f.name]
        origin = getattr(f.type, "__origin__", None)
        if f.type is str and not isinstance(value, str):
            return None, f"input {f.name!r} must be str"
        if (f.type is int and not isinstance(value, int)) or (isinstance(value, bool) and f.type is int):
            if not isinstance(value, int) or isinstance(value, bool):
                return None, f"input {f.name!r} must be int"
        if f.type is bool and not isinstance(value, bool):
            return None, f"input {f.name!r} must be bool"
        if origin is not None:
            pass  # container types pass through for the handler to validate
        coerced[f.name] = value
    try:
        return schema(**coerced), None
    except TypeError as exc:
        return None, f"input construction failed: {exc}"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _fault_envelope(
    spec: CommandSpec | None,
    code: str,
    summary: str,
    *,
    started: float,
    detail: Mapping[str, Any] | None,
    projection: str,
    remediation: tuple[str, ...] = (),
    data: Any = None,
) -> CommandEnvelope:
    exit_code = FAULT_CODE_TO_EXIT[code]
    fault = FaultBlock(code=code, remediation=remediation, detail=detail)
    execution = ExecutionBlock(
        command_id=spec.command_id if spec else "",
        platform=_platform(),
        started=_now_iso(),
        duration_ms=int((time.monotonic() - started) * 1000),
        exit_code=exit_code,
        projection=projection,
    )
    crumbs = _breadcrumbs(spec, remediation) if spec is not None else ()
    return CommandEnvelope(
        ok=False,
        data=data,
        summary=summary,
        breadcrumbs=crumbs,
        fault=fault,
        execution=execution,
    )


def _open_command_budget_effect(spec: CommandSpec, ctx: ExecutionContext):
    """Reserve one `command` budget unit for a declared-mutating command, at the one authority.

    Same shape as the runtime tools' mutation doors: None when no operator rule is active for
    the class (a budget is a configured limit, not a default deny); a lifecycle-shaped handle
    whose unit is consumed when the handler actually runs and settles from the command's real
    outcome; a typed `EffectBudgetRefusedError` when the gate says no. With an active ledger
    (a served turn) the gateway resolves the session/turn identity itself; a projection with no
    ledger (operator CLI/API) reserves directly, carrying whatever scoped identity the context
    declares — a budget must not be bypassable by arriving from an unforeseen path.
    """
    from core import effect_budget

    if not effect_budget.active_budgets("command"):
        return None
    from core.effect_gateway import (
        DECISION_ALLOWED,
        LIFECYCLE_AUTHORIZED,
        EffectReceipt,
        current_effect_ledger,
    )

    extra = dict(ctx.extra or {})
    ledger = current_effect_ledger()
    if ledger is None:
        receipt = effect_budget.reserve_effect_units(
            "command",
            owner_ref="command_registry.execute_door",
            turn_id=str(extra.get("turn_id") or ""),
            session_id=str(extra.get("session_id") or ""),
            project_key=str(extra.get("workspace_root") or extra.get("project_key") or ""),
        )
        from core.runtime_execution_tools import _DirectBudgetHandle

        return _DirectBudgetHandle(receipt) if not receipt.unbudgeted else None
    return ledger.open_effect(
        EffectReceipt(
            effect_class="command",
            decision=DECISION_ALLOWED,
            lifecycle=LIFECYCLE_AUTHORIZED,
            reason=f"command registry {spec.command_id}",
            decided_by="command_registry.execute_door",
        )
    )


def _budget_refusal_envelope(
    spec: CommandSpec,
    refusal,
    *,
    started: float,
    projection: str,
) -> CommandEnvelope:
    """The typed `budget_refused` fault envelope: the handler never ran, `ok` is false, and
    the authority's typed code and refusing rule are on the record."""
    exit_code = FAULT_CODE_TO_EXIT["budget_refused"]
    execution = ExecutionBlock(
        command_id=spec.command_id,
        platform=_platform(),
        started=_now_iso(),
        duration_ms=int((time.monotonic() - started) * 1000),
        exit_code=exit_code,
        projection=projection,
    )
    fault = FaultBlock(
        code="budget_refused",
        remediation=("vool operator budgets --json",),
        detail={"code": refusal.code, "detail": refusal.detail, "rule": refusal.rule},
    )
    return CommandEnvelope(
        ok=False,
        data=None,
        summary=f"{spec.command_id} refused by the effect budget: {refusal.code}: {refusal.detail}",
        breadcrumbs=_breadcrumbs(spec, fault.remediation),
        fault=fault,
        execution=execution,
    )


def execute_command(
    surface: str,
    input_data: Mapping[str, Any] | None = None,
    *,
    reg: CommandRegistry | None = None,
    context: ExecutionContext | None = None,
) -> CommandEnvelope:
    reg = reg or registry_default()
    ctx = context or ExecutionContext()
    started = time.monotonic()

    spec = reg.lookup(surface)
    if spec is None:
        suggestion = _did_you_mean(surface, reg)
        detail: dict[str, Any] = {"surface": surface}
        remediation: tuple[str, ...] = ("vool commands --json",)
        if suggestion:
            detail["did_you_mean"] = suggestion
            remediation = (f"vool {suggestion}", "vool commands --json")
        return _fault_envelope(
            None,
            "unknown_command",
            f"Unknown command: {surface}" + (f" — did you mean {suggestion!r}?" if suggestion else ""),
            started=started,
            detail=detail,
            projection=ctx.projection,
            remediation=remediation,
        )

    typed_input, input_error = _build_input(spec, input_data)
    if input_error is not None:
        return _fault_envelope(
            spec,
            "usage",
            f"Bad arguments for {spec.command_id}: {input_error}",
            started=started,
            detail={"reason": input_error},
            projection=ctx.projection,
        )

    if spec.availability is not None:
        try:
            probe = resolve_dotted(spec.availability.probe)
            available, reason = probe(dict(input_data or {}))
        except Exception as exc:
            available, reason = False, f"availability probe failed: {exc}"
        if not available:
            return _fault_envelope(
                spec,
                "unavailable",
                f"{spec.command_id} is unavailable: {reason}",
                started=started,
                detail={"reason": reason, "evidence": spec.availability.probe},
                projection=ctx.projection,
            )

    # -- permission gate (dispatch-time, never handler convention) ----------
    if isinstance(spec.permission, ApprovalGate):
        try:
            verifier = resolve_dotted(spec.permission.verifier)
            decision = verifier(typed_input, ctx)
        except Exception as exc:
            decision = ApprovalDecision(required=True, reason=f"gate error: {exc}")
        if getattr(decision, "required", False):
            return _fault_envelope(
                spec,
                "permission_required",
                f"{spec.command_id} requires approval"
                + (f" ({decision.reason})" if decision.reason else ""),
                started=started,
                detail={"kind": spec.permission.kind, "reason": decision.reason},
                projection=ctx.projection,
                remediation=(
                    f"vool approvals resolve {decision.approval_id} --decision allow",
                )
                if getattr(decision, "approval_id", None)
                else (),
                data={"approval_id": decision.approval_id} if getattr(decision, "approval_id", None) else None,
            )
    elif isinstance(spec.permission, OperatorAuthority):
        try:
            verifier = resolve_dotted(spec.permission.verifier)
            decision = verifier(typed_input, ctx)
        except Exception as exc:
            decision = AuthorityDecision(granted=False, reason=f"gate error: {exc}")
        if not getattr(decision, "granted", False):
            return _fault_envelope(
                spec,
                "permission_denied",
                f"{spec.command_id} refused: operator authority missing"
                + (f" — {decision.reason}" if decision.reason else ""),
                started=started,
                detail={"kind": spec.permission.kind, "reason": decision.reason},
                projection=ctx.projection,
                remediation=(f"vool {spec.permission.kind}",),
            )

    # -- CP1 amendment: the command budget door ------------------------------
    # A command whose spec declares a mutating effect (mutating / destructive /
    # idempotent_write — `read_only` never reserves) owes its `command` budget unit at the ONE
    # authority (`core.effect_budget`) BEFORE the handler runs, through the same reservation the
    # gateway's open_effect seam makes: a refusal returns a typed `budget_refused` fault envelope
    # and the handler NEVER runs — zero handler calls, zero writes, zero success-shaped
    # receipts. Unbudgeted (no operator rule): an honest pass, no rows, no events.
    from core.effect_budget import EffectBudgetRefusedError

    budget_effect = None
    if spec.effects != "read_only":
        try:
            budget_effect = _open_command_budget_effect(spec, ctx)
        except EffectBudgetRefusedError as refusal:
            return _budget_refusal_envelope(spec, refusal, started=started, projection=ctx.projection)

    # -- handler ------------------------------------------------------------
    try:
        handler = resolve_dotted(spec.handler.dotted) if spec.handler else None
    except Exception as exc:
        if budget_effect is not None:
            budget_effect.cancel(reason=f"handler binding failed for {spec.command_id}")
        return _fault_envelope(
            spec,
            "internal",
            f"{spec.command_id} failed: {exc}",
            started=started,
            detail={"error": str(exc)},
            projection=ctx.projection,
        )
    if handler is None:
        if budget_effect is not None:
            # reserved-then-never-run: nothing executed, the unit returns
            budget_effect.cancel(reason=f"unbound handler {spec.command_id}")
        return _fault_envelope(
            spec,
            "internal",
            f"{spec.command_id} is declared without a handler",
            started=started,
            detail={"reason": "unbound handler"},
            projection=ctx.projection,
        )
    if budget_effect is not None:
        # consume immediately before execution — the unit is spent the moment the
        # handler is about to act; a crashed handler stays charged
        budget_effect.begin_attempt()
    try:
        result = handler(typed_input, ctx)
    except Exception as exc:
        if budget_effect is not None:
            budget_effect.fail(reason="command handler raised before an outcome")
        return _fault_envelope(
            spec,
            "internal",
            f"{spec.command_id} failed: {exc}",
            started=started,
            detail={"error": str(exc)},
            projection=ctx.projection,
        )
    if budget_effect is not None:
        if isinstance(result, HandlerOk):
            budget_effect.succeed(reason="command handler settled ok")
        else:
            budget_effect.fail(reason="command handler returned a fault result")

    duration_ms = int((time.monotonic() - started) * 1000)
    execution = ExecutionBlock(
        command_id=spec.command_id,
        platform=_platform(),
        started=_now_iso(),
        duration_ms=duration_ms,
        exit_code=0,
        projection=ctx.projection,
    )
    if isinstance(result, HandlerFault):
        code = result.fault_code if result.fault_code in FAULT_CODE_TO_EXIT else "internal"
        execution = dataclasses.replace(execution, exit_code=FAULT_CODE_TO_EXIT[code])
        declared = tuple(
            pointer
            for binding in spec.fault_bindings
            if binding.when == (result.fault_code if result.fault_code != "internal" else binding.when)
            for pointer in binding.remediation
        )
        return CommandEnvelope(
            ok=False,
            summary=result.summary,
            breadcrumbs=_breadcrumbs(spec, declared),
            fault=FaultBlock(code=code, remediation=declared, detail=result.detail),
            execution=execution,
        )
    if isinstance(result, HandlerOk):
        return CommandEnvelope(
            ok=True,
            data=result.data,
            summary=result.summary,
            breadcrumbs=_breadcrumbs(spec),
            receipts=tuple(result.receipts),
            execution=execution,
        )
    # Handlers may return a plain dataclass/list/dict → treated as typed output.
    return CommandEnvelope(
        ok=True,
        data=result,
        breadcrumbs=_breadcrumbs(spec),
        execution=execution,
    )


def registry_default() -> CommandRegistry:
    from core.command_registry.registry import registry

    return registry()
