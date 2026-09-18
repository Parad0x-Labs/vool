"""Blackbox group — the byte-exact workspace flight recorder.

Binds to ``core.blackbox.operator`` (status / turns / verify / rollback_turn).
The rollback gate mirrors the product's real law: without an operator-granted
internal authority (the two-press ``--approve`` pattern, a bounded expiring
scope for ``workspace.rollback_last_change``), the mode matrix answers
``pending_approval`` and nothing is written. The registry enforces the same
decision at dispatch: no approval context → typed PERMISSION_REQUIRED (exit 20),
no side effect. With one, the real matrix still decides inside the executor.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.blackbox import operator as blackbox_operator
from core.command_registry.spec import (
    ApprovalDecision,
    ApprovalGate,
    Availability,
    CommandSpec,
    FaultBinding,
    GroupSpec,
    Handler,
    HandlerFault,
    HandlerOk,
    NextAction,
)


@dataclass(frozen=True)
class TurnsInput:
    limit: int = 20


@dataclass(frozen=True)
class RollbackInput:
    turn_id: str
    workspace_root: str
    operator: str = "vool"
    session: str = ""


# -- availability probes (machine evidence, never constants) ------------------


def _probe_store_healthy(context: dict) -> tuple[bool, str]:
    try:
        state = blackbox_operator.status()
        if not isinstance(state, dict):
            return False, "blackbox store status unreadable"
        return True, ""
    except Exception as exc:
        return False, f"blackbox store unreachable: {exc}"


def _probe_turns_exist(context: dict) -> tuple[bool, str]:
    try:
        turns = blackbox_operator.list_turns()
        if not turns:
            return False, "no recorded blackbox turns — nothing to roll back"
        return True, f"{len(turns)} recorded turn(s)"
    except Exception as exc:
        return False, f"blackbox store unreachable: {exc}"


# -- permission gate ----------------------------------------------------------


def _gate_rollback_approval(inp, ctx) -> ApprovalDecision:
    """Dispatch-time approval gate for a delete-class effect.

    The operator grants a bounded authority out-of-band
    (``grant_internal_authority`` for ``workspace.rollback_last_change`` under
    this root); the executor re-validates it — this gate only refuses the
    unapproved road early, with a typed envelope instead of a silent prompt.
    """
    approval = ctx.approval_context or {}
    token = str(approval.get("authority_token") or "").strip()
    if not token:
        return ApprovalDecision(
            required=True,
            reason="rollback is a delete-class effect — operator approval required (bounded authority scope)",
        )
    return ApprovalDecision(required=False)


# -- handlers (thin adapters over the authority) ------------------------------


def _handle_status(inp, ctx):
    return HandlerOk(data=blackbox_operator.status(), summary="Blackbox store status")


def _handle_turns(inp, ctx):
    limit = max(1, min(int(inp.limit or 20), 200))
    turns = blackbox_operator.list_turns()[:limit]
    return HandlerOk(data={"count": len(turns), "turns": turns}, summary=f"{len(turns)} recorded turn(s)")


def _handle_verify(inp, ctx):
    state = blackbox_operator.verify()
    ok = bool(isinstance(state, dict) and state.get("ok", state.get("verified", False)))
    if not ok:
        return HandlerFault(
            fault_code="fault_validation",
            summary="Blackbox journal verification failed",
            detail=state if isinstance(state, dict) else {"state": str(state)},
        )
    return HandlerOk(data=state, summary="Blackbox journal verified")


def _handle_rollback(inp, ctx):
    approval = ctx.approval_context or {}
    try:
        result = blackbox_operator.rollback_turn(
            inp.turn_id,
            workspace_root=Path(inp.workspace_root).expanduser(),
            session_id=inp.session or f"operator:{inp.operator}",
            operator=inp.operator,
            source_context={"surface": "command_registry", "projection": ctx.projection},
            authority_token=str(approval.get("authority_token") or "").strip() or None,
        )
    except Exception as exc:
        return HandlerFault(
            fault_code="fault_validation",
            summary=f"Rollback of {inp.turn_id!r} failed: {exc}",
            detail={"turn_id": inp.turn_id, "error": str(exc)},
        )
    ok = bool(getattr(result, "ok", False))
    status = str(getattr(result, "status", "") or "")
    details = dict(getattr(result, "details", {}) or {})
    if not ok:
        code = "permission_required" if "pending_approval" in status else "fault_tool"
        return HandlerFault(
            fault_code=code,
            summary=f"Rollback of {inp.turn_id!r} refused ({status or 'refused'})",
            detail={"turn_id": inp.turn_id, "status": status, "details": details},
        )
    return HandlerOk(
        data={
            "status": status,
            "details": details,
            "response_text": str(getattr(result, "response_text", "") or ""),
        },
        summary=f"Turn {inp.turn_id} rolled back",
        receipts=({"kind": "blackbox_rollback", "turn_id": inp.turn_id},),
    )


def register(reg) -> None:
    reg.add_group(
        GroupSpec(
            group_id="blackbox",
            description="byte-exact workspace flight recorder: status, turns, verify, rollback",
        )
    )
    reg.add(
        CommandSpec(
            command_id="blackbox.status",
            group="blackbox",
            description="Blackbox recorder store status",
            aliases=("blackbox",),
            effects="read_only",
            capabilities=frozenset({"blackbox.read"}),
            handler=Handler("core.command_registry.groups.blackbox_group:_handle_status"),
            availability=Availability("core.command_registry.groups.blackbox_group:_probe_store_healthy"),
            exit_codes=(0, 10),
            model_offerable=True
        )
    )
    reg.add(
        CommandSpec(
            command_id="blackbox.turns",
            group="blackbox",
            description="List recorded blackbox turns (authorized workspace effects)",
            input_schema=TurnsInput,
            effects="read_only",
            capabilities=frozenset({"blackbox.read"}),
            handler=Handler("core.command_registry.groups.blackbox_group:_handle_turns"),
            exit_codes=(0, 2),
            next_actions=(NextAction(command_id="blackbox.status", label="Store status"),),
            model_offerable=True
        )
    )
    reg.add(
        CommandSpec(
            command_id="blackbox.verify",
            group="blackbox",
            description="Verify the blackbox journal (chain integrity)",
            effects="read_only",
            capabilities=frozenset({"blackbox.read"}),
            handler=Handler("core.command_registry.groups.blackbox_group:_handle_verify"),
            availability=Availability("core.command_registry.groups.blackbox_group:_probe_store_healthy"),
            fault_bindings=(
                FaultBinding(when="chain_corrupt", fault_code="fault_validation", remediation=("vool faults list",)),
            ),
            exit_codes=(0, 10, 42),
        )
    )
    reg.add(
        CommandSpec(
            command_id="blackbox.rollback",
            group="blackbox",
            description="Roll back one recorded turn (delete-class effect; operator approval required)",
            input_schema=RollbackInput,
            effects="destructive",
            capabilities=frozenset({"workspace.rollback_last_change"}),
            permission=ApprovalGate(
                kind="workspace.rollback_last_change",
                verifier="core.command_registry.groups.blackbox_group:_gate_rollback_approval",
            ),
            handler=Handler("core.command_registry.groups.blackbox_group:_handle_rollback"),
            availability=Availability("core.command_registry.groups.blackbox_group:_probe_turns_exist"),
            fault_bindings=(
                FaultBinding(when="turn_missing", fault_code="fault_validation", remediation=("vool blackbox turns",)),
                FaultBinding(when="cas_conflict", fault_code="conflict", remediation=("vool blackbox turns",)),
            ),
            exit_codes=(0, 2, 10, 20, 30, 40, 42),
            lifecycle="preview",
            next_actions=(
                NextAction(command_id="blackbox.verify", label="Verify the journal after rollback"),
                NextAction(command_id="blackbox.turns", label="List remaining turns"),
            ),
        )
    )
