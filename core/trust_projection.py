"""C23's thin read-only projection: one view over authorities that already exist.

The row C23 owns says a normal operator has no single coherent place to read
whether the system is behaving: the fault catalog, security events, the effect
budget, the honesty receipt chain and skill provenance are each real, each wired,
and each reachable only on its own terms. This composes them. It does nothing
else.

**It is not an authority and must never become one.** Three properties keep that
true, and each has a test that bites:

*It writes nothing.* Every accessor below is a read. Nothing here reserves,
consumes, records or repairs.

*It invents no verdict.* Where a source has its own words for a state, those words
are carried through unchanged — the receipt section copies the verifier's proven
and not-proven claims verbatim rather than restating them. Where a source cannot
be read, the section says so and carries NO value. That matters more than it
looks: a budget section that renders empty on a failed read is indistinguishable
from "no limits configured", so absent data would read as permissive. It refuses
to.

*It never flattens the two kinds of limit.* `enforced_budgets` are the hard effect
budgets in `core.effect_budget`: a reservation that exceeds one is refused and the
effect does not happen. `soft_guides` is the Settings `daily_token_budget`, a
cloud-token guide that nothing enforces and no gate reads. They are different
authorities with different consequences, they are returned under different keys,
and there is deliberately no helper that merges them — a projection that showed
one number for "your limits" would be telling the operator that a guide will stop
something, which it will not.
"""
from __future__ import annotations

from typing import Any

#: Section key -> (authority module, the exact accessor a caller runs for raw evidence).
#: Rendered into the payload so nothing here hides the source it summarised.
EVIDENCE_POINTERS: dict[str, tuple[str, str]] = {
    "enforced_budgets": ("core.effect_budget", "core.effect_budget.budget_status(<class>)"),
    "soft_guides": ("core.user_preferences", "core.user_preferences.load_preferences()"),
    "security_events": ("core.security_events.store", "core.security_events.store.list_security_events()"),
    "receipt_chain": ("core.honesty_receipt", "core.honesty_receipt.verify_honesty_chain(<receipts>)"),
    "fault_vocabulary": ("core.faults.catalog", "core.faults.catalog.CATEGORIES"),
}


def _unavailable(section: str, exc: BaseException) -> dict[str, Any]:
    """A section that could not be read carries no value at all.

    Not an empty list, not a zero, not a default: those are the shapes that read
    as "nothing to worry about" while meaning "I could not look".
    """
    authority, accessor = EVIDENCE_POINTERS[section]
    return {
        "available": False,
        "unavailable_reason": f"{type(exc).__name__}: {exc}",
        "authority": authority,
        "raw_evidence": accessor,
    }


def _ok(section: str, **payload: Any) -> dict[str, Any]:
    authority, accessor = EVIDENCE_POINTERS[section]
    return {"available": True, "authority": authority, "raw_evidence": accessor, **payload}


def _enforced_budgets(*, session_id: str, turn_id: str) -> dict[str, Any]:
    try:
        from core.effect_budget import BUDGET_CLASSES, budget_status

        rules: list[dict[str, Any]] = []
        for budget_class in sorted(BUDGET_CLASSES):
            for status in budget_status(budget_class, session_id=session_id, turn_id=turn_id):
                rules.append(
                    {
                        "budget_class": budget_class,
                        "rule": getattr(status.rule, "rule_id", "") or str(status.rule),
                        "limit": status.limit,
                        "used": status.used,
                        "remaining": status.remaining,
                        "applies_to_this_identity": status.applies,
                    }
                )
        return _ok(
            "enforced_budgets",
            enforced=True,
            consequence="a reservation beyond the limit is refused and the effect does not happen",
            rules=rules,
            configured_rule_count=len(rules),
            # Stated rather than inferred from an empty list. "I read the store and
            # nothing is configured" and "I could not read the store" are different
            # facts that render identically if a caller only counts rows.
            no_rules_configured=not rules,
        )
    except BaseException as exc:  # any failure must not read as "no limits"
        return _unavailable("enforced_budgets", exc)


def _soft_guides() -> dict[str, Any]:
    try:
        from core.user_preferences import load_preferences

        prefs = load_preferences()
        daily = int(getattr(prefs, "daily_token_budget", 0) or 0)
        return _ok(
            "soft_guides",
            enforced=False,
            consequence="nothing reads this before spending; it stops no effect",
            guides=[
                {
                    "name": "daily_token_budget",
                    "value": daily,
                    "unit": "cloud tokens per day",
                    "unlimited": daily == 0,
                    "enforced": False,
                    "not_the_same_as": "enforced_budgets (core.effect_budget)",
                }
            ],
        )
    except BaseException as exc:
        return _unavailable("soft_guides", exc)


def _security_events(limit: int) -> dict[str, Any]:
    try:
        from core.security_events.store import list_security_events

        events = list_security_events(limit=limit)
        by_code: dict[str, int] = {}
        for event in events:
            code = str(getattr(event, "code", "") or "unknown")
            by_code[code] = by_code.get(code, 0) + 1
        return _ok(
            "security_events",
            count=len(events),
            by_code=dict(sorted(by_code.items())),
            window="most recent",
            limit=limit,
        )
    except BaseException as exc:
        return _unavailable("security_events", exc)


def _receipt_chain(session_id: str) -> dict[str, Any]:
    try:
        from core.honesty_receipt import (
            CHAIN_PROVEN_CLAIM,
            CHAIN_UNPROVEN_CLAIM,
            list_honesty_receipts,
            verify_honesty_chain,
        )

        receipts = list_honesty_receipts(session_id) if session_id else []
        ok, reason = verify_honesty_chain(receipts)
        return _ok(
            "receipt_chain",
            chain_consistent=bool(ok),
            receipt_count=len(receipts),
            failure_reason=("" if ok else str(reason)),
            # Carried verbatim from the verifier. Restating them here in this
            # module's own words is how a projection turns into a second claim.
            proven=CHAIN_PROVEN_CLAIM,
            not_proven=CHAIN_UNPROVEN_CLAIM,
            completeness_proven=False,
        )
    except BaseException as exc:
        return _unavailable("receipt_chain", exc)


def _fault_vocabulary() -> dict[str, Any]:
    try:
        from core.faults import catalog

        categories = [
            str(value)
            for name, value in vars(catalog).items()
            if name.startswith("CATEGORY_") and isinstance(value, str)
        ]
        return _ok(
            "fault_vocabulary",
            categories=sorted(categories),
            schema=str(getattr(catalog, "FAULT_SCHEMA", "")),
            catalog_version=int(getattr(catalog, "CATALOG_VERSION", 0) or 0),
        )
    except BaseException as exc:
        return _unavailable("fault_vocabulary", exc)


def trust_projection(
    *, session_id: str = "", turn_id: str = "", security_event_limit: int = 50
) -> dict[str, Any]:
    """Compose the existing authorities into one read-only view.

    Sections are independent: one unreadable source does not blank the others, and
    does not silently become a reassuring default.
    """
    sections = {
        "enforced_budgets": _enforced_budgets(session_id=session_id, turn_id=turn_id),
        "soft_guides": _soft_guides(),
        "security_events": _security_events(security_event_limit),
        "receipt_chain": _receipt_chain(session_id),
        "fault_vocabulary": _fault_vocabulary(),
    }
    return {
        "projection_version": 1,
        "is_authority": False,
        "reads_only": True,
        "session_id": session_id,
        "sections": sections,
        "unavailable_sections": sorted(
            key for key, value in sections.items() if not value.get("available")
        ),
    }
