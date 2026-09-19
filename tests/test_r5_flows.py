
"""Revision-5 flows: the verified credential TRANSACTION, durable operation IDENTITY, and
coherent model DISPATCH — each with its original review shape, a genuinely different novel
case, and refusal/preservation controls, through the real store, the real served dispatcher,
the real route authority and real loopback provider stand-ins. Synthetic keys and model
stand-ins only; the wire Authorization digest at the loopback services is the oracle.
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from tests._credential_intelligence_support import FakeProviderServer
from tests.first_run_pact_rig import pact_rig
from tests.test_quarantine_destination_isolation import (
    LATER_KEY,
    THIRD_KEY,
    _begin_classify,
    _point_custom_at,
)

LATER_BEARER = hashlib.sha256(f"Bearer {LATER_KEY}".encode()).hexdigest()
THIRD_BEARER = hashlib.sha256(f"Bearer {THIRD_KEY}".encode()).hexdigest()


def _store_and_descriptor():
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.store import CredentialStore

    return CredentialStore(default_registry()), default_registry().get("custom")


def _verified():
    return SimpleNamespace(status="verified", account="disposable", checked_at="2026-09-15T00:00:00Z")


def _openai_chat(valid_keys, reply="disposable model stand-in reply"):
    """A loopback OpenAI-compatible stand-in: /models proves the key, /chat/completions answers
    a real completion. Labelled synthetic service — never a live provider."""
    accepted = {hashlib.sha256(f"Bearer {k}".encode()).hexdigest() for k in valid_keys}

    def respond(record):
        keyed = record["auth_sha256"] in accepted
        if not keyed:
            return (401, {"error": {"message": "no", "type": "invalid_request_error", "code": "invalid_api_key"}})
        if record["path"].endswith("/models"):
            return (200, {"object": "list", "data": [{"id": "lab/one", "context_length": 8192}]})
        if record["path"].endswith("/chat/completions"):
            return (200, {
                "model": "lab/one",
                "choices": [{"message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
            })
        return (404, {"error": {"message": "no"}})

    return respond


def _served_verified_save(rig, key, base_url):
    """The real served Settings Save: intake begin → classify → preview → verify → complete."""
    sid = _begin_classify(rig, key, base_url=base_url)
    status, verified = rig.post("/api/intake/verify", {
        "session_id": sid, "provider_id": "custom", "base_url": base_url})
    assert status == 200 and verified["outcome"] == "verified", verified
    status, done = rig.post("/api/intake/complete", {"session_id": sid})
    assert status == 200, done
    return done


def _auth_probe(rig, monkeypatch, base_url):
    """Point the probe's descriptor resolution at the endpoint under test, then run the REAL
    Test door once (rate-limit reset). Returns the probe result."""
    from core import cloud_connection_state as ccs

    _point_custom_at(monkeypatch, base_url)
    ccs.reset_probe_rate_limit_for_tests()
    return ccs.run_auth_probe(provider="custom", now=3000.0)


# ================================================================== F1: the pair transaction

def test_lost_reply_fails_closed_then_reconcile_recovers_a_real_probe(pact_rig, monkeypatch):
    """ORIGINAL F1 shape plus its recovery, end to end: the key write LANDS and then RAISES
    (lost reply). The Test door must put key B nowhere (fail-closed on the pending intent);
    reconcile completes the committed pair from the journal's operation record; the SAME Test
    door then really probes the NEW endpoint and succeeds — recovery to a coherent permitted
    call, not a permanent blanket refusal."""
    from core import credential_store as backend
    from core.credential_intelligence.store import StorageConflictError, StoreWriteTimeoutError
    from core.unattended_preflight import SecureStorageError

    with FakeProviderServer(_openai_chat([LATER_KEY])) as a, FakeProviderServer(_openai_chat([THIRD_KEY])) as b:
        _point_custom_at(monkeypatch, a.url + "/v1")
        store, descriptor = _store_and_descriptor()
        store.save_verified(descriptor, LATER_KEY, _verified(), endpoint=a.url + "/v1")

        original = backend.store_credential
        armed = [True]

        def landed_but_no_receipt(slot, value, label=""):
            result = original(slot, value, label=label)
            if slot == "llm.cloud.custom" and value == THIRD_KEY and armed[0]:
                armed[0] = False
                raise SecureStorageError("simulated reply lost after committed write", "timeout", "retry")
            return result

        monkeypatch.setattr(backend, "store_credential", landed_but_no_receipt)
        with pytest.raises(StoreWriteTimeoutError):
            store.save_verified(descriptor, THIRD_KEY, _verified(), endpoint=b.url + "/v1")
        monkeypatch.setattr(backend, "store_credential", original)

        # fail-closed: the pending intent admits no pair, nothing travels anywhere
        with pytest.raises(StorageConflictError):
            _auth_probe(pact_rig, monkeypatch, b.url + "/v1")
        assert a.request_count == 0 and b.request_count == 0, "a keyed request was made on an unresolved pair"

        # recovery: reconcile decides the pending operation from its durable record
        store2, _ = _store_and_descriptor()
        report = store2.reconcile()
        assert "custom" in report.adopted, report

        result = _auth_probe(pact_rig, monkeypatch, b.url + "/v1")
        assert result["state"] == "ok", result
        assert b.saw_bearer(THIRD_KEY), "the recovered probe did not reach the new endpoint with the new key"
        assert not any(r["auth_sha256"] == LATER_BEARER for r in b.requests), "old key reached the new endpoint"
        assert a.request_count == 0, "the old endpoint received a request after the replacement"


def test_restoration_failure_is_reported_unresolved_and_reconcile_finishes_the_rollback(pact_rig, monkeypatch):
    """NOVEL F1 case (different data): the endpoint write lands, the key write raises BEFORE
    landing, and the in-transaction restoration of the prior endpoint FAILS. The surfaced
    error must not claim the prior pair was restored; the mixed live state must yield NO
    request; reconcile finishes the rollback; the probe then works against the RESTORED pair."""
    from core import credential_store as backend
    from core.credential_intelligence.store import StorageConflictError, StoreWriteTimeoutError
    from core.unattended_preflight import SecureStorageError

    with FakeProviderServer(_openai_chat([LATER_KEY])) as a, FakeProviderServer(_openai_chat([THIRD_KEY])) as b:
        _point_custom_at(monkeypatch, a.url + "/v1")
        store, descriptor = _store_and_descriptor()
        store.save_verified(descriptor, LATER_KEY, _verified(), endpoint=a.url + "/v1")

        original = backend.store_credential
        broken_restore = [True]

        def key_write_raises_and_restoration_fails(slot, value, label=""):
            if slot == "llm.cloud.custom" and value == THIRD_KEY:
                raise SecureStorageError("simulated bounded write failure", "timeout", "retry")
            if slot == "llm.cloud.custom_base_url" and value == a.url + "/v1" and broken_restore[0]:
                broken_restore[0] = False  # only the transaction's own restoration fails
                raise SecureStorageError("simulated restoration failure", "locked", "retry")
            return original(slot, value, label=label)

        monkeypatch.setattr(backend, "store_credential", key_write_raises_and_restoration_fails)
        with pytest.raises(StoreWriteTimeoutError) as caught:
            store.save_verified(descriptor, THIRD_KEY, _verified(), endpoint=b.url + "/v1")
        monkeypatch.setattr(backend, "store_credential", original)
        assert "FAILED" in str(caught.value) or "could not" in str(caught.value), caught.value
        assert "was restored and verified" not in str(caught.value), (
            "the error claims a restoration the schedule made impossible"
        )

        # the live state is mixed (endpoint B beside key A): no pair may leave the store
        with pytest.raises(StorageConflictError):
            _auth_probe(pact_rig, monkeypatch, a.url + "/v1")
        assert a.request_count == 0 and b.request_count == 0

        store2, _ = _store_and_descriptor()
        report = store2.reconcile()
        assert report.adopted == [] and report.conflicts == [], report  # a rollback, not an adoption

        result = _auth_probe(pact_rig, monkeypatch, a.url + "/v1")
        assert result["state"] == "ok", result
        assert a.saw_bearer(LATER_KEY)
        assert b.request_count == 0, "the abandoned endpoint received a request"


def test_late_completion_after_restart_adopts_the_committed_pair(pact_rig, monkeypatch):
    """NOVEL F1 case: the transaction fails with NOTHING landed; after a 'restart' (a fresh
    store instance) the bounded backend write completes LATE. Until reconcile the pair is
    unresolved and refuses; reconcile adopts the late landing as the committed pair from the
    pending intent's record; the probe then succeeds against the NEW endpoint only."""
    from core import credential_store as backend
    from core.credential_intelligence.store import StorageConflictError
    from core.unattended_preflight import SecureStorageError

    with FakeProviderServer(_openai_chat([LATER_KEY])) as a, FakeProviderServer(_openai_chat([THIRD_KEY])) as b:
        _point_custom_at(monkeypatch, a.url + "/v1")
        store, descriptor = _store_and_descriptor()
        store.save_verified(descriptor, LATER_KEY, _verified(), endpoint=a.url + "/v1")

        original = backend.store_credential

        def base_write_never_lands(slot, value, label=""):
            if slot == "llm.cloud.custom_base_url" and value == b.url + "/v1":
                raise SecureStorageError("simulated bounded write timeout", "timeout", "retry")
            return original(slot, value, label=label)

        monkeypatch.setattr(backend, "store_credential", base_write_never_lands)
        from core.credential_intelligence.store import StoreWriteTimeoutError

        with pytest.raises(StoreWriteTimeoutError):
            store.save_verified(descriptor, THIRD_KEY, _verified(), endpoint=b.url + "/v1")
        monkeypatch.setattr(backend, "store_credential", original)

        # restart: a fresh store sees the same durable journal
        store2, _ = _store_and_descriptor()
        with pytest.raises(StorageConflictError):
            _auth_probe(pact_rig, monkeypatch, a.url + "/v1")
        assert a.request_count == 0 and b.request_count == 0

        # the bounded door completes the write LATE, out from under the failed transaction
        backend.store_credential("llm.cloud.custom_base_url", b.url + "/v1", label="Custom base URL")
        backend.store_credential("llm.cloud.custom", THIRD_KEY, label="Custom (OpenAI-compatible)")

        # still refusing: the pending intent owns the state until reconcile decides it
        with pytest.raises(StorageConflictError):
            _auth_probe(pact_rig, monkeypatch, b.url + "/v1")
        assert a.request_count == 0 and b.request_count == 0

        report = store2.reconcile()
        assert "custom" in report.adopted, report
        result = _auth_probe(pact_rig, monkeypatch, b.url + "/v1")
        assert result["state"] == "ok", result
        assert b.saw_bearer(THIRD_KEY)
        assert a.request_count == 0


def test_index_commit_failure_leaves_pending_then_reconcile_completes_the_binding(pact_rig, monkeypatch):
    """Interrupted INDEX/READBACK leg: both backend writes land, the index commit fails. The
    pair refuses until reconcile writes the binding row from the pending intent; then the
    probe works against the NEW pair. Both slots stay together throughout."""
    import core.credential_intelligence.store as module
    from core.credential_intelligence.store import StorageConflictError

    with FakeProviderServer(_openai_chat([LATER_KEY])) as a, FakeProviderServer(_openai_chat([THIRD_KEY])) as b:
        _point_custom_at(monkeypatch, a.url + "/v1")
        store, descriptor = _store_and_descriptor()
        store.save_verified(descriptor, LATER_KEY, _verified(), endpoint=a.url + "/v1")

        real = module.save_index
        monkeypatch.setattr(module, "save_index", lambda rows: (_ for _ in ()).throw(OSError("injected index commit failure")))
        with pytest.raises(OSError):
            store.save_verified(descriptor, THIRD_KEY, _verified(), endpoint=b.url + "/v1")
        monkeypatch.setattr(module, "save_index", real)

        with pytest.raises(StorageConflictError):
            _auth_probe(pact_rig, monkeypatch, b.url + "/v1")
        assert a.request_count == 0 and b.request_count == 0

        store2, _ = _store_and_descriptor()
        report = store2.reconcile()
        assert "custom" in report.adopted, report
        result = _auth_probe(pact_rig, monkeypatch, b.url + "/v1")
        assert result["state"] == "ok", result
        assert b.saw_bearer(THIRD_KEY)
        assert a.request_count == 0


def test_served_replacement_keeps_normal_save_and_test_working(pact_rig, monkeypatch):
    """PRESERVATION control through the served doors: a clean verified replacement still saves,
    and the Test door goes green against the new pair — the transaction laws did not break the
    ordinary path."""
    from core import cloud_connection_state as ccs

    with FakeProviderServer(_openai_chat([LATER_KEY])) as a, FakeProviderServer(_openai_chat([THIRD_KEY])) as b:
        _point_custom_at(monkeypatch, a.url + "/v1")
        _served_verified_save(pact_rig, LATER_KEY, a.url + "/v1")
        ccs.reset_probe_rate_limit_for_tests()
        first = ccs.run_auth_probe(provider="custom", now=3100.0)
        assert first["state"] == "ok", first

        _point_custom_at(monkeypatch, b.url + "/v1")
        _served_verified_save(pact_rig, THIRD_KEY, b.url + "/v1")
        ccs.reset_probe_rate_limit_for_tests()
        second = ccs.run_auth_probe(provider="custom", now=3200.0)
        assert second["state"] == "ok", second
        assert b.saw_bearer(THIRD_KEY)
        assert not any(r["auth_sha256"] == LATER_BEARER for r in b.requests)
        assert a.saw_bearer(LATER_KEY), "the first save's probe did not happen"


# ================================================================== F2: operation identity

def test_invalid_schema_epoch_authority_refuses_instead_of_resetting(pact_rig):
    """NOVEL F2 case: the epoch authority exists with a WRONG SCHEMA (a JSON list). Allocation
    must refuse typed — a reset would hand a recreated paste the deleted operation's identity."""
    from core.runtime_paths import active_data_dir

    store, descriptor = _store_and_descriptor()
    store.save_quarantined(descriptor, LATER_KEY, endpoint="http://127.0.0.1:9/v1")
    old = store.quarantine_snapshot("custom")
    assert store.delete_quarantined("custom", snapshot=old).removed
    (active_data_dir() / "quarantine_epochs.json").write_text('["not", "a", "mapping"]')

    store2, descriptor2 = _store_and_descriptor()
    with pytest.raises(Exception) as caught:
        store2.save_quarantined(descriptor2, LATER_KEY, endpoint="http://127.0.0.1:9/v1")
    assert "epoch" in str(caught.value).lower(), caught.value
    assert store2.quarantine_snapshot("custom") is None, "a new operation was created over damaged authority"


def test_unreadable_epoch_authority_refuses(pact_rig):
    """NOVEL F2 case: the authority exists but cannot be READ (permissions damage). Refusal,
    not a fresh counter."""
    from core.runtime_paths import active_data_dir

    store, descriptor = _store_and_descriptor()
    store.save_quarantined(descriptor, LATER_KEY, endpoint="http://127.0.0.1:9/v1")
    old = store.quarantine_snapshot("custom")
    assert store.delete_quarantined("custom", snapshot=old).removed
    path = active_data_dir() / "quarantine_epochs.json"
    path.write_text("{}")
    path.chmod(0o000)
    try:
        store2, descriptor2 = _store_and_descriptor()
        with pytest.raises(Exception) as caught:
            store2.save_quarantined(descriptor2, LATER_KEY, endpoint="http://127.0.0.1:9/v1")
        assert "epoch" in str(caught.value).lower(), caught.value
    finally:
        path.chmod(0o644)


def test_epoch_authority_behind_its_rows_refuses(pact_rig):
    """NOVEL F2 case: the file PARSES but is BEHIND epochs its own rows carry (truncated
    damage). Refusal — a derived lower epoch could collide with a deleted operation."""
    from core.runtime_paths import active_data_dir

    store, descriptor = _store_and_descriptor()
    store.save_quarantined(descriptor, LATER_KEY, endpoint="http://127.0.0.1:9/v1")
    store.save_quarantined(descriptor, THIRD_KEY, endpoint="http://127.0.0.1:10/v1")
    # damage: forget every issued epoch while the live rows still name them
    (active_data_dir() / "quarantine_epochs.json").write_text("{}")
    store2, descriptor2 = _store_and_descriptor()
    with pytest.raises(Exception) as caught:
        store2.save_quarantined(descriptor2, LATER_KEY, endpoint="http://127.0.0.1:9/v1")
    assert "behind" in str(caught.value).lower() or "epoch" in str(caught.value).lower(), caught.value


def test_epochs_stay_monotonic_and_the_current_snapshot_still_deletes(pact_rig):
    """PRESERVATION controls: first initialization starts at 1; allocation is monotonic across
    instances, deletion and re-creation; and the CURRENT operation's own snapshot can still
    delete it (the authority gates staleness, not legitimate ownership)."""
    store, descriptor = _store_and_descriptor()
    store.save_quarantined(descriptor, LATER_KEY, endpoint="http://127.0.0.1:9/v1")
    first = store.quarantine_snapshot("custom")
    assert first.epoch == 1, first.epoch

    store2, descriptor2 = _store_and_descriptor()
    store2.save_quarantined(descriptor2, LATER_KEY, endpoint="http://127.0.0.1:10/v1")
    snap2 = store2.quarantine_snapshot("custom")
    assert snap2.epoch > first.epoch, (first.epoch, snap2.epoch)
    assert store2.delete_quarantined("custom", snapshot=snap2).removed

    store3, descriptor3 = _store_and_descriptor()
    store3.save_quarantined(descriptor3, LATER_KEY, endpoint="http://127.0.0.1:9/v1")
    snap3 = store3.quarantine_snapshot("custom")
    assert snap3.epoch > snap2.epoch, (snap2.epoch, snap3.epoch)
    assert store3.delete_quarantined("custom", snapshot=snap3).removed


# ================================================================== F3: coherent dispatch

def _byok_registry_and_adapter(base_url):
    """The production registrar + adapter, pointed at the committed pair the store holds."""
    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
    from core.model_registry import ModelRegistry
    from core.runtime_provider_defaults import _register_provider_byok_manifest

    registry = ModelRegistry()
    _register_provider_byok_manifest(
        registry, provider_id="custom", model_name="lab/one", env={}, api_key_env="",
        capabilities=["summarize"])
    manifest = registry.get_manifest("custom-byok", "lab/one")
    return registry, manifest, OpenAICompatibleAdapter(manifest)


def test_stream_adapter_after_replacement_refuses_without_disclosure(pact_rig, monkeypatch):
    """NOVEL F3 case: the STREAM path. An adapter holding endpoint A must not stream (or
    attempt to stream) with replacement key B against it."""
    from adapters.base_adapter import ModelRequest

    with FakeProviderServer(_openai_chat([LATER_KEY])) as a, FakeProviderServer(_openai_chat([THIRD_KEY])) as b:
        _point_custom_at(monkeypatch, a.url + "/v1")
        store, descriptor = _store_and_descriptor()
        store.save_verified(descriptor, LATER_KEY, _verified(), endpoint=a.url + "/v1")
        _registry, _manifest, adapter = _byok_registry_and_adapter(a.url + "/v1")

        store.save_verified(descriptor, THIRD_KEY, _verified(), endpoint=b.url + "/v1")
        request = ModelRequest(
            task_kind="chat", prompt="disposable stream review",
            messages=[{"role": "user", "content": "disposable stream review"}])
        with pytest.raises(Exception) as caught:
            list(adapter.stream_text_task(request))
        assert "registered for" in str(caught.value), caught.value
        assert a.request_count == 0 and b.request_count == 0, "a stale stream touched a wire"


def test_health_after_replacement_refuses_typed_without_disclosure(pact_rig, monkeypatch):
    """NOVEL F3 case: the health/Test consumer of the SAME adapter class. Typed stale refusal,
    zero requests — the current key never travels to the frozen destination."""
    with FakeProviderServer(_openai_chat([LATER_KEY])) as a, FakeProviderServer(_openai_chat([THIRD_KEY])) as b:
        _point_custom_at(monkeypatch, a.url + "/v1")
        store, descriptor = _store_and_descriptor()
        store.save_verified(descriptor, LATER_KEY, _verified(), endpoint=a.url + "/v1")
        _registry, _manifest, adapter = _byok_registry_and_adapter(a.url + "/v1")

        store.save_verified(descriptor, THIRD_KEY, _verified(), endpoint=b.url + "/v1")
        health = adapter.health_check()
        assert health["ok"] is False, health
        assert health.get("reason") == "stale_provider_binding", health
        assert a.request_count == 0 and b.request_count == 0


def _paid_call_auth(model_id: str, task_id: str):
    """A labelled SYNTHETIC paid-call authorization: the router's permission route runs its
    REAL checks (provider/model/task/reservation linkage) against this fixture."""
    call_id = f"model-call-r5-{hashlib.sha256(model_id.encode()).hexdigest()[:8]}"
    reservation = SimpleNamespace(status="reserved", model_call_id=call_id)
    capsule = SimpleNamespace(task_id=task_id)
    escalation = SimpleNamespace(model_id=model_id, capsule=capsule)
    return SimpleNamespace(
        model_call_id=call_id, escalation=escalation, capsule=capsule, reservation=reservation,
        provider_id=f"custom-byok:{model_id}")


def test_router_re_resolves_a_stale_lane_and_dispatches_to_the_committed_pair(pact_rig, monkeypatch):
    """F3 recovery at the ROUTE AUTHORITY: a caller holding the lane manifest from BEFORE a
    replacement drives the production invocation seam. The stale binding refuses, the lane is
    re-registered against the committed pair through the existing BYOK registrar, and the REAL
    model request lands at the NEW endpoint with the NEW key — fail-closed then recovered,
    never a silent redirect, never the old destination."""
    from adapters.base_adapter import ModelRequest
    from core.memory_first_router import MemoryFirstRouter
    from core.model_registry import ModelRegistry

    with FakeProviderServer(_openai_chat([LATER_KEY], reply="first stand-in reply")) as a, \
         FakeProviderServer(_openai_chat([THIRD_KEY], reply="second stand-in reply")) as b:
        # served Settings Save at pair A, then a real model selection on the custom lane
        _point_custom_at(monkeypatch, a.url + "/v1")
        _served_verified_save(pact_rig, LATER_KEY, a.url + "/v1")
        status, picked = pact_rig.post("/api/cloud/model", {
            "provider": "custom", "model": "lab/one", "confirm_paid": True})
        assert status == 200, picked

        stale_manifest = ModelRegistry().get_manifest("custom-byok", "lab/one")
        assert stale_manifest is not None and stale_manifest.runtime_config["base_url"] == a.url + "/v1"

        # served verified REPLACEMENT to pair B (this also re-registers the lane at B)
        _point_custom_at(monkeypatch, b.url + "/v1")
        _served_verified_save(pact_rig, THIRD_KEY, b.url + "/v1")

        router = MemoryFirstRouter(ModelRegistry())
        task = SimpleNamespace(task_id="r5-stale-lane-task")
        request = ModelRequest(
            task_kind="chat", prompt="disposable routed review",
            messages=[{"role": "user", "content": "disposable routed review"}],
            metadata={"planner_call_kind": "task_plan"})
        source_context = {
            "authorized_paid_call": _paid_call_auth("lab/one", "r5-stale-lane-task"),
            "_owner_local": True,
        }
        _adapter, response, error = router._invoke_manifest(
            manifest=stale_manifest, request=request, output_mode="plain_text",
            task=task, source_context=source_context)

        assert error is None, error
        assert response is not None and "second stand-in reply" in str(response.output_text), (
            "the recovered dispatch did not complete against the committed pair"
        )
        assert b.saw_bearer(THIRD_KEY), "the model request did not reach the new endpoint with the new key"
        assert not any(r["auth_sha256"] == LATER_BEARER for r in b.requests)
        assert not any(r["auth_sha256"] == THIRD_BEARER for r in a.requests), (
            "the replacement key reached the stale destination"
        )


def test_non_custom_lane_keeps_its_own_credential_resolution(pact_rig, monkeypatch):
    """PRESERVATION control: an explicitly NON-custom manifest credential (an env-keyed
    OpenAI-compatible lane at a fixed destination) keeps its existing key resolution — the
    coherence gate is scoped to the replaceable custom pair, not a blanket behavior change."""
    from adapters.base_adapter import ModelRequest
    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
    from core.model_registry import ModelRegistry
    from core.runtime_provider_defaults import _register_provider_byok_manifest

    env_key = "sk-env-lane-r5-" + hashlib.sha256(b"r5-env").hexdigest()[:24]
    monkeypatch.setenv("R5_ENV_LANE_KEY", env_key)
    with FakeProviderServer(_openai_chat([env_key], reply="env lane reply")) as service:
        registry = ModelRegistry()
        _register_provider_byok_manifest(
            registry, provider_id="moonshot", model_name="lab/one", env={},
            api_key_env="R5_ENV_LANE_KEY", capabilities=["summarize"])
        manifest = registry.get_manifest("moonshot-byok", "lab/one")
        # the moonshot descriptor's fixed vendor URL is redirected to the stand-in for the test
        manifest = manifest.model_copy(update={
            "runtime_config": {**manifest.runtime_config, "base_url": service.url + "/v1"}})
        adapter = OpenAICompatibleAdapter(manifest)
        response = adapter.run_text_task(ModelRequest(
            task_kind="chat", prompt="env lane check",
            messages=[{"role": "user", "content": "env lane check"}]))
        assert "env lane reply" in str(response.output_text)
        assert service.saw_bearer(env_key), "an env-keyed lane lost its credential resolution"


# ================================================================== the served dispatch flow

def test_served_save_test_then_real_model_request_after_replacement(pact_rig, monkeypatch):
    """The mission's served-flow acceptance: a real served Settings Save + Test at pair A, a
    real served verified REPLACEMENT to pair B, a second Test, and then an ACTUAL model
    request through the production invocation seam on the CURRENT lane — the wire
    Authorization digest at the loopback services is the oracle, the reply is the model
    stand-in's (labelled synthetic). The request is a PLANNER-role call (its real product
    consumer for an uncertified lane); the paid-call authorization is a labelled synthetic
    fixture the router's permission route fully re-checks."""
    from adapters.base_adapter import ModelRequest
    from core import cloud_connection_state as ccs
    from core.memory_first_router import MemoryFirstRouter
    from core.model_registry import ModelRegistry

    with FakeProviderServer(_openai_chat([LATER_KEY], reply="served first reply")) as a, \
         FakeProviderServer(_openai_chat([THIRD_KEY], reply="served replacement reply")) as b:
        # -- served Settings Save at pair A, real Test
        _point_custom_at(monkeypatch, a.url + "/v1")
        _served_verified_save(pact_rig, LATER_KEY, a.url + "/v1")
        ccs.reset_probe_rate_limit_for_tests()
        first = ccs.run_auth_probe(provider="custom", now=3300.0)
        assert first["state"] == "ok", first
        assert a.saw_bearer(LATER_KEY)

        status, picked = pact_rig.post("/api/cloud/model", {
            "provider": "custom", "model": "lab/one", "confirm_paid": True})
        assert status == 200, picked

        # -- served verified REPLACEMENT to pair B, real Test again
        _point_custom_at(monkeypatch, b.url + "/v1")
        _served_verified_save(pact_rig, THIRD_KEY, b.url + "/v1")
        ccs.reset_probe_rate_limit_for_tests()
        second = ccs.run_auth_probe(provider="custom", now=3400.0)
        assert second["state"] == "ok", second
        assert b.saw_bearer(THIRD_KEY)

        # -- an ACTUAL model request on the current lane, through the production consumer
        router = MemoryFirstRouter(ModelRegistry())
        manifest = ModelRegistry().get_manifest("custom-byok", "lab/one")
        assert manifest is not None, "the custom lane was not registered after the replacement"
        task = SimpleNamespace(task_id="r5-served-flow-task")
        request = ModelRequest(
            task_kind="chat", prompt="disposable served flow review",
            messages=[{"role": "user", "content": "disposable served flow review"}],
            metadata={"planner_call_kind": "task_plan"})
        _adapter, response, error = router._invoke_manifest(
            manifest=manifest, request=request, output_mode="plain_text",
            task=task, source_context={
                "authorized_paid_call": _paid_call_auth("lab/one", "r5-served-flow-task"),
                "_owner_local": True,
            })
        assert error is None, error
        assert response is not None and "served replacement reply" in str(response.output_text)

        completions = [r for r in b.requests if r["path"].endswith("/chat/completions")]
        assert completions and completions[-1]["auth_sha256"] == THIRD_BEARER, (
            "the production model request did not carry the committed key to the committed endpoint"
        )
        assert not any(r["auth_sha256"] == THIRD_BEARER for r in a.requests), (
            "the replacement key reached the OLD endpoint through any consumer"
        )
        assert not any(r["auth_sha256"] == LATER_BEARER for r in b.requests), (
            "the replaced key reached the NEW endpoint"
        )
