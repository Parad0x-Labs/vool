from __future__ import annotations

import pytest

from core.hardware_tier import MachineProbe
from core.install_recommendations import cpu_fallback_model


@pytest.fixture(autouse=True)
def _clear_model_overrides(monkeypatch) -> None:
    # select_qwen_tier honours these as a hard override that would defeat the fallback logic.
    for name in ("VOOL_OLLAMA_MODEL", "VOOL_FORCE_OLLAMA_MODEL"):
        monkeypatch.delenv(name, raising=False)


def test_prefers_the_cpu_pick_on_a_capable_box() -> None:
    # A 32 GB RAM CPU box: the CPU fallback is the CPU ladder's responsive qwen3.5:4b, never a
    # bigger model the RAM alone would allow.
    probe = MachineProbe(cpu_cores=16, ram_gb=32.0, gpu_name=None, vram_gb=None, accelerator="cpu")
    assert cpu_fallback_model(probe) == "qwen3.5:4b"


def test_never_larger_than_hardware_pick_on_a_tiny_box() -> None:
    # A tiny box (4 GB RAM) cannot fit the preferred qwen3.5:4b, so select_qwen_tier floors to
    # the nano tag; cpu_fallback_model must NOT hand back the larger preferred 4b tag.
    probe = MachineProbe(cpu_cores=2, ram_gb=4.0, gpu_name=None, vram_gb=None, accelerator="cpu")
    assert cpu_fallback_model(probe) == "qwen3.5:0.8b"


def test_gpu_box_still_downsizes_to_cpu_responsive_tag() -> None:
    # Even a box whose static accelerator looks GPU-capable gets the small CPU tag, since the
    # fallback is only ever consulted after GPU verification failed.
    probe = MachineProbe(
        cpu_cores=8,
        ram_gb=16.0,
        gpu_name="NVIDIA GeForce GTX 1080",
        vram_gb=8.0,
        accelerator="cuda",
    )
    assert cpu_fallback_model(probe) == "qwen3.5:4b"
