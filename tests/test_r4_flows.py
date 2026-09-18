# ruff: noqa: F811 (imported pytest fixtures are re-exposed as test parameters by design)
"""Revision-4 flows: for each repaired contract, the original shape, a genuinely different
novel case, and refusal/preservation controls -- through the real store, the real intake
dispatcher and real isolated vault, with loopback providers only.
"""
from __future__ import annotations

import hashlib
import json as _json

import pytest

from tests._credential_intelligence_support import FakeProviderServer
from tests.first_run_pact_rig import pact_rig  # noqa: F401 — fixture
from tests.test_quarantine_destination_isolation import (
    LATER_KEY,
    THIRD_KEY,
    _begin_classify,
    _openai_compatible,
    _point_custom_at,
)

LATER_BEARER = hashlib.sha256(f"Bearer {LATER_KEY}".encode()).hexdigest()
THIRD_BEARER = hashlib.sha256(f"Bearer {THIRD_KEY}".encode()).hexdigest()


# ------------------------------------------------------------------ R1: incoherent state never discloses

def test_direct_retry_before_reconcile_is_typed_and_silent_then_recovers(pact_rig, monkeypatch):
    """Original R1 shape: failed index commit leaves the new secret beside the OLD row. Direct
    retry (no reconcile first -- the product path) answers a typed refusal, sends NOTHING
    anywhere; reconcile then resolves the pending intent and a retry verifies coherently
    against the NEW endpoint."""
    import core.credential_intelligence.store as module
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.store import CredentialStore

    with FakeProviderServer(_openai_compatible([LATER_KEY])) as old:
        with FakeProviderServer(_openai_compatible([THIRD_KEY])) as new:
            _point_custom_at(monkeypatch, old.url + "/v1")
            store = CredentialStore(default_registry())
            descriptor = default_registry().get("custom")
            store.save_quarantined(descriptor, LATER_KEY, endpoint=old.url + "/v1")

            real = module.save_index
            monkeypatch.setattr(module, "save_index", lambda rows: (_ for _ in ()).throw(OSError("injected")))
            with pytest.raises(OSError):
                store.save_quarantined(descriptor, THIRD_KEY, endpoint=new.url + "/v1")
            monkeypatch.setattr(module, "save_index", real)

            status, result = pact_rig.post("/api/intake/quarantine/retry", {"provider_id": "custom"})
            assert status == 409, result
            assert result.get("error") == "quarantine_incoherent", result
            assert old.request_count == 0 and new.request_count == 0, "a keyed request was made on an incoherent state"

            report = CredentialStore(default_registry()).reconcile()
            assert "custom" in report.adopted, report
            status, retried = pact_rig.post("/api/intake/quarantine/retry", {"provider_id": "custom"})
            assert status == 200 and retried["promoted"] is True, retried
            assert not any(r["auth_sha256"] == LATER_BEARER for r in new.requests), "old secret reached the new endpoint"
            from core import credential_store

            assert credential_store.get_credential("llm.cloud.custom_base_url") == new.url + "/v1"


def test_late_bounded_write_restart_recovers_the_same_way(pact_rig, monkeypatch):
    """Novel R1 case, different data: the bounded backend write completes LATE (after the
    caller failed) -- simulated as the crash window -- and the process 'restarts'. The first
    retry after restart hits the typed incoherence, reconcile adopts the landed write from its
    pending intent, and the second retry works."""
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.store import CredentialStore

    with FakeProviderServer(_openai_compatible([LATER_KEY])) as old:
        with FakeProviderServer(_openai_compatible([THIRD_KEY])) as new:
            _point_custom_at(monkeypatch, old.url + "/v1")
            store = CredentialStore(default_registry())
            descriptor = default_registry().get("custom")
            store.save_quarantined(descriptor, LATER_KEY, endpoint=old.url + "/v1")
            # crash window: the backend value landed for the new paste, the row and the
            # journal close never did
            from core import credential_store

            credential_store.store_credential("quarantine.llm.cloud.custom", THIRD_KEY, label="(unverified)")
            journal_path = pact_rig.home / "data" / "credential_intake_journal.json"
            journal = _json.loads(journal_path.read_text())
            journal.append({
                "slot": "quarantine.llm.cloud.custom", "provider_id": "custom",
                "digest": hashlib.sha256(THIRD_KEY.encode()).hexdigest(),
                "phase": "write_pending", "kind": "quarantine", "ts": "2026-09-15T00:00:00Z",
                "endpoint": new.url + "/v1",
                "generation": __import__("core.credential_intelligence.store", fromlist=["quarantine_generation_for"]).quarantine_generation_for(THIRD_KEY, new.url + "/v1"),
                "epoch": 99, "operation_id": "late" * 8,
            })
            journal_path.write_text(_json.dumps(journal))

            status, result = pact_rig.post("/api/intake/quarantine/retry", {"provider_id": "custom"})
            assert status == 409 and result.get("error") == "quarantine_incoherent", result
            assert old.request_count == 0 and new.request_count == 0

            CredentialStore(default_registry()).reconcile()
            status, retried = pact_rig.post("/api/intake/quarantine/retry", {"provider_id": "custom"})
            assert status == 200 and retried["promoted"] is True, retried
            assert any(r["auth_sha256"] == THIRD_BEARER for r in new.requests)


def test_coherent_normal_retry_still_works(pact_rig, monkeypatch):
    """Preservation control: the coherence gate did not break the ordinary path."""
    with FakeProviderServer(_openai_compatible([LATER_KEY])) as target:
        _point_custom_at(monkeypatch, target.url + "/v1")
        sid = _begin_classify(pact_rig, LATER_KEY, base_url=target.url + "/v1")
        pact_rig.post("/api/intake/complete", {"session_id": sid, "persist": "later"})
        status, retried = pact_rig.post("/api/intake/quarantine/retry", {"provider_id": "custom"})
        assert status == 200 and retried["promoted"] is True, retried


# ------------------------------------------------------------------ R2: operation identity is never reused

def test_completed_delete_recreate_refuses_stale_promote_and_delete_allows_new(pact_rig, monkeypatch):
    """Novel R2 shapes beyond the review probe: after a COMPLETED delete and an identical
    recreate (fresh store, durable epochs), a stale snapshot can neither PROMOTE nor DELETE;
    the genuinely new snapshot can delete."""
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.store import CredentialStore, IntakeRefusedError

    with FakeProviderServer(_openai_compatible([LATER_KEY])) as target:
        _point_custom_at(monkeypatch, target.url + "/v1")
        store = CredentialStore(default_registry())
        descriptor = default_registry().get("custom")
        store.save_quarantined(descriptor, LATER_KEY, endpoint=target.url + "/v1")
        old = store.quarantine_snapshot("custom")
        assert store.delete_quarantined("custom", snapshot=old).removed

        fresh = CredentialStore(default_registry())  # a restarted instance
        fresh.save_quarantined(descriptor, LATER_KEY, endpoint=target.url + "/v1")
        current = fresh.quarantine_snapshot("custom")
        assert current.epoch != old.epoch, "the recreated paste reused the deleted operation's epoch"

        verified = type("Outcome", (), {"status": "verified", "account": "a"})()
        with pytest.raises(IntakeRefusedError):
            fresh.promote_quarantined(descriptor, old.secret, verified, snapshot=old)
        with pytest.raises(IntakeRefusedError):
            fresh.delete_quarantined("custom", snapshot=old)
        assert fresh.quarantine_snapshot("custom") == current, "a stale operation mutated the new quarantine"

        assert fresh.delete_quarantined("custom", snapshot=current).removed, "the intended new deletion was refused"


def test_epoch_survives_restart_and_pending_intent_recovery(pact_rig, monkeypatch):
    """The durable high-water mark persists across instances, and a pending recovery intent
    bound to an old epoch cannot act on a newer operation."""
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.store import CredentialStore

    with FakeProviderServer(_openai_compatible([LATER_KEY])) as target:
        _point_custom_at(monkeypatch, target.url + "/v1")
        store = CredentialStore(default_registry())
        descriptor = default_registry().get("custom")
        store.save_quarantined(descriptor, LATER_KEY, endpoint=target.url + "/v1")
        first = store.quarantine_snapshot("custom")

        restarted = CredentialStore(default_registry())
        restarted.save_quarantined(descriptor, THIRD_KEY, endpoint=target.url + "/v1")
        second = restarted.quarantine_snapshot("custom")
        assert second.epoch > first.epoch, "epochs are not durably monotonic across instances"

        # a pending delete for the FIRST operation must not touch the second
        journal_path = pact_rig.home / "data" / "credential_intake_journal.json"
        journal = _json.loads(journal_path.read_text())
        journal.append({
            "slot": "quarantine.llm.cloud.custom", "provider_id": "custom", "digest": "",
            "phase": "delete_pending", "kind": "quarantine_delete", "ts": "2026-09-15T00:00:00Z",
            "generation": first.generation, "epoch": first.epoch, "operation_id": "stale" * 8,
        })
        journal_path.write_text(_json.dumps(journal))
        CredentialStore(default_registry()).reconcile()
        from core import credential_store

        assert credential_store.get_credential("quarantine.llm.cloud.custom") == THIRD_KEY, (
            "a stale pending delete removed a newer operation's key")


# ------------------------------------------------------------------ R3: the live pair commits as one fact

def test_failure_between_endpoint_and_key_writes_restores_the_prior_pair(pact_rig, monkeypatch):
    """Novel R3 case: the backend fails BETWEEN the endpoint and key writes inside the
    verified commit. Compensation restores the prior working pair; the door reports the typed
    failure; nothing mixed goes live."""
    import core.credential_store as backend
    from core.unattended_preflight import SecureStorageError

    live_key = "nv1_prior-" + hashlib.sha256(b"r4-prior").hexdigest()[:32]
    new_key = "nv1_new-" + hashlib.sha256(b"r4-new").hexdigest()[:32]
    with FakeProviderServer(_openai_compatible([live_key])) as prior_endpoint:
        with FakeProviderServer(_openai_compatible([new_key])) as new_endpoint:
            _point_custom_at(monkeypatch, prior_endpoint.url + "/v1")
            # a working verified binding exists
            sa = _begin_classify(pact_rig, live_key, base_url=prior_endpoint.url + "/v1")
            status, verified = pact_rig.post("/api/intake/verify", {
                "session_id": sa, "provider_id": "custom", "base_url": prior_endpoint.url + "/v1"})
            assert status == 200 and verified["outcome"] == "verified"
            assert pact_rig.post("/api/intake/complete", {"session_id": sa})[0] == 200

            # the replacement fails exactly between the two writes
            real_store = backend.store_credential
            armed = {"failed": False}

            def fail_on_key(slot, value, label=""):
                if slot == "llm.cloud.custom" and value == new_key and not armed["failed"]:
                    armed["failed"] = True
                    raise SecureStorageError("bounded door timeout", "timeout", "retry")
                return real_store(slot, value, label=label)

            monkeypatch.setattr(backend, "store_credential", fail_on_key)
            sb = _begin_classify(pact_rig, new_key, base_url=new_endpoint.url + "/v1")
            status2, verified2 = pact_rig.post("/api/intake/verify", {
                "session_id": sb, "provider_id": "custom", "base_url": new_endpoint.url + "/v1"})
            monkeypatch.setattr(backend, "store_credential", real_store)
            assert status2 == 200 and verified2["outcome"] == "verified"
            monkeypatch.setattr(backend, "store_credential", fail_on_key)
            status3, done = pact_rig.post("/api/intake/complete", {"session_id": sb})
            monkeypatch.setattr(backend, "store_credential", real_store)
            assert status3 != 200, done  # the failure is surfaced, never a false success

            from core import credential_store

            assert credential_store.get_credential("llm.cloud.custom") == live_key, "the prior working key was lost"
            assert credential_store.get_credential("llm.cloud.custom_base_url") == prior_endpoint.url + "/v1", (
                "a failed replacement left a mixed live pair")


def test_refusal_before_any_write_preserves_the_working_pair(pact_rig, monkeypatch):
    """Preservation control: an unverified outcome refuses before ANY slot is touched."""
    live_key = "nv1_live2-" + hashlib.sha256(b"r4-live2").hexdigest()[:32]
    with FakeProviderServer(_openai_compatible([live_key])) as live:
        with FakeProviderServer() as rejecting:
            rejecting.responses = lambda record: (401, {"error": {"message": "no", "code": "invalid_api_key"}})
            _point_custom_at(monkeypatch, live.url + "/v1")
            sa = _begin_classify(pact_rig, live_key, base_url=live.url + "/v1")
            status, verified = pact_rig.post("/api/intake/verify", {
                "session_id": sa, "provider_id": "custom", "base_url": live.url + "/v1"})
            assert verified["outcome"] == "verified"
            assert pact_rig.post("/api/intake/complete", {"session_id": sa})[0] == 200

            bad_key = "nv1_bad-" + hashlib.sha256(b"r4-bad").hexdigest()[:32]
            sb = _begin_classify(pact_rig, bad_key, base_url=rejecting.url + "/v1")
            status2, verified2 = pact_rig.post("/api/intake/verify", {
                "session_id": sb, "provider_id": "custom", "base_url": rejecting.url + "/v1"})
            assert status2 == 200 and verified2["outcome"] == "invalid"
            status3, done = pact_rig.post("/api/intake/complete", {"session_id": sb})
            assert status3 != 200, done

            from core import credential_store

            assert credential_store.get_credential("llm.cloud.custom") == live_key
            assert credential_store.get_credential("llm.cloud.custom_base_url") == live.url + "/v1"


def test_test_door_reads_the_pair_under_the_writer_lock(pact_rig, monkeypatch):
    """The served Test door observes the custom pair atomically: after a verified save the
    probe goes green against the NEW pair; a mixed pair is not observable through it."""
    with FakeProviderServer(_openai_compatible([LATER_KEY])) as a:
        with FakeProviderServer(_openai_compatible([THIRD_KEY])) as b:
            _point_custom_at(monkeypatch, a.url + "/v1")
            sa = _begin_classify(pact_rig, LATER_KEY, base_url=a.url + "/v1")
            status, v = pact_rig.post("/api/intake/verify", {"session_id": sa, "provider_id": "custom", "base_url": a.url + "/v1"})
            assert v["outcome"] == "verified"
            assert pact_rig.post("/api/intake/complete", {"session_id": sa})[0] == 200

            # a verified replacement to pair B
            _point_custom_at(monkeypatch, b.url + "/v1")
            sb = _begin_classify(pact_rig, THIRD_KEY, base_url=b.url + "/v1")
            status, v = pact_rig.post("/api/intake/verify", {"session_id": sb, "provider_id": "custom", "base_url": b.url + "/v1"})
            assert v["outcome"] == "verified"
            assert pact_rig.post("/api/intake/complete", {"session_id": sb})[0] == 200

            from core import cloud_connection_state as ccs

            ccs.reset_probe_rate_limit_for_tests()
            result = ccs.run_auth_probe(provider="custom", now=2000.0)
            assert result["state"] == ccs.STATE_OK, result
            # the OLD endpoint's key never went to the NEW endpoint and vice versa
            assert not any(r["auth_sha256"] == LATER_BEARER for r in b.requests)
