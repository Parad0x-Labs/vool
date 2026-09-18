from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.cloud_provider_contract import (
    CloudModelMetadata,
    CloudTaskRequirements,
    PricingState,
    is_text_generation_model,
)
from core.enum_compat import StrEnum


class CloudRouteMode(StrEnum):
    LOCAL_ONLY = "local-only"
    LOCAL_FREE = "local+free"
    LOCAL_FREE_PAID = "local+free+approved-paid"
    EXPLICIT = "explicit"


@dataclass(frozen=True)
class CloudRoutePlan:
    mode: CloudRouteMode
    primary: CloudModelMetadata | None
    fallbacks: tuple[CloudModelMetadata, ...]
    rejected: tuple[dict[str, str], ...]
    route_reason: str
    estimated_max_usd: float | None = None
    scores: dict[str, float] = field(default_factory=dict)


def _is_fresh(model: CloudModelMetadata, *, now: datetime) -> bool:
    try:
        expiry = datetime.fromisoformat(model.expires_at)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry > now
    except (TypeError, ValueError):
        return False


def _estimated_cost(model: CloudModelMetadata, requirements: CloudTaskRequirements) -> float | None:
    prices = (model.input_usd_per_token, model.output_usd_per_token, model.request_usd)
    if any(value is None for value in prices):
        return None
    input_tokens = max(0, int(requirements.min_context_tokens))
    output_tokens = max(0, int(requirements.expected_output_tokens))
    return round(
        input_tokens * float(model.input_usd_per_token or 0.0)
        + output_tokens * float(model.output_usd_per_token or 0.0)
        + float(model.request_usd or 0.0),
        8,
    )


def _rejection_reason(
    model: CloudModelMetadata,
    *,
    requirements: CloudTaskRequirements,
    mode: CloudRouteMode,
    enabled_providers: set[str],
    network_allowed_providers: set[str],
    privacy_allowed: bool,
    paid_approved: bool,
    paid_budget_remaining_usd: float,
    now: datetime,
) -> str:
    if model.provider_id not in enabled_providers:
        return "provider_disabled"
    if not _is_fresh(model, now=now):
        return "catalog_stale"
    if model.health_state.lower() in {"unhealthy", "blocked", "offline"}:
        return "model_unhealthy"
    capabilities = {str(item).strip().lower() for item in model.capabilities}
    if not set(requirements.required_capabilities).issubset(capabilities):
        return "capability_mismatch"
    # Every cloud task routed here writes text (a chat answer, a tool call, a document reading):
    # a model that generates audio or images alongside is never the right lane for it.
    if not is_text_generation_model(model):
        return "non_text_generation_model"
    if model.context_window <= 0 or model.context_window < max(0, requirements.min_context_tokens):
        return "context_too_small"
    if model.provider_id not in network_allowed_providers:
        return "network_policy_denied"
    if mode == CloudRouteMode.LOCAL_ONLY:
        return "local_only"
    if model.pricing_state == PricingState.UNKNOWN:
        return "pricing_unknown"
    if mode == CloudRouteMode.LOCAL_FREE:
        if model.pricing_state not in {PricingState.FREE, PricingState.PROMOTIONAL} or not model.verified_zero_price:
            return "not_verified_free"
    elif model.pricing_state == PricingState.PAID:
        if mode not in {CloudRouteMode.LOCAL_FREE_PAID, CloudRouteMode.EXPLICIT} or not paid_approved:
            return "paid_not_approved"
        estimate = _estimated_cost(model, requirements)
        if estimate is None:
            return "paid_cost_unknown"
        if paid_budget_remaining_usd <= 0 or estimate > paid_budget_remaining_usd:
            return "paid_budget_exceeded"
    elif not model.verified_zero_price:
        return "free_price_not_verified"
    if model.quota_remaining is not None and model.quota_remaining <= 0:
        return "quota_exhausted"
    if model.rate_limit_remaining is not None and model.rate_limit_remaining <= 0:
        return "rate_limit_exhausted"
    if model.pricing_state == PricingState.PROMOTIONAL and model.quota_remaining is None:
        return "promotional_quota_unknown"
    if not privacy_allowed:
        return "privacy_policy_denied"
    return ""


def _score(model: CloudModelMetadata, requirements: CloudTaskRequirements) -> float:
    score = 0.0
    health = model.health_state.lower()
    if health in {"ready", "healthy"}:
        score += 3.0
    elif health == "degraded":
        score += 0.5
    if model.failure_rate is not None:
        score -= max(0.0, min(1.0, float(model.failure_rate))) * 2.0
    if model.latency_ms is not None:
        latency = max(0.0, float(model.latency_ms))
        score -= min(2.0, latency / (500.0 if requirements.prefer_low_latency else 1500.0))
    if model.context_window > 0:
        headroom = max(0, model.context_window - requirements.min_context_tokens)
        score += min(1.0, headroom / max(1.0, float(model.context_window)))
    if model.pricing_state == PricingState.FREE:
        score += 0.2
    return round(score, 6)


def select_cloud_route(
    models: tuple[CloudModelMetadata, ...],
    *,
    requirements: CloudTaskRequirements,
    mode: CloudRouteMode,
    enabled_providers: tuple[str, ...],
    network_allowed_providers: tuple[str, ...],
    privacy_allowed: bool,
    paid_approved: bool = False,
    paid_budget_remaining_usd: float = 0.0,
    max_fallbacks: int = 2,
    now: datetime | None = None,
) -> CloudRoutePlan:
    moment = now or datetime.now(timezone.utc)
    enabled = set(enabled_providers)
    network_allowed = set(network_allowed_providers)
    rejected: list[dict[str, str]] = []
    eligible: list[tuple[float, CloudModelMetadata]] = []
    for model in models:
        reason = _rejection_reason(
            model,
            requirements=requirements,
            mode=mode,
            enabled_providers=enabled,
            network_allowed_providers=network_allowed,
            privacy_allowed=privacy_allowed,
            paid_approved=paid_approved,
            paid_budget_remaining_usd=paid_budget_remaining_usd,
            now=moment,
        )
        if reason:
            rejected.append({"provider_id": model.provider_id, "model_id": model.model_id, "reason": reason})
            continue
        eligible.append((_score(model, requirements), model))

    eligible.sort(key=lambda item: (-item[0], item[1].provider_id, item[1].model_id))
    primary = eligible[0][1] if eligible else None
    fallbacks = tuple(item[1] for item in eligible[1 : 1 + max(0, int(max_fallbacks))])
    scores = {model.catalog_key: score for score, model in eligible}
    if primary is None:
        return CloudRoutePlan(mode, None, (), tuple(rejected), "no_eligible_cloud_route", None, scores)
    estimate = _estimated_cost(primary, requirements)
    reason = (
        "verified_zero_cost_capability_and_health_fit"
        if primary.verified_zero_price
        else "approved_paid_capability_and_health_fit"
    )
    return CloudRoutePlan(mode, primary, fallbacks, tuple(rejected), reason, estimate, scores)


def route_plan_to_dict(plan: CloudRoutePlan) -> dict[str, Any]:
    return {
        "mode": plan.mode.value,
        "primary": plan.primary.to_dict() if plan.primary else None,
        "fallbacks": [model.to_dict() for model in plan.fallbacks],
        "rejected": list(plan.rejected),
        "route_reason": plan.route_reason,
        "estimated_max_usd": plan.estimated_max_usd,
        "scores": dict(plan.scores),
    }


__all__ = ["CloudRouteMode", "CloudRoutePlan", "route_plan_to_dict", "select_cloud_route"]
