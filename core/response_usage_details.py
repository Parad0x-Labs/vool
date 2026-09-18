"""Request-scoped display evidence. Never derives a charge from an account balance."""
from __future__ import annotations

import math
from decimal import ROUND_CEILING, Decimal
from typing import Any


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def response_usage_details(response: Any, *, cost_class: str = "") -> dict[str, Any]:
    try:
        return _response_usage_details(response, cost_class=cost_class)
    except (TypeError, ValueError, AttributeError, ArithmeticError):
        return {"cost_state": "unreported"}


def _response_usage_details(response: Any, *, cost_class: str = "") -> dict[str, Any]:
    usage = dict(getattr(response, "usage", None) or {})
    metadata = dict(getattr(response, "provider_metadata", None) or {})
    def count(*keys: str) -> int | None:
        for key in keys:
            value = _number(usage.get(key))
            if value is not None and value.is_integer():
                return int(value)
        return None
    result: dict[str, Any] = {
        "input_tokens": count("prompt_tokens", "input_tokens", "prompt_eval_count"),
        "output_tokens": count("completion_tokens", "output_tokens", "eval_count"),
        "request_seconds": _number(metadata.get("vool_call_seconds")),
        "cost_state": "unreported",
    }
    receipt = metadata.get("receipt") or {}
    if receipt.get("schema") == "vool.usepod.receipt.v1" and receipt.get("operation_id"):
        result["operation_id"] = receipt["operation_id"]
        cost = receipt.get("cost") or {}
        result["currency"] = "USDC"
        exact = _number(cost.get("exact_atomic"))
        bound = _number(cost.get("usage_upper_bound_atomic"))
        if exact is not None and cost.get("unit") == "usdc_microunit":
            result.update(cost_state="exact", amount=exact / 1_000_000)
        elif bound is not None and receipt.get("pricing_unit") == "usdc_microunit":
            result.update(cost_state="upper_bound", amount=bound / 1_000_000)
        # These axes are estimates at this operation's approved ceilings, never exact charges.
        rates = receipt.get("price_ceiling_microunits_per_million") or {}
        tokens = receipt.get("usage") or {}
        for axis in ("input", "output"):
            n, rate = _number(tokens.get(axis + "_tokens")), _number(rates.get(axis))
            if n is not None and rate is not None:
                result[axis + "_bound"] = str(Decimal(str(n)) * Decimal(str(rate)) / Decimal(10**12))
    else:
        exact = _number(usage.get("cost"))
        details = usage.get("cost_details") or {}
        if usage.get("is_byok") or details.get("is_byok"):
            upstream = _number(details.get("upstream_inference_cost"))
            if exact is not None and upstream is not None:
                exact += upstream
        if exact is not None:
            result.update(cost_state="exact", amount=exact, currency="USD")
        elif cost_class in {"free_local", "free_cloud"}:
            result.update(cost_state="free", amount=0, currency="USD")
    return result


def usage_display_segments(details: list[dict[str, Any]]) -> list[str]:
    """Combine this turn's uniquely recorded calls; unknown calls prevent an exact total."""
    if not details:
        return []
    segments: list[str] = []
    timed = [d for d in details if d.get("request_seconds") and d.get("output_tokens") is not None]
    if len(timed) == len(details):
        seconds = sum(d["request_seconds"] for d in timed)
        output = sum(d["output_tokens"] for d in timed)
        if seconds > 0:
            segments.append(f"{output / seconds:.1f} output tok/s (request time)")
    groups: dict[str, list[dict[str, Any]]] = {}
    unknown = any(d.get("cost_state") == "unreported" for d in details)
    for d in details:
        if d.get("cost_state") in {"exact", "upper_bound"}:
            groups.setdefault(d["currency"], []).append(d)
    for currency, rows in groups.items():
        amount = sum(Decimal(str(d["amount"])) for d in rows)
        text = format(amount, '.8f').rstrip('0').rstrip('.') or '0'
        bounded = any(d["cost_state"] == "upper_bound" for d in rows)
        prefix = "reported calls " if unknown else ""
        segments.append(f"{prefix}{'≤' if bounded else ''}{text} {currency}" + (" (upper bound)" if bounded else " charged"))
        if len(rows) == len(details) and all("input_bound" in d and "output_bound" in d for d in rows):
            axes = []
            for axis in ("input", "output"):
                value = sum(Decimal(d[axis + "_bound"]) for d in rows)
                axes.append(f"{axis} ≤{format(value.quantize(Decimal('0.00000001'), rounding=ROUND_CEILING), 'f').rstrip('0').rstrip('.') or '0'}")
            segments.append(' / '.join(axes) + ' ' + currency + ' (at approved rates)')
    if unknown:
        segments.append("some call costs unreported" if groups else "cost unreported")
    return segments
