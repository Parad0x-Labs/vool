
"""Revision-6 flows: the four residual contracts of the revision-5 review.

The review's own probes (its test_residuals.py / test_recovery_residual.py) stay the original
failing shapes and run unchanged from the review directory. This file holds the genuinely
different cases and the refusal/preservation controls for each contract, through the real
store, the real served doors, the real router and real loopback provider stand-ins. Synthetic
keys and labelled stand-ins only; the wire Authorization digest at the stand-ins is the
disclosure oracle.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from tests._credential_intelligence_support import FakeProviderServer
from tests.first_run_pact_rig import pact_rig
from tests.test_quarantine_destination_isolation import _begin_classify, _point_custom_at
from tests.test_r5_flows import _openai_chat, _store_and_descriptor

R6_KEY = "nv1_r6identity-" + hashlib.sha256(b"revision-6-identity").hexdigest()[:32]


def _payload(body):
    """Served JSON envelopes carry the handler data either at the top level or under `data`."""
    return body.get("data") if isinstance(body, dict) and isinstance(body.get("data"), dict) else body


# ================================================================== R1: operation identity

def test_stale_served_retry_cannot_promote_a_same_value_repaste_after_the_epoch_mark_is_lost(pact_rig, monkeypatch):
    """R1 NOVEL case — a different operation (promotion through the served Retry door, not a
    store delete), different data, a different damage shape and a different answer than the
    review probe. Retry takes its snapshot and verifies; while that verification is in flight
    the operator deletes the paste through the served Delete door, the epoch authority loses THIS
    provider's mark (the file stays a valid mapping that still carries another provider's mark)
    and the operator re-pastes the identical key for the identical endpoint. The counter
    restarts at the deleted operation's epoch; the stale Retry must still answer
    quarantine_changed, the live slot must stay empty and the re-paste must survive intact."""
    import core.credential_intelligence.verification as verification
    from core import credential_store
    from core.runtime_paths import active_data_dir

    with FakeProviderServer(_openai_chat([R6_KEY])) as target:
        endpoint = f"{target.url}/v1"
        _point_custom_at(monkeypatch, endpoint)
        sid = _begin_classify(pact_rig, R6_KEY, base_url=endpoint)
        status, saved = pact_rig.post("/api/intake/complete", {"session_id": sid, "persist": "later"})
        assert status == 200, saved
        before = _store_and_descriptor()[0].quarantine_snapshot("custom")
        assert before is not None and before.epoch >= 1

        real_verify = verification.verify_provider_credential
        interleaved: dict[str, object] = {}

        def verify_while_the_operator_deletes_and_repastes(secret, descriptor, **kw):
            outcome = real_verify(secret, descriptor, **kw)
            if not interleaved:
                s, deleted = pact_rig.post("/api/intake/quarantine/delete", {"provider_id": "custom"})
                assert s == 200 and _payload(deleted).get("deleted") is True, deleted
                (active_data_dir() / "quarantine_epochs.json").write_text(json.dumps({"openrouter": 7}))
                sid2 = _begin_classify(pact_rig, R6_KEY, base_url=endpoint)
                s, repasted = pact_rig.post("/api/intake/complete", {"session_id": sid2, "persist": "later"})
                assert s == 200, repasted
                interleaved["after"] = _store_and_descriptor()[0].quarantine_snapshot("custom")
            return outcome

        monkeypatch.setattr(verification, "verify_provider_credential", verify_while_the_operator_deletes_and_repastes)
        status, retried = pact_rig.post("/api/intake/quarantine/retry", {"provider_id": "custom"})
        assert status == 200, retried
        after = interleaved["after"]
        assert after is not None and after.epoch == before.epoch, (
            "precondition: the lost mark restarts the counter at the deleted operation's epoch"
        )
        body = _payload(retried)
        assert body.get("promoted") is False and body.get("outcome") == "quarantine_changed", retried
        assert credential_store.get_credential("llm.cloud.custom") is None, "a stale retry promoted the re-paste"
        current = _store_and_descriptor()[0].quarantine_snapshot("custom")
        assert current == after and current.secret == R6_KEY, "the re-paste did not survive intact"


def test_current_handle_still_operates_after_the_epoch_mark_is_lost(pact_rig):
    """R1 PRESERVATION control: losing the epoch authority after a completed delete does not
    refuse ordinary work. The same-value re-paste is created normally, the deleted operation's
    handle is refused, and the NEW operation's own handle still deletes it."""
    from core.credential_intelligence.store import IntakeRefusedError
    from core.runtime_paths import active_data_dir

    store, descriptor = _store_and_descriptor()
    endpoint = "http://127.0.0.1:11/v1"
    store.save_quarantined(descriptor, R6_KEY, endpoint=endpoint)
    old = store.quarantine_snapshot("custom")
    assert old.epoch == 1, "first initialization starts at epoch 1"
    assert store.delete_quarantined("custom", snapshot=old).removed
    (active_data_dir() / "quarantine_epochs.json").unlink()

    restarted, descriptor = _store_and_descriptor()
    restarted.save_quarantined(descriptor, R6_KEY, endpoint=endpoint)
    new = restarted.quarantine_snapshot("custom")
    with pytest.raises(IntakeRefusedError):
        restarted.delete_quarantined("custom", snapshot=old)
    assert restarted.quarantine_snapshot("custom") == new, "a refused stale handle changed the new paste"
    assert restarted.delete_quarantined("custom", snapshot=new).removed, "the current handle was refused"
    assert restarted.quarantine_snapshot("custom") is None


def test_legacy_row_without_operation_identity_keeps_its_handle_and_cannot_capture_a_new_paste(pact_rig):
    """R1 MIGRATION control: a quarantine row written before operation identities existed (epoch,
    no operation id — the revision-5 row shape) keeps its own current handle working; once that
    legacy operation is deleted and the epoch authority is lost, its stale handle cannot delete
    the recreated same-value paste, whose own handle can."""
    from core.credential_intelligence.binding import index_path
    from core.credential_intelligence.store import IntakeRefusedError
    from core.runtime_paths import active_data_dir

    store, descriptor = _store_and_descriptor()
    endpoint = "http://127.0.0.1:12/v1"
    store.save_quarantined(descriptor, R6_KEY, endpoint=endpoint)
    rows = json.loads(index_path().read_text(encoding="utf-8"))
    rows["custom"].pop("quarantine_operation_id", None)
    index_path().write_text(json.dumps(rows), encoding="utf-8")

    legacy = store.quarantine_snapshot("custom")
    assert store.delete_quarantined("custom", snapshot=legacy).removed, "a legacy row's own handle was refused"
    (active_data_dir() / "quarantine_epochs.json").unlink()

    store.save_quarantined(descriptor, R6_KEY, endpoint=endpoint)
    recreated = store.quarantine_snapshot("custom")
    assert recreated.epoch == legacy.epoch, "precondition: the recreated paste reuses the legacy epoch"
    with pytest.raises(IntakeRefusedError):
        store.delete_quarantined("custom", snapshot=legacy)
    assert store.delete_quarantined("custom", snapshot=recreated).removed


# ================================================================== R4: strict reads

def test_a_vault_writer_never_replaces_a_vault_it_could_not_read(pact_rig):
    """R4 NOVEL backend case: a crash mid-write leaves the vault file truncated (its writer is not
    atomic). The next credential save must refuse rather than read the damaged file as empty and
    replace it — together with every credential it still holds — by a one-entry vault."""
    from core import credential_store as cs
    from core.runtime_paths import data_path

    cs.store_credential("r6.writer.one", "writer-value-one", label="r6 writer")
    cs.store_credential("r6.writer.two", "writer-value-two", label="r6 writer")
    vault = data_path("credentials.enc.json")
    intact = vault.read_bytes()
    damaged = intact[: len(intact) // 2]
    vault.write_bytes(damaged)
    with pytest.raises(Exception) as caught:
        cs.store_credential("r6.writer.three", "writer-value-three", label="r6 writer")
    assert vault.read_bytes() == damaged, "a writer replaced a vault it could not read"
    assert type(caught.value).__name__ == "CredentialReadError", repr(caught.value)


def test_strict_reads_tell_unreadable_vault_states_from_absence(pact_rig):
    """R4 backend-boundary control: inside strict_reads an unreadable vault file (permissions) and an
    entry that does not authenticate raise CredentialReadError, while a genuinely absent name in a
    readable vault reads None; outside the scope every other consumer keeps its lenient
    value-or-None contract; and a clean home still reconciles with nothing unresolved."""
    from core import credential_store as cs
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.store import CredentialStore
    from core.runtime_paths import data_path

    clean = CredentialStore(default_registry()).reconcile()
    assert clean.unresolved == [] and clean.adopted == [], clean
    cs.store_credential("r6.boundary.one", "boundary-value-one", label="r6 boundary")
    with cs.strict_reads():
        assert cs.get_credential("r6.boundary.absent") is None, "a successful absent read must stay None"
        assert cs.get_credential("r6.boundary.one") == "boundary-value-one"

    vault = data_path("credentials.enc.json")
    vault.chmod(0o000)
    try:
        assert cs.get_credential("r6.boundary.one") is None, "the lenient contract changed for other consumers"
        with cs.strict_reads(), pytest.raises(cs.CredentialReadError):
            cs.get_credential("r6.boundary.absent")
    finally:
        vault.chmod(0o600)

    raw = json.loads(vault.read_text(encoding="utf-8"))
    raw["r6.boundary.one"]["ct_b64"] = raw["r6.boundary.one"]["ct_b64"][:-4] + "AAAA"
    vault.write_text(json.dumps(raw), encoding="utf-8")
    assert cs.get_credential("r6.boundary.one") is None
    with cs.strict_reads(), pytest.raises(cs.CredentialReadError):
        cs.get_credential("r6.boundary.one")


def test_replacement_save_over_unreadable_storage_refuses_before_journaling_and_the_prior_pair_survives(
    pact_rig, monkeypatch,
):
    """R4 NOVEL public-save case: pair A is committed; the credential vault becomes unreadable (file
    permissions). A verified replacement Save to B must refuse typed BEFORE it journals or writes
    anything — an unreadable prior pair is not "no prior pair". Once the vault is readable again,
    pair A still resolves and the real Test door goes green at A; the replacement then commits
    cleanly and A's key never reaches B's endpoint."""
    from core.cloud_providers import resolved_custom_pair
    from core.credential_intelligence.store import StorageUnavailableError
    from core.runtime_paths import active_data_dir, data_path
    from tests.test_r5_flows import LATER_BEARER, LATER_KEY, THIRD_KEY, _auth_probe, _verified

    with FakeProviderServer(_openai_chat([LATER_KEY])) as a, FakeProviderServer(_openai_chat([THIRD_KEY])) as b:
        _point_custom_at(monkeypatch, a.url + "/v1")
        store, descriptor = _store_and_descriptor()
        store.save_verified(descriptor, LATER_KEY, _verified(), endpoint=a.url + "/v1")
        vault = data_path("credentials.enc.json")
        journal = active_data_dir() / "credential_intake_journal.json"
        vault_before = vault.read_bytes()
        journal_before = journal.read_bytes() if journal.exists() else b""

        vault.chmod(0o000)
        try:
            with pytest.raises(StorageUnavailableError):
                store.save_verified(descriptor, THIRD_KEY, _verified(), endpoint=b.url + "/v1")
            # the pair reader tells the unreadable store from an absent pair too: typed, never ("", "")
            from core.credential_intelligence.store import StorageReadError

            with pytest.raises(StorageReadError):
                resolved_custom_pair()
        finally:
            vault.chmod(0o600)

        assert store._pending_intents() == {}, "an operation was journaled over an unreadable prior pair"
        assert (journal.read_bytes() if journal.exists() else b"") == journal_before
        assert vault.read_bytes() == vault_before, "the vault was rewritten while unreadable"
        assert resolved_custom_pair() == (a.url + "/v1", LATER_KEY)
        assert _auth_probe(pact_rig, monkeypatch, a.url + "/v1")["state"] == "ok"

        store.save_verified(descriptor, THIRD_KEY, _verified(), endpoint=b.url + "/v1")
        assert resolved_custom_pair() == (b.url + "/v1", THIRD_KEY)
        assert _auth_probe(pact_rig, monkeypatch, b.url + "/v1")["state"] == "ok"
        assert not any(r["auth_sha256"] == LATER_BEARER for r in b.requests), "A's key reached B's endpoint"


def test_failed_save_then_reconcile_over_unreadable_storage_retains_recovery_and_degrades_nothing(
    pact_rig, monkeypatch,
):
    """R4 NOVEL public failed-save / reconcile / restore flow: a verified replacement's index commit
    fails after both halves landed (pending intent). The vault then becomes unreadable and reconcile
    runs: nothing may be decided from reads that failed — the intent stays pending, committed row A
    is not degraded to missing, nothing is adopted and the pair keeps refusing with zero requests.
    Once storage is readable, a restarted reconcile completes the replacement and the real Test door
    goes green at B only."""
    import core.credential_intelligence.store as module
    from core.credential_intelligence.binding import load_index
    from core.credential_intelligence.store import StorageConflictError
    from core.runtime_paths import data_path
    from tests.test_r5_flows import LATER_KEY, THIRD_KEY, _auth_probe, _verified

    with FakeProviderServer(_openai_chat([LATER_KEY])) as a, FakeProviderServer(_openai_chat([THIRD_KEY])) as b:
        _point_custom_at(monkeypatch, a.url + "/v1")
        store, descriptor = _store_and_descriptor()
        store.save_verified(descriptor, LATER_KEY, _verified(), endpoint=a.url + "/v1")
        real_save_index = module.save_index

        def index_outage(rows):
            raise OSError("synthetic index commit failure")

        monkeypatch.setattr(module, "save_index", index_outage)
        with pytest.raises(OSError):
            store.save_verified(descriptor, THIRD_KEY, _verified(), endpoint=b.url + "/v1")
        monkeypatch.setattr(module, "save_index", real_save_index)
        assert "llm.cloud.custom" in store._pending_intents()

        vault = data_path("credentials.enc.json")
        vault.chmod(0o000)
        try:
            report = _store_and_descriptor()[0].reconcile()
        finally:
            vault.chmod(0o600)
        row = load_index()["custom"]
        assert row["status"] == "verified" and row.get("endpoint") == a.url + "/v1", (
            f"an unreadable read degraded the committed row: {row}")
        assert report.missing == [] and report.adopted == [] and report.deletions_completed == [], report
        assert "llm.cloud.custom" in store._pending_intents(), "an unknown read closed the pending operation"
        assert "custom" in report.unresolved, report
        with pytest.raises(StorageConflictError):
            _auth_probe(pact_rig, monkeypatch, b.url + "/v1")
        assert a.request_count == 0 and b.request_count == 0

        report = _store_and_descriptor()[0].reconcile()
        assert "custom" in report.adopted and report.unresolved == [], report
        assert _auth_probe(pact_rig, monkeypatch, b.url + "/v1")["state"] == "ok"
        assert b.saw_bearer(THIRD_KEY) and a.request_count == 0


class _ReadFailingKeyring:
    """An in-memory Keychain stand-in whose reads can fail the way a real backend does when it
    cannot answer: an exception that is neither a timeout nor an authorization denial."""

    class errors:
        class PasswordDeleteError(Exception):
            pass

    def __init__(self):
        self._store: dict[tuple[str, str], str] = {}
        self.read_failure: Exception | None = None

    def set_password(self, service, account, password):
        self._store[(service, account)] = password

    def get_password(self, service, account):
        if self.read_failure is not None:
            raise self.read_failure
        return self._store.get((service, account))

    def delete_password(self, service, account):
        if (service, account) not in self._store:
            raise self.errors.PasswordDeleteError("not found")
        del self._store[(service, account)]


def test_keychain_read_failures_keep_a_pending_delete_pending_and_the_binding_intact(pact_rig, monkeypatch):
    """R4 NOVEL Keychain case: a verified OpenRouter-slot binding lives in the Keychain and its delete
    times out at the bounded door (pending intent, binding retained). Reconcile then meets (a) a
    Keychain backend error that is neither a timeout nor a denial and (b) the supported failure
    signal, the process-wide breaker armed by a timed-out call. Neither may decide anything: the
    delete is not reported completed, the intent stays pending and the row is not degraded. With
    reads restored the secret is still present, so the unexecuted delete correctly stays pending."""
    import core.bounded_keyring as bounded_keyring
    import core.credential_store as backend
    from core.credential_intelligence.binding import load_index
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.store import CredentialStore, StorageUnavailableError
    from core.unattended_preflight import SecureStorageError
    from tests.test_r5_flows import _verified

    fake = _ReadFailingKeyring()
    monkeypatch.delenv("VOOL_CREDENTIAL_STORE", raising=False)
    monkeypatch.setenv("VOOL_KEYCHAIN_ALLOWED", "1")
    monkeypatch.setattr(backend, "_load_keyring", lambda: fake)
    try:
        assert backend.active_backend() == "keychain", "precondition: the Keychain path is live"
        registry = default_registry()
        descriptor = registry.get("openrouter")
        store = CredentialStore(registry)
        secret = "r6-keychain-synthetic-" + hashlib.sha256(b"r6-keychain").hexdigest()[:32]
        store.save_verified(descriptor, secret, _verified())

        real_delete = backend.delete_credential

        def delete_times_out(name):
            raise SecureStorageError("timeout_prompt_pending", "synthetic bounded delete timeout", "retry later")

        monkeypatch.setattr(backend, "delete_credential", delete_times_out)
        with pytest.raises(StorageUnavailableError):
            store.delete("openrouter")
        monkeypatch.setattr(backend, "delete_credential", real_delete)
        assert descriptor.credential_slot in store._pending_intents()

        for failure in ("backend_error", "breaker_armed"):
            if failure == "backend_error":
                fake.read_failure = RuntimeError("synthetic Keychain backend failure")
            else:
                bounded_keyring.note_keychain_blocked()
            try:
                report = CredentialStore(registry).reconcile()
            finally:
                fake.read_failure = None
                bounded_keyring._KEYCHAIN_BLOCKED = False
            row = load_index()["openrouter"]
            assert row["status"] == "verified", f"{failure}: an unknown read degraded the binding: {row}"
            assert "openrouter" not in report.deletions_completed, f"{failure}: a delete was claimed on an unknown read"
            assert descriptor.credential_slot in store._pending_intents(), f"{failure}: the pending delete was closed"
            assert "openrouter" in report.unresolved, (failure, report)

        report = CredentialStore(registry).reconcile()
        assert report.unresolved == [] and "openrouter" not in report.deletions_completed, report
        assert descriptor.credential_slot in store._pending_intents(), "the unexecuted delete must stay pending"
        assert backend.get_credential(descriptor.credential_slot) == secret
    finally:
        bounded_keyring._KEYCHAIN_BLOCKED = False


# ================================================================== R3: recovery survives until committed

def test_reconcile_keeps_a_rollback_pending_until_its_restoration_is_verified(pact_rig, monkeypatch):
    """R3 NOVEL case — a different branch (rollback, not adoption), a different interruption (the
    reconcile's restoring write returns without landing, not an index outage) and a different answer
    (prior pair A comes back, not the replacement). A replacement's key write fails after its endpoint
    landed and the in-transaction restoration fails too, leaving endpoint B beside key A under a
    pending intent. Reconcile's restoring write then returns without landing: the rollback is not
    verified, so the intent must stay pending instead of closing over a mixed pair that would refuse
    forever. A restarted reconcile restores A for real; the Test door is green at A, B never reached."""
    from core import credential_store as backend
    from core.credential_intelligence.store import StorageConflictError, StoreWriteTimeoutError
    from core.unattended_preflight import SecureStorageError
    from tests.test_r5_flows import LATER_KEY, THIRD_KEY, _auth_probe, _verified

    with FakeProviderServer(_openai_chat([LATER_KEY])) as a, FakeProviderServer(_openai_chat([THIRD_KEY])) as b:
        _point_custom_at(monkeypatch, a.url + "/v1")
        store, descriptor = _store_and_descriptor()
        store.save_verified(descriptor, LATER_KEY, _verified(), endpoint=a.url + "/v1")
        original = backend.store_credential
        broken_restore = [True]

        def key_write_fails_and_restoration_fails(slot, value, label=""):
            if slot == "llm.cloud.custom" and value == THIRD_KEY:
                raise SecureStorageError("timeout_prompt_pending", "synthetic bounded key write failure", "retry")
            if slot == "llm.cloud.custom_base_url" and value == a.url + "/v1" and broken_restore[0]:
                broken_restore[0] = False
                raise SecureStorageError("locked", "synthetic restoration failure", "retry")
            return original(slot, value, label=label)

        monkeypatch.setattr(backend, "store_credential", key_write_fails_and_restoration_fails)
        with pytest.raises(StoreWriteTimeoutError):
            store.save_verified(descriptor, THIRD_KEY, _verified(), endpoint=b.url + "/v1")

        def restoration_returns_without_landing(slot, value, label=""):
            if slot == "llm.cloud.custom_base_url" and value == a.url + "/v1":
                return None  # the call returns; the stored endpoint does not change
            return original(slot, value, label=label)

        monkeypatch.setattr(backend, "store_credential", restoration_returns_without_landing)
        _store_and_descriptor()[0].reconcile()
        monkeypatch.setattr(backend, "store_credential", original)
        assert "llm.cloud.custom" in store._pending_intents(), "an unverified rollback closed its recovery record"
        with pytest.raises(StorageConflictError):
            _auth_probe(pact_rig, monkeypatch, a.url + "/v1")
        assert a.request_count == 0 and b.request_count == 0

        report = _store_and_descriptor()[0].reconcile()
        assert report.conflicts == [] and report.unresolved == [], report
        assert store._pending_intents() == {}
        assert _auth_probe(pact_rig, monkeypatch, a.url + "/v1")["state"] == "ok"
        assert a.saw_bearer(LATER_KEY) and b.request_count == 0


def test_a_commit_whose_index_write_does_not_read_back_keeps_the_operation_pending(pact_rig, monkeypatch):
    """R3 related branch (the Save path's own commit): the replacement's endpoint and key land and the
    index write returns without persisting. The commit is not verifiable, so the Save must fail typed
    with its operation still pending — never close the record over an index that still names pair A,
    which would refuse the live pair B forever. Reconcile then completes the binding at B."""
    import core.credential_intelligence.store as module
    from core.cloud_providers import resolved_custom_pair
    from core.credential_intelligence.store import StorageConflictError
    from tests.test_r5_flows import LATER_KEY, THIRD_KEY, _auth_probe, _verified

    with FakeProviderServer(_openai_chat([LATER_KEY])) as a, FakeProviderServer(_openai_chat([THIRD_KEY])) as b:
        _point_custom_at(monkeypatch, a.url + "/v1")
        store, descriptor = _store_and_descriptor()
        store.save_verified(descriptor, LATER_KEY, _verified(), endpoint=a.url + "/v1")
        real_save_index = module.save_index
        monkeypatch.setattr(module, "save_index", lambda rows: None)  # returns, persists nothing
        with pytest.raises(StorageConflictError):
            store.save_verified(descriptor, THIRD_KEY, _verified(), endpoint=b.url + "/v1")
        monkeypatch.setattr(module, "save_index", real_save_index)
        assert "llm.cloud.custom" in store._pending_intents(), "an unverified index commit closed the operation"
        with pytest.raises(StorageConflictError):
            _auth_probe(pact_rig, monkeypatch, b.url + "/v1")
        assert a.request_count == 0 and b.request_count == 0

        report = _store_and_descriptor()[0].reconcile()
        assert "custom" in report.adopted, report
        assert resolved_custom_pair() == (b.url + "/v1", THIRD_KEY)
        assert _auth_probe(pact_rig, monkeypatch, b.url + "/v1")["state"] == "ok"


def test_restart_through_the_boot_path_recovers_a_failed_served_replacement_without_a_new_paste(
    pact_rig, monkeypatch,
):
    """R3 restart wiring through the served product: a served replacement Save's index commit fails
    after both halves landed, and an on-demand reconcile fails during the same outage (a second
    interruption). Then only a restart happens — no reconcile call, no new paste. The real boot path
    (apps.vool_api_server._bootstrap, the bootstrap a daemon start runs) must decide the pending
    operation before the provider lanes register, so the restarted runtime holds pair B, registers the
    custom lane at B and its Test door is green at B, with A's key never at B."""
    import core.credential_intelligence.store as module
    from apps.vool_api_server import _bootstrap
    from core.cloud_providers import resolved_custom_pair
    from core.model_registry import ModelRegistry
    from tests.test_r5_flows import LATER_BEARER, LATER_KEY, THIRD_KEY, _auth_probe, _served_verified_save

    with FakeProviderServer(_openai_chat([LATER_KEY])) as a, FakeProviderServer(_openai_chat([THIRD_KEY])) as b:
        _point_custom_at(monkeypatch, a.url + "/v1")
        _served_verified_save(pact_rig, LATER_KEY, a.url + "/v1")
        status, picked = pact_rig.post("/api/cloud/model", {"provider": "custom", "model": "lab/one", "confirm_paid": True})
        assert status == 200, picked

        _point_custom_at(monkeypatch, b.url + "/v1")
        sid = _begin_classify(pact_rig, THIRD_KEY, base_url=b.url + "/v1")
        status, verified = pact_rig.post("/api/intake/verify", {
            "session_id": sid, "provider_id": "custom", "base_url": b.url + "/v1"})
        assert status == 200 and verified["outcome"] == "verified", verified
        real_save_index = module.save_index

        def index_outage(rows):
            raise OSError("synthetic index commit failure")

        monkeypatch.setattr(module, "save_index", index_outage)
        try:
            status, done = pact_rig.post("/api/intake/complete", {"session_id": sid})
        except OSError as exc:
            status, done = None, repr(exc)
        assert status != 200, done
        with pytest.raises(OSError):
            _store_and_descriptor()[0].reconcile()
        monkeypatch.setattr(module, "save_index", real_save_index)
        assert "llm.cloud.custom" in _store_and_descriptor()[0]._pending_intents(), (
            "the second interruption lost the recovery record")

        _bootstrap(run_prewarm=False)  # restart: no reconcile call, no new paste

        assert _store_and_descriptor()[0]._pending_intents() == {}, "the restart did not decide the operation"
        assert resolved_custom_pair() == (b.url + "/v1", THIRD_KEY)
        lane = ModelRegistry().get_manifest("custom-byok", "lab/one")
        assert lane is not None and lane.runtime_config["base_url"] == b.url + "/v1", (
            "the restarted runtime did not register the recovered lane at B")
        assert _auth_probe(pact_rig, monkeypatch, b.url + "/v1")["state"] == "ok"
        assert b.saw_bearer(THIRD_KEY) and not any(r["auth_sha256"] == LATER_BEARER for r in b.requests)


def test_an_undecidable_pending_pair_at_restart_keeps_its_record_refuses_and_does_not_abort_boot(
    pact_rig, monkeypatch,
):
    """R3 refusal/preservation control: a replacement leaves a FOREIGN key (neither the replacement nor
    the prior key) in the slot its pending intent names. Recovery at restart cannot decide that — it
    is the operator's — so the restarted boot must still complete, the record must stay pending, and
    the pair must keep refusing with no further requests to either endpoint."""
    from apps.vool_api_server import _bootstrap
    from core import credential_store as backend
    from core.cloud_providers import resolved_custom_pair
    from core.credential_intelligence.store import StorageConflictError
    from core.unattended_preflight import SecureStorageError
    from tests.test_r5_flows import LATER_KEY, THIRD_KEY, _auth_probe, _served_verified_save, _verified

    foreign = "nv1_foreign-" + hashlib.sha256(b"r6-foreign").hexdigest()[:32]
    with FakeProviderServer(_openai_chat([LATER_KEY])) as a, FakeProviderServer(_openai_chat([THIRD_KEY])) as b:
        _point_custom_at(monkeypatch, a.url + "/v1")
        _served_verified_save(pact_rig, LATER_KEY, a.url + "/v1")
        status, picked = pact_rig.post("/api/cloud/model", {"provider": "custom", "model": "lab/one", "confirm_paid": True})
        assert status == 200, picked
        store, descriptor = _store_and_descriptor()
        original = backend.store_credential

        def a_foreign_value_lands(slot, value, label=""):
            if slot == "llm.cloud.custom" and value == THIRD_KEY:
                original(slot, foreign, label=label)
                raise SecureStorageError("timeout_prompt_pending", "synthetic lost reply", "retry")
            return original(slot, value, label=label)

        monkeypatch.setattr(backend, "store_credential", a_foreign_value_lands)
        with pytest.raises(StorageConflictError):
            store.save_verified(descriptor, THIRD_KEY, _verified(), endpoint=b.url + "/v1")
        monkeypatch.setattr(backend, "store_credential", original)
        requests_before = (a.request_count, b.request_count)

        runtime = _bootstrap(run_prewarm=False)  # must complete: an undecidable pair never aborts boot
        assert runtime is not None
        assert "llm.cloud.custom" in _store_and_descriptor()[0]._pending_intents(), "recovery closed a foreign conflict"
        with pytest.raises(StorageConflictError):
            resolved_custom_pair()
        with pytest.raises(StorageConflictError):
            _auth_probe(pact_rig, monkeypatch, b.url + "/v1")
        assert (a.request_count, b.request_count) == requests_before, "a request was made on an undecided pair"


# ================================================================== R2: the dispatched lane is the decided lane

def _completions(server):
    return [record for record in server.requests if record["path"].endswith("/chat/completions")]


def _final_answer_request(text):
    """A final-answer request: no planner, conductor, verifier or tool-intent marker."""
    from adapters.base_adapter import ModelRequest

    return ModelRequest(task_kind="chat", prompt=text, messages=[{"role": "user", "content": text}])


def _certify_lane(manifest):
    """The REAL local tool-certification probe over one lane, driven by the sealed scripted probe
    exchange (a labelled model stand-in answering the probe's own tool protocol). The production store
    records the verdict under a fingerprint that binds the lane's destination."""
    from core.local_model_tool_certification import run_local_model_tool_certification
    from tests.test_local_model_tool_certification import ProbeExchange

    return run_local_model_tool_certification(manifest, exchange=ProbeExchange())


def _real_reservation(manifest, task_id):
    """A REAL paid reservation through the production owner-pick path and the spend ledger."""
    from types import SimpleNamespace

    from core.paid_call_reservation import reserve_owner_pick_paid_call

    denial: dict[str, str] = {}
    authorization = reserve_owner_pick_paid_call(
        manifest=manifest, task=SimpleNamespace(task_id=task_id),
        source_context={"_owner_local": True, "turn_id": f"turn-{task_id}", "session_id": "r6-dispatch"},
        task_kind="chat", call_role="answer_generation", denial=denial,
    )
    assert authorization is not None, f"precondition: a real reservation could not be made: {denial}"
    return authorization


def _answer_context(authorization, **extra):
    """The source context the router builds for a reserved answer call (model_call_role is the
    final-answer role token)."""
    return {"authorized_paid_call": authorization, "_owner_local": True,
            "model_call_role": "answer_generation", **extra}


def _reservation_rows(task_id):
    from storage.db import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT model_call_id, status FROM model_spend_reservations WHERE task_id = ?", (task_id,)
        ).fetchall()
        return [tuple(row) for row in rows]
    finally:
        conn.close()


def _chat_buffered_or_streaming(valid_keys, reply, mode):
    """A loopback OpenAI-compatible stand-in whose completions answer as JSON, or as an SSE stream
    while ``mode["stream"]`` is set. Labelled synthetic service — never a live provider."""
    accepted = {hashlib.sha256(f"Bearer {k}".encode()).hexdigest() for k in valid_keys}

    def respond(record):
        if record["auth_sha256"] not in accepted:
            return (401, {"error": {"message": "no", "type": "invalid_request_error", "code": "invalid_api_key"}})
        if record["path"].endswith("/models"):
            return (200, {"object": "list", "data": [{"id": "lab/one", "context_length": 8192}]})
        if record["path"].endswith("/chat/completions"):
            if mode.get("stream"):
                frames = [
                    {"model": "lab/one", "choices": [{"index": 0, "delta": {"role": "assistant", "content": reply},
                                                      "finish_reason": None}]},
                    {"model": "lab/one", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                     "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}},
                ]
                body = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n"
                return (200, body, {"Content-Type": "text/event-stream"})
            return (200, {
                "model": "lab/one",
                "choices": [{"message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
            })
        return (404, {"error": {"message": "no"}})

    return respond


def _served_custom_lane(pact_rig, monkeypatch, key, base_url):
    """Served Settings Save at ``base_url`` plus a real model selection; returns the registered lane."""
    from core.model_registry import ModelRegistry
    from tests.test_r5_flows import _served_verified_save

    _point_custom_at(monkeypatch, base_url)
    _served_verified_save(pact_rig, key, base_url)
    status, picked = pact_rig.post("/api/cloud/model", {"provider": "custom", "model": "lab/one", "confirm_paid": True})
    assert status == 200, picked
    lane = ModelRegistry().get_manifest("custom-byok", "lab/one")
    assert lane is not None and lane.runtime_config["base_url"] == base_url
    return lane


def test_the_actual_certification_authority_refuses_a_replaced_destination_for_a_stale_lane(pact_rig, monkeypatch):
    """R2 NOVEL case with the ACTUAL eligibility authority (no stand-in verdict), a final-answer role
    and REAL paid reservations. Lane A is measured by the real certification probe; the operator
    replaces the pair with B through the served Save; B was never measured, so its fingerprint reads
    stale. A caller still holding lane A must be refused exactly like a direct invocation of the
    current lane B — author_not_certified_for_final_answer, no request to either endpoint — and each
    reservation stays standing, neither consumed nor charged."""
    from types import SimpleNamespace

    from core import final_answer_authorship
    from core.memory_first_router import MemoryFirstRouter
    from core.model_registry import ModelRegistry
    from core.model_spend_ledger import get_spend_reservation
    from tests.test_r5_flows import LATER_KEY, THIRD_KEY

    final_answer_authorship.reset_for_tests()
    with FakeProviderServer(_openai_chat([LATER_KEY], reply="lane A stand-in reply")) as a, \
         FakeProviderServer(_openai_chat([THIRD_KEY], reply="lane B stand-in reply")) as b:
        stale = _served_custom_lane(pact_rig, monkeypatch, LATER_KEY, a.url + "/v1")
        assert _certify_lane(stale)["state"] == "verified"
        assert final_answer_authorship.author_certification(stale).certified, "precondition: lane A is certified"

        current = _served_custom_lane(pact_rig, monkeypatch, THIRD_KEY, b.url + "/v1")
        assert not final_answer_authorship.author_certification(current).certified, (
            "the certification verdict measured at destination A was served for destination B")

        router = MemoryFirstRouter(ModelRegistry())
        requests_before = (len(a.requests), len(b.requests))
        for label, held in (("direct", current), ("stale", stale)):
            task_id = f"r6-refused-{label}"
            authorization = _real_reservation(held, task_id)
            _adapter, response, error = router._invoke_manifest(
                manifest=held, request=_final_answer_request("disposable eligibility probe"),
                output_mode="plain_text", task=SimpleNamespace(task_id=task_id),
                source_context=_answer_context(authorization))
            assert error == "author_not_certified_for_final_answer" and response is None, (label, error)
            assert (len(a.requests), len(b.requests)) == requests_before, f"{label}: a request reached a provider"
            reservation = get_spend_reservation(authorization.model_call_id)
            assert reservation.status == "reserved" and float(reservation.actual_usd) == 0.0, (label, reservation)


def test_a_stale_lane_reaches_a_newly_certified_destination_buffered_and_streaming(pact_rig, monkeypatch):
    """R2 NOVEL case in the other direction, same actual authority: lane A was never measured; the
    operator replaces the pair with B and the real probe certifies B. A caller still holding lane A
    gets what a direct invocation of B gets — the final answer from B, with B's key — on the buffered
    path AND on the streaming path. Each call carries its own standing reservation (exactly one
    reservation row, never a second) and settles it once; A is never contacted, A's key never reaches
    B."""
    from types import SimpleNamespace

    from core import final_answer_authorship
    from core.memory_first_router import MemoryFirstRouter
    from core.model_registry import ModelRegistry
    from tests.test_r5_flows import LATER_BEARER, LATER_KEY, THIRD_BEARER, THIRD_KEY

    final_answer_authorship.reset_for_tests()
    mode = {"stream": False}
    with FakeProviderServer(_openai_chat([LATER_KEY], reply="lane A stand-in reply")) as a, \
         FakeProviderServer(_chat_buffered_or_streaming([THIRD_KEY], "lane B stand-in reply", mode)) as b:
        stale = _served_custom_lane(pact_rig, monkeypatch, LATER_KEY, a.url + "/v1")
        assert not final_answer_authorship.author_certification(stale).certified, "precondition: A never certified"

        current = _served_custom_lane(pact_rig, monkeypatch, THIRD_KEY, b.url + "/v1")
        assert _certify_lane(current)["state"] == "verified"
        assert final_answer_authorship.author_certification(current).certified, (
            "the probe's verdict for destination B was not served for B")

        router = MemoryFirstRouter(ModelRegistry())
        a_before = len(a.requests)
        for path in ("buffered", "streaming"):
            mode["stream"] = path == "streaming"
            task_id = f"r6-allowed-{path}"
            authorization = _real_reservation(stale, task_id)
            extra = {"runtime_event_stream_id": f"r6-stream-{task_id}"} if path == "streaming" else {}
            completions_before = len(_completions(b))
            _adapter, response, error = router._invoke_manifest(
                manifest=stale, request=_final_answer_request(f"disposable {path} dispatch"),
                output_mode="plain_text", task=SimpleNamespace(task_id=task_id),
                source_context=_answer_context(authorization, **extra))
            assert error is None and response is not None, (path, error)
            assert "lane B stand-in reply" in str(response.output_text), (path, response.output_text)
            dispatched = _completions(b)[completions_before:]
            assert len(dispatched) == 1 and dispatched[0]["auth_sha256"] == THIRD_BEARER, (path, dispatched)
            assert len(a.requests) == a_before, f"{path}: the replaced destination was contacted"
            rows = _reservation_rows(task_id)
            assert len(rows) == 1 and rows[0][1] == "settled", (path, rows)
        assert not any(record["auth_sha256"] == LATER_BEARER for record in b.requests), "A's key reached B"


def test_a_new_certification_run_supersedes_a_cached_refusal_for_that_destination(pact_rig, monkeypatch):
    """R2 FOLLOW-UP found by the served drive (daemon HTTP): a turn refused because the current
    destination B was uncertified leaves that verdict cached; the operator then certifies B with the
    real probe. The very next turn must be decided on the NEW measured verdict — answered by B with
    B's key — not refused from the cached verdict for the rest of its lifetime."""
    from types import SimpleNamespace

    from core import final_answer_authorship
    from core.memory_first_router import MemoryFirstRouter
    from core.model_registry import ModelRegistry
    from tests.test_r5_flows import LATER_KEY, THIRD_BEARER, THIRD_KEY

    final_answer_authorship.reset_for_tests()
    with FakeProviderServer(_openai_chat([LATER_KEY], reply="lane A stand-in reply")) as a, \
         FakeProviderServer(_openai_chat([THIRD_KEY], reply="lane B stand-in reply")) as b:
        stale = _served_custom_lane(pact_rig, monkeypatch, LATER_KEY, a.url + "/v1")
        current = _served_custom_lane(pact_rig, monkeypatch, THIRD_KEY, b.url + "/v1")
        router = MemoryFirstRouter(ModelRegistry())

        refused = _real_reservation(stale, "r6-before-certification")
        _adapter, response, error = router._invoke_manifest(
            manifest=stale, request=_final_answer_request("disposable pre-certification turn"),
            output_mode="plain_text", task=SimpleNamespace(task_id="r6-before-certification"),
            source_context=_answer_context(refused))
        assert error == "author_not_certified_for_final_answer" and response is None, error

        assert _certify_lane(current)["state"] == "verified"
        authorization = _real_reservation(stale, "r6-after-certification")
        completions_before = len(_completions(b))
        _adapter, response, error = router._invoke_manifest(
            manifest=stale, request=_final_answer_request("disposable post-certification turn"),
            output_mode="plain_text", task=SimpleNamespace(task_id="r6-after-certification"),
            source_context=_answer_context(authorization))
        assert error is None and response is not None, f"the new certification was not honored: {error}"
        assert "lane B stand-in reply" in str(response.output_text), response.output_text
        dispatched = _completions(b)[completions_before:]
        assert len(dispatched) == 1 and dispatched[0]["auth_sha256"] == THIRD_BEARER, dispatched
        assert not _completions(a), "the replaced destination received a completion"


def test_local_only_still_refuses_the_resolved_lane_before_any_adapter_or_connection(pact_rig, monkeypatch):
    """R2 PRESERVATION control for the Local Only gate (it passed before the repair too, and is not
    claimed as a failure-first case): Local Only classifies locality by cost class, and the registrar
    mints every custom lane paid_cloud, so it refuses a custom lane on ANY destination. What this pins
    is that moving resolution ahead of the gates kept that refusal whole: lane A is certified and holds
    a real reservation, the committed pair is replaced by a REMOTE destination (a synthetic verified
    outcome stands in for the remote verification), and a caller holding lane A — like a direct
    invocation of the resolved remote lane — is refused with auto_local_only_blocked_cloud_manifest
    before any adapter is entered, with no connection attempt, and with its reservation left
    standing."""
    import socket
    from types import SimpleNamespace

    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
    from core import final_answer_authorship
    from core.memory_first_router import MemoryFirstRouter
    from core.model_registry import ModelRegistry
    from core.runtime_provider_defaults import activate_provider_byok
    from tests.test_r5_flows import LATER_KEY, _verified

    remote = "https://r6-remote-destination.invalid/v1"
    remote_key = "nv1_r6remote-" + hashlib.sha256(b"r6-remote").hexdigest()[:32]
    final_answer_authorship.reset_for_tests()
    with FakeProviderServer(_openai_chat([LATER_KEY])) as a:
        stale = _served_custom_lane(pact_rig, monkeypatch, LATER_KEY, a.url + "/v1")
        assert _certify_lane(stale)["state"] == "verified"
        store, descriptor = _store_and_descriptor()
        store.save_verified(descriptor, remote_key, _verified(), endpoint=remote)

        attempted_hosts: list[str] = []
        real_getaddrinfo = socket.getaddrinfo

        def recording_getaddrinfo(host, *args, **kwargs):
            if str(host) not in {"127.0.0.1", "localhost", "::1"}:
                attempted_hosts.append(str(host))
            return real_getaddrinfo(host, *args, **kwargs)

        entered: list[str] = []
        real_run = OpenAICompatibleAdapter.run_text_task

        def recording_run(self, request):
            entered.append(str(self.manifest.runtime_config.get("base_url")))
            return real_run(self, request)

        monkeypatch.setattr(socket, "getaddrinfo", recording_getaddrinfo)
        monkeypatch.setattr(OpenAICompatibleAdapter, "run_text_task", recording_run)
        router = MemoryFirstRouter(ModelRegistry())
        a_before = len(a.requests)

        authorization = _real_reservation(stale, "r6-local-only-stale")
        _adapter, response, error = router._invoke_manifest(
            manifest=stale, request=_final_answer_request("disposable local-only dispatch"),
            output_mode="plain_text", task=SimpleNamespace(task_id="r6-local-only-stale"),
            source_context=_answer_context(authorization, local_only=True))
        assert error == "auto_local_only_blocked_cloud_manifest" and response is None, error
        from core.model_spend_ledger import get_spend_reservation

        standing = get_spend_reservation(authorization.model_call_id)
        assert standing.status == "reserved" and float(standing.actual_usd) == 0.0, standing

        activate_provider_byok("custom")
        direct = ModelRegistry().get_manifest("custom-byok", "lab/one")
        assert direct is not None and direct.runtime_config["base_url"] == remote
        _adapter, response, error = router._invoke_manifest(
            manifest=direct, request=_final_answer_request("disposable local-only dispatch"),
            output_mode="plain_text", task=SimpleNamespace(task_id="r6-local-only-direct"),
            source_context={"_owner_local": True, "model_call_role": "answer_generation", "local_only": True})
        assert error == "auto_local_only_blocked_cloud_manifest" and response is None, error
        assert entered == [], f"an adapter was entered under Local Only: {entered}"
        assert attempted_hosts == [], f"a connection was attempted: {attempted_hosts}"
        assert len(a.requests) == a_before


def test_dispatch_resolution_keeps_cancellation_first_and_never_carries_a_reservation_onto_new_terms(
    pact_rig, monkeypatch,
):
    """R2 refusal/preservation controls, on a pair the store committed without re-registering the lane
    (the registry is behind the store). (a) An operator cancellation still wins before anything is
    resolved: no registrar call, no request. (b) A resolved lane without a reservation still refuses at
    the paid gate (a planner-role call: this proves the paid gate, not chat authorship). (c) CONTROLLED
    registrar output — labelled: the production registrar always mints paid_cloud custom lanes — when
    the re-registered lane carries different economic terms than the reservation was granted under,
    the reservation is released, never consumed or carried, and the call refuses with
    paid_call_terms_changed and no request."""
    import threading
    from types import SimpleNamespace

    import core.runtime_provider_defaults as registrar
    from adapters.base_adapter import ModelRequest
    from core.memory_first_router import MemoryFirstRouter
    from core.model_registry import ModelRegistry
    from core.model_spend_ledger import get_spend_reservation
    from tests.test_r5_flows import LATER_KEY, THIRD_KEY, _verified

    with FakeProviderServer(_openai_chat([LATER_KEY])) as a, FakeProviderServer(_openai_chat([THIRD_KEY])) as b:
        stale = _served_custom_lane(pact_rig, monkeypatch, LATER_KEY, a.url + "/v1")
        _point_custom_at(monkeypatch, b.url + "/v1")
        store, descriptor = _store_and_descriptor()
        store.save_verified(descriptor, THIRD_KEY, _verified(), endpoint=b.url + "/v1")
        router = MemoryFirstRouter(ModelRegistry())
        requests_before = (len(a.requests), len(b.requests))

        def planner_request(text):
            return ModelRequest(task_kind="chat", prompt=text, messages=[{"role": "user", "content": text}],
                                metadata={"planner_call_kind": "task_plan"})

        activations: list[str] = []
        real_activate = registrar.activate_provider_byok

        def counted_activate(provider_id, env=None):
            activations.append(provider_id)
            return real_activate(provider_id, env)

        monkeypatch.setattr(registrar, "activate_provider_byok", counted_activate)
        cancelled = threading.Event()
        cancelled.set()
        _adapter, response, error = router._invoke_manifest(
            manifest=stale, request=planner_request("disposable cancelled call"), output_mode="plain_text",
            task=SimpleNamespace(task_id="r6-cancelled"), source_context={"cancel_event": cancelled, "_owner_local": True})
        assert error == "turn_cancelled" and response is None and activations == [], (error, activations)

        _adapter, response, error = router._invoke_manifest(
            manifest=stale, request=planner_request("disposable unpaid call"), output_mode="plain_text",
            task=SimpleNamespace(task_id="r6-unpaid"), source_context={"_owner_local": True})
        assert error == "paid_call_not_authorized_or_reserved" and response is None, error

        def registrar_with_changed_terms(provider_id, env=None):
            result = real_activate(provider_id, env)
            fresh = ModelRegistry().get_manifest("custom-byok", "lab/one")
            if fresh is not None:
                ModelRegistry().register_manifest(fresh.model_copy(update={
                    "metadata": {**fresh.metadata, "cost_class": "remote_unknown"}}))
            return result

        monkeypatch.setattr(registrar, "activate_provider_byok", registrar_with_changed_terms)
        authorization = _real_reservation(stale, "r6-terms")
        _adapter, response, error = router._invoke_manifest(
            manifest=stale, request=planner_request("disposable re-termed call"), output_mode="plain_text",
            task=SimpleNamespace(task_id="r6-terms"),
            source_context={"authorized_paid_call": authorization, "_owner_local": True})
        assert error == "paid_call_terms_changed" and response is None, error
        assert get_spend_reservation(authorization.model_call_id).status != "reserved", "the reservation was left standing"
        assert (len(a.requests), len(b.requests)) == requests_before, "a request reached a provider"
