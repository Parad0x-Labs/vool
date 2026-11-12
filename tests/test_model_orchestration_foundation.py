from __future__ import annotations

from core.candidate_knowledge_lane import build_task_hash
from core.hardware_tier import MachineProbe
from core.memory_first_router import _candidate_cache_scope, _emergency_model_can_answer
from core.model_lane_config import LOCAL_FAST, logical_lane
from core.provider_routing import ProviderCapabilityTruth, provider_hardware_fit_rejection


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


def test_a_legacy_tiny_lane_name_is_the_fast_local_lane() -> None:
    assert logical_lane("tiny") == LOCAL_FAST
