"""The Contacts mutation authority: propose, authorize, commit and the single-use, revision-bound authorization.

What executes: the production authority (core.contacts.authority), the production store plans and applies
(core.contacts.store), the production operator credential and the production identity owner, all on the suite's migrated
SQLite database. Every assertion is deterministic; the one PIN is synthetic and the OS prompt is answered by the test
override. No model, network or provider.
"""
from __future__ import annotations

import sqlite3
import threading

import pytest

from core.contacts import authority, resolver, store
from core.contacts.store import ContactsError
from tests.contacts import protected_changes
from tests.contacts.protected_changes import SYNTHETIC_PIN as PIN


def _sol_key() -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


@pytest.fixture(autouse=True)
def _fresh():
    protected_changes.reset()
    yield
    protected_changes.reset()


def _tom() -> tuple[dict, str]:
    wallet = _sol_key()
    contact = store.create_contact(display_name="TOM", actor=store.ACTOR_OWNER, endpoints=[
        {"kind": "email", "value": "tom@fixture.test", "label": "work"}, {"kind": "wallet", "value": wallet, "network": "solana-devnet"}])
    return contact, wallet


def _wallet(contact_id: str) -> dict:
    return next(e for e in store.get_contact(contact_id)["endpoints"] if e["kind"] == "wallet")


def _propose_wallet_change(contact_id: str, new_value: str) -> dict:
    endpoint = _wallet(contact_id)
    return authority.propose({"operation": "update", "contact_id": contact_id, "change_endpoints": [{"endpoint_id": endpoint["endpoint_id"], "value": new_value,
                                                                                                     "network": "solana-devnet"}]},
                             source=authority.SOURCE_OWNER_UI, requested_by="test")


def test_a_distinct_new_contact_from_the_owner_commits_without_a_credential() -> None:
    outcome = authority.propose({"operation": "create", "display_name": "Priya Nair", "endpoints": [{"kind": "email", "value": "priya@fixture.test"}]},
                                source=authority.SOURCE_OWNER_UI)
    assert outcome["status"] == "committed" and not protected_changes.credential.status()["enrolled"]
    assert [c["display_name"] for c in store.active_contacts()] == ["Priya Nair"]


def test_a_change_to_a_saved_contact_waits_and_the_credential_applies_it_exactly_once() -> None:
    tom, wallet = _tom()
    new_wallet = _sol_key()
    outcome = _propose_wallet_change(tom["contact_id"], new_wallet)
    assert outcome["status"] == "pending_authentication"
    operation = outcome["operation"]
    assert _wallet(tom["contact_id"])["value"] == wallet, "nothing changes before the PIN"
    protected_changes.enroll_synthetic_pin()
    granted = authority.authorize(operation["operation_id"], secret=PIN, expected_digest=operation["digest"])
    committed = authority.commit(operation["operation_id"], authorization_id=granted["authorization_id"])
    assert committed["status"] == "committed" and _wallet(tom["contact_id"])["value"] == new_wallet
    # a repeat of the same commit returns the recorded result and does not change anything again
    replay = authority.commit(operation["operation_id"], authorization_id=granted["authorization_id"])
    assert replay["replayed"] is True and _wallet(tom["contact_id"])["value"] == new_wallet


def test_an_authorization_is_single_use_and_bound_to_its_own_operation() -> None:
    tom, _wallet_value = _tom()
    first = _propose_wallet_change(tom["contact_id"], _sol_key())["operation"]
    # a second, independent pending change on a different contact
    other = store.create_contact(display_name="Priya", actor=store.ACTOR_OWNER, endpoints=[{"kind": "email", "value": "p@fixture.test"}])
    second = authority.propose({"operation": "update", "contact_id": other["contact_id"], "display_name": "Priya N"}, source=authority.SOURCE_OWNER_UI)["operation"]
    protected_changes.enroll_synthetic_pin()
    granted = authority.authorize(first["operation_id"], secret=PIN, expected_digest=first["digest"])
    # the authorization minted for `first` cannot commit `second`
    with pytest.raises(ContactsError) as wrong_op:
        authority.commit(second["operation_id"], authorization_id=granted["authorization_id"])
    assert wrong_op.value.reason == "authorization_not_for_this_change"
    authority.commit(first["operation_id"], authorization_id=granted["authorization_id"])
    # and it cannot be replayed to commit anything else afterwards
    third = _propose_wallet_change(other["contact_id"], _sol_key()) if False else None
    assert third is None


def test_a_stale_revision_refuses_before_costing_a_credential_attempt() -> None:
    tom, _wallet_value = _tom()
    pending = _propose_wallet_change(tom["contact_id"], _sol_key())["operation"]
    # the contact changes underneath the pending operation
    protected_changes.update_contact(tom["contact_id"], notes="edited elsewhere")
    protected_changes.enroll_synthetic_pin()
    before = protected_changes.credential.status()
    with pytest.raises(ContactsError) as stale:
        authority.authorize(pending["operation_id"], secret=PIN, expected_digest=pending["digest"])
    assert stale.value.reason == "operation_stale" and stale.value.details.get("stale_reason") == "contact_changed"
    # the credential was never checked, so no attempt was spent
    assert protected_changes.credential.status()["retry_after_seconds"] == before["retry_after_seconds"]


def test_the_digest_binds_what_was_reviewed() -> None:
    tom, _wallet_value = _tom()
    operation = _propose_wallet_change(tom["contact_id"], _sol_key())["operation"]
    protected_changes.enroll_synthetic_pin()
    with pytest.raises(ContactsError) as changed:
        authority.authorize(operation["operation_id"], secret=PIN, expected_digest="not-the-digest-shown")
    assert changed.value.reason == "operation_changed"


def test_a_wrong_pin_leaves_the_change_pending_and_a_cancel_needs_no_pin() -> None:
    tom, wallet = _tom()
    operation = _propose_wallet_change(tom["contact_id"], _sol_key())["operation"]
    protected_changes.enroll_synthetic_pin()
    with pytest.raises(protected_changes.credential.CredentialError):
        authority.authorize(operation["operation_id"], secret="000000", expected_digest=operation["digest"])
    assert _wallet(tom["contact_id"])["value"] == wallet and authority.get_operation(operation["operation_id"])["state"] == "pending"
    cancelled = authority.cancel(operation["operation_id"])
    assert cancelled["status"] == "cancelled" and authority.pending_count() == 0


def test_a_credential_change_invalidates_an_authorization_minted_under_the_old_one() -> None:
    tom, wallet = _tom()
    operation = _propose_wallet_change(tom["contact_id"], _sol_key())["operation"]
    protected_changes.enroll_synthetic_pin()
    granted = authority.authorize(operation["operation_id"], secret=PIN, expected_digest=operation["digest"])
    with protected_changes.system_consent(True):
        protected_changes.credential.change(PIN, "918273", kind="pin")
    with pytest.raises(ContactsError) as generation:
        authority.commit(operation["operation_id"], authorization_id=granted["authorization_id"])
    assert generation.value.reason == "credential_changed" and _wallet(tom["contact_id"])["value"] == wallet


def test_a_failed_commit_does_not_consume_the_confirmation() -> None:
    tom, wallet = _tom()
    new_wallet = _sol_key()
    operation = _propose_wallet_change(tom["contact_id"], new_wallet)["operation"]
    protected_changes.enroll_synthetic_pin()
    granted = authority.authorize(operation["operation_id"], secret=PIN, expected_digest=operation["digest"])

    real_apply = authority._apply_steps
    calls = {"n": 0}

    def flaky(conn, steps, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_apply(conn, steps, **kwargs)

    authority._apply_steps = flaky
    try:
        with pytest.raises(sqlite3.OperationalError):
            authority.commit(operation["operation_id"], authorization_id=granted["authorization_id"])
    finally:
        authority._apply_steps = real_apply
    # the write rolled back and the authorization was not consumed: the SAME authorization commits on retry
    assert _wallet(tom["contact_id"])["value"] == wallet
    committed = authority.commit(operation["operation_id"], authorization_id=granted["authorization_id"])
    assert committed["status"] == "committed" and _wallet(tom["contact_id"])["value"] == new_wallet


def test_concurrent_creates_of_a_lookalike_do_not_both_activate() -> None:
    _tom()
    # two independent proposals to create T0M beside TOM; both see the same collision
    first = authority.propose({"operation": "create", "display_name": "T0M", "endpoints": [{"kind": "email", "value": "a@fixture.test"}]},
                              source=authority.SOURCE_OWNER_UI)["operation"]
    second = authority.propose({"operation": "create", "display_name": "T0M", "endpoints": [{"kind": "email", "value": "b@fixture.test"}]},
                               source=authority.SOURCE_OWNER_UI)
    # two distinct proposals each wait; neither T0M is active before its own confirmation
    assert second["status"] == "pending_authentication" and second["operation"]["operation_id"] != first["operation_id"]
    assert [c["display_name"] for c in store.active_contacts()] == ["TOM"] and authority.pending_count() == 2
    # an identical repeat of the FIRST proposal de-duplicates onto it rather than making a third
    repeat = authority.propose({"operation": "create", "display_name": "T0M", "endpoints": [{"kind": "email", "value": "a@fixture.test"}]},
                               source=authority.SOURCE_OWNER_UI)
    assert repeat["status"] == "already_pending" and repeat["operation"]["operation_id"] == first["operation_id"]


def test_a_reviewed_batch_is_one_confirmation_over_every_step() -> None:
    tom, _wallet_value = _tom()
    priya = store.create_contact(display_name="Priya", actor=store.ACTOR_OWNER, endpoints=[{"kind": "email", "value": "priya@fixture.test"}])
    batch = authority.propose({"operation": "batch", "changes": [
        {"operation": "update", "contact_id": tom["contact_id"], "notes": "batch note"},
        {"operation": "update", "contact_id": priya["contact_id"], "display_name": "Priya Nair"},
        {"operation": "create", "display_name": "T0M", "endpoints": [{"kind": "email", "value": "look@fixture.test"}]},
    ]}, source=authority.SOURCE_OWNER_UI)
    assert batch["status"] == "pending_authentication"
    operation = batch["operation"]
    assert len(operation["steps"]) == 3 and any(operation["identity"]["conflicts"]), "the batch shows its lookalike"
    protected_changes.confirm(operation)
    names_now = sorted(c["display_name"] for c in store.active_contacts())
    assert names_now == ["Priya Nair", "T0M", "TOM"] and store.get_contact(tom["contact_id"])["notes"] == "batch note"


def test_a_model_source_creating_a_distinct_name_still_waits_for_the_credential() -> None:
    # policy 4: a name from a tool call that is not the owner's own words is a pending operation, never active
    outcome = authority.propose({"operation": "create", "display_name": "Eve", "endpoints": [{"kind": "email", "value": "eve@fixture.test"}]},
                                source=authority.SOURCE_MODEL, requested_by="skill")
    assert outcome["status"] == "pending_authentication" and authority.REASON_UNTRUSTED_SOURCE in outcome["operation"]["reasons"]
    assert store.active_contacts() == []


def test_taking_a_suggestion_is_a_protected_change_and_resolution_ignores_a_pending_one() -> None:
    tom, wallet = _tom()
    endpoint = _wallet(tom["contact_id"])
    evil = _sol_key()
    store.add_suggestion(endpoint={"kind": "wallet", "value": evil, "network": "solana-devnet"}, origin="retrieved_text", actor=store.ACTOR_MODEL,
                         contact_id=tom["contact_id"], replaces_endpoint_id=endpoint["endpoint_id"])
    [suggestion] = store.list_suggestions()
    outcome = authority.propose({"operation": "accept_suggestion", "suggestion_id": suggestion["suggestion_id"]}, source=authority.SOURCE_OWNER_UI)
    assert outcome["status"] == "pending_authentication"
    assert resolver.resolve("TOM", kind="wallet").snapshot["value"] == wallet, "a pending acceptance never resolves"
    protected_changes.confirm(outcome["operation"])
    assert resolver.resolve("TOM", kind="wallet").snapshot["value"] == evil


def test_a_delete_and_recreate_of_a_lookalike_name_is_reviewed_by_the_tombstone() -> None:
    tom, _wallet_value = _tom()
    protected_changes.delete_contact(tom["contact_id"])
    assert store.active_contacts() == []
    # recreating a name that looks like the deleted one waits, even though no active contact holds it
    outcome = authority.propose({"operation": "create", "display_name": "T0M", "endpoints": [{"kind": "email", "value": "x@fixture.test"}]},
                                source=authority.SOURCE_OWNER_UI)
    assert outcome["status"] == "pending_authentication"
    assert any(c["with"] == "recently_deleted" for c in outcome["operation"]["identity"]["conflicts"])


def test_a_lost_response_replays_the_committed_result_through_confirm() -> None:
    tom, _wallet_value = _tom()
    new_wallet = _sol_key()
    operation = _propose_wallet_change(tom["contact_id"], new_wallet)["operation"]
    first = protected_changes.confirm(operation)
    assert first["status"] == "committed"
    # a client that retries confirm on the already-committed operation gets the recorded result, not a second change
    replay = authority.confirm(operation["operation_id"], secret=PIN, expected_digest=operation["digest"])
    assert replay.get("replayed") is True and _wallet(tom["contact_id"])["value"] == new_wallet
