from __future__ import annotations

from dataclasses import dataclass, field

from storage.model_provider_manifest import ModelProviderManifest

CANONICAL_CAPABILITIES = {
    "summarize",
    "classify",
    "format",
    "extract",
    "code_basic",
    "code_complex",
    "long_context",
    "structured_json",
    "tool_intent",
    "multimodal",
}

TASK_KIND_TO_CAPABILITIES: dict[str, set[str]] = {
    "summarization": {"summarize"},
    "classification": {"classify", "structured_json"},
    "normalization_assist": {"format"},
    "candidate_shard_generation": {"extract", "structured_json"},
    "action_plan": {"summarize", "structured_json"},
    "tool_intent": {"tool_intent", "structured_json"},
    "coding_help_basic": {"code_basic"},
    "coding_help_complex": {"code_complex", "long_context"},
    "multimodal_review": {"multimodal", "summarize"},
    # new task kinds
    "format": {"format"},
    "extract": {"extract", "structured_json"},
    "tag": {"classify", "structured_json"},
    "reasoning": {"summarize", "structured_json"},
    "agent_planning": {"summarize", "structured_json"},
    "qa_short": {"summarize"},
    "qa_long": {"summarize", "long_context"},
}

OUTPUT_MODE_TO_CAPABILITIES: dict[str, set[str]] = {
    "plain_text": set(),
    "json_object": {"structured_json"},
    "action_plan": {"structured_json"},
    "tool_intent": {"tool_intent", "structured_json"},
    "summary_block": {"summarize", "structured_json"},
}


def normalize_capabilities(capabilities: list[str]) -> list[str]:
    seen: list[str] = []
    for capability in capabilities:
        clean = str(capability).strip().lower()
        if clean and clean in CANONICAL_CAPABILITIES and clean not in seen:
            seen.append(clean)
    return seen


@dataclass
class ModelCapabilityProfile:
    provider_name: str
    model_name: str
    capabilities: list[str] = field(default_factory=list)

    @property
    def provider_id(self) -> str:
        return f"{self.provider_name}:{self.model_name}"

    def supports(self, capability: str) -> bool:
        return capability in set(self.capabilities)


def profile_from_manifest(manifest: ModelProviderManifest) -> ModelCapabilityProfile:
    return ModelCapabilityProfile(
        provider_name=manifest.provider_name,
        model_name=manifest.model_name,
        capabilities=normalize_capabilities(manifest.capabilities),
    )


def required_capabilities(task_kind: str, output_mode: str = "plain_text") -> set[str]:
    return set(TASK_KIND_TO_CAPABILITIES.get(task_kind, set())) | set(OUTPUT_MODE_TO_CAPABILITIES.get(output_mode, set()))


def capability_score(manifest: ModelProviderManifest, *, task_kind: str, output_mode: str = "plain_text") -> float:
    profile = profile_from_manifest(manifest)
    required = required_capabilities(task_kind, output_mode)
    score = 0.0
    if not required:
        score += 0.3
    for capability in required:
        if profile.supports(capability):
            score += 1.0
    coverage = (len(required & set(profile.capabilities)) / max(1, len(required))) if required else 1.0
    score += 0.4 * coverage
    if "long_context" in profile.capabilities and task_kind in {"action_plan", "candidate_shard_generation"}:
        score += 0.15
    if "structured_json" in profile.capabilities and output_mode in {"json_object", "action_plan", "tool_intent", "summary_block"}:
        score += 0.25
    return score
