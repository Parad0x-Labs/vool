from __future__ import annotations

from core.hardware_tier import MachineProbe
from installer.llamacpp_backend_select import asset_name_for_tag, select_backend_candidates


def test_select_backend_candidates_nvidia_gtx_1080_gets_cuda_and_vulkan() -> None:
    probe = MachineProbe(cpu_cores=8, ram_gb=7.9, gpu_name="NVIDIA GeForce GTX 1080", vram_gb=8.0, accelerator="cpu")

    selection = select_backend_candidates(probe)

    assert selection.gpu_vendor == "nvidia"
    assert [c.backend for c in selection.candidates] == ["cuda", "vulkan"]


def test_select_backend_candidates_no_gpu_returns_empty() -> None:
    probe = MachineProbe(cpu_cores=4, ram_gb=8.0, gpu_name=None, vram_gb=None, accelerator="cpu")

    selection = select_backend_candidates(probe)

    assert selection.candidates == ()
    assert selection.gpu_name == ""


def test_asset_name_for_tag_substitutes_release_tag() -> None:
    probe = MachineProbe(cpu_cores=8, ram_gb=16.0, gpu_name="AMD Radeon RX 7900 XTX", vram_gb=24.0, accelerator="cpu")
    selection = select_backend_candidates(probe)
    hip_candidate = selection.candidates[0]

    assert asset_name_for_tag(hip_candidate, tag="b9856") == "llama-b9856-bin-win-hip-radeon-x64.zip"
