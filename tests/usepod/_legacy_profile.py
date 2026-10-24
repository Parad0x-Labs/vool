"""Real historical credential/manifest fixtures for the retained monetary lane.

The retired central Settings and provider-registration UI no longer exist.
These fixtures construct their persisted v0.7 shapes through the actual vault,
transaction and typed model registry. They grant no monetary authority.
"""
from pathlib import Path


def isolate_reference(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "plugin-lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "installed-plugins"))
    enable_reference()


def enable_reference():
    from core import plugin_lifecycle, plugin_manager

    identity = "vool-plugin-usepod"
    record = plugin_lifecycle.record_for(identity)
    if record is None or record.stage == plugin_lifecycle.STAGE_UNINSTALLED:
        source = Path(__file__).resolve().parents[2] / "examples/plugins/vool-plugin-usepod"
        review = plugin_manager.prepare_local(str(source))
        info = plugin_manager.install(review["review_id"], accepted=True, digest=review["digest"])["plugin"]
        plugin_manager.enable(identity, accepted=True, approval_digest=info["approval_digest"])
    assert plugin_lifecycle.is_available(identity)


def _store():
    from core.credential_intelligence.provider_registry import ProviderDescriptor, ProviderRegistry
    from core.credential_intelligence.store import CredentialStore
    from core.usepod.legacy_config import CONFIG

    descriptor = ProviderDescriptor(
        provider_id=CONFIG.provider_id, label=CONFIG.label, kind="llm_cloud", credential_slot=CONFIG.credential_slot,
        verify_endpoint=CONFIG.base_url + CONFIG.credential_path_template + CONFIG.probe_path,
        auth_style="url_path_token", capability_family="cloud_chat", paid=True,
        endpoint_slot=CONFIG.endpoint_slot, default_endpoint=CONFIG.base_url,
        verify_path=CONFIG.credential_path_template + CONFIG.probe_path,
        response_path=CONFIG.probe_response_path, response_kind=CONFIG.probe_response_kind,
    )
    return CredentialStore(ProviderRegistry({CONFIG.provider_id: descriptor})), descriptor


def save_pair(value, origin):
    from core import plugin_manager
    from core.usepod.discovery import plan_credential_binding

    enable_reference()
    plan = plan_credential_binding(value, explicit_origin=origin)
    store, descriptor = _store()
    store.save_operator_pair(descriptor, plan.token, endpoint=plan.origin)
    plugin_manager.connect("vool-plugin-usepod", "access", plan.token)
    # The historical Settings save refreshed already persisted lane origins.
    # Preserve that fixture state without restoring a removed public API.
    from storage.model_provider_manifest import list_provider_manifests

    for row in list_provider_manifests(enabled_only=True):
        if row.provider_name == "usepod-byok":
            register_model(row.model_name)
    return {"origin": plan.origin, "origin_is_default": plan.origin_is_default,
            "credential_fingerprint": plan.fingerprint, "token_shape": plan.token_shape}


def delete_pair():
    from core.credential_store import delete_credential
    from core.usepod.legacy_config import ORIGIN_SLOT

    store, _ = _store()
    removed = store.delete("usepod").removed
    delete_credential(ORIGIN_SLOT)
    delete_credential("plugin.vool-plugin-usepod.access")
    return bool(removed)


def register_model(model_name):
    from core.model_registry import ModelRegistry
    from core.plugin_providers import require_legacy_x402
    from core.usepod.descriptor import protocol_path
    from core.usepod.lane import load_lane_preference
    from core.usepod.legacy_config import CONFIG, origin
    from storage.model_provider_manifest import ModelProviderManifest

    require_legacy_x402()
    lane, _ = load_lane_preference()
    caps = ["summarize", "classify", "format", "extract", "code_basic", "code_complex", "structured_json", "long_context", "tool_intent"]
    manifest = ModelProviderManifest(
        provider_name="usepod-byok", model_name=model_name, source_type="http", adapter_type="usepod",
        license_name="Provider", license_reference="user-managed", license_url_or_reference="user-managed",
        weight_location="external", redistribution_allowed=False, runtime_dependency="remote-usepod-inference-marketplace",
        notes="Historical monetary lane; the credential stays in the vault.", capabilities=caps,
        runtime_config={"base_url": origin(), "api_path": protocol_path(lane.protocol), "protocol": lane.protocol,
                        "transport_mode": lane.transport_mode, "timeout_seconds": 180, "health_timeout_seconds": 10,
                        "temperature": 0.3, "supports_json_mode": False, "tool_dialect": "native",
                        "credential_key": CONFIG.credential_slot},
        metadata={"chat_selection_only": True, "runtime_family": "openai-compatible", "confidence_baseline": 0.75,
                  "orchestration_role": "queen", "deployment_class": "remote", "cost_class": "paid_cloud",
                  "context_window": 0, "tool_support": ["structured_json", "code_complex", "tool_calls"], "max_safe_concurrency": 2},
        enabled=True,
    )
    ModelRegistry().register_manifest(manifest)
    return manifest.provider_id
