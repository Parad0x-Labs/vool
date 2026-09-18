"""Resolution: attach a canonical identity to the slots a proof bound, and nothing else.

    prove  ->  capture  ->  RESOLVE  ->  realize  ->  bind  ->  execute  ->  reduce

This module owns exactly one stage of that, and the narrowness is the point. A resolver's only
authority is over a SLOT. It cannot reach the requirement's id, its edges, or whether it is
required -- those are fixed at capture, which is what makes a resolver failure survivable.
`Palladium` resolves to nothing and stays a requirement the answer owes the reader a sentence
about, and because it never resolves, nothing ever calls a quote for it.

What used to live here and no longer does: a second projection of the ledger into nodes, a second
per-family table of subject roles, and `CompletionVerdict` -- three questions about shipping that
were computed here, computed again in the obligation floor, and answered a third time at the
dispatch seam. Realization owns projection now (`core.conductor.realization`) and
`core.conductor.product_decision` owns the shipping answer, once. Which role carries a family's
subject is declared in `core.conductor.frame_contracts` and read from there, so the table exists in
one place rather than three.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from core.conductor.registry import OperationDefectError, operation_spec
from core.conductor.requirements import (
    RequirementLedger,
    SlotResolution,
    bind_derived_inputs,
)
from core.conductor.semantic_proof import frame_contract


def resolve_ledger(ledger: RequirementLedger) -> RequirementLedger:
    """Attach a canonical identity to every slot that has one. Adds and removes nothing."""
    resolved = ledger
    for requirement in ledger.requirements:
        contract = frame_contract(requirement.family)
        if contract is None or not contract.roles:
            continue
        spec = operation_spec(requirement.family)
        slots = []
        for slot in requirement.slots:
            if slot.role not in contract.roles:
                slots.append(slot)
                continue
            try:
                arguments = _resolve_slot(spec, contract, slot.surface)
            except OperationDefectError as exc:
                # A resolver that CRASHED is not a subject nobody could identify. Typed apart here
                # so the difference survives all the way to the reader.
                slots.append(
                    replace(
                        slot,
                        resolution=SlotResolution.RESOLVER_DEFECT,
                        canonical=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue
            if arguments is None:
                slots.append(replace(slot, resolution=SlotResolution.UNRESOLVED_SUBJECT))
            else:
                slots.append(
                    replace(
                        slot,
                        resolution=SlotResolution.RESOLVED,
                        canonical=str(arguments.get("entity") or slot.surface),
                        arguments=dict(arguments),
                    )
                )
        resolved = resolved.with_requirement(replace(requirement, slots=tuple(slots)))
    # Slot-level narrowing runs here, once canonical identity exists. Requirement-level edges were
    # fixed at capture and are not touched.
    return bind_derived_inputs(resolved)


def _resolve_slot(spec: Any, contract: Any, surface: str) -> dict[str, Any] | None:
    """One slot surface into typed arguments, or None when nothing can bind it.

    A family with no fan-out role binds at the REQUIREMENT level, where its roles combine -- a base
    currency alone is not a conversion -- so its slots are accepted as written and the whole frame
    is realized together. That is read from the frame contract rather than from a set of family
    names kept here, which is one fewer table to disagree with the others.
    """
    if not contract.resolves_per_role:
        return {"entity": surface}
    if spec is None or spec.realize_subject is None:
        return None
    from core.conductor.registry import realize_subject

    return realize_subject(spec, surface)


__all__ = ["resolve_ledger"]
