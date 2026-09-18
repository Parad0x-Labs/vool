"""Model Radar feed adapters — wire-format translation ONLY.

Each adapter does exactly one job: turn one provider's published payload into typed
``ModelObservation`` rows for the normalized authority (core/model_radar) to judge.
No adapter decides what qualifies, stores anything, or invents a field the source
did not state. Two fail-closed laws:

- a structurally invalid payload raises ``FeedMalformedError`` and the WHOLE feed is
  rejected — a poisoned feed can never land a partial set of rows that happens to
  include a fake discount;
- an absent or unreadable value becomes ``None`` / "" (unknown), never a zero or a
  guess, because unknown must stay on the failing side of every qualification gate.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from core.model_radar import (
    OFFER_PERMANENT,
    OFFER_SUBSIDY,
    OFFER_UNKNOWN,
    ModelObservation,
    PriceComponents,
)

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
# Verified against the live docs page on 2026-09-01: free (:free) variants are
# platform-rate-limited at 20 requests/minute and 50 requests/day (1,000/day once
# 10+ credits have been purchased). This is the limits evidence a :free row carries;
# the per-model catalog payload itself states no limits.
_OPENROUTER_FREE_LIMITS = "20 requests/minute, 50 requests/day (1,000/day with 10+ credits purchased)"
_OPENROUTER_FREE_LIMITS_URL = "https://openrouter.ai/docs/api-reference/limits"


class FeedMalformedError(ValueError):
    """The feed's structure violated the adapter contract; nothing from it may land."""


def _price(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise FeedMalformedError(f"{field}: boolean is not a price")
    try:
        price = float(value)
    except (TypeError, ValueError) as exc:
        raise FeedMalformedError(f"{field}: unreadable price {value!r}") from exc
    if price != price or price in (float("inf"), float("-inf")):
        raise FeedMalformedError(f"{field}: non-finite price {value!r}")
    if price < 0:
        raise FeedMalformedError(f"{field}: negative price {value!r}")
    return price


def _bool_field(row: dict[str, Any], name: str) -> bool:
    value = row.get(name)
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    raise FeedMalformedError(f"{name}: expected a boolean, got {value!r}")


def parse_normalized_feed(
    provider_id: str,
    payload: Any,
    *,
    fetched_at: str,
    source_feed: str = "norm-json",
) -> tuple[ModelObservation, ...]:
    """The normalized JSON feed contract used by tests and future providers.

    Shape: ``{"models": [{"id", "name"?, "input_usd_per_m", "output_usd_per_m",
    "cached_input_usd_per_m"?, "offer_kind"?, "limits"?, "expires_at"?,
    "context_length"?, "supports_tools"?, "supports_images"?, "privacy_terms"?,
    "evidence_url"?}, ...]}``. A missing ``offer_kind`` reads as ``unknown`` — the
    fail-closed default that qualifies nothing.
    """
    provider = str(provider_id or "").strip().lower()
    if not provider:
        raise FeedMalformedError("provider_id is required")
    if not isinstance(payload, dict):
        raise FeedMalformedError("payload must be a JSON object")
    rows = payload.get("models")
    if not isinstance(rows, list):
        raise FeedMalformedError("payload.models must be a list")
    observations: list[ModelObservation] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise FeedMalformedError(f"models[{index}] must be an object")
        model_id = str(row.get("id") or "").strip()
        if not model_id:
            raise FeedMalformedError(f"models[{index}].id is required")
        offer_kind = str(row.get("offer_kind") or "").strip().lower() or OFFER_UNKNOWN
        try:
            observations.append(
                ModelObservation(
                    provider_id=provider,
                    model_id=model_id,
                    display_name=str(row.get("name") or model_id),
                    prices=PriceComponents(
                        input_usd_per_m=_price(row.get("input_usd_per_m"), field=f"models[{index}].input_usd_per_m"),
                        output_usd_per_m=_price(row.get("output_usd_per_m"), field=f"models[{index}].output_usd_per_m"),
                        cached_input_usd_per_m=_price(
                            row.get("cached_input_usd_per_m"), field=f"models[{index}].cached_input_usd_per_m"
                        ),
                    ),
                    offer_kind=offer_kind,
                    limits_stated=str(row.get("limits") or ""),
                    limits_evidence_url=str(row.get("limits_evidence_url") or ""),
                    expires_at=str(row.get("expires_at") or ""),
                    context_length=max(0, int(row.get("context_length") or 0)),
                    supports_tools=_bool_field(row, "supports_tools"),
                    supports_images=_bool_field(row, "supports_images"),
                    privacy_terms=str(row.get("privacy_terms") or ""),
                    evidence_url=str(row.get("evidence_url") or ""),
                    evidence_fetched_at=str(fetched_at or ""),
                    source_feed=str(source_feed or "norm-json"),
                )
            )
        except ValueError as exc:
            if "unknown offer kind" in str(exc):
                raise FeedMalformedError(f"models[{index}]: {exc}") from exc
            raise
    return tuple(observations)


def openrouter_observations(models: Iterable[Any]) -> tuple[ModelObservation, ...]:
    """Translate the live OpenRouter catalog rows (core.openrouter_catalog.OpenRouterModel).

    The catalog payload publishes per-token prices, context, supported parameters
    and modalities; it publishes NO cached-token price and NO per-model limits, so
    those stay None/"" — except the ``:free`` variants, whose platform rate limits
    are documented at the limits page referenced below and are the evidence that
    lets a subsidized free row state its limits.
    """
    observations: list[ModelObservation] = []
    for model in models:
        model_id = str(getattr(model, "model_id", "") or "").strip()
        if not model_id:
            continue
        prompt = getattr(model, "prompt_usd_per_token", None)
        completion = getattr(model, "completion_usd_per_token", None)
        request_fee = getattr(model, "request_usd", None) or 0.0
        is_free_variant = model_id.lower().endswith(":free")
        supported = tuple(str(p) for p in (getattr(model, "supported_parameters", ()) or ()))
        input_modalities = tuple(str(m) for m in (getattr(model, "input_modalities", ()) or ()))
        prompt_per_m = None if prompt is None else round(float(prompt) * 1_000_000, 6)
        completion_per_m = None if completion is None else round(float(completion) * 1_000_000, 6)
        observations.append(
            ModelObservation(
                provider_id="openrouter",
                model_id=model_id,
                display_name=str(getattr(model, "name", "") or model_id),
                prices=PriceComponents(
                    input_usd_per_m=prompt_per_m,
                    output_usd_per_m=completion_per_m,
                    cached_input_usd_per_m=None,  # not published by this feed
                ),
                offer_kind=OFFER_SUBSIDY if is_free_variant else OFFER_PERMANENT,
                limits_stated=_OPENROUTER_FREE_LIMITS if is_free_variant else "",
                limits_evidence_url=_OPENROUTER_FREE_LIMITS_URL if is_free_variant else "",
                expires_at="",
                context_length=max(0, int(getattr(model, "context_length", 0) or 0)),
                supports_tools="tools" in supported,
                supports_images="image" in input_modalities,
                privacy_terms="",  # not published per-model by this feed
                evidence_url=_OPENROUTER_MODELS_URL,
                evidence_fetched_at=str(getattr(model, "fetched_at", "") or ""),
                source_feed="openrouter:v1",
            )
        )
        # A nonzero per-request fee on a ":free" row would contradict "free": the
        # request surcharge is part of what a turn costs. Fold it into the input
        # component only when it is a real (nonzero) published value.
        if is_free_variant and request_fee:
            observations[-1] = ModelObservation(
                **{
                    **observations[-1].to_dict(),
                    "prices": {
                        "input_usd_per_m": (prompt_per_m or 0.0) + round(float(request_fee) * 1_000_000, 6),
                        "output_usd_per_m": completion_per_m,
                        "cached_input_usd_per_m": None,
                    },
                }
            )
    return tuple(observations)


__all__ = ["FeedMalformedError", "openrouter_observations", "parse_normalized_feed"]
