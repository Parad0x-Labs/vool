"""What is known -- and what is not -- about who can see a request on each UsePod route.

A UsePod marketplace or key-relay route is not a first-party OpenAI or Anthropic endpoint, and a
cheap route is not evidence of anything about retention. This module states the route classes the
docs describe, the trust mechanisms the docs say are live, and -- just as explicitly -- what is not
established: no cryptographic proof of which route or model served a request, and no retention
policy VOOL can rely on. Nothing is upgraded from marketing language.

Every route shares one fact: the UsePod gateway itself receives the request. The route decides who
else does.

The policy consequence is narrow and fail-closed. A request that the runtime has classified as
sensitive (private files, source code, persistent memory, personal data) may be sent over UsePod
only under an owner grant that names that data class AND every route class the approved routing
policy allows; secrets and wallet material never go. A request nobody classified is recorded as
unclassified -- the absence of a label is reported, not converted into one.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from core.usepod.descriptor import DOCS_READ_ON, EVIDENCE_DOCUMENTED
from core.usepod.routing import ROUTE_CLASS_CENTRALIZED, ROUTE_CLASS_KEY_RELAY, ROUTE_CLASS_MARKETPLACE

VERIFICATION_NONE_DOCUMENTED = "no_cryptographic_route_or_model_verification_documented"
RETENTION_UNKNOWN = "unknown_not_stated_for_this_route"

NEVER_REMOTE_CLASSES = frozenset({"secrets", "wallet"})
SENSITIVE_CLASSES = frozenset({"private-files", "source-code", "persistent-memory", "personal"})
OPEN_CLASSES = frozenset({"public", "user-approved"})
UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class RouteTrustProfile:
    route_class: str
    receives_request: tuple[str, ...]
    live_mechanisms: tuple[str, ...]
    verification: str
    retention: str
    source: str
    evidence: str
    observed_on: str = DOCS_READ_ON

    def as_dict(self) -> dict[str, Any]:
        return {
            "route_class": self.route_class,
            "receives_request": list(self.receives_request),
            "live_mechanisms": list(self.live_mechanisms),
            "verification": self.verification,
            "retention": self.retention,
            "source": self.source,
            "evidence": self.evidence,
            "observed_on": self.observed_on,
        }


ROUTE_TRUST: Mapping[str, RouteTrustProfile] = MappingProxyType(
    {
        ROUTE_CLASS_MARKETPLACE: RouteTrustProfile(
            route_class=ROUTE_CLASS_MARKETPLACE,
            receives_request=("usepod_gateway", "independent_operator_agent", "operator_local_backend"),
            live_mechanisms=("provider_bond", "reputation_score", "hidden_benchmark_canaries"),
            verification=VERIFICATION_NONE_DOCUMENTED,
            retention=RETENTION_UNKNOWN,
            source="https://docs.usepod.ai/marketplace/trust/",
            evidence=EVIDENCE_DOCUMENTED,
        ),
        ROUTE_CLASS_KEY_RELAY: RouteTrustProfile(
            route_class=ROUTE_CLASS_KEY_RELAY,
            receives_request=("usepod_gateway", "upstream_provider_under_operator_key"),
            live_mechanisms=("provider_bond", "reputation_score"),
            verification=VERIFICATION_NONE_DOCUMENTED,
            retention=RETENTION_UNKNOWN,
            source="https://docs.usepod.ai/introduction/how-it-works/",
            evidence=EVIDENCE_DOCUMENTED,
        ),
        ROUTE_CLASS_CENTRALIZED: RouteTrustProfile(
            route_class=ROUTE_CLASS_CENTRALIZED,
            receives_request=("usepod_gateway", "centralized_upstream_provider"),
            live_mechanisms=("provider_health_demotion",),
            verification=VERIFICATION_NONE_DOCUMENTED,
            retention=RETENTION_UNKNOWN,
            source="https://docs.usepod.ai/marketplace/routing/",
            evidence=EVIDENCE_DOCUMENTED,
        ),
    }
)


@dataclass(frozen=True)
class RouteTrustGrant:
    """An owner's explicit permission to send data of these classes over these UsePod route classes."""

    privacy_classes: tuple[str, ...] = ()
    route_classes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"privacy_classes": list(self.privacy_classes), "route_classes": list(self.route_classes)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> RouteTrustGrant:
        payload = dict(data or {})
        classes = tuple(sorted({str(item).strip().lower() for item in list(payload.get("privacy_classes") or []) if str(item).strip()}))
        routes = tuple(sorted({str(item).strip().lower() for item in list(payload.get("route_classes") or []) if str(item).strip()}))
        if any(item in NEVER_REMOTE_CLASSES for item in classes):
            raise ValueError("secrets and wallet material cannot be granted to a remote route")
        if any(item not in ROUTE_TRUST for item in routes):
            raise ValueError("unknown route class in grant")
        return cls(privacy_classes=classes, route_classes=routes)


@dataclass(frozen=True)
class RouteTrustDecision:
    allowed: bool
    reason: str
    privacy_class: str
    route_classes: tuple[str, ...]
    profiles: tuple[RouteTrustProfile, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "privacy_class": self.privacy_class,
            "route_classes": list(self.route_classes),
            "profiles": [profile.as_dict() for profile in self.profiles],
        }


def evaluate_route_trust(
    *,
    privacy_class: str | None,
    allowed_route_classes: Iterable[str],
    grant: RouteTrustGrant | None,
) -> RouteTrustDecision:
    classes = tuple(str(item) for item in allowed_route_classes)
    profiles = tuple(ROUTE_TRUST[item] for item in classes if item in ROUTE_TRUST)
    label = str(privacy_class or "").strip().lower() or UNCLASSIFIED
    if label in NEVER_REMOTE_CLASSES:
        return RouteTrustDecision(False, f"privacy_class_never_remote:{label}", label, classes, profiles)
    if label == "unknown":
        # The runtime's own privacy vocabulary treats an explicitly UNKNOWN class as never remote.
        return RouteTrustDecision(False, "privacy_class_unknown_never_remote", label, classes, profiles)
    if label in SENSITIVE_CLASSES:
        active = grant or RouteTrustGrant()
        if label not in active.privacy_classes or any(item not in active.route_classes for item in classes):
            return RouteTrustDecision(False, f"route_trust_grant_required:{label}", label, classes, profiles)
        return RouteTrustDecision(True, f"route_trust_granted:{label}", label, classes, profiles)
    if label in OPEN_CLASSES:
        return RouteTrustDecision(True, f"privacy_class_open:{label}", label, classes, profiles)
    if label == UNCLASSIFIED:
        return RouteTrustDecision(True, "request_unclassified_route_trust_recorded", label, classes, profiles)
    return RouteTrustDecision(False, f"privacy_class_unrecognized:{label}", label, classes, profiles)


__all__ = [
    "NEVER_REMOTE_CLASSES",
    "RETENTION_UNKNOWN",
    "ROUTE_TRUST",
    "SENSITIVE_CLASSES",
    "UNCLASSIFIED",
    "VERIFICATION_NONE_DOCUMENTED",
    "RouteTrustDecision",
    "RouteTrustGrant",
    "RouteTrustProfile",
    "evaluate_route_trust",
]
