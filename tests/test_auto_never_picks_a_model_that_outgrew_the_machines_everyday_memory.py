"""Auto counts a local model at the most memory it has held on this machine, never at its size on disk.

2026-11-27, 24 GB gate iMac: qwen3.5:9b (6.6 GB on disk) grew under Ollama's MLX engine from 8.2 GB to 16.4 GB in one
session and never shrank; Auto kept picking it and turns took 71 s in swap (CUT-LIST 66). The rule is the model's own
measured peak (Ollama's /api/ps) against half of the machine's memory, for any model and engine."""
from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from core import local_inference_autopilot as autopilot
from core import local_model_footprint_record as record
from core import runtime_paths, system_resources
from core.local_inference_autopilot import ProviderCapabilityTruth


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_paths, "active_data_dir", lambda: tmp_path)
    record._reset_for_tests()
    yield
    record._reset_for_tests()


def _ram(monkeypatch, total: float) -> None:
    monkeypatch.setattr(system_resources, "snapshot", lambda: SimpleNamespace(total_ram_gb=total, available_ram_gb=4.0))


def _local(model: str) -> ProviderCapabilityTruth:
    return ProviderCapabilityTruth(
        provider_id=f"ollama-local:{model}", model_id=model, role_fit="general", context_window=32768,
        tool_support=("structured_json",), structured_output_support=True, tokens_per_second=0.0,
        ram_budget_gb=0.0, vram_budget_gb=0.0, quantization="", locality="local", privacy_class="local_private",
        queue_depth=0, max_safe_concurrency=1,
    )


def test_the_peak_each_model_held_is_kept_and_only_rises(tmp_path):
    record.record_resident([{"name": "qwen3.5:9b", "size_gb": 8.2}, {"name": "qwen3:8b", "size_gb": 6.1}])
    record.record_resident([{"name": "qwen3.5:9b", "size_gb": 16.4}])
    record.record_resident([{"name": "qwen3.5:9b", "size_gb": 9.0}])
    assert record.peak_resident_gb("qwen3.5:9b") == 16.4 and record.peak_resident_gb("qwen3:8b") == 6.1
    record._reset_for_tests()  # read back from the file, as after a restart
    assert record.peak_resident_gb("qwen3.5:9b:latest".replace(":latest", "")) == 16.4
    assert (tmp_path / "local_model_footprints.json").is_file()
    assert record.peak_resident_gb("never-loaded:1b") == 0.0


@pytest.mark.parametrize(("total", "expected"), [(24.0, "qwen3:8b"), (16.0, "qwen3:8b"), (64.0, None)])
def test_a_model_that_outgrew_half_the_machine_is_not_autos_pick(monkeypatch, total, expected):
    _ram(monkeypatch, total)
    record.record_resident([{"name": "qwen3.5:9b", "size_gb": 16.4}, {"name": "qwen3:8b", "size_gb": 6.1}])
    pool = [_local("qwen3.5:9b"), _local("qwen3:8b")]
    kept = [item.model_id for item in autopilot._without_local_models_that_outgrow_everyday_memory(pool)]
    assert kept == ([expected] if expected else ["qwen3.5:9b", "qwen3:8b"])


def test_a_model_never_seen_loaded_or_a_machine_of_unknown_size_changes_nothing(monkeypatch):
    _ram(monkeypatch, 24.0)
    pool = [_local("qwen3.5:9b"), _local("qwen3:8b")]
    assert autopilot._without_local_models_that_outgrow_everyday_memory(pool) == pool
    record.record_resident([{"name": "qwen3.5:9b", "size_gb": 16.4}])
    _ram(monkeypatch, 0.0)
    assert autopilot._without_local_models_that_outgrow_everyday_memory(pool) == pool


def test_when_every_local_model_outgrew_it_nothing_is_removed(monkeypatch):
    _ram(monkeypatch, 8.0)
    record.record_resident([{"name": "qwen3.5:9b", "size_gb": 16.4}, {"name": "qwen3:8b", "size_gb": 6.1}])
    pool = [_local("qwen3.5:9b"), _local("qwen3:8b")]
    assert autopilot._without_local_models_that_outgrow_everyday_memory(pool) == pool


def test_the_daily_pick_skips_the_grown_model(monkeypatch):
    _ram(monkeypatch, 24.0)
    # Measured faster, the grown model is the daily lane's pick until its peak is known.
    pool = (dataclasses.replace(_local("qwen3.5:9b"), tokens_per_second=60.0, measurement_source="benchmark"),
            dataclasses.replace(_local("qwen3:8b"), tokens_per_second=10.0, measurement_source="benchmark"))
    assert autopilot.prioritize_daily_residency_capabilities(pool)[0].model_id == "qwen3.5:9b"
    record.record_resident([{"name": "qwen3.5:9b", "size_gb": 8.2}])
    assert autopilot.prioritize_daily_residency_capabilities(pool)[0].model_id == "qwen3.5:9b"
    record.record_resident([{"name": "qwen3.5:9b", "size_gb": 16.4}])
    assert autopilot.prioritize_daily_residency_capabilities(pool)[0].model_id == "qwen3:8b"
