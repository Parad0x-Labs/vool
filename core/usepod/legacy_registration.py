"""Registration for retained monetary manifests, gated by explicit reference enablement.

This does not add a built-in provider, select a model, grant spend or make a request.
The original manifest and lane-refresh semantics remain in this private lane.
"""
from __future__ import annotations

from core.model_registry import ModelRegistry
from storage.model_provider_manifest import ModelProviderManifest


def _register_path_token_manifest(
    registry: ModelRegistry,
    *,
    cfg,
    model_name: str,
    capabilities: list[str],
    chat_selection_only: bool = False,
) -> str:
    """Register the lane for a provider whose credential is a URL path segment (UsePod).

    The manifest table persists ``runtime_config`` as plain JSON, so this manifest carries the ORIGIN
    and the credential SLOT name only; the adapter reads the token from the credential store at
    dispatch time. The dialect and payment transport come from the owner's lane preference. A price
    bound is approved per model elsewhere and is never implied by a lane existing.
    """
    from core.usepod.descriptor import protocol_path
    from core.usepod.lane import load_lane_preference
    from core.usepod.legacy_config import origin as resolved_origin

    origin = resolved_origin()
    if not origin or not model_name:
        return ""
    lane, _lane_error = load_lane_preference()
    provider_name = f"{cfg.provider_id}-byok"
    lane_capabilities = list(capabilities) + (["tool_intent"] if "tool_intent" not in capabilities else [])
    runtime_config: dict[str, object] = {
        "base_url": origin,
        "api_path": protocol_path(lane.protocol),
        "protocol": lane.protocol,
        "transport_mode": lane.transport_mode,
        "timeout_seconds": 180,
        "health_timeout_seconds": 10,
        "temperature": 0.3,
        "supports_json_mode": False,
        "tool_dialect": "native",
        "credential_key": cfg.credential_slot,
    }
    existing = registry.get_manifest(provider_name, model_name)
    if (
        existing
        and existing.enabled
        and dict(existing.runtime_config or {}) == runtime_config
        and list(existing.capabilities or []) == lane_capabilities
        and (chat_selection_only or not existing.metadata.get("chat_selection_only"))
    ):
        return existing.provider_id
    manifest = ModelProviderManifest(
        provider_name=provider_name,
        model_name=model_name,
        source_type="http",
        adapter_type="usepod",
        license_name="Provider",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        redistribution_allowed=False,
        runtime_dependency="remote-usepod-inference-marketplace",
        notes=f"{cfg.label} lane — routes and price bounds are approved per model; the token lives only in the credential store.",
        capabilities=lane_capabilities,
        runtime_config=runtime_config,
        metadata={
            "chat_selection_only": chat_selection_only,
            "runtime_family": "openai-compatible",
            "confidence_baseline": 0.75,
            "orchestration_role": "queen",
            "deployment_class": "remote",
            "cost_class": "paid_cloud",
            # Zero means unknown: the marketplace feed publishes no context window per model.
            "context_window": 0,
            "tool_support": ["structured_json", "code_complex", "tool_calls"],
            "max_safe_concurrency": 2,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest.provider_id


def refresh_path_token_lanes(provider_id: str) -> list[str]:
    """Rewrite every enabled lane of a path-token provider with the owner's current lane preference.

    The dialect and payment transport live in each manifest's ``runtime_config``. After the owner changes
    them, existing lanes are re-registered in place (same provider name and model) so the next dispatch
    speaks the chosen protocol. Approved price bounds are untouched: they belong to the route store.
    """
    from core.plugin_providers import require_legacy_x402
    from core.usepod.legacy_config import CONFIG
    from storage.model_provider_manifest import list_provider_manifests

    require_legacy_x402()
    cfg = CONFIG if provider_id == CONFIG.provider_id else None
    if cfg is None or cfg.auth_placement != "url_path_token":
        return []
    registry = ModelRegistry()
    refreshed: list[str] = []
    for manifest in list_provider_manifests(enabled_only=True):
        if manifest.provider_name != f"{cfg.provider_id}-byok":
            continue
        registered = _register_path_token_manifest(
            registry,
            cfg=cfg,
            model_name=manifest.model_name,
            capabilities=[item for item in manifest.capabilities if item != "tool_intent"],
            chat_selection_only=bool(manifest.metadata.get("chat_selection_only")),
        )
        if registered:
            refreshed.append(registered)
    return refreshed


def register_model(model_name: str) -> str:
    from core.plugin_providers import require_legacy_x402
    from core.runtime_provider_defaults import _DIRECT_BYOK_CAPS
    from core.usepod.legacy_config import CONFIG

    require_legacy_x402()
    return _register_path_token_manifest(ModelRegistry(), cfg=CONFIG, model_name=model_name,
                                         capabilities=list(_DIRECT_BYOK_CAPS), chat_selection_only=True)
