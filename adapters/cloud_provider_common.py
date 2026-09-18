from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from core.cloud_provider_contract import (
    CloudModelMetadata,
    PricingState,
    ProviderError,
    ProviderErrorKind,
    normalized_price,
    pricing_state_for_prices,
)


class CloudProviderRequestError(RuntimeError):
    def __init__(self, status_code: int, safe_message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(safe_message)
        self.status_code = int(status_code)
        self.retry_after_seconds = retry_after_seconds


def discovery_window(*, ttl_seconds: int = 3600) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return now.isoformat(), (now + timedelta(seconds=max(1, int(ttl_seconds)))).isoformat()


def normalized_capabilities(payload: dict[str, Any]) -> tuple[str, ...]:
    supported = {str(item).strip().lower() for item in list(payload.get("supported_parameters") or [])}
    architecture = dict(payload.get("architecture") or {})
    input_modalities = {str(item).strip().lower() for item in list(architecture.get("input_modalities") or [])}
    output_modalities = {str(item).strip().lower() for item in list(architecture.get("output_modalities") or [])}
    capabilities = {"text"}
    if "image" in input_modalities or "vision" in supported:
        capabilities.add("image_input")
    if supported & {"tools", "tool_choice", "tool_calls"}:
        capabilities.add("tool_calling")
    if supported & {"response_format", "structured_outputs", "json_schema"}:
        capabilities.add("structured_output")
    if supported & {"reasoning", "include_reasoning"}:
        capabilities.add("reasoning")
    if "text" in output_modalities or not output_modalities:
        capabilities.add("streaming")
    return tuple(sorted(capabilities))


def normalized_pricing(payload: dict[str, Any]) -> tuple[PricingState, float | None, float | None, float | None]:
    pricing = dict(payload.get("pricing") or {})
    input_price = normalized_price(pricing.get("prompt", pricing.get("input")))
    output_price = normalized_price(pricing.get("completion", pricing.get("output")))
    request_price = normalized_price(pricing.get("request"))
    promotional = str(payload.get("pricing_state") or "").strip().lower() == PricingState.PROMOTIONAL
    state = pricing_state_for_prices(input_price, output_price, request_price, promotional=promotional)
    explicit_state = str(payload.get("pricing_state") or "").strip().lower()
    if explicit_state in {PricingState.PAID, PricingState.UNKNOWN}:
        state = PricingState(explicit_state)
    return state, input_price, output_price, request_price


def estimate_cost(
    model: CloudModelMetadata,
    *,
    input_tokens: int,
    output_tokens: int,
) -> float | None:
    prices = (model.input_usd_per_token, model.output_usd_per_token, model.request_usd)
    if any(value is None for value in prices):
        return None
    return round(
        max(0, int(input_tokens)) * float(model.input_usd_per_token or 0.0)
        + max(0, int(output_tokens)) * float(model.output_usd_per_token or 0.0)
        + float(model.request_usd or 0.0),
        8,
    )


def classify_error(error: Any) -> ProviderError:
    status = int(getattr(error, "status_code", 0) or 0)
    message = str(error or "").lower()
    retry_after = getattr(error, "retry_after_seconds", None)
    if status == 401:
        return ProviderError(ProviderErrorKind.AUTH, "provider rejected the API key (HTTP 401)")
    if status == 403:
        # A 403 is per request or per model (moderation, a model this key may not use), not a dead
        # key. Saying "authentication failed" here made an authorized key read as broken.
        return ProviderError(ProviderErrorKind.AUTH, "provider refused this request for this model (HTTP 403)")
    if status in {404, 410}:
        return ProviderError(ProviderErrorKind.MODEL_REMOVED, "provider model is unavailable")
    if status == 429:
        kind = ProviderErrorKind.QUOTA_EXHAUSTED if "quota" in message or "credit" in message else ProviderErrorKind.RATE_LIMITED
        return ProviderError(kind, "provider rate or quota limit reached", retry_after_seconds=retry_after)
    if status in {408, 504} or "timeout" in message:
        return ProviderError(ProviderErrorKind.TIMEOUT, "provider request timed out", billing_ambiguous=True)
    if status >= 500:
        return ProviderError(ProviderErrorKind.TEMPORARY, "provider is temporarily unavailable")
    if "context" in message and ("length" in message or "token" in message):
        return ProviderError(ProviderErrorKind.CONTEXT_OVERFLOW, "provider context limit exceeded")
    if "required_tools_not_offered" in message:
        # Raised by assert_envelope_carries_tools (core/execution_requirements.py) at the final
        # pre-invocation boundary: the built envelope itself carried no tools, distinct from a
        # provider telling US it cannot support tools at all (the branch just below).
        return ProviderError(ProviderErrorKind.TOOL_UNSUPPORTED, "required tools were not offered in the outbound request")
    if "tool" in message and ("unsupported" in message or "not support" in message):
        return ProviderError(ProviderErrorKind.TOOL_UNSUPPORTED, "provider model does not support required tools")
    if "malformed" in message or "json" in message:
        return ProviderError(ProviderErrorKind.MALFORMED_RESPONSE, "provider returned a malformed response")
    if "network" in message or "dns" in message or "host" in message:
        return ProviderError(ProviderErrorKind.NETWORK, "provider network request failed")
    return ProviderError(ProviderErrorKind.UNKNOWN, "provider request failed", billing_ambiguous=status == 0)


def require_success(status: int, headers: dict[str, str], payload: Any) -> None:
    if 200 <= int(status) < 300:
        return
    retry_after: float | None = None
    try:
        retry_after = max(0.0, float(headers.get("retry-after") or 0.0)) or None
    except (TypeError, ValueError):
        retry_after = None
    safe_hint = ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            safe_hint = str(error.get("code") or error.get("type") or "")[:80]
        elif isinstance(error, str):
            safe_hint = error[:80]
    raise CloudProviderRequestError(int(status), f"provider_http_{int(status)}:{safe_hint}", retry_after_seconds=retry_after)


__all__ = [
    "CloudProviderRequestError",
    "classify_error",
    "discovery_window",
    "estimate_cost",
    "normalized_capabilities",
    "normalized_pricing",
    "require_success",
]
