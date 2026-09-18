from __future__ import annotations

from core.candidate_knowledge_lane import build_task_hash
from core.hardware_tier import MachineProbe
from core.memory_first_router import _candidate_cache_scope, _emergency_model_can_answer
from core.model_capability_registry import build_model_capability_registry
from core.model_lane_config import LOCAL_DAILY, LOCAL_FAST, logical_lane
from core.provider_routing import ProviderCapabilityTruth, provider_hardware_fit_rejection
from storage.migrations import run_migrations
from storage.model_provider_manifest import ModelProviderManifest


def _local_manifest(model_name: str) -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="ollama-local",
        model_name=model_name,
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen2.5",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "structured_json", "tool_intent"],
        runtime_config={"base_url": "http://127.0.0.1:11434", "context_window": 32768},
        metadata={
            "deployment_class": "local",
            "runtime_family": "ollama",
            "ram_budget_gb": 6.5,
            "vram_budget_gb": 6.5,
            "tool_support": ["structured_json", "tool_calls"],
        },
    )


def _truth(model_id: str, vram_gb: float) -> ProviderCapabilityTruth:
    return ProviderCapabilityTruth(
        provider_id=f"ollama-local:{model_id}",
        model_id=model_id,
        role_fit="drone",
        context_window=32768,
        tool_support=(),
        structured_output_support=True,
        tokens_per_second=0.0,
        locality="local",
        privacy_class="local_private",
        queue_depth=0,
        max_safe_concurrency=1,
        ram_budget_gb=vram_gb,
        vram_budget_gb=vram_gb,
        quantization="q4",
    )


def test_known_model_hardware_gate_excludes_oversized_failover() -> None:
    probe = MachineProbe(
        cpu_cores=8,
        ram_gb=32.0,
        gpu_name="GTX 1080",
        vram_gb=8.0,
        accelerator="cuda",
    )

    assert provider_hardware_fit_rejection(_truth("qwen2.5:7b", 6.5), probe=probe) == ""
    assert provider_hardware_fit_rejection(_truth("qwen3:8b", 7.5), probe=probe) == "model_exceeds_hardware_budget"
    assert provider_hardware_fit_rejection(_truth("qwen3:8b", 7.5), probe=probe, force_cpu=True) == ""
    assert provider_hardware_fit_rejection(_truth("deepseek-r1:14b", 12.0), probe=probe) == "model_exceeds_hardware_budget"


def test_emergency_model_degrades_for_synthesis_but_keeps_tiny_tasks() -> None:
    assert not _emergency_model_can_answer(model_name="qwen3:0.6b", lane="daily")
    assert not _emergency_model_can_answer(model_name="qwen3:0.6b", lane="deep")
    assert _emergency_model_can_answer(model_name="qwen3:0.6b", lane="tiny")


def test_candidate_cache_is_scoped_to_session_and_turn() -> None:
    first_scope = _candidate_cache_scope({"session_id": "session-a", "turn_id": "turn-1"})
    second_scope = _candidate_cache_scope({"session_id": "session-a", "turn_id": "turn-2"})
    assert first_scope
    assert second_scope
    assert _candidate_cache_scope({"session_id": "session-a"}) == ""

    base = dict(normalized_input="same text", task_class="research", output_mode="plain_text")
    assert build_task_hash(**base, scope_id=first_scope) != build_task_hash(**base, scope_id=second_scope)


def test_capability_registry_reports_runtime_state_and_lane(monkeypatch) -> None:
    run_migrations()
    manifest = _local_manifest("qwen2.5:7b")
    monkeypatch.setenv("VOOL_LOCAL_DAILY_MODEL", manifest.model_name)

    records = build_model_capability_registry(
        [manifest],
        installed_ollama=[manifest.model_name],
        loaded_ollama=[manifest.model_name],
    )

    assert len(records) == 1
    record = records[0]
    assert record.installed is True
    assert record.loaded is True
    assert record.warm is True
    assert record.context_limit == 32768
    assert record.tool_calling is True
    assert record.structured_output is True
    assert record.assigned_lanes == (LOCAL_DAILY,)
    assert logical_lane("tiny") == LOCAL_FAST
