# ruff: noqa: F811 (imported pytest fixtures are re-exposed as test parameters by design)
"""P0 credential intelligence — the credential store: persistence, opaque bindings, explicit
lifecycle (replace/delete/revoke/reconcile), timeout reconciliation, malformed storage, restart.

THE LAW UNDER TEST
------------------
* The raw secret lands ONLY in the existing encrypted store (Keychain or AES vault, via
  ``core.credential_store``); the binding index carries provider/account/capability/status/
  last-verified and a digest — never the value, never a masked suffix.
* A bounded Keychain write that times out cannot leave a DUPLICATE (same value live in two
  backends) or a SHADOW (a keychain value with no index row): the write intent is journaled,
  and reconciliation after restart adopts-or-conflicts — exactly one row, one source of truth.
* ``replace`` requires an existing binding; ``delete`` proves removal (or keeps the row and
  says so); ``revoke`` withdraws capability and keeps an honest tombstone.
* Corrupt index storage raises a typed error — it is never silently treated as "no bindings".
* A restart reloads bindings with their statuses intact; unverified stays unverified.
"""
from __future__ import annotations

import json

import pytest

from tests._credential_intelligence_support import (  # noqa: F401 (fixtures resolve via module namespace)
    ODD_KEY,
    descriptor_for,
    isolated_home,
    keychain_home,
    registry_of,
    sweep_home_for_secret,
    vault_home,
)

VERIFIED_OUTCOME = {"status": "verified", "provider_id": "provtest", "http_status": 200,
                    "account": "acct-1", "checked_at": "2026-09-02T12:00:00Z"}


def _store():
    from core.credential_intelligence.store import CredentialStore

    return CredentialStore(registry_of(descriptor_for(None)))


def _verified_outcome(provider_id="provtest"):
    from core.credential_intelligence.verification import VerificationOutcome

    return VerificationOutcome(
        status="verified", provider_id=provider_id, http_status=200, detail="authorized",
        account="acct-1", checked_at="2026-09-02T12:00:00Z", endpoint_host="prov.example",
    )


# ------------------------------------------------------------------ persistence + exposure

def test_save_verified_persists_secret_only_in_the_credential_store(vault_home):
    store = _store()
    desc = descriptor_for(None)
    binding = store.save_verified(desc, ODD_KEY, _verified_outcome())
    from core import credential_store

    assert credential_store.get_credential(desc.credential_slot) == ODD_KEY
    assert binding.provider_id == "provtest"
    assert binding.account == "acct-1"
    assert binding.status == "verified"
    assert binding.last_verified_at == "2026-09-02T12:00:00Z"


def test_binding_to_dict_exposes_exactly_the_allowed_fields(vault_home):
    store = _store()
    binding = store.save_verified(descriptor_for(None), ODD_KEY, _verified_outcome())
    payload = binding.to_dict()
    assert set(payload) == {
        "binding_id", "provider_id", "provider_label", "account",
        "capability_family", "status", "last_verified_at", "created_at",
    }
    text = json.dumps(payload)
    assert ODD_KEY not in text and "Bearer" not in text
    assert "…" not in text and "masked" not in text, "no last-4 hint on a binding"
    assert binding.__repr__().count(ODD_KEY) == 0


def test_only_verified_outcomes_may_persist(vault_home):
    from core.credential_intelligence.store import IntakeRefusedError
    from core.credential_intelligence.verification import VerificationOutcome

    store = _store()
    bad = VerificationOutcome(
        status="invalid", provider_id="provtest", http_status=401, detail="no",
        account="", checked_at="2026-09-02T12:00:00Z", endpoint_host="prov.example",
    )
    with pytest.raises(IntakeRefusedError):
        store.save_verified(descriptor_for(None), ODD_KEY, bad)
    from core import credential_store

    assert credential_store.get_credential("llm.cloud.provtest") is None
    assert store.bindings() == []


def test_no_plaintext_secret_anywhere_in_the_home(vault_home):
    store = _store()
    store.save_verified(descriptor_for(None), ODD_KEY, _verified_outcome())
    hits = sweep_home_for_secret(vault_home, ODD_KEY)
    assert hits == [], f"raw secret found in plaintext files: {hits}"


# ------------------------------------------------------------------ explicit lifecycle

def test_replace_requires_an_existing_binding(vault_home):
    from core.credential_intelligence.store import IntakeRefusedError

    store = _store()
    desc = descriptor_for(None)
    with pytest.raises(IntakeRefusedError):
        store.replace(desc, ODD_KEY, _verified_outcome())
    store.save_verified(desc, ODD_KEY, _verified_outcome())
    new_key = "zk9-replacement-key-0123456789abcdef"
    binding = store.replace(desc, new_key, _verified_outcome())
    from core import credential_store

    assert credential_store.get_credential(desc.credential_slot) == new_key
    assert len(store.bindings()) == 1, "replace must not append a second binding"
    assert binding.binding_id == store.find("provtest").binding_id


def test_delete_proves_removal(vault_home):
    store = _store()
    desc = descriptor_for(None)
    store.save_verified(desc, ODD_KEY, _verified_outcome())
    result = store.delete("provtest")
    assert result.removed is True
    from core import credential_store

    assert credential_store.get_credential(desc.credential_slot) is None
    assert store.find("provtest") is None


def test_delete_of_unknown_provider_is_an_honest_noop(vault_home):
    result = _store().delete("nobody-here")
    assert result.removed is False


def test_revoke_keeps_a_tombstone_and_clears_the_secret(vault_home):
    store = _store()
    desc = descriptor_for(None)
    store.save_verified(desc, ODD_KEY, _verified_outcome())
    tomb = store.revoke("provtest")
    from core import credential_store

    assert tomb.status == "revoked"
    assert credential_store.get_credential(desc.credential_slot) is None
    assert store.find("provtest").status == "revoked", "the revocation must stay visible"


# ------------------------------------------------------------------ timeout reconciliation

def _hang_the_write(monkeypatch, hanging_keyring):
    import core.bounded_keyring as bk

    monkeypatch.setattr(bk, "DEFAULT_TIMEOUT_S", 0.3)
    return hanging_keyring


def test_timeout_leaves_a_pending_intent_and_no_binding(keychain_home, monkeypatch):
    from core.credential_intelligence.store import CredentialStore, StoreWriteTimeoutError

    _hang_the_write(monkeypatch, keychain_home)
    store = CredentialStore(registry_of(descriptor_for(None)))
    with pytest.raises(StoreWriteTimeoutError):
        store.save_verified(descriptor_for(None), ODD_KEY, _verified_outcome())
    assert store.find("provtest") is None, "an unproven write must not become a binding"
    journal = store._journal_rows()
    assert any(r["phase"] == "write_pending" for r in journal), "the intent must be journaled"


def test_late_write_after_timeout_is_reconciled_to_exactly_one_row(keychain_home, isolated_home, monkeypatch):
    """The full duplicate/shadow scenario: the bounded write times out, the dialog is later
    answered (the daemon thread completes the keychain write), and the process restarts.
    Reconciliation must end with ONE index row, NO vault duplicate, NO unindexed keychain
    value — and the adopted binding is honestly unverified."""
    from core.bounded_keyring import bounded_keyring_call
    from core.credential_intelligence.store import CredentialStore, StoreWriteTimeoutError

    _hang_the_write(monkeypatch, keychain_home)
    store = CredentialStore(registry_of(descriptor_for(None)))
    with pytest.raises(StoreWriteTimeoutError):
        store.save_verified(descriptor_for(None), ODD_KEY, _verified_outcome())

    # the prompt is answered after the timeout: the late keychain write completes
    keychain_home.unblock_late_write()
    import time as _time

    _time.sleep(0.2)

    # "restart": breaker reset (plain assignment — monkeypatch would restore the armed value
    # at teardown), fresh store instance
    import core.bounded_keyring as bk

    bk._KEYCHAIN_BLOCKED = False
    restarted = CredentialStore(registry_of(descriptor_for(None)))
    report = restarted.reconcile()

    assert restarted.find("provtest") is not None, "the late write was adopted, not shadowed"
    assert restarted.find("provtest").status == "unverified"
    assert len(restarted.bindings()) == 1
    assert report.adopted == ["provtest"]
    # no duplicate: the vault must not hold a second copy once the keychain one is adopted
    from core import credential_store as cs

    keyring_now = bounded_keyring_call(
        lambda: cs._load_keyring().get_password(cs._KC_SERVICE, "llm.cloud.provtest"),
        what="post-reconcile check",
    )
    assert keyring_now == ODD_KEY
    vault_entries = json.loads((isolated_home / "data" / "credentials.enc.json").read_text()) if (isolated_home / "data" / "credentials.enc.json").exists() else {}
    assert "llm.cloud.provtest" not in vault_entries, "duplicate: value live in vault AND keychain"


def test_reconcile_surfaces_a_digest_conflict_instead_of_silently_picking(keychain_home, monkeypatch):
    """Pending intent for a value that is NOT what ended up in the keychain: a typed
    conflict, nothing deleted, nothing guessed."""
    from core.credential_intelligence.store import CredentialStore
    from tests._credential_intelligence_support import descriptor_for as _df

    desc = _df(None)
    store = CredentialStore(registry_of(desc))
    # a foreign value lands in the keychain slot with a journaled pending intent for another
    keychain_home._store[(store._KC_SERVICE, desc.credential_slot)] = "someone-elses-key-0123456789"
    store._journal_append({
        "slot": desc.credential_slot, "provider_id": desc.provider_id,
        "digest": store._digest(ODD_KEY), "phase": "write_pending", "ts": "2026-09-02T12:00:00Z",
    })
    report = store.reconcile()
    assert report.conflicts == ["provtest"]
    assert store.find("provtest") is None, "a conflicted adoption must not guess a winner"


def test_shadow_detection_without_reconcile_is_reported(keychain_home):
    """The sabotage companion: with reconciliation suppressed (the mutation this family
    guards against), the store's invariant report must still SEE the shadow."""
    from core.credential_intelligence.store import CredentialStore

    desc = descriptor_for(None)
    store = CredentialStore(registry_of(desc))
    keychain_home._store[(store._KC_SERVICE, desc.credential_slot)] = ODD_KEY
    store._journal_append({
        "slot": desc.credential_slot, "provider_id": desc.provider_id,
        "digest": store._digest(ODD_KEY), "phase": "write_pending", "ts": "2026-09-02T12:00:00Z",
    })
    violations = store.binding_invariants()
    assert any("shadow" in v for v in violations), f"shadow not detected: {violations}"


# ------------------------------------------------------------------ malformed storage + restart

def test_corrupt_index_raises_typed_error_not_silent_empty(vault_home):
    from core.credential_intelligence.binding import MalformedBindingIndexError
    from core.credential_intelligence.store import CredentialStore

    store = CredentialStore(registry_of(descriptor_for(None)))
    store._index_path().parent.mkdir(parents=True, exist_ok=True)
    store._index_path().write_text("{not json", encoding="utf-8")
    with pytest.raises(MalformedBindingIndexError):
        store.bindings()


def test_malformed_index_row_is_reconciled_honestly(vault_home):
    from core.credential_intelligence.store import CredentialStore

    store = CredentialStore(registry_of(descriptor_for(None)))
    store._index_path().parent.mkdir(parents=True, exist_ok=True)
    store._index_path().write_text(json.dumps({"provtest": {"provider_id": "provtest", "garbage": True}}), encoding="utf-8")
    report = store.reconcile()
    assert report.malformed_rows == ["provtest"]
    assert store.find("provtest") is None


def test_restart_preserves_bindings_and_statuses(vault_home):
    from core.credential_intelligence.store import CredentialStore

    desc = descriptor_for(None)
    first = CredentialStore(registry_of(desc))
    first.save_verified(desc, ODD_KEY, _verified_outcome())

    restarted = CredentialStore(registry_of(desc))
    found = restarted.find("provtest")
    assert found is not None and found.status == "verified"
    assert found.last_verified_at == "2026-09-02T12:00:00Z"

    restarted.revoke("provtest")
    again = CredentialStore(registry_of(desc))
    assert again.find("provtest").status == "revoked", "revocation must survive a restart"


def test_slot_without_index_adopted_as_unverified_never_verified(vault_home):
    """A key set through some legacy path (present in the store, no binding row) is adopted
    as UNVERIFIED — capability evidence can only come from a completed verification."""
    from core import credential_store
    from core.credential_intelligence.store import CredentialStore

    desc = descriptor_for(None)
    credential_store.store_credential(desc.credential_slot, ODD_KEY, label="legacy")
    store = CredentialStore(registry_of(desc))
    report = store.reconcile()
    assert report.adopted == ["provtest"]
    assert store.find("provtest").status == "unverified"


def test_index_row_with_missing_secret_degrades_to_missing(vault_home):
    from core.credential_intelligence.store import CredentialStore

    desc = descriptor_for(None)
    store = CredentialStore(registry_of(desc))
    store.save_verified(desc, ODD_KEY, _verified_outcome())
    from core import credential_store

    credential_store.delete_credential(desc.credential_slot)
    report = store.reconcile()
    assert report.missing == ["provtest"]
    assert store.find("provtest").status == "missing"
