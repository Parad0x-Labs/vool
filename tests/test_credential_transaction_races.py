# ruff: noqa: F811 (imported pytest fixtures are re-exposed as test parameters by design)
"""The review's F1/F2 races and their siblings, translated to the corrected boundary.

The review's probes hooked operations INSIDE the previously unguarded windows. Those windows
no longer exist — the whole mutating sequence is one transaction — so a hook there would only
deadlock on the non-reentrant lock. The translations below therefore inject competing
LEGITIMATE operations where they can genuinely land (before the snapshot, during the
network-only verification gap, or as truly concurrent workers), and assert the required
outcomes:

* no OLD key is ever sent to a LATER paste's endpoint (F1 — confidentiality);
* no newer binding/key is destroyed by a stale result (F2 — preservation);
* no active endpoint is repointed independently of its verified key;
* no false success, and pending recovery survives restarts including same-value ABA;
* transient backend/index failures are truthful, never silent success.
"""
from __future__ import annotations

import hashlib
import json as _json
import threading

from tests._credential_intelligence_support import FakeProviderServer
from tests.first_run_pact_rig import pact_rig  # noqa: F401 — fixture
from tests.test_quarantine_destination_isolation import (
    LATER_KEY,
    THIRD_KEY,
    _begin_classify,
    _openai_compatible,
    _point_custom_at,
)

FORBIDDEN_OLD_BEARER = hashlib.sha256(f"Bearer {LATER_KEY}".encode()).hexdigest()


def _retry(pact_rig):
    return pact_rig.post("/api/intake/quarantine/retry", {"provider_id": "custom"})


# ------------------------------------------------------------------ F1: destination confidentiality

def test_retry_never_sends_the_old_secret_to_a_later_pastes_endpoint(pact_rig, monkeypatch):
    """The review's F1, original shape: a legitimate new paste (different key AND endpoint)
    lands after the retry's snapshot read but before its verification. The old secret may only
    ever travel to ITS OWN endpoint; the later paste's endpoint must never see it."""
    import core.credential_intelligence.verification as verification
    from core.credential_intelligence.provider_registry import default_registry

    with FakeProviderServer(_openai_compatible([LATER_KEY])) as old:
        with FakeProviderServer(_openai_compatible([THIRD_KEY])) as new:
            _point_custom_at(monkeypatch, old.url + "/v1")
            sid = _begin_classify(pact_rig, LATER_KEY, base_url=old.url + "/v1")
            pact_rig.post("/api/intake/complete", {"session_id": sid, "persist": "later"})

            real_verify = verification.verify_provider_credential
            armed = {"done": False}

            def verify_then_competing_paste(secret, descriptor, **kw):
                # a legitimate save-for-later lands during the (network-only) verification gap
                if not armed["done"]:
                    armed["done"] = True
                    from core.credential_intelligence.store import CredentialStore

                    CredentialStore(default_registry()).save_quarantined(
                        default_registry().get("custom"), THIRD_KEY, endpoint=new.url + "/v1")
                return real_verify(secret, descriptor, **kw)

            monkeypatch.setattr(verification, "verify_provider_credential", verify_then_competing_paste)
            status, retried = _retry(pact_rig)
            assert status == 200 and retried["promoted"] is False, retried

            assert not any(r["auth_sha256"] == FORBIDDEN_OLD_BEARER for r in new.requests), (
                "the old quarantined credential was sent to the newly pasted different endpoint")
            # the old key went only to its own destination
            assert any(r["auth_sha256"] == FORBIDDEN_OLD_BEARER for r in old.requests)
            # the newer paste is intact
            from core import credential_store

            assert credential_store.get_credential("quarantine.llm.cloud.custom") == THIRD_KEY
            assert credential_store.get_credential("llm.cloud.custom") is None


def test_retry_after_a_committed_paste_uses_only_the_new_destinations_key(pact_rig, monkeypatch):
    """Novel shape, different data: the competing paste commits BEFORE the retry starts. The
    retry verifies the NEW key against the NEW endpoint and may promote it; the old key is
    never read, never sent anywhere."""
    with FakeProviderServer(_openai_compatible([LATER_KEY])) as old:
        with FakeProviderServer(_openai_compatible([THIRD_KEY])) as new:
            _point_custom_at(monkeypatch, old.url + "/v1")
            sid = _begin_classify(pact_rig, LATER_KEY, base_url=old.url + "/v1")
            pact_rig.post("/api/intake/complete", {"session_id": sid, "persist": "later"})
            with old._lock:
                old.requests.clear()

            sid2 = _begin_classify(pact_rig, THIRD_KEY, base_url=new.url + "/v1")
            pact_rig.post("/api/intake/complete", {"session_id": sid2, "persist": "later"})

            status, retried = _retry(pact_rig)
            assert status == 200 and retried["promoted"] is True, retried
            assert old.request_count == 0, "the superseded key's endpoint was contacted again"
            from core import credential_store

            assert credential_store.get_credential("llm.cloud.custom") == THIRD_KEY
            assert credential_store.get_credential("quarantine.llm.cloud.custom") is None
            assert credential_store.get_credential("llm.cloud.custom_base_url") == new.url + "/v1"


# ------------------------------------------------------------------ F2: stale results preserve newer state

def test_verified_replacement_racing_promotion_wins_cleanly(pact_rig, monkeypatch):
    """A verified save commits during a promotion's verification gap: the promotion declines,
    the ACTIVE key and its endpoint are untouched by the stale quarantine result."""
    import core.credential_intelligence.verification as verification
    from core.credential_intelligence.provider_registry import default_registry

    live_key = "nv1_live-" + hashlib.sha256(b"r3-live").hexdigest()[:32]
    with FakeProviderServer(_openai_compatible([LATER_KEY])) as quarantine_target:
        with FakeProviderServer(_openai_compatible([live_key])) as live_endpoint:
            _point_custom_at(monkeypatch, quarantine_target.url + "/v1")
            sid = _begin_classify(pact_rig, LATER_KEY, base_url=quarantine_target.url + "/v1")
            pact_rig.post("/api/intake/complete", {"session_id": sid, "persist": "later"})

            real_verify = verification.verify_provider_credential
            armed = {"done": False}

            def verify_then_verified_save(secret, descriptor, **kw):
                if not armed["done"]:
                    armed["done"] = True
                    sid2 = _begin_classify(pact_rig, live_key, base_url=live_endpoint.url + "/v1")
                    s, verified = pact_rig.post("/api/intake/verify", {
                        "session_id": sid2, "provider_id": "custom",
                        "base_url": live_endpoint.url + "/v1"})
                    assert s == 200 and verified["outcome"] == "verified", verified
                    done = pact_rig.post("/api/intake/complete", {"session_id": sid2})
                    assert done[0] == 200, done
                return real_verify(secret, descriptor, **kw)

            monkeypatch.setattr(verification, "verify_provider_credential", verify_then_verified_save)
            status, retried = _retry(pact_rig)
            assert status == 200 and retried["promoted"] is False, retried

            from core import credential_store

            assert credential_store.get_credential("llm.cloud.custom") == live_key, "a stale promotion displaced the active key"
            assert credential_store.get_credential("llm.cloud.custom_base_url") == live_endpoint.url + "/v1", \
                "a stale promotion repointed the active endpoint"
            assert credential_store.get_credential("quarantine.llm.cloud.custom") is None, \
                "the quarantine shadow of an active binding was retained"


def test_two_different_provider_writes_commit_together(pact_rig, monkeypatch):
    """Genuinely concurrent writes for two providers: both land; neither row is lost; the
    binding index stays coherent."""
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.store import CredentialStore

    with FakeProviderServer(_openai_compatible([LATER_KEY])) as target:
        _point_custom_at(monkeypatch, target.url + "/v1")
        sid = _begin_classify(pact_rig, LATER_KEY, base_url=target.url + "/v1")
        pact_rig.post("/api/intake/complete", {"session_id": sid, "persist": "later"})

        store = CredentialStore(default_registry())
        barrier = threading.Barrier(2)
        errors: list[str] = []

        def write(pid: str, key: str):
            try:
                barrier.wait(timeout=10)
                CredentialStore(default_registry()).save_quarantined(
                    default_registry().get(pid), key, endpoint=f"{target.url}/v1" if pid == "custom" else "")
            except Exception as exc:  # recorded, asserted below
                errors.append(f"{pid}: {exc!r}")

        workers = [
            threading.Thread(target=write, args=("custom", THIRD_KEY)),
            threading.Thread(target=write, args=("openrouter", "sk-or-v1-" + "9" * 40)),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=30)
        assert not errors, errors
        assert not any(worker.is_alive() for worker in workers), "a writer deadlocked"
        rows = _json.loads((pact_rig.home / "data" / "credential_bindings.json").read_text())
        assert rows["custom"]["status"] == "unverified_quarantined"
        assert rows["openrouter"]["status"] == "unverified_quarantined"


def test_pending_delete_across_restart_does_not_remove_a_newer_same_value_paste(pact_rig, monkeypatch):
    """Restart recovery + ABA: a pending quarantine_delete intent (crash window) meets a
    RECREATED paste of the SAME value. The content hash is identical — only the epoch
    distinguishes the operations — so reconcile must not complete the stale delete."""
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.store import CredentialStore

    with FakeProviderServer(_openai_compatible([LATER_KEY])) as target:
        _point_custom_at(monkeypatch, target.url + "/v1")
        sid = _begin_classify(pact_rig, LATER_KEY, base_url=target.url + "/v1")
        pact_rig.post("/api/intake/complete", {"session_id": sid, "persist": "later"})

        store = CredentialStore(default_registry())
        first = store.quarantine_snapshot("custom")
        # simulate the crash: delete journaled pending, nothing executed
        journal_path = pact_rig.home / "data" / "credential_intake_journal.json"
        journal = _json.loads(journal_path.read_text())
        journal.append({
            "slot": "quarantine.llm.cloud.custom", "provider_id": "custom", "digest": "",
            "phase": "delete_pending", "kind": "quarantine_delete",
            "ts": first and "" or "", "generation": first.generation, "epoch": first.epoch,
            "operation_id": "deadbeef" * 4,
        })
        journal_path.write_text(_json.dumps(journal))
        # the SAME value is pasted again: identical content, NEW epoch
        sid2 = _begin_classify(pact_rig, LATER_KEY, base_url=target.url + "/v1")
        status, _ = pact_rig.post("/api/intake/complete", {"session_id": sid2, "persist": "later"})
        assert status == 200

        report = CredentialStore(default_registry()).reconcile()
        from core import credential_store

        assert credential_store.get_credential("quarantine.llm.cloud.custom") == LATER_KEY, (
            "a stale pending delete removed a recreated same-value paste (ABA)")


def test_transient_index_failure_is_truthful_and_leaves_recovery(pact_rig, monkeypatch):
    """A transient index write failure inside a quarantine save raises truthfully (no false
    success), leaves the pending intent for reconcile, and does not corrupt existing rows."""
    import core.credential_intelligence.binding as binding
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.store import CredentialStore

    with FakeProviderServer(_openai_compatible([LATER_KEY])) as target:
        _point_custom_at(monkeypatch, target.url + "/v1")
        sid = _begin_classify(pact_rig, LATER_KEY, base_url=target.url + "/v1")
        pact_rig.post("/api/intake/complete", {"session_id": sid, "persist": "later"})

        real_save_index = binding.save_index
        broken = {"on": False}

        def flaky_save_index(rows):
            if broken["on"]:
                raise OSError("transient index write failure")
            return real_save_index(rows)

        import pytest

        import core.credential_intelligence.store as store_module
        monkeypatch.setattr(store_module, "save_index", flaky_save_index)
        store = CredentialStore(default_registry())
        broken["on"] = True
        with pytest.raises(Exception):
            store.save_quarantined(default_registry().get("custom"), THIRD_KEY, endpoint=target.url + "/v1")
        broken["on"] = False

        rows = _json.loads((pact_rig.home / "data" / "credential_bindings.json").read_text())
        assert rows["custom"]["status"] == "unverified_quarantined", "a failed write claimed or corrupted the row"
        journal = _json.loads((pact_rig.home / "data" / "credential_intake_journal.json").read_text())
        assert any(e.get("phase") == "write_pending" and e.get("kind") == "quarantine" for e in journal), (
            "the transient failure left no recoverable pending intent")
        report = CredentialStore(default_registry()).reconcile()
        assert isinstance(report.adopted, list)  # recovery runs cleanly after the transient clears
