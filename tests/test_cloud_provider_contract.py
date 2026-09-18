from __future__ import annotations

from core.cloud_provider_contract import (
    CloudModelMetadata,
    PricingState,
    normalized_price,
    pricing_state_for_prices,
)


def test_missing_or_malformed_price_is_unknown_not_free() -> None:
    assert normalized_price(None) is None
    assert normalized_price("not-a-price") is None
    assert normalized_price(-1) is None
    assert pricing_state_for_prices(None, 0.0, 0.0) == PricingState.UNKNOWN


def test_zero_price_requires_every_component_to_be_known_zero() -> None:
    assert pricing_state_for_prices(0.0, 0.0, 0.0) == PricingState.FREE
    assert pricing_state_for_prices(0.0, 0.0, 0.0, promotional=True) == PricingState.PROMOTIONAL
    assert pricing_state_for_prices(0.0, 0.001, 0.0) == PricingState.PAID


def test_verified_zero_price_rejects_unknown_component() -> None:
    safe = CloudModelMetadata(
        provider_id="p",
        model_id="m",
        display_name="M",
        pricing_state=PricingState.FREE,
        input_usd_per_token=0.0,
        output_usd_per_token=0.0,
        request_usd=0.0,
    )
    unknown = CloudModelMetadata(
        provider_id="p",
        model_id="u",
        display_name="U",
        pricing_state=PricingState.FREE,
        input_usd_per_token=None,
        output_usd_per_token=0.0,
        request_usd=0.0,
    )
    assert safe.verified_zero_price is True
    assert unknown.verified_zero_price is False


def test_string_enum_values_are_wire_compatible() -> None:
    assert str(PricingState.PROMOTIONAL) == "promotional"
