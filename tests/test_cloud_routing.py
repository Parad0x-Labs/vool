from __future__ import annotations

from datetime import datetime, timezone

from core.cloud_provider_contract import CloudModelMetadata, CloudTaskRequirements, PricingState, PrivacyClass
from core.cloud_routing import CloudRouteMode, select_cloud_route

NOW = datetime(2026, 7, 14, tzinfo=timezone.utc)


def _model(
    model_id: str,
    *,
    provider_id: str = "provider",
    state: PricingState = PricingState.FREE,
    prices: tuple[float | None, float | None, float | None] = (0.0, 0.0, 0.0),
    health: str = "healthy",
    latency: float = 100.0,
    quota: float | None = 10.0,
    rate_limit: int | None = None,
    expires_at: str = "2026-07-15T00:00:00+00:00",
    capabilities: tuple[str, ...] = ("text", "structured_output"),
    context: int = 8192,
) -> CloudModelMetadata:
    return CloudModelMetadata(
        provider_id=provider_id,
        model_id=model_id,
        display_name=model_id,
        pricing_state=state,
        input_usd_per_token=prices[0],
        output_usd_per_token=prices[1],
        request_usd=prices[2],
        context_window=context,
        capabilities=capabilities,
        discovered_at="2026-07-14T00:00:00+00:00",
        expires_at=expires_at,
        health_state=health,
        latency_ms=latency,
        quota_remaining=quota,
        rate_limit_remaining=rate_limit,
    )


REQ = CloudTaskRequirements(
    min_context_tokens=1000,
    expected_output_tokens=100,
    required_capabilities=("structured_output",),
    privacy_class=PrivacyClass.PUBLIC,
)


def _select(models, *, mode=CloudRouteMode.LOCAL_FREE, **kwargs):
    providers = tuple({model.provider_id for model in models})
    return select_cloud_route(
        tuple(models),
        requirements=REQ,
        mode=mode,
        enabled_providers=providers,
        network_allowed_providers=providers,
        privacy_allowed=True,
        now=NOW,
        **kwargs,
    )


def test_local_only_never_selects_cloud() -> None:
    plan = _select([_model("free")], mode=CloudRouteMode.LOCAL_ONLY)
    assert plan.primary is None
    assert plan.rejected[0]["reason"] == "local_only"


def test_free_mode_never_selects_paid_or_unknown() -> None:
    plan = _select(
        [
            _model("paid", state=PricingState.PAID, prices=(0.001, 0.001, 0.0)),
            _model("unknown", state=PricingState.UNKNOWN, prices=(None, None, None)),
            _model("free"),
        ]
    )
    assert plan.primary and plan.primary.model_id == "free"
    assert {item["reason"] for item in plan.rejected} == {"not_verified_free", "pricing_unknown"}


def test_stale_disappeared_or_unhealthy_models_are_ineligible() -> None:
    plan = _select(
        [
            _model("stale", expires_at="2026-07-13T00:00:00+00:00"),
            _model("bad", health="unhealthy"),
        ]
    )
    assert plan.primary is None
    assert {item["reason"] for item in plan.rejected} == {"catalog_stale", "model_unhealthy"}


def test_capability_context_network_quota_and_privacy_are_hard_gates() -> None:
    models = [
        _model("cap", capabilities=("text",)),
        _model("ctx", context=100),
        _model("quota", quota=0),
        _model("rate", rate_limit=0),
    ]
    plan = _select(models)
    assert plan.primary is None
    assert {item["reason"] for item in plan.rejected} == {
        "capability_mismatch",
        "context_too_small",
        "quota_exhausted",
        "rate_limit_exhausted",
    }
    privacy = select_cloud_route(
        (_model("privacy"),),
        requirements=REQ,
        mode=CloudRouteMode.LOCAL_FREE,
        enabled_providers=("provider",),
        network_allowed_providers=("provider",),
        privacy_allowed=False,
        now=NOW,
    )
    assert privacy.primary is None and privacy.rejected[0]["reason"] == "privacy_policy_denied"


def test_promotional_requires_known_positive_quota() -> None:
    plan = _select([_model("promo", state=PricingState.PROMOTIONAL, quota=None)])
    assert plan.primary is None
    assert plan.rejected[0]["reason"] == "promotional_quota_unknown"


def test_paid_requires_opt_in_and_budget() -> None:
    paid = _model("paid", state=PricingState.PAID, prices=(0.000001, 0.000002, 0.0))
    denied = _select([paid], mode=CloudRouteMode.LOCAL_FREE_PAID)
    assert denied.primary is None and denied.rejected[0]["reason"] == "paid_not_approved"
    over = _select(
        [paid],
        mode=CloudRouteMode.LOCAL_FREE_PAID,
        paid_approved=True,
        paid_budget_remaining_usd=0.0001,
    )
    assert over.primary is None and over.rejected[0]["reason"] == "paid_budget_exceeded"
    approved = _select(
        [paid],
        mode=CloudRouteMode.LOCAL_FREE_PAID,
        paid_approved=True,
        paid_budget_remaining_usd=1.0,
    )
    assert approved.primary == paid and approved.estimated_max_usd is not None


def test_health_and_latency_score_is_deterministic_and_explainable() -> None:
    slow = _model("slow", provider_id="a", latency=900.0)
    fast = _model("fast", provider_id="b", latency=50.0)
    plan = _select([slow, fast])
    assert plan.primary == fast
    assert plan.fallbacks == (slow,)
    assert plan.scores[fast.catalog_key] > plan.scores[slow.catalog_key]
    assert plan.route_reason == "verified_zero_cost_capability_and_health_fit"
