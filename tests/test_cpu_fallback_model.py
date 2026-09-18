from __future__ import annotations

import pytest

from core.hardware_tier import MachineProbe
from core.install_recommendations import cpu_fallback_model


@pytest.fixture(autouse=True)
def _clear_model_overrides(monkeypatch) -> None:
    # select_qwen_tier honours these as a hard override that would defeat the fallback logic.
    for name in ("VOOL_OLLAMA_MODEL", "VOOL_FORCE_OLLAMA_MODEL"):
        monkeypatch.delenv(name, raising=False)


def test_prefers_qwen25_3b_on_a_capable_box() -> None:
    # 32 GB RAM CPU box would otherwise pick qwen2.5:14b (larger than 3b), so the CPU
    # fallback caps down to the responsive qwen2.5:3b tag.
    probe = MachineProbe(cpu_cores=16, ram_gb=32.0, gpu_name=None, vram_gb=None, accelerator="cpu")
    assert cpu_fallback_model(probe) == "qwen2.5:3b"


def test_never_larger_than_hardware_pick_on_a_tiny_box() -> None:
    # A tiny box (4 GB RAM) cannot even fit qwen2.5:3b, so select_qwen_tier floors to the
    # nano tag; cpu_fallback_model must NOT hand back the larger preferred 3b tag.
    probe = MachineProbe(cpu_cores=2, ram_gb=4.0, gpu_name=None, vram_gb=None, accelerator="cpu")
    assert cpu_fallback_model(probe) == "qwen2.5:0.5b"


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
    assert cpu_fallback_model(probe) == "qwen2.5:3b"
