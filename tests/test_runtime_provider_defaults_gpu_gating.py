from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from core.hardware_tier import MachineProbe, select_qwen_tier
from core.runtime_provider_defaults import _profile_allows_aux_local_providers, default_runtime_model_tag


def _gpu_probe(vram_gb: float) -> MachineProbe:
    """A usable discrete NVIDIA GPU with the given VRAM (GTX 1080-class default)."""
    return MachineProbe(
        cpu_cores=8,
        ram_gb=16.0,
        gpu_name="NVIDIA GeForce GTX 1080",
        vram_gb=vram_gb,
        accelerator="cuda",
        accelerator_status="usable",
    )


def _write_verified_cache(runtime_home: Path) -> None:
    cache_path = runtime_home / "config" / "llamacpp-capability-probe.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {
                "geforce gtx 1080|b9856|1": {
                    "schema": "vool.llamacpp_capability_probe.v1",
                    "probed_at_epoch": 9999999999.0,
                    "probe_version": 1,
                    "gpu_name": "GeForce GTX 1080",
                    "gpu_vendor": "nvidia",
                    "backend_tested": "vulkan",
                    "binary_release_tag": "b9856",
                    "cpu_baseline_tokens_per_second": 2.0,
                    "gpu_tokens_per_second": 36.0,
                    "speedup_ratio": 18.0,
                    "status": "gpu_confirmed_fast",
                    "verdict_backend": "vulkan",
                    "detail": "",
                }
            }
        ),
        encoding="utf-8",
    )


def test_local_max_and_full_orchestrated_always_allowed() -> None:
    assert _profile_allows_aux_local_providers("local-max") is True
    assert _profile_allows_aux_local_providers("full-orchestrated") is True


def test_empty_profile_id_is_allowed() -> None:
    assert _profile_allows_aux_local_providers("") is True


def test_local_only_denied_without_runtime_home() -> None:
    assert _profile_allows_aux_local_providers("local-only") is False


def test_local_only_denied_when_no_verified_probe_cached(tmp_path) -> None:
    assert _profile_allows_aux_local_providers("local-only", runtime_home=str(tmp_path)) is False


def test_local_only_allowed_when_gpu_capability_was_live_verified(tmp_path) -> None:
    _write_verified_cache(tmp_path)

    assert _profile_allows_aux_local_providers("local-only", runtime_home=str(tmp_path)) is True


def test_local_only_denied_when_cached_probe_rejected_the_gpu(tmp_path) -> None:
    cache_path = tmp_path / "config" / "llamacpp-capability-probe.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {
                "ancient igpu|b9856|1": {
                    "schema": "vool.llamacpp_capability_probe.v1",
                    "probed_at_epoch": 9999999999.0,
                    "probe_version": 1,
                    "gpu_name": "Ancient iGPU",
                    "gpu_vendor": "intel",
                    "backend_tested": "vulkan",
                    "binary_release_tag": "b9856",
                    "cpu_baseline_tokens_per_second": 2.0,
                    "gpu_tokens_per_second": 2.1,
                    "speedup_ratio": 1.05,
                    "status": "gpu_rejected_slow",
                    "verdict_backend": "cpu",
                    "detail": "",
                }
            }
        ),
        encoding="utf-8",
    )

    assert _profile_allows_aux_local_providers("local-only", runtime_home=str(tmp_path)) is False


def test_default_model_tag_on_7_8gb_gpu_fits_and_is_not_oversized() -> None:
    # Public-tester risk guard: a 6-8GB card must resolve to qwen2.5:7b (fits, fast),
    # never qwen3:8b (partial CPU offload -> multi-second calls that effectively fail).
    for vram in (6.0, 7.0, 8.0):
        with mock.patch(
            "core.runtime_provider_defaults.probe_machine", return_value=_gpu_probe(vram)
        ), mock.patch("core.runtime_provider_defaults._default_free_disk_gb", return_value=200.0):
            tag = default_runtime_model_tag(env={})
        assert tag == "qwen2.5:7b", f"{vram}GB GPU resolved to {tag!r}, expected qwen2.5:7b"
        assert "qwen3" not in tag


def test_select_qwen_tier_caps_small_gpu_below_the_10gb_step_up() -> None:
    # The VRAM tier ladder must keep 6-9GB cards on the 7b tier and only step up at 10GB+,
    # so the oversized-model regression can't sneak back in via the tier fallback path.
    for vram in (6.0, 7.0, 8.0, 9.0):
        tier = select_qwen_tier(_gpu_probe(vram))
        assert tier.ollama_tag == "qwen2.5:7b", f"{vram}GB -> {tier.ollama_tag}"
        assert tier.min_vram_gb <= vram
    assert select_qwen_tier(_gpu_probe(10.0)).ollama_tag == "qwen2.5:14b"
