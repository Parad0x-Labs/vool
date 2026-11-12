"""Validate external usage before it enters the existing local ledgers."""
from __future__ import annotations

import math
from typing import Any


def reported_count(usage: dict, *keys: str) -> int | None:
    for key in keys:
        if key not in usage:
            continue
        value = usage[key]
        if type(value) is int:
            return value if 0 <= value <= 2**63 - 1 else None
        if type(value) is float and math.isfinite(value) and 0 <= value <= 2**63 - 1 and value.is_integer():
            return int(value)
        return None
    return None


def _amount(value: Any) -> bool:
    if type(value) not in {int, float}:
        return False
    try:
        return math.isfinite(float(value)) and value >= 0
    except (OverflowError, ValueError):
        return False


def reported_bill(usage: dict) -> float | None:
    from core.model_pricing import provider_reported_usd
    if not _amount(usage.get('cost')):
        return None
    details = usage.get('cost_details')
    details = details if isinstance(details, dict) else {}
    flags = [usage.get('is_byok'), details.get('is_byok')]
    if any(flag is not None and type(flag) is not bool for flag in flags):
        return None
    if any(flag is True for flag in flags) and not _amount(details.get('upstream_inference_cost')):
        return None
    total = provider_reported_usd(usage)
    return total if total is not None and math.isfinite(total) else None
