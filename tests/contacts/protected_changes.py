"""The protected-change flow for tests: the production mutation authority (core.contacts.authority) and the production
operator credential (core.operator_credential) with one synthetic PIN.

Nothing here stands in for the authority. A test that changes a saved contact proposes the change and confirms it with
the synthetic PIN exactly as the review in Contacts does, through ``authority.confirm`` or the
/api/contacts/operations/confirm door. The operating system's consent prompt that guards enrollment is answered by the
test override (core.os_consent_gate.set_consent_override_for_tests), so no real prompt can appear during a run.
"""
from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from typing import Any

from core import os_consent_gate
from core.contacts import authority, store
from core.operator_credential import authority as credential

SYNTHETIC_PIN = "482913"


@contextlib.contextmanager
def system_consent(granted: bool = True) -> Iterator[list[str]]:
    """Answer the operating system's consent prompt for the duration; yields the reasons it was asked with."""
    asked: list[str] = []

    def answer(reason: str) -> bool:
        asked.append(reason)
        return granted

    os_consent_gate.set_consent_override_for_tests(answer)
    try:
        yield asked
    finally:
        os_consent_gate.set_consent_override_for_tests(None)


def enroll_synthetic_pin() -> None:
    if not credential.status()["enrolled"]:
        with system_consent(True):
            credential.enroll(SYNTHETIC_PIN, kind="pin")


def reset() -> None:
    store.reset_contacts_for_tests()
    credential.reset_for_tests()


def confirm(operation: dict[str, Any]) -> dict[str, Any]:
    """Confirm one pending operation with the synthetic PIN, as the Contacts review does. Returns the commit."""
    enroll_synthetic_pin()
    committed = authority.confirm(operation["operation_id"], secret=SYNTHETIC_PIN, expected_digest=operation["digest"])
    assert committed["status"] == "committed", committed
    return committed


def apply(change: dict[str, Any], *, source: str = authority.SOURCE_OWNER_UI) -> dict[str, Any]:
    """Propose a change and confirm it when it waits. Returns the committed result (created, updated, deleted)."""
    outcome = authority.propose(change, source=source, requested_by="test")
    if outcome["status"] == "committed":
        return outcome["result"]
    if outcome["status"] == "unchanged":
        return {"created": [], "updated": [], "deleted": [], "accepted_suggestions": []}
    assert outcome["status"] in ("pending_authentication", "already_pending"), outcome
    return confirm(outcome["operation"])["result"]


def update_contact(contact_id: str, **changes: Any) -> dict[str, Any]:
    result = apply({"operation": "update", "contact_id": contact_id, **changes})
    updated = next((entry for entry in result.get("updated") or [] if entry["contact_id"] == contact_id), {"changes": []})
    return {"contact": store.get_contact(contact_id), "changes": updated["changes"]}


def delete_contact(contact_id: str) -> dict[str, Any]:
    return apply({"operation": "delete", "contact_id": contact_id})["deleted"][0]


def create_contact(**fields: Any) -> dict[str, Any]:
    """Create a contact even when its name is the same as, or looks like, a saved one (confirmed with the synthetic PIN)."""
    result = apply({"operation": "create", **fields})
    contact = store.get_contact(result["created"][0]["contact_id"])
    assert contact is not None
    return contact


def confirm_through_the_door(post: Callable[..., tuple[int, dict[str, Any]]], operation: dict[str, Any], *, secret: str = SYNTHETIC_PIN) -> tuple[int, dict[str, Any]]:
    """The owner's review action through /api/contacts/operations/confirm, with the PIN in the body."""
    enroll_synthetic_pin()
    return post("/api/contacts/operations/confirm", {"operation_id": operation["operation_id"], "digest": operation["digest"], "secret": secret})


__all__ = ["SYNTHETIC_PIN", "apply", "confirm", "confirm_through_the_door", "create_contact", "delete_contact", "enroll_synthetic_pin", "reset",
           "system_consent", "update_contact"]
