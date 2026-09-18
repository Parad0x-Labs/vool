from __future__ import annotations

from dataclasses import dataclass, field

from core.model_health import circuit_is_open
from core.model_registry import ModelRegistry
from core.model_selection_policy import ModelSelectionRequest
from storage.model_provider_manifest import ModelProviderManifest


@dataclass
class FailoverDecision:
    selected: ModelProviderManifest | None
    attempted_provider_ids: list[str] = field(default_factory=list)
    reason: str = "no_provider"


def select_with_failover(
    registry: ModelRegistry,
    request: ModelSelectionRequest,
    *,
    attempted_provider_ids: list[str] | None = None,
) -> FailoverDecision:
    attempted = list(attempted_provider_ids or [])
    ranked = registry.rank_manifests(request, exclude_provider_ids=attempted)
    for manifest in ranked:
        if circuit_is_open(manifest.provider_id):
            attempted.append(manifest.provider_id)
            continue
        return FailoverDecision(selected=manifest, attempted_provider_ids=attempted, reason="selected")
    return FailoverDecision(selected=None, attempted_provider_ids=attempted, reason="no_healthy_provider")
