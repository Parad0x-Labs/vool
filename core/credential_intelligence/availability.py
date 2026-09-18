"""Capability availability derived from verified evidence — and ONLY verified evidence.

This module answers one question: "which providers can actually be used right now?" from the
binding statuses. A provider is AVAILABLE exactly when a binding for it is ``verified``.

It also owns the lane wiring a verified (or verified-dead) outcome triggers on the runtime
side: ``activate_provider_byok`` when a key proves good, ``deactivate_provider_byok`` when a
provider's own response proves the key dead (invalid / unauthorized / exhausted). Inconclusive
outcomes — throttled, unreachable — change NOTHING, because they are not evidence about the
key.

What this module never does: flip any paid-fallback preference. Making a provider AVAILABLE is
not authorization to burst to it on the product's own initiative — that switch belongs to the
operator's routing preferences, which this module neither reads nor writes. Verifying a paid
key therefore cannot silently enable paid usage.
"""
from __future__ import annotations

from collections.abc import Iterable

from core.credential_intelligence.binding import STATUS_VERIFIED, CredentialBinding
from core.credential_intelligence.provider_registry import ProviderDescriptor
from core.credential_intelligence.verification import (
    STATUS_EXHAUSTED,
    STATUS_INVALID,
    STATUS_NETWORK_UNAVAILABLE,
    STATUS_RATE_LIMITED,
    STATUS_UNAUTHORIZED,
    VerificationOutcome,
)

#: Outcomes that carry positive evidence about the key itself.
_LANE_WITHDRAW_STATUSES = (STATUS_INVALID, STATUS_UNAUTHORIZED, STATUS_EXHAUSTED)
#: Outcomes with no key evidence — no lane change permitted.
_INCONCLUSIVE_STATUSES = (STATUS_RATE_LIMITED, STATUS_NETWORK_UNAVAILABLE)


def provider_availability(bindings: Iterable[CredentialBinding]) -> dict[str, dict[str, object]]:
    """The availability snapshot: one entry per binding, available iff verified. Exposes
    provider/capability/status/last-verified — the binding's own opaque fields."""
    snapshot: dict[str, dict[str, object]] = {}
    for binding in bindings:
        snapshot[binding.provider_id] = {
            "available": binding.status == STATUS_VERIFIED,
            "capability_family": binding.capability_family,
            "status": binding.status,
            "last_verified_at": binding.last_verified_at,
        }
    return snapshot


def apply_verification(descriptor: ProviderDescriptor, outcome: VerificationOutcome) -> dict[str, object]:
    """Apply one verification's evidence to the runtime: activate the provider's lane on a
    verified key, withdraw it on provider-confirmed death, do NOTHING on inconclusive
    outcomes. Returns the resulting availability entry for this provider."""
    activated = False
    withdrawn = False
    if descriptor.kind == "llm_cloud":
        if outcome.status == STATUS_VERIFIED:
            activated = _activate_lane(descriptor.provider_id)
        elif outcome.status in _LANE_WITHDRAW_STATUSES:
            withdrawn = _withdraw_lane(descriptor.provider_id)
    return {
        "provider_id": descriptor.provider_id,
        "available": outcome.status == STATUS_VERIFIED,
        "capability_family": descriptor.capability_family,
        "status": outcome.status,
        "last_verified_at": outcome.checked_at,
        "lane_activated": activated,
        "lane_withdrawn": withdrawn,
    }


def _activate_lane(provider_id: str) -> bool:
    try:
        from core.runtime_provider_defaults import activate_provider_byok

        activate_provider_byok(provider_id)
        return True
    except Exception:
        return False


def _withdraw_lane(provider_id: str) -> bool:
    try:
        from core.runtime_provider_defaults import deactivate_provider_byok

        deactivate_provider_byok(provider_id)
        return True
    except Exception:
        return False


__all__ = ["apply_verification", "provider_availability"]
