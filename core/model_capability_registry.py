from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from core.local_inference_evidence import latest_local_inference_benchmarks
from core.local_model_bundles import model_parameter_billions
from core.local_ollama_inventory import installed_ollama_model_names, loaded_ollama_model_names
from core.model_health import get_provider_health
from core.model_lane_config import LOGICAL_MODEL_LANES, LogicalModelLane, load_model_lane_config
from core.provider_routing import ProviderCapabilityTruth, provider_capability_truth_for_manifest
from storage.model_provider_manifest import ModelProviderManifest


@dataclass(frozen=True)
class ModelCapabilityRecord:
    provider_id: str
    model_id: str
    runtime: str
    locality: str
    installed: bool
    loaded: bool
    warm: bool
    context_limit: int
    tool_calling: bool
    structured_output: bool
    vision: bool
    coding_capability: float
    reasoning_capability: float
    tokens_per_second: float
    ram_requirement_gb: float
    vram_requirement_gb: float
    measured_reliability: float
    health: str
    last_benchmark: str
    assigned_lanes: tuple[LogicalModelLane, ...]
    cost_metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "vool.model_capability.v1",
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "runtime": self.runtime,
            "locality": self.locality,
            "installed": self.installed,
            "loaded": self.loaded,
            "warm": self.warm,
            "context_limit": self.context_limit,
            "tool_calling": self.tool_calling,
            "structured_output": self.structured_output,
            "vision": self.vision,
            "coding_capability": self.coding_capability,
            "reasoning_capability": self.reasoning_capability,
            "tokens_per_second": self.tokens_per_second,
            "ram_requirement_gb": self.ram_requirement_gb,
            "vram_requirement_gb": self.vram_requirement_gb,
            "measured_reliability": self.measured_reliability,
            "health": self.health,
            "last_benchmark": self.last_benchmark,
            "assigned_lanes": list(self.assigned_lanes),
            "cost_metadata": dict(self.cost_metadata),
        }


def build_model_capability_registry(
    manifests: Iterable[ModelProviderManifest],
    *,
    installed_ollama: Iterable[str] | None = None,
    loaded_ollama: Iterable[str] | None = None,
) -> tuple[ModelCapabilityRecord, ...]:
    entries = tuple(manifests)
    installed = {str(item).strip().lower() for item in (installed_ollama if installed_ollama is not None else installed_ollama_model_names())}
    loaded = {str(item).strip().lower() for item in (loaded_ollama if loaded_ollama is not None else loaded_ollama_model_names())}
    lane_config = load_model_lane_config()
    latest = {}
    try:
        latest = {fact.provider_id: fact for fact in latest_local_inference_benchmarks(limit=max(64, len(entries) * 4))}
    except Exception:
        latest = {}
    out: list[ModelCapabilityRecord] = []
    for manifest in entries:
        truth = provider_capability_truth_for_manifest(manifest)
        metadata = dict(manifest.metadata or {})
        capabilities = {str(item).strip().lower() for item in manifest.capabilities}
        tool_support = {str(item).strip().lower() for item in truth.tool_support}
        is_ollama = str(metadata.get("runtime_family") or "").strip().lower() == "ollama"
        local_installed = truth.locality != "local" or not is_ollama or manifest.model_name.lower() in installed
        local_loaded = bool(is_ollama and manifest.model_name.lower() in loaded)
        health = get_provider_health(manifest.provider_id)
        total = int(health.total_successes + health.total_failures)
        reliability = (float(health.total_successes) / float(total)) if total else float(metadata.get("measured_reliability") or 0.0)
        benchmark = latest.get(manifest.provider_id)
        tps = float(getattr(benchmark, "tokens_per_second", 0.0) or truth.tokens_per_second)
        assigned = tuple(
            lane for lane in LOGICAL_MODEL_LANES if lane_config.model_for(lane).lower() in {manifest.model_name.lower(), manifest.provider_id.lower()}
        )
        out.append(
            ModelCapabilityRecord(
                provider_id=manifest.provider_id,
                model_id=manifest.model_name,
                runtime=str(metadata.get("runtime_family") or manifest.runtime_dependency or manifest.adapter_type or "unknown"),
                locality=truth.locality,
                installed=local_installed,
                loaded=local_loaded,
                warm=local_loaded or (health.last_success_at is not None and not health.circuit_open),
                context_limit=truth.context_window,
                tool_calling="tool_calls" in tool_support or "tool_intent" in capabilities,
                structured_output=truth.structured_output_support,
                vision="multimodal" in capabilities or "vision" in tool_support,
                coding_capability=_capability_score(capabilities, basic="code_basic", advanced="code_complex"),
                reasoning_capability=_reasoning_score(capabilities, truth, manifest.model_name),
                tokens_per_second=tps,
                ram_requirement_gb=truth.ram_budget_gb,
                vram_requirement_gb=truth.vram_budget_gb,
                measured_reliability=max(0.0, min(1.0, reliability)),
                health=truth.availability_state,
                last_benchmark=str(getattr(benchmark, "created_at", "") or truth.measured_at),
                assigned_lanes=assigned,
                cost_metadata=dict(metadata.get("pricing") or {}),
            )
        )
    return tuple(sorted(out, key=lambda item: (item.locality, item.provider_id)))


def _capability_score(capabilities: set[str], *, basic: str, advanced: str) -> float:
    if advanced in capabilities:
        return 1.0
    if basic in capabilities:
        return 0.6
    return 0.0


def _reasoning_score(capabilities: set[str], truth: ProviderCapabilityTruth, model_id: str) -> float:
    score = 0.35
    if "long_context" in capabilities:
        score += 0.2
    if "code_complex" in capabilities:
        score += 0.2
    if truth.role_fit == "queen":
        score += 0.15
    if model_parameter_billions(model_id) >= 13.0:
        score += 0.1
    return min(1.0, score)


__all__ = ["ModelCapabilityRecord", "build_model_capability_registry"]
