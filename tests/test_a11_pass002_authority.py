"""A11 minimum product truth — PASS 002.

Behavioral fences for the deterministic A11 PASS001 blockers the three reviewers
independently reproduced:

- the paid-model decision lives in the ONE mutation authority (set_cloud_model), so the
  HTTP endpoint can never be the only gate: the chat command, the NL switch intent, and
  direct/internal callers all refuse unconfirmed paid/unknown-cost pins identically;
- cold/missing catalog cost knowledge fails CLOSED (MODEL_COST_UNKNOWN /
  PAID_STATUS_UNKNOWN) — it never silently becomes "free" and never invents pricing;
- provider + model identity is canonicalized before the decision, so case, spacing, and
  prefix variants cannot dodge the row they really are;
- GET /api/cloud/model is owner-local symmetric with its POST;
- receipts derive `fallback_from` from the ACTUAL immediately preceding EXECUTED hop —
  never from the planned candidate chain, never self-referencing, never fabricated for
  candidates that never executed;
- the durable approval lifecycle is coherent across memory, disk mirror, restore flag,
  issuance, resolution, reset, and expiry: reset cannot be resurrected from disk, and a
  post-restart mint cannot erase another still-pending approval.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from adapters.cloud_provider_common import CloudProviderRequestError, classify_error
from core import runtime_paths
from core.cloud_broker import CloudModelBroker
from core.cloud_privacy_policy import CloudPrivacyGrant
from core.cloud_provider_contract import (
    CloudAccountLimits,
    CloudModelMetadata,
    CloudModelRequest,
    CloudModelResponse,
    CloudTaskRequirements,
    PricingState,
    PrivacyClass,
)
from core.cloud_route_receipt import list_cloud_route_receipts
from core.cloud_routing import CloudRouteMode

# ------------------------------------------------------------------ shared fixtures

CATALOG_PAYLOAD = {
    "data": [
        {"id": "zzz/alpha-big", "name": "Alpha Big", "context_length": 500000,
         "pricing": {"prompt": "0.000001", "completion": "0.000002", "request": "0"}},
        {"id": "deepseek/deepseek-chat-v3:free", "name": "DeepSeek Free", "context_length": 163840,
         "pricing": {"prompt": "0", "completion": "0", "request": "0"}},
    ]
}


@pytest.fixture()
def _cached_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    from datetime import datetime, timezone

    import core.openrouter_catalog as cat

    cache = tmp_path / "catalog_cache.json"
    cache.write_text(
        json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "payload": CATALOG_PAYLOAD})
    )
    monkeypatch.setattr(cat, "_cache_path", lambda: cache)
    monkeypatch.setattr(
        cat, "refresh_openrouter_catalog",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("no network in tests")),
    )
    yield
    runtime_paths.configure_runtime_home(None)


@pytest.fixture(autouse=True)
def _no_credentials(monkeypatch):
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)


def _model_post(body, client_host="127.0.0.1"):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    res = dispatch_post(
        path="/api/cloud/model",
        body=body,
        headers={"content-type": "application/json"},
        runtime=RuntimeServices(display_name="N"),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host=client_host,
    )
    try:
        return res.status, json.loads(res.body.decode("utf-8"))
    except Exception:
        return res.status, {}


def _pin_is(policy_model: str) -> bool:
    from core.cloud_escalation_policy import load_policy

    return load_policy().model == policy_model


# ------------------------------------------------- 1+2+3: one authority, fail closed, canonical

def test_paid_pin_refused_on_every_surface_identically(_cached_catalog):
    """THE headline counterexample: endpoint refuses but the chat command persists — dead.

    The same PAID id is refused by the HTTP endpoint AND by the `cloud model <id>` chat
    command, and the policy is untouched by both. Only the request-scoped confirmation
    gets the pin through, on either surface."""
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_model_command

    paid_id = "zzz/alpha-big"

    status, payload = _model_post({"model": paid_id})
    assert status == 409 and payload["code"] == "paid_model_confirm_required"
    assert not _pin_is(paid_id)

    reply = maybe_handle_cloud_model_command(f"cloud model {paid_id}", owner_local=True)
    assert reply is not None and "PAID_MODEL_CONFIRM_REQUIRED" in reply
    assert not _pin_is(paid_id), "the chat command persisted what the endpoint refused"

    # The request-scoped confirmation is validated per call by the SAME authority.
    _ok_status, _p = _model_post({"model": paid_id, "confirm_paid": True})
    assert _ok_status == 200 and _pin_is(paid_id)

    from core.cloud_escalation_policy import load_policy
    from core.cloud_model_control import set_cloud_model

    set_cloud_model("default", owner_local=True)
    assert load_policy().model == ""

    reply = maybe_handle_cloud_model_command(f"cloud model {paid_id} --paid", owner_local=True)
    assert reply is not None and "Cloud model set to" in reply
    assert _pin_is(paid_id), "explicit per-command confirmation is the other valid lane"


def test_case_spacing_and_prefix_variants_cannot_dodge_the_paid_decision(_cached_catalog):
    """Identity is canonicalized BEFORE the decision — the uppercase/whitespace bypass is dead.

    The catalog row is `zzz/alpha-big`; `ZZZ/Alpha-Big` and friends used to miss the exact
    row match and fall through to an unclassified (persist-anyway) pin. Every variant now
    hits the same refusal, on both surfaces."""
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_model_command

    variants = [
        "ZZZ/ALPHA-BIG",          # uppercase everything
        "zzz/Alpha-Big",          # mixed case
        " openrouter:ZZZ/Alpha-Big ",  # provider prefix variant with case + padding
    ]
    for variant in variants:
        model_field = variant.strip()
        status, payload = _model_post({"model": model_field})
        assert status == 409, (variant, status, payload)
        assert payload["code"] in {"paid_model_confirm_required", "MODEL_COST_UNKNOWN", "PAID_STATUS_UNKNOWN"}
        assert not _pin_is("ZZZ/ALPHA-BIG".lower().replace("openrouter:", "")), variant

        reply = maybe_handle_cloud_model_command(f"cloud model {model_field}", owner_local=True)
        assert reply is not None and "PAID_MODEL_CONFIRM_REQUIRED" in reply, variant
        assert not _pin_is("zzz/alpha-big"), f"variant {variant!r} persisted as a pin"


def test_adversarial_non_free_suffix_on_a_paid_base_is_not_read_as_free(_cached_catalog):
    """Near-miss: `zzz/alpha-big:nitro` looks like the free-variant shape but carries a
    non-:free tag on a base the catalog only knows as PAID. It must NOT be classified free
    (the :free suffix is the ONLY trusted free-shape tag); it is not in the catalog under
    that id, so the answer is unknown-cost, refused — never a silent pin, never 'free'."""
    status, payload = _model_post({"model": "zzz/alpha-big:nitro"})
    assert status == 409
    assert payload["code"] in {"MODEL_COST_UNKNOWN", "PAID_STATUS_UNKNOWN", "paid_model_confirm_required"}
    assert not _pin_is("zzz/alpha-big:nitro")


def test_free_and_control_pins_stay_allowed_without_confirmation(_cached_catalog):
    """Negative controls: the law narrows nothing it must not."""
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_model_command

    status, payload = _model_post({"model": "deepseek/deepseek-chat-v3:free"})
    assert status == 200 and payload["ok"] is True
    assert _pin_is("deepseek/deepseek-chat-v3:free")

    from core.cloud_escalation_policy import load_policy
    from core.cloud_model_control import set_cloud_model

    set_cloud_model("default", owner_local=True)
    assert load_policy().model == ""

    assert "Cloud model set to" in maybe_handle_cloud_model_command(
        "cloud model deepseek/deepseek-chat-v3:free", owner_local=True
    )


def test_remote_caller_cannot_pin_on_any_surface(_cached_catalog):
    """Owner-local enforcement is unchanged — and now ALSO first on the POST path."""
    status, payload = _model_post({"model": "zzz/alpha-big"}, client_host="10.9.9.9")
    assert status == 403 and payload["error"] == "owner_local_required"

    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_model_command

    assert "local session" in maybe_handle_cloud_model_command(
        "cloud model deepseek/deepseek-chat-v3:free", owner_local=False
    )
    assert not _pin_is("zzz/alpha-big") and not _pin_is("deepseek/deepseek-chat-v3:free")


def test_nl_switch_intent_cannot_self_certify_a_paid_model(monkeypatch, tmp_path):
    """'switch to gemmax' with no free variant in the family used to pin the PAID id AND
    assert _verified_free=True over it — the intent decided pricing. Now the intent consults
    the same server classifier, refuses, and names how to consent explicitly."""
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    try:
        import core.openrouter_catalog as cat

        models = (
            SimpleNamespace(model_id="vendor/gemmax2"),
            SimpleNamespace(model_id="other/tiny:free"),
        )
        monkeypatch.setattr(cat, "safe_all_models", lambda **kw: (models, 0.0))
        monkeypatch.setattr(cat, "model_is_free", lambda m: str(m.model_id).endswith(":free"))
        monkeypatch.setattr(cat, "model_pricing_is_known", lambda m: True)

        from core.agent_runtime.fast_command_surface import maybe_handle_cloud_switch_intent

        result = maybe_handle_cloud_switch_intent("switch to gemmax2", owner_local=True)
        assert result is not None
        assert result.get("reason") == "cloud_switch_paid_confirm_required"
        assert result.get("success") is False and result.get("advice_only") is True
        from core.cloud_escalation_policy import load_policy

        assert load_policy().model != "vendor/gemmax2"

        # The free variant of a family still switches directly, like the family contract says.
        models_free = (SimpleNamespace(model_id="vendor/gemmax2:free"),)
        monkeypatch.setattr(cat, "safe_all_models", lambda **kw: (models_free, 0.0))
        result = maybe_handle_cloud_switch_intent("switch to gemmax2", owner_local=True)
        assert result is not None and result.get("success") is True
        assert load_policy().model == "vendor/gemmax2:free"
    finally:
        runtime_paths.configure_runtime_home(None)


# --------------------------------------------------------------- GET/POST owner symmetry

def test_get_is_gated_like_the_post_that_sets_the_same_state(_cached_catalog):
    status, _payload = _model_post({"model": "deepseek/deepseek-chat-v3:free"})
    assert status == 200
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    res = dispatch_get(
        path="/api/cloud/model", query={}, runtime=RuntimeServices(display_name="N"),
        model_name="vool", client_host="10.2.2.2",
    )
    assert res.status == 403 and json.loads(res.body)["error"] == "owner_local_required"
    # The served selection state (loopback) still exposes the server-derived cost verdict.
    res_ok = dispatch_get(
        path="/api/cloud/model", query={}, runtime=RuntimeServices(display_name="N"),
        model_name="vool", client_host="127.0.0.1",
    )
    body = json.loads(res_ok.body)
    assert body["cost_state"] == "free" and body["model"] == "deepseek/deepseek-chat-v3:free"


# ------------------------------------------------------- 6: receipts reflect execution truth

REQ = CloudTaskRequirements(
    min_context_tokens=100,
    expected_output_tokens=20,
    required_capabilities=("text",),
    privacy_class=PrivacyClass.PUBLIC,
)
REQUEST = CloudModelRequest(
    task_id="task",
    turn_id="turn",
    subtask_id="sub",
    model_call_id="call",
    model_id="",
    messages=({"role": "user", "content": "hello"},),
    max_output_tokens=20,
    metadata={"session_id": "p2-broker"},
)


def _model(model_id="model", *, state=PricingState.FREE, price=0.0, provider_id="provider", context=8192):
    return CloudModelMetadata(
        provider_id=provider_id,
        model_id=model_id,
        display_name=model_id,
        pricing_state=state,
        input_usd_per_token=price,
        output_usd_per_token=price,
        request_usd=price,
        context_window=context,
        capabilities=("text",),
        discovered_at="2026-07-14T00:00:00+00:00",
        expires_at="2099-01-01T00:00:00+00:00",
        health_state="healthy",
        quota_remaining=10,
    )


class _Provider:
    def __init__(self, snapshots, responses=None, provider_id="provider"):
        self.provider_id = provider_id
        self.snapshots = list(snapshots)
        self.responses = list(responses or [CloudModelResponse("ok", {"cost": 0.0})])
        self.send_count = 0

    def discover_models(self, _transport):
        return self.snapshots.pop(0) if len(self.snapshots) > 1 else self.snapshots[0]

    def send_request(self, _transport, _request):
        self.send_count += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def classify_provider_error(self, error):
        return classify_error(error)

    def validate_credentials(self, _transport):
        return True, "ok"

    def normalize_model_metadata(self, payload, *, discovered_at):
        return None

    def get_account_limits(self, _transport):
        return CloudAccountLimits()

    def estimate_request_cost(self, model, *, input_tokens, output_tokens):
        return 0.0

    def check_model_health(self, _transport, model):
        return {"ok": True}

    def parse_usage(self, payload):
        return {}

    def revoke_or_clear_session_credentials(self):
        return None


class _Registry:
    def __init__(self, providers):
        self.providers = {provider.provider_id: provider for provider in providers}

    def list(self):
        return tuple(self.providers.values())

    def get(self, provider_id):
        return self.providers.get(provider_id)


def _receipts():
    rows = list_cloud_route_receipts("p2-broker")
    return [r.to_dict() if hasattr(r, "to_dict") else dict(r) for r in rows]


def test_single_candidate_first_try_success_reports_no_fallback(monkeypatch, tmp_path):
    """One candidate, first attempt succeeds -> `fallback_from` MUST be empty.

    The planned chain used to leak into this field as the model itself (fallback_chain[0]
    == the executing model on hop 0): a self-referencing fallback that never happened."""
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    provider = _Provider([(_model(),)], [CloudModelResponse("ok", {"cost": 0.0})])
    broker = CloudModelBroker(registry=_Registry([provider]), transports={"provider": object()}, sleeper=lambda _s: None)
    result = broker.execute(
        REQUEST, requirements=REQ, mode=CloudRouteMode.LOCAL_FREE,
        privacy_grant=CloudPrivacyGrant(allow_provider_fallback=True), max_attempts=5,
    )
    assert result.used_cloud
    started = [r for r in _receipts() if r["phase"] == "started"]
    assert started and all(r["fallback_from"] == "" for r in started), started


def test_multi_hop_fallback_names_the_actual_previous_executed_hop(monkeypatch, tmp_path):
    """Real fallback: hop N's receipt names the ACTUAL immediately preceding EXECUTED hop —
    not fallback_chain[0] of the plan, and not itself. Same-model retries stay empty."""
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    transient = CloudProviderRequestError(503, "temporary")
    provider = _Provider(
        [(_model("alpha", context=9000), _model("beta", context=100))],
        [transient, transient, CloudModelResponse("late", {"cost": 0.0})],
    )
    broker = CloudModelBroker(registry=_Registry([provider]), transports={"provider": object()}, sleeper=lambda _s: None)
    result = broker.execute(
        REQUEST, requirements=REQ, mode=CloudRouteMode.LOCAL_FREE,
        privacy_grant=CloudPrivacyGrant(allow_provider_fallback=True), max_attempts=5,
    )
    assert result.used_cloud and result.model_id == "beta"
    started = [r for r in _receipts() if r["phase"] == "started"]
    by_model = {r["model_id"]: r for r in started}
    assert set(by_model) == {"alpha", "beta"}
    assert by_model["alpha"]["fallback_from"] == "", "first hop has no predecessor"
    assert by_model["beta"]["fallback_from"] == "provider:alpha", started
    # Retrying alpha (attempt 2 on the same model) is a retry, not a fallback.
    alpha_started = [r for r in started if r["model_id"] == "alpha"]
    assert len(alpha_started) == 2
    assert all(r["fallback_from"] == "" for r in alpha_started)


def test_never_executed_candidates_are_not_named_as_predecessors(monkeypatch, tmp_path):
    """A candidate the guards rejected BEFORE any signed receipt (never executed) is not a
    hop — the next candidate's predecessor is the last model that ACTUALLY executed, or
    empty when none did."""
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    provider = _Provider(
        [(_model("alpha", context=9000), _model("beta", context=100)), (_model("beta", context=100),)],
        [CloudModelResponse("ok", {"cost": 0.0})],
    )
    broker = CloudModelBroker(registry=_Registry([provider]), transports={"provider": object()}, sleeper=lambda _s: None)
    result = broker.execute(
        REQUEST, requirements=REQ, mode=CloudRouteMode.LOCAL_FREE,
        privacy_grant=CloudPrivacyGrant(allow_provider_fallback=True), max_attempts=5,
    )
    assert result.used_cloud and result.model_id == "beta"
    started = [r for r in _receipts() if r["phase"] == "started"]
    assert [r["model_id"] for r in started] == ["beta"]
    assert started[0]["fallback_from"] == "", (
        "alpha never issued a receipt; beta's predecessor cannot be fabricated as provider:alpha"
    )


# --------------------------------------------------------------- 7: approval lifecycle

def _mint_pending(**overrides):
    from core.mode_permission_policy import OperatingMode, PermissionAction, _new_approval_request

    params = dict(
        session_id="p2-session",
        task_id="p2-task",
        intent="workspace.write_file",
        arguments={"path": "p2.txt"},
        actions=(PermissionAction.OVERWRITE_EXISTING_FILES,),
        mode=OperatingMode.MANUAL,
        mode_revision=0,
        source_context={},
        fingerprint="fp-p2",
    )
    params.update(overrides)
    return _new_approval_request(**params)


def _mirror_path():
    return runtime_paths.active_data_dir() / "pending_approvals.json"


@pytest.fixture()
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    # Reset approval state WHILE still pointed at the tmp home (the reset removes
    # pending_approvals.json; it must never touch the real data dir).
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    runtime_paths.configure_runtime_home(None)


def test_reset_cannot_be_resurrected_from_the_disk_mirror(_isolated_home):
    """Failure A: reset cleared memory, but the disk restore brought the supposedly cleared
    approval back. Reset now invalidates in BOTH places — the mirror cannot revive it."""
    import core.mode_permission_policy as mpp

    token = _mint_pending()["approval_id"]
    assert token in json.loads(_mirror_path().read_text())

    mpp.reset_mode_permission_state()
    assert token not in mpp._APPROVALS
    assert not _mirror_path().exists(), "the mirror was a second authority waiting to revive the reset"

    # Simulated restart AFTER the reset: restore runs, finds nothing, revives nothing.
    mpp._APPROVALS.clear()
    mpp._PERSISTED_APPROVALS_RESTORED = False
    mpp._ensure_approvals_restored()
    assert token not in mpp._APPROVALS
    assert mpp.resolve_approval(token, decision="allow") is None


def test_post_restart_mint_cannot_erase_another_pending_approval(_isolated_home):
    """Failure B: with a pending approval on disk and restore not yet run, minting a NEW
    approval persisted a snapshot from un-restored memory — erasing the other pending one.
    Every mirror write now restores first, so the snapshot holds BOTH."""
    import core.mode_permission_policy as mpp

    foreign = {
        "status": "pending",
        "session_id": "other-session",
        "task_id": "other-task",
        "intent": "workspace.write_file",
        "fingerprint": "fp-foreign",
        "expires_at": 9999999999.0,
        "scope": "once",
        "scope_options": ["once"],
        "actions": ["overwrite_existing_files"],
        "batch_fingerprints": [],
        "remaining_batch": [],
    }
    # Deterministic fresh-restart state: nothing restored, empty memory, mirror pre-seeded.
    mpp._APPROVALS.clear()
    mpp._PERSISTED_APPROVALS_RESTORED = False
    path = _mirror_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"foreign-token": foreign}))

    assert mpp._PERSISTED_APPROVALS_RESTORED is False
    assert "foreign-token" not in mpp._APPROVALS
    _mint_pending()

    mirror = json.loads(path.read_text())
    assert "foreign-token" in mirror, "the mint overwrote the mirror and erased a pending approval"
    assert any(entry.get("session_id") == "p2-session" for entry in mirror.values())

    # And the restored foreign approval is genuinely resolvable after restore.
    mpp._ensure_approvals_restored()
    resolved = mpp.resolve_approval("foreign-token", decision="allow")
    assert resolved is not None and resolved["status"] == "approved"


def test_expiry_is_enforced_on_resolution_of_a_live_process_entry(_isolated_home):
    """One coherent lifecycle includes expiry: an entry that expires while sitting in the
    live process's memory is refused and honestly terminal (expired), not grantable."""
    import time as _time

    import core.mode_permission_policy as mpp

    token = _mint_pending()["approval_id"]
    mpp._APPROVALS[token]["expires_at"] = _time.time() - 1
    assert mpp.resolve_approval(token, decision="allow") is None
    assert mpp._APPROVALS[token]["status"] == "expired"
    mirror = json.loads(_mirror_path().read_text())
    assert token not in mirror, "expired entries leave the pending-only mirror"
