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


def _inventory(monkeypatch, models: dict[str, tuple[float, str]]) -> None:
    from core import local_ollama_inventory
    from core.local_ollama_inventory import InstalledOllamaModel

    monkeypatch.setattr(local_ollama_inventory, "installed_ollama_model_inventory", lambda **_k: tuple(
        InstalledOllamaModel(name=n, size_bytes=int(gb * 1e9), weights_format=f) for n, (gb, f) in models.items()))
    record._reset_for_tests()


@pytest.mark.parametrize(("total", "kept"), [(24.0, ["qwen3:8b"]), (64.0, ["qwen3.5:9b", "qwen3:8b"])])
def test_a_growing_runner_is_counted_at_its_growth_before_it_ever_loaded(monkeypatch, total, kept):
    """Gate54 on 0c0901943: with nothing measured yet a fresh profile picked and certified qwen3.5:9b (safetensors,
    Ollama's MLX runner), which reached 16 GB in five minutes. A GGUF model holds a fixed allocation."""
    _ram(monkeypatch, total)
    _inventory(monkeypatch, {"qwen3.5:9b": (6.6, "safetensors"), "qwen3:8b": (5.2, "gguf")})
    assert record.everyday_gb("qwen3.5:9b") == pytest.approx(16.5)
    assert record.everyday_gb("qwen3:8b") == 0.0
    pool = [_local("qwen3.5:9b"), _local("qwen3:8b")]
    assert [i.model_id for i in autopilot._without_local_models_that_outgrow_everyday_memory(pool)] == kept


def test_every_reading_of_what_ollama_holds_is_recorded(monkeypatch):
    import io
    import json as _json

    from core import resource_governor

    body = _json.dumps({"models": [{"name": "qwen3.5:9b", "size": int(15.2 * 1024 ** 3), "runner": "mlx"}]})
    monkeypatch.setattr(resource_governor, "_local_ollama_base", lambda _b=None: "http://169.254.0.9:11434")
    monkeypatch.setattr("core.local_model_policy.local_models_enabled", lambda *a, **k: True)
    monkeypatch.setattr(resource_governor, "urlopen", lambda *_a, **_k: io.BytesIO(body.encode()))
    assert resource_governor.resident_models()[0]["name"] == "qwen3.5:9b"
    assert record.peak_resident_gb("qwen3.5:9b") == 15.2


def test_boot_checks_a_model_that_fits_everyday_use_before_one_that_does_not(monkeypatch):
    from core import local_model_readiness, local_model_tool_certification

    monkeypatch.setattr(local_model_tool_certification, "certification_applies", lambda _m: True)

    class M:
        def __init__(self, name):
            self.model_name = name

    order: list[str] = []
    rows = local_model_readiness.ready_local_models(
        [M("qwen2.5:7b"), M("qwen3.5:9b"), M("qwen3:8b")], primary_model="qwen2.5:7b",
        status_of=lambda _m: {"state": "unknown"},
        run_check=lambda m: order.append(m.model_name) or {"state": "degraded"},
        everyday=lambda m: m.model_name != "qwen3.5:9b",
    )
    assert order == ["qwen2.5:7b", "qwen3:8b", "qwen3.5:9b"] and len(rows) == 3


def test_a_boot_primary_that_outgrows_everyday_memory_is_checked_after_one_that_fits(monkeypatch):
    """Gate55 on ace1c42cc: boot's adoption pass named qwen3.5:9b the primary, and it was checked before qwen3:8b."""
    from core import local_model_readiness, local_model_tool_certification

    monkeypatch.setattr(local_model_tool_certification, "certification_applies", lambda _m: True)

    class M:
        def __init__(self, name):
            self.model_name = name

    order: list[str] = []
    local_model_readiness.ready_local_models(
        [M("qwen3.5:9b"), M("qwen3:8b")], primary_model="qwen3.5:9b",
        status_of=lambda _m: {"state": "unknown"},
        run_check=lambda m: order.append(m.model_name) or {"state": "degraded"},
        everyday=lambda m: m.model_name != "qwen3.5:9b",
    )
    assert order == ["qwen3:8b", "qwen3.5:9b"]
