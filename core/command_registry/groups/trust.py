"""C23's projection, exposed where the operator already looks for truth.

One command, `read_only`, no new state. The handler calls
``core.trust_projection.trust_projection`` and returns what it composed; it adds
no field the projection did not produce, so there is exactly one place where the
shape of this view is decided.

Not registered as a second authority: the capabilities it declares are the READ
capabilities of the authorities it summarises, so a permission decision about this
command is the same decision as reading each source directly.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.command_registry.spec import (
    CommandSpec,
    GroupSpec,
    Handler,
    HandlerFault,
    HandlerOk,
    NextAction,
)


@dataclass(frozen=True)
class ProjectionInput:
    session_id: str = ""
    security_event_limit: int = 50


def _handle_trust_projection(inp, ctx):
    from core.trust_projection import trust_projection

    payload = trust_projection(
        session_id=str(getattr(inp, "session_id", "") or ""),
        security_event_limit=max(1, min(int(getattr(inp, "security_event_limit", 50) or 50), 500)),
    )
    unavailable = list(payload.get("unavailable_sections") or [])
    if len(unavailable) == len(payload.get("sections") or {}):
        # Every source unreadable. Returning an "all clear" shaped payload here is
        # the exact failure this projection exists to avoid.
        return HandlerFault(
            fault_code="fault_validation",
            summary="no trust authority could be read; this is not a clean state",
            detail={"unavailable_sections": unavailable},
        )
    summary = "trust projection over " + ", ".join(sorted(payload.get("sections") or {}))
    if unavailable:
        summary += f" — unreadable: {', '.join(unavailable)}"
    return HandlerOk(data=payload, summary=summary)


def register(reg) -> None:
    reg.add_group(
        GroupSpec(
            group_id="trust",
            description="one read-only view over the budget, security, receipt and fault authorities",
        )
    )
    reg.add(
        CommandSpec(
            command_id="trust.projection",
            group="trust",
            description=(
                "Read-only projection of enforced effect budgets, soft cloud-token guides, "
                "recent security events, receipt-chain consistency and the fault vocabulary"
            ),
            input_schema=ProjectionInput,
            effects="read_only",
            capabilities=frozenset(
                {"effect_budget.read", "security_events.read", "honesty_chain.verify"}
            ),
            handler=Handler("core.command_registry.groups.trust:_handle_trust_projection"),
            exit_codes=(0, 2),
            next_actions=(
                NextAction(command_id="receipts.verify", label="Verify the honesty chain itself"),
                NextAction(command_id="receipts.list", label="Read the raw finalization receipts"),
            ),
            model_offerable=True,
        )
    )
