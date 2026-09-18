"""Approvals group — the pending-approval queue and its resolver.

Binds to ``core.mode_permission_policy``: the persisted pending-approval store
and ``resolve_approval`` (allow/deny, once/scope semantics preserved verbatim).
The resolver is the operator's authority act: only operator principals may
dispatch it — the model lane never holds this principal.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.command_registry.spec import (
    AuthorityDecision,
    Availability,
    CommandSpec,
    FaultBinding,
    GroupSpec,
    Handler,
    HandlerFault,
    HandlerOk,
    NextAction,
    OperatorAuthority,
)


@dataclass(frozen=True)
class ResolveInput:
    approval_id: str
    decision: str
    # The width the operator chose on the resolving surface ("once"/"task"/"request"/"project").
    # The policy stays the sole authority on whether a scope may mint: "request" only for an
    # approval that carries a real fingerprint batch, "project" only when eligible, and an edit
    # batch never broadens past "once".
    scope: str = "once"


def _probe_approval_store(context: dict) -> tuple[bool, str]:
    try:
        from core.mode_permission_policy import _LOCK, _ensure_approvals_restored

        with _LOCK:
            _ensure_approvals_restored()
        return True, ""
    except Exception as exc:
        return False, f"approval store unreachable: {exc}"


def _gate_resolve(inp, ctx) -> AuthorityDecision:
    if str(ctx.principal or "") != "operator":
        return AuthorityDecision(granted=False, reason="only the operator may resolve approvals")
    return AuthorityDecision(granted=True)


def _pending_rows() -> list[dict]:
    from core.mode_permission_policy import _APPROVALS, _LOCK, _ensure_approvals_restored

    with _LOCK:
        _ensure_approvals_restored()
        return [dict(a) for a in _APPROVALS.values() if a.get("status") == "pending"]


def _handle_pending(inp, ctx):
    pending = [
        {
            "approval_id": str(a.get("approval_id") or a.get("token") or ""),
            "intent": str(a.get("intent") or ""),
            "action": str(a.get("action") or ""),
            "expected_side_effects": str(a.get("expected_side_effects") or ""),
            "expires_at": a.get("expires_at"),
            "one_time": bool(a.get("one_time", True)),
        }
        for a in _pending_rows()
    ]
    pending.sort(key=lambda p: float(p.get("expires_at") or 0))
    return HandlerOk(
        data={"count": len(pending), "pending": pending},
        summary=f"{len(pending)} pending approval(s)",
    )


def _handle_resolve(inp, ctx):
    from core.mode_permission_policy import resolve_approval

    decision = inp.decision.strip().lower()
    if decision not in {"allow", "deny"}:
        return HandlerFault(
            fault_code="usage",
            summary=f"decision must be 'allow' or 'deny', got {inp.decision!r}",
            detail={"decision": inp.decision},
        )
    approval = resolve_approval(inp.approval_id, decision=decision, scope=str(inp.scope or "once"))
    if approval is None:
        return HandlerFault(
            fault_code="conflict",
            summary=f"Approval {inp.approval_id!r} is missing, stale, or already resolved",
            detail={"approval_id": inp.approval_id},
        )
    return HandlerOk(
        data=approval,
        summary=f"Approval {inp.approval_id} resolved: {decision}",
        receipts=({"kind": "approval_resolved", "approval_id": inp.approval_id, "decision": decision},),
    )


def register(reg) -> None:
    reg.add_group(
        GroupSpec(
            group_id="approvals",
            description="pending operator approvals and their resolution",
            aliases=("permissions",),
        )
    )
    reg.add(
        CommandSpec(
            command_id="approvals.pending",
            group="approvals",
            description="List approvals waiting for an operator decision",
            aliases=("approvals",),
            effects="read_only",
            capabilities=frozenset({"approvals.read"}),
            handler=Handler("core.command_registry.groups.approvals:_handle_pending"),
            exit_codes=(0,),
            next_actions=(NextAction(command_id="approvals.resolve", label="Resolve a pending approval"),),
        )
    )
    reg.add(
        CommandSpec(
            command_id="approvals.resolve",
            group="approvals",
            description="Resolve a pending approval (allow or deny)",
            input_schema=ResolveInput,
            effects="mutating",
            capabilities=frozenset({"approvals.resolve"}),
            permission=OperatorAuthority(
                kind="approvals.resolve",
                verifier="core.command_registry.groups.approvals:_gate_resolve",
            ),
            handler=Handler("core.command_registry.groups.approvals:_handle_resolve"),
            availability=Availability("core.command_registry.groups.approvals:_probe_approval_store"),
            fault_bindings=(FaultBinding(when="stale", fault_code="conflict", remediation=("vool approvals pending",)),),
            exit_codes=(0, 2, 21, 30),
            next_actions=(NextAction(command_id="approvals.pending", label="List pending approvals"),),
        )
    )
