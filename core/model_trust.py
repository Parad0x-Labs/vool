from __future__ import annotations

from storage.model_provider_manifest import ModelProviderManifest


def provider_base_trust(manifest: ModelProviderManifest) -> float:
    trust = 0.55
    if manifest.license_name and manifest.resolved_license_reference:
        trust += 0.08
    if manifest.source_type in {"local_path", "subprocess"}:
        trust += 0.05
    if manifest.adapter_type == "peft_lora_adapter":
        trust += 0.03
        if bool(manifest.metadata.get("adaptation_promoted")):
            trust += 0.05
    if manifest.adapter_type == "local_qwen_provider":
        trust += 0.07
    if manifest.adapter_type == "cloud_fallback_provider":
        trust += 0.10
    if manifest.weights_are_bundled:
        trust -= 0.25
    if not manifest.enabled:
        trust -= 0.1
    return max(0.0, min(1.0, trust))


def output_trust_score(
    *,
    manifest: ModelProviderManifest,
    raw_confidence: float,
    contract_ok: bool,
    trust_penalty: float,
    freshness_score: float,
    reviewed: bool = False,
    agreement_score: float = 0.0,
) -> float:
    trust = provider_base_trust(manifest)
    trust += 0.25 * max(0.0, min(1.0, raw_confidence))
    trust += 0.15 * max(0.0, min(1.0, freshness_score))
    trust += 0.10 * max(0.0, min(1.0, agreement_score))
    if reviewed:
        trust += 0.08
    if contract_ok:
        trust += 0.07
    trust -= max(0.0, trust_penalty)
    return max(0.0, min(1.0, trust))
