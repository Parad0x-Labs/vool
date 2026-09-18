"""The legacy paid-USD lane's release hazard, and UsePod's exit from it.

Task 03's base-gap finding, reproduced HERE on this tree for the record: ``call_failed`` returns a
possibly-billed reservation to the cap, and a killed process's reservation is never reconciled --
measured exposure 6.00 against a 4.00 cap. The migration route task 03 documented for a paid lane
is the money law. This file proves, in-process:

* the HAZARD still exists on the legacy lane for a NON-UsePod provider (its callers are preserved
  with their limits; fixing them is the open convergence item, not this lane's to claim);
* a UsePod pick NEVER touches that lane: no legacy ledger row exists for it, its marker
  authorization makes every legacy release/settle a no-op, and the lane's caps are the operator's
  money grant.
"""
from __future__ import annotations

import time
import uuid
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=False)
def legacy_home(tmp_path, monkeypatch):
    import os

    from storage.db import configure_default_db_path

    configure_default_db_path(os.path.join(tmp_path, "effect_budget.db"))
    from core import effect_budget

    effect_budget.reset_effect_budget_process_state()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    from core import runtime_paths

    runtime_paths.configure_runtime_home(home)
    yield home
    effect_budget.reset_effect_budget_process_state()
    configure_default_db_path(None)


def _legacy_authorization(provider: str = "openrouter", model: str = "test/paid-one"):
    """A REAL legacy authorization through the production reserve path (owner-local context)."""
    from core.paid_call_reservation import reserve_owner_pick_paid_call

    manifest = SimpleNamespace(provider_id=f"{provider}:lane", model_name=model, adapter_type="openai_compatible", metadata={"cost_class": "paid_cloud"}, provider_name=provider)
    task = SimpleNamespace(task_id=str(uuid.uuid4()), prompt_tokens=100, max_output_tokens=100)
    context = {
        "request_is_owner_local": True,
        "owner_local": True,
        "session_id": "legacy-test",
        "runtime_session_id": "legacy-test",
        "turn_id": "turn-legacy",
    }
    return reserve_owner_pick_paid_call(
        manifest=manifest, task=task, source_context=context, task_kind="answer", call_role="answer_generation", denial={}
    )


# --- the hazard, reproduced on the legacy lane --------------------------------------------------------


def test_the_legacy_lane_still_releases_a_possibly_billed_reservation_on_call_failed(legacy_home) -> None:
    """The reported hazard, live on this tree, for a NON-UsePod paid provider.

    A reservation whose request WENT OUT and may have been billed is returned to the cap by the
    ``call_failed`` release, so a second call can reserve the same ceiling again: exposure above
    the cap. Preserved here as the honest OPEN item for that lane's own migration; unrelated
    callers keep their limits.
    """
    authorization = _legacy_authorization()
    assert authorization is not None, "the legacy lane must still reserve for non-usepod providers"
    model_call_id = authorization.model_call_id
    assert model_call_id, "a legacy reservation carries a ledger row id"

    from core.model_spend_ledger import get_spend_reservation
    from core.paid_call_reservation import release_owner_pick_paid_call

    before = get_spend_reservation(model_call_id)
    assert str(before.status) == "reserved"
    # The request went out and may have been billed -- and the release returns the ceiling anyway.
    release_owner_pick_paid_call(authorization, reason="call_failed")
    after = get_spend_reservation(model_call_id)
    assert str(after.status) != "reserved", "call_failed returned a possibly-billed reservation to the cap"
    # A second pick can reserve the same ceiling immediately: exposure 2x the cap.
    second = _legacy_authorization()
    assert second is not None and second.model_call_id != model_call_id


# --- UsePod's exit ------------------------------------------------------------------------------------


class _UsePodManifest(SimpleNamespace):
    pass


def _usepod_manifest() -> _UsePodManifest:
    return _UsePodManifest(provider_id="usepod-byok:meridian-synth-chat", model_name="meridian-synth-chat", adapter_type="usepod", metadata={"cost_class": "paid_cloud"}, provider_name="usepod-byok")


def _approve_route(home) -> None:
    """Store a minimal approved route bound for the model, so these tests exercise the MONEY
    gate (the pick's route check is proven by the served suites and its own lane tests)."""
    from core.usepod import routing

    policy = routing.RoutePolicy(mode=routing.RoutingMode.MARKETPLACE_ONLY)
    bound = routing.ApprovedRouteBound(
        approval_id="apr_legacy_test",
        model_id="meridian-synth-chat",
        policy=policy,
        max_input_microunits_per_million=510_000,
        max_output_microunits_per_million=1_530_000,
        input_basis="explicit_policy_ceiling",
        output_basis="explicit_policy_ceiling",
        allowed_route_classes=(routing.ROUTE_CLASS_MARKETPLACE, routing.ROUTE_CLASS_KEY_RELAY),
        header_routing_mode="marketplace-only",
        header_providers="",
        discovered_prices=(),
        unlisted_pins=(),
        snapshot_sha256="0" * 64,
        snapshot_fetched_at=time.time(),
        snapshot_evidence="synthetic:test",
        approved_at=time.time(),
        origin="http://127.0.0.1:9",
    )
    routing.save_route_policy(policy)
    routing.save_approved_bound(bound)


def _production_authority():
    """The suite's autouse fixture resets the authority to Unavailable for isolation; these
    tests exercise the PRODUCTION gate, so they install the money-law authority explicitly."""
    from core.usepod.money_law import AUTHORITY_LABEL, EffectBudgetMonetaryAuthority
    from core.usepod.monetary import install_monetary_authority, reset_monetary_authority

    authority = EffectBudgetMonetaryAuthority()
    install_monetary_authority(authority, label=AUTHORITY_LABEL)
    return reset_monetary_authority  # handed back for teardown by the caller


def _pick(manifest, denial=None):
    from core.paid_call_reservation import reserve_owner_pick_paid_call

    task = SimpleNamespace(task_id=str(uuid.uuid4()), prompt_tokens=100, max_output_tokens=100)
    context = {"request_is_owner_local": True, "owner_local": True, "session_id": "s", "runtime_session_id": "s", "turn_id": "t"}
    denial = denial if denial is not None else {}
    authorization = reserve_owner_pick_paid_call(
        manifest=manifest, task=task, source_context=context, task_kind="answer", call_role="answer_generation", denial=denial
    )
    return authorization, denial


def test_a_usepod_pick_without_a_grant_refuses_with_the_money_code(legacy_home) -> None:
    teardown = _production_authority()
    try:
        _approve_route(legacy_home)
        authorization, denial = _pick(_usepod_manifest())
    finally:
        teardown()
    assert authorization is None
    assert denial["reason"] == "MONEY_AUTHORITY_INVALID"


def test_a_usepod_pick_with_a_grant_authorizes_without_the_legacy_ledger(legacy_home) -> None:
    teardown = _production_authority()
    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import AssetIdentity, MoneyGrantSpec, grant_money_authority
    from core.usepod.money_law import USEPOD_ACCOUNT_NETWORK

    operator = grant_operator_budget_authority(note="synthetic test funds: usepod money-law integration")
    grant_money_authority(
        operator,
        MoneyGrantSpec(
            kind="single_payment", operation_kinds=("inference_prepaid",), provider_id="usepod",
            asset=AssetIdentity(network=USEPOD_ACCOUNT_NETWORK, asset="USDC", decimals=6),
            max_total_atomic=8_000_000, per_operation_max_atomic=4_000_000,
            models=("meridian-synth-chat",), routes=("key_relay+marketplace",),
            expires_epoch=time.time() + 3600.0, credit_liquidity="not_required",
            provider_account="upc_" + "2" * 32,
            approval_ref="synthetic:test", note="synthetic test funds: usepod money-law integration",
        ),
    )
    try:
        _approve_route(legacy_home)
        authorization, denial = _pick(_usepod_manifest())
    finally:
        teardown()
    assert authorization is not None, denial
    # The marker satisfies the router's gates...
    from core.memory_first_router import _paid_call_authorization

    task = None
    context = {"authorized_paid_call": authorization}
    escalation = authorization.escalation
    assert escalation.model_id == "meridian-synth-chat"
    # ...but holds NO legacy ledger row: every legacy terminal path is a no-op on it.
    assert authorization.model_call_id == ""
    from core.paid_call_reservation import release_owner_pick_paid_call, settle_owner_pick_paid_call

    release_owner_pick_paid_call(authorization, reason="call_failed")   # must not raise, must not touch money
    settle_owner_pick_paid_call(authorization, actual_usd=0.01)
    from core.model_spend_ledger import get_spend_reservation

    assert get_spend_reservation("") is None


def test_the_marker_passes_the_router_paid_authorization_gate(legacy_home) -> None:
    """The duck-typed marker must satisfy exactly the gate ``_paid_call_authorization`` runs, or
    the router would exclude the paid lane it just authorized."""
    teardown = _production_authority()
    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import AssetIdentity, MoneyGrantSpec, grant_money_authority
    from core.memory_first_router import _paid_call_authorization
    from core.usepod.money_law import USEPOD_ACCOUNT_NETWORK

    operator = grant_operator_budget_authority(note="synthetic test funds: usepod money-law integration")
    grant_money_authority(
        operator,
        MoneyGrantSpec(
            kind="single_payment", operation_kinds=("inference_prepaid",), provider_id="usepod",
            asset=AssetIdentity(network=USEPOD_ACCOUNT_NETWORK, asset="USDC", decimals=6),
            max_total_atomic=8_000_000, per_operation_max_atomic=4_000_000,
            models=("meridian-synth-chat",), routes=("key_relay+marketplace",),
            expires_epoch=time.time() + 3600.0, credit_liquidity="not_required",
            provider_account="upc_" + "2" * 32, note="synthetic test funds",
        ),
    )
    try:
        _approve_route(legacy_home)
        authorization, _denial = _pick(_usepod_manifest())
    finally:
        teardown()
    assert authorization is not None
    task = SimpleNamespace(task_id=authorization.escalation.capsule.task_id)
    gated = _paid_call_authorization({"authorized_paid_call": authorization}, manifest=_usepod_manifest(), task=task)
    assert gated is authorization


# --- the paid gate binds the provider the reservation was made for ---------------------------------


def _paid_manifest(provider_name: str, model: str = "test/paid-one"):
    """A real registry manifest for a paid lane: ``provider_id`` is ``<provider_name>:<model>``."""
    from storage.model_provider_manifest import ModelProviderManifest

    return ModelProviderManifest(
        provider_name=provider_name, model_name=model, source_type="http",
        adapter_type="openai_compatible", license_name="Provider", license_reference="user-managed",
        weight_location="external", runtime_dependency=provider_name, capabilities=["summarize"],
        runtime_config={"base_url": "https://provider.invalid/v1"},
        metadata={"deployment_class": "cloud", "cost_class": "paid_cloud"},
    )


def _reserve_for(manifest):
    """The production owner-pick reservation for ``manifest`` (owner-local context), and its task."""
    from core.paid_call_reservation import reserve_owner_pick_paid_call

    task = SimpleNamespace(task_id=str(uuid.uuid4()), prompt_tokens=100, max_output_tokens=100)
    context = {"request_is_owner_local": True, "owner_local": True, "session_id": "binding", "runtime_session_id": "binding", "turn_id": "turn-binding"}
    authorization = reserve_owner_pick_paid_call(
        manifest=manifest, task=task, source_context=context, task_kind="answer", call_role="answer_generation", denial={}
    )
    return authorization, task


def test_a_paid_reservation_authorizes_only_the_provider_it_was_reserved_for(legacy_home) -> None:
    """The reservation names the ONE lane the owner picked. Another paid provider serving a model
    with the same name is a different destination with different terms: the paid gate refuses it,
    although the model name, the task and the reservation all match."""
    from core.memory_first_router import _paid_call_authorization

    picked, lookalike = _paid_manifest("openrouter-byok"), _paid_manifest("custom-byok")
    assert picked.model_name == lookalike.model_name and picked.provider_id != lookalike.provider_id
    authorization, task = _reserve_for(picked)
    assert authorization is not None and authorization.provider_id == picked.provider_id
    context = {"authorized_paid_call": authorization}
    assert _paid_call_authorization(context, manifest=picked, task=task) is authorization
    assert _paid_call_authorization(context, manifest=lookalike, task=task) is None


def test_a_usepod_pick_does_not_authorize_another_provider_with_the_same_model_name(legacy_home) -> None:
    teardown = _production_authority()
    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import AssetIdentity, MoneyGrantSpec, grant_money_authority
    from core.memory_first_router import _paid_call_authorization
    from core.usepod.money_law import USEPOD_ACCOUNT_NETWORK

    operator = grant_operator_budget_authority(note="synthetic test funds: usepod money-law integration")
    grant_money_authority(
        operator,
        MoneyGrantSpec(
            kind="single_payment", operation_kinds=("inference_prepaid",), provider_id="usepod",
            asset=AssetIdentity(network=USEPOD_ACCOUNT_NETWORK, asset="USDC", decimals=6),
            max_total_atomic=8_000_000, per_operation_max_atomic=4_000_000,
            models=("meridian-synth-chat",), routes=("key_relay+marketplace",),
            expires_epoch=time.time() + 3600.0, credit_liquidity="not_required",
            provider_account="upc_" + "2" * 32, note="synthetic test funds",
        ),
    )
    try:
        _approve_route(legacy_home)
        authorization, _denial = _pick(_usepod_manifest())
    finally:
        teardown()
    assert authorization is not None
    task = SimpleNamespace(task_id=authorization.escalation.capsule.task_id)
    lookalike = SimpleNamespace(
        provider_id="custom-byok:meridian-synth-chat", model_name="meridian-synth-chat", adapter_type="openai_compatible",
        metadata={"cost_class": "paid_cloud"}, provider_name="custom-byok",
    )
    assert _paid_call_authorization({"authorized_paid_call": authorization}, manifest=_usepod_manifest(), task=task) is authorization
    assert _paid_call_authorization({"authorized_paid_call": authorization}, manifest=lookalike, task=task) is None


def test_the_invocation_seam_builds_no_adapter_for_another_provider_under_the_pick(legacy_home) -> None:
    """The same binding at the one seam every provider call crosses: no adapter is built for the
    lookalike lane, while the picked lane still dispatches on the same reservation."""
    from unittest import mock

    from adapters.base_adapter import ModelRequest, ModelResponse
    from core.memory_first_router import MemoryFirstRouter

    picked, lookalike = _paid_manifest("openrouter-byok"), _paid_manifest("custom-byok")
    authorization, task = _reserve_for(picked)
    assert authorization is not None
    adapter = mock.Mock()
    adapter.health_check.return_value = {"ok": True}
    adapter.supports_streaming.return_value = False
    adapter.run_text_task.return_value = ModelResponse(output_text="bounded answer")
    router = MemoryFirstRouter()
    context = {"authorized_paid_call": authorization}
    with mock.patch.object(router.registry, "build_adapter", return_value=adapter) as build_adapter:
        _, refused, refused_error = router._invoke_manifest(
            manifest=lookalike, request=ModelRequest(task_kind="diagnosis", prompt="bounded capsule"),
            output_mode="plain_text", task=task, source_context=context,
        )
        assert (refused, refused_error, build_adapter.call_count) == (None, "paid_call_not_authorized_or_reserved", 0)
        _, response, error = router._invoke_manifest(
            manifest=picked, request=ModelRequest(task_kind="diagnosis", prompt="bounded capsule"),
            output_mode="plain_text", task=task, source_context=context,
        )
    assert error is None and response is not None
    assert build_adapter.call_count == 1
