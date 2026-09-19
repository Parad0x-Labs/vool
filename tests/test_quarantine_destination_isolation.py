
"""Reproductions for the review's higher-priority defect: a REJECTED save-for-later must not
repoint the live custom endpoint, and quarantine retry/promote/delete must be bound to the
generation they were saved under.

Run BEFORE the fix: test_rejected_save_for_later_must_not_repoint_the_live_custom_endpoint
fails (the base URL is written before the refusal), proving the defect at the real intake
boundary. After the fix, the whole file is the regression.
"""
from __future__ import annotations

import dataclasses
import hashlib

from tests._credential_intelligence_support import FakeProviderServer
from tests.first_run_pact_rig import pact_rig

LIVE_KEY = "nv1_live-verified-" + hashlib.sha256(b"correction-live").hexdigest()[:32]
LATER_KEY = "nv1_unverified-" + hashlib.sha256(b"correction-later").hexdigest()[:32]
THIRD_KEY = "nv1_third-" + hashlib.sha256(b"correction-third").hexdigest()[:32]


def _openai_compatible(valid_keys):
    accepted = {hashlib.sha256(f"Bearer {k}".encode()).hexdigest() for k in valid_keys}

    def respond(record):
        keyed = record["auth_sha256"] in accepted
        if record["path"].endswith("/models"):
            if not keyed:
                return (401, {"error": {"message": "no", "type": "invalid_request_error", "code": "invalid_api_key"}})
            return (200, {"object": "list", "data": [{"id": "lab/one", "context_length": 8192}]})
        return (404, {"error": {"message": "no"}})

    return respond


def _point_custom_at(monkeypatch, base_url: str) -> None:
    import core.cloud_providers as cloud_providers

    monkeypatch.setitem(
        cloud_providers.PROVIDERS, "custom",
        dataclasses.replace(cloud_providers.PROVIDERS["custom"], base_url=base_url),
    )


def _begin_classify(rig, value, provider_id="custom", base_url=""):
    status, begun = rig.post("/api/intake/begin", {})
    assert status == 200, begun
    sid = begun["session_id"]
    status, classified = rig.post("/api/intake/classify", {"session_id": sid, "value": value})
    assert status == 200, classified
    body = {"session_id": sid, "provider_id": provider_id}
    if base_url:
        body["base_url"] = base_url
    status, seen = rig.post("/api/intake/preview", body)
    assert status == 200, seen
    return sid


def _stored_base_url():
    from core import credential_store

    return credential_store.get_credential("llm.cloud.custom_base_url")


# ------------------------------------------------------------------ reproduction: the original failure

def test_rejected_save_for_later_must_not_repoint_the_live_custom_endpoint(pact_rig, monkeypatch):
    """A verified custom binding exists at endpoint A. A save-for-later paste for endpoint B is
    refused (verified binding wins). The refusal must leave EVERYTHING alone — including the
    live endpoint the active key executes against."""
    with FakeProviderServer(_openai_compatible([LIVE_KEY])) as live:
        with FakeProviderServer() as other:
            _point_custom_at(monkeypatch, f"{live.url}/v1")
            # the verified save (journey) binds LIVE_KEY at endpoint A (real isolated vault)
            sid = _begin_classify(pact_rig, LIVE_KEY, base_url=f"{live.url}/v1")
            status, verified = pact_rig.post("/api/intake/verify", {"session_id": sid, "provider_id": "custom", "base_url": f"{live.url}/v1"})
            assert status == 200 and verified["outcome"] == "verified", verified
            status, done = pact_rig.post("/api/intake/complete", {"session_id": sid})
            assert status == 200, done
            assert _stored_base_url() == f"{live.url}/v1"

            # the rejected save-for-later for a DIFFERENT endpoint
            sid2 = _begin_classify(pact_rig, LATER_KEY, base_url=f"{other.url}/v1")
            status, refused = pact_rig.post("/api/intake/complete", {"session_id": sid2, "persist": "later"})
            assert status != 200, refused

            from core import credential_store

            assert credential_store.get_credential("llm.cloud.custom") == LIVE_KEY, "the active key changed"
            assert _stored_base_url() == f"{live.url}/v1", "a REJECTED save-for-later repointed the live custom endpoint"


# ------------------------------------------------------------------ quarantine keeps its own destination

def test_quarantined_custom_keeps_its_endpoint_until_promotion(pact_rig, monkeypatch):
    """No verified binding: a save-for-later at endpoint B quarantines with B recorded as ITS
    OWN destination; the global custom base URL stays empty; retry verifies against B; only
    promotion installs B as the live endpoint."""
    with FakeProviderServer(_openai_compatible([LATER_KEY])) as target:
        _point_custom_at(monkeypatch, f"{target.url}/v1")
        sid = _begin_classify(pact_rig, LATER_KEY, base_url=f"{target.url}/v1")
        status, later = pact_rig.post("/api/intake/complete", {"session_id": sid, "persist": "later"})
        assert status == 200, later
        assert _stored_base_url() is None, "a quarantined save changed the live custom endpoint"

        status, retried = pact_rig.post("/api/intake/quarantine/retry", {"provider_id": "custom"})
        assert status == 200 and retried["promoted"] is True, retried
        assert _stored_base_url() == f"{target.url}/v1", "promotion did not install the quarantined endpoint"
        from core import credential_store

        assert credential_store.get_credential("llm.cloud.custom") == LATER_KEY


def test_retry_uses_the_quarantined_endpoint_not_the_current_global(pact_rig, monkeypatch):
    """The global custom endpoint moved (env) after the quarantine: retry must still verify
    against the endpoint the quarantine was saved with, not wherever the global points now."""
    with FakeProviderServer(_openai_compatible([LATER_KEY])) as saved_endpoint:
        with FakeProviderServer() as elsewhere:
            _point_custom_at(monkeypatch, f"{saved_endpoint.url}/v1")
            sid = _begin_classify(pact_rig, LATER_KEY, base_url=f"{saved_endpoint.url}/v1")
            pact_rig.post("/api/intake/complete", {"session_id": sid, "persist": "later"})
            # the global moves somewhere that would reject the key: retry must still use the
            # endpoint the quarantine was saved with
            monkeypatch.setenv("VOOL_CUSTOM_BASE_URL", f"{elsewhere.url}/v1")
            status, retried = pact_rig.post("/api/intake/quarantine/retry", {"provider_id": "custom"})
            assert status == 200 and retried["promoted"] is True, retried


# ------------------------------------------------------------------ generation binding on retry/promote/delete

def test_stale_retry_cannot_overwrite_a_newer_paste(pact_rig, monkeypatch):
    """A retry starts; while its verification is in flight the operator pastes a NEW key for
    later (replacing the quarantined row). The stale retry's promote must be refused — it
    belongs to a generation nobody stored anymore."""
    import core.credential_intelligence.verification as verification

    with FakeProviderServer(_openai_compatible([LATER_KEY])) as target:
        _point_custom_at(monkeypatch, f"{target.url}/v1")
        sid = _begin_classify(pact_rig, LATER_KEY, base_url=f"{target.url}/v1")
        pact_rig.post("/api/intake/complete", {"session_id": sid, "persist": "later"})

        real_verify = verification.verify_provider_credential
        flipped = {"done": False}

        def verify_then_new_paste(secret, descriptor, **kw):
            outcome = real_verify(secret, descriptor, **kw)
            if not flipped["done"]:
                flipped["done"] = True
                sid2 = _begin_classify(pact_rig, THIRD_KEY, base_url=f"{target.url}/v1")
                s, _ = pact_rig.post("/api/intake/complete", {"session_id": sid2, "persist": "later"})
                assert s == 200
            return outcome

        monkeypatch.setattr(verification, "verify_provider_credential", verify_then_new_paste)
        status, retried = pact_rig.post("/api/intake/quarantine/retry", {"provider_id": "custom"})
        from core import credential_store

        assert credential_store.get_credential("llm.cloud.custom") is None, "a stale retry promoted over a newer paste"
        assert credential_store.get_credential("quarantine.llm.cloud.custom") == THIRD_KEY, "the newer quarantined paste was overwritten"
        status, listed = pact_rig.get("/api/intake/quarantine/list")
        rows = listed.get("quarantined") or (listed.get("data") or {}).get("quarantined") or []
        assert [r["provider_id"] for r in rows] == ["custom"]


def test_stale_delete_cannot_remove_a_newer_paste(pact_rig, monkeypatch):
    """The review's F2, translated to competing workers now that the whole delete is one
    transaction: a delete and a newer paste for the same slot run CONCURRENTLY (no injected
    hook inside the locked window -- that would only deadlock). Whatever interleaving the
    serializing lock admits, the NEWER key survives: either the delete committed first (the
    paste then lands cleanly) or the paste committed first (the delete's operation comparison
    refuses). The final state is always the newer key, quarantined and coherent."""
    import threading

    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.store import CredentialStore, IntakeRefusedError

    with FakeProviderServer(_openai_compatible([LATER_KEY])) as target:
        _point_custom_at(monkeypatch, f"{target.url}/v1")
        sid = _begin_classify(pact_rig, LATER_KEY, base_url=f"{target.url}/v1")
        pact_rig.post("/api/intake/complete", {"session_id": sid, "persist": "later"})

        store = CredentialStore(default_registry())
        snapshot = store.quarantine_snapshot("custom")
        assert snapshot is not None and snapshot.epoch >= 1

        barrier = threading.Barrier(2)
        outcome: dict[str, object] = {}

        def delete_old():
            barrier.wait(timeout=10)
            try:
                store.delete_quarantined("custom", snapshot=snapshot)
                outcome["delete"] = "deleted"
            except IntakeRefusedError:
                outcome["delete"] = "refused"

        def paste_new():
            barrier.wait(timeout=10)
            store2 = CredentialStore(default_registry())
            store2.save_quarantined(
                default_registry().get("custom"), THIRD_KEY, endpoint=f"{target.url}/v1")
            outcome["paste"] = "saved"

        workers = [threading.Thread(target=delete_old), threading.Thread(target=paste_new)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=30)
        assert not any(worker.is_alive() for worker in workers), "a worker deadlocked"

        from core import credential_store

        assert credential_store.get_credential("quarantine.llm.cloud.custom") == THIRD_KEY, (
            f"the newer paste was lost (delete={outcome.get('delete')})")
        fresh = store.quarantine_snapshot("custom")
        assert fresh is not None and fresh.secret == THIRD_KEY, "row and slot disagree after the race"
        status, listed = pact_rig.get("/api/intake/quarantine/list")
        rows = listed.get("quarantined") or (listed.get("data") or {}).get("quarantined") or []
        assert [r["provider_id"] for r in rows] == ["custom"]


def test_quarantine_row_keeps_endpoint_generation_and_survives_restart(pact_rig, monkeypatch, tmp_path):
    import json as _json

    with FakeProviderServer(_openai_compatible([LATER_KEY])) as target:
        _point_custom_at(monkeypatch, f"{target.url}/v1")
        sid = _begin_classify(pact_rig, LATER_KEY, base_url=f"{target.url}/v1")
        pact_rig.post("/api/intake/complete", {"session_id": sid, "persist": "later"})
        row_path = pact_rig.home / "data" / "credential_bindings.json"
        row = _json.loads(row_path.read_text())["custom"]
        assert row["status"] == "unverified_quarantined"
        assert row.get("quarantine_endpoint") == f"{target.url}/v1", "the quarantined destination was not durably recorded"
        assert row.get("quarantine_generation"), "no durable generation binds the quarantined secret to its destination"


# ------------------------------------------------------------------ recovery: failed writes and restarts

def test_reconcile_adopts_a_late_quarantine_write_as_quarantined(pact_rig, monkeypatch):
    """A bounded-door timeout leaves a pending quarantine intent; the value lands late. After a
    restart, reconcile adopts it as exactly one honest QUARANTINED row bound to its own
    destination — never a generic unverified row, never near a real slot."""
    import json as _json

    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.store import CredentialStore, quarantine_generation_for

    with FakeProviderServer(_openai_compatible([LATER_KEY])) as target:
        _point_custom_at(monkeypatch, f"{target.url}/v1")
        sid = _begin_classify(pact_rig, LATER_KEY, base_url=f"{target.url}/v1")
        status, later = pact_rig.post("/api/intake/complete", {"session_id": sid, "persist": "later"})
        assert status == 200, later

        # simulate the crash window: row gone, journal intent pending, value still sealed
        import hashlib
        rows_path = pact_rig.home / "data" / "credential_bindings.json"
        rows = _json.loads(rows_path.read_text())
        row = rows.pop("custom")
        rows_path.write_text(_json.dumps(rows))
        journal_path = pact_rig.home / "data" / "credential_intake_journal.json"
        journal = _json.loads(journal_path.read_text())
        # the crash window: the write landed and was journaled pending, but the row and the
        # close never happened -- reconstruct the pending intent the product would carry
        import hashlib as _hl
        journal.append({
            "slot": row["slot"], "provider_id": "custom",
            "digest": _hl.sha256(LATER_KEY.encode()).hexdigest(),
            "phase": "write_pending", "kind": "quarantine", "ts": row["created_at"],
            "endpoint": f"{target.url}/v1", "generation": row["quarantine_generation"],
        })
        journal_path.write_text(_json.dumps(journal))

        report = CredentialStore(default_registry()).reconcile()
        assert "custom" in report.adopted, report
        restored = _json.loads(rows_path.read_text())["custom"]
        assert restored["status"] == "unverified_quarantined"
        assert restored["quarantine_endpoint"] == f"{target.url}/v1"
        assert restored["quarantine_generation"] == quarantine_generation_for(LATER_KEY, f"{target.url}/v1")
        from core import credential_store

        assert credential_store.get_credential("llm.cloud.custom") is None, "recovery promoted a quarantined key into a real slot"


def test_reconcile_drops_a_quarantine_shadow_of_an_active_binding(pact_rig, monkeypatch):
    """Promotion completed but its quarantine-copy delete failed (restart in between): the row
    is active and the leftover quarantine value is a shadow — reconcile removes it, and the
    product can no longer claim the key remains quarantined."""
    import hashlib

    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.store import CredentialStore

    with FakeProviderServer(_openai_compatible([LATER_KEY])) as target:
        _point_custom_at(monkeypatch, f"{target.url}/v1")
        sid = _begin_classify(pact_rig, LATER_KEY, base_url=f"{target.url}/v1")
        pact_rig.post("/api/intake/complete", {"session_id": sid, "persist": "later"})
        status, retried = pact_rig.post("/api/intake/quarantine/retry", {"provider_id": "custom"})
        assert status == 200 and retried["promoted"] is True, retried

        # simulate the failed cleanup: the quarantine copy comes back
        from core import credential_store

        credential_store.store_credential("quarantine.llm.cloud.custom", LATER_KEY, label="leftover")
        report = CredentialStore(default_registry()).reconcile()
        assert "custom" in report.deduped, report
        assert credential_store.get_credential("quarantine.llm.cloud.custom") is None
        assert credential_store.get_credential("llm.cloud.custom") == LATER_KEY
