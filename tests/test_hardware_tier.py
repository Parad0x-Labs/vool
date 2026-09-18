from __future__ import annotations

import subprocess
from unittest import mock

from core.hardware_tier import (
    GPUDevice,
    MachineProbe,
    _cuda_device_result,
    _select_accelerator,
    _try_cuda_devices,
    detect_gpu_devices,
    select_qwen_tier,
    tier_summary,
)
from core.local_model_bundles import capacity_bucket_for_machine, local_multi_llm_fit_from_probe


def test_select_qwen_tier_honors_env_override(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_OLLAMA_MODEL", "qwen2.5:7b")
    probe = MachineProbe(cpu_cores=10, ram_gb=24.0, gpu_name="Apple Silicon", vram_gb=24.0, accelerator="mps")

    tier = select_qwen_tier(probe)

    assert tier.tier_name == "base"
    assert tier.ollama_tag == "qwen2.5:7b"


def test_select_qwen_tier_supports_custom_override_tag(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_OLLAMA_MODEL", "ollama/custom-qwen")
    probe = MachineProbe(cpu_cores=10, ram_gb=24.0, gpu_name="Apple Silicon", vram_gb=24.0, accelerator="mps")

    tier = select_qwen_tier(probe)

    assert tier.tier_name == "override"
    assert tier.ollama_tag == "custom-qwen"


def test_select_qwen_tier_uses_ram_thresholds_for_apple_unified_memory(monkeypatch) -> None:
    monkeypatch.delenv("VOOL_OLLAMA_MODEL", raising=False)
    probe = MachineProbe(cpu_cores=10, ram_gb=24.0, gpu_name="Apple Silicon", vram_gb=24.0, accelerator="mps")

    tier = select_qwen_tier(probe)

    assert tier.tier_name == "base"
    assert tier.ollama_tag == "qwen2.5:7b"


def test_select_qwen_tier_unlocks_14b_on_higher_ram_apple_unified_memory(monkeypatch) -> None:
    monkeypatch.delenv("VOOL_OLLAMA_MODEL", raising=False)
    probe = MachineProbe(cpu_cores=12, ram_gb=36.0, gpu_name="Apple Silicon", vram_gb=36.0, accelerator="mps")

    tier = select_qwen_tier(probe)

    assert tier.tier_name == "mid"
    assert tier.ollama_tag == "qwen2.5:14b"


def test_select_qwen_tier_keeps_discrete_vram_selection_for_non_mps(monkeypatch) -> None:
    monkeypatch.delenv("VOOL_OLLAMA_MODEL", raising=False)
    probe = MachineProbe(cpu_cores=16, ram_gb=16.0, gpu_name="NVIDIA", vram_gb=24.0, accelerator="cuda")

    tier = select_qwen_tier(probe)

    assert tier.tier_name == "heavy"
    assert tier.ollama_tag == "qwen2.5:32b"


def test_select_qwen_tier_ignores_discrete_vram_when_accelerator_is_cpu(monkeypatch) -> None:
    monkeypatch.delenv("VOOL_OLLAMA_MODEL", raising=False)
    probe = MachineProbe(
        cpu_cores=8,
        ram_gb=8.0,
        gpu_name="NVIDIA GeForce GTX 1080",
        vram_gb=8.0,
        accelerator="cpu",
        accelerator_status="legacy_cuda_cpu_recommended",
    )

    tier = select_qwen_tier(probe)

    assert tier.tier_name == "lite"
    assert tier.ollama_tag == "qwen2.5:3b"
    assert local_multi_llm_fit_from_probe(probe) == "single_model_only"
    assert capacity_bucket_for_machine(probe=probe, free_disk_gb=120.0) == "A"


def test_cuda_card_classified_by_compute_capability(monkeypatch) -> None:
    # Compute capability is the architecture-accurate signal: CC >= 5.0 (Maxwell+) is a
    # first-class GPU; older (Kepler/Fermi, CC < 5.0) is legacy CPU-recommended.
    monkeypatch.delenv("VOOL_ALLOW_LEGACY_CUDA", raising=False)
    with mock.patch("core.hardware_tier.platform.system", return_value="Windows"):
        pascal = _cuda_device_result(
            index=0, gpu_name="NVIDIA GeForce GTX 1080", vram_gb=8.0, source="test", compute_cap=6.1
        )
        kepler = _cuda_device_result(
            index=0, gpu_name="NVIDIA GeForce GTX 780", vram_gb=3.0, source="test", compute_cap=3.5
        )
    assert pascal.status == "usable"  # a GTX 1080 is NOT legacy anymore
    assert kepler.status == "legacy_cuda_cpu_recommended"
    assert "VOOL_ALLOW_LEGACY_CUDA=1" in kepler.advice


def test_cuda_device_surfaces_compute_cap_for_driver_detector() -> None:
    # Regression: hardware_tier parses compute_cap for the legacy check, so it must also expose it
    # on GPUDevice. The static driver-compat detector reads it off the probe (it was dropped before,
    # so the detector saw no compute cap and returned "unknown" on the install/status path).
    device = _cuda_device_result(
        index=0, gpu_name="NVIDIA GeForce GTX 1080", vram_gb=8.0, source="test", compute_cap=6.1
    )
    assert device.compute_cap == 6.1
    assert device.to_dict()["compute_cap"] == 6.1


def test_cuda_device_compute_cap_is_none_when_unknown() -> None:
    device = _cuda_device_result(index=0, gpu_name="NVIDIA GeForce GTX 1070", vram_gb=8.0, source="test")
    assert device.compute_cap is None
    assert device.to_dict()["compute_cap"] is None


def test_cuda_legacy_name_fallback_when_compute_cap_unknown(monkeypatch) -> None:
    # Old driver / torch missing -> no compute cap -> conservative name heuristic. Pascal
    # (GTX 10xx) is still trusted; pre-Pascal (GTX 9xx and older) falls back to CPU sizing.
    monkeypatch.delenv("VOOL_ALLOW_LEGACY_CUDA", raising=False)
    with mock.patch("core.hardware_tier.platform.system", return_value="Windows"):
        pascal = _cuda_device_result(index=0, gpu_name="NVIDIA GeForce GTX 1070", vram_gb=8.0, source="test")
        maxwell = _cuda_device_result(index=0, gpu_name="NVIDIA GeForce GTX 980", vram_gb=4.0, source="test")
    assert pascal.status == "usable"
    assert maxwell.status == "legacy_cuda_cpu_recommended"


def test_allow_legacy_cuda_env_makes_old_card_usable(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_ALLOW_LEGACY_CUDA", "1")
    with mock.patch("core.hardware_tier.platform.system", return_value="Windows"):
        old = _cuda_device_result(
            index=0, gpu_name="NVIDIA GeForce GTX 780", vram_gb=3.0, source="test", compute_cap=3.5
        )
    assert old.status == "usable"  # opt-in overrides the legacy classification


def _bucket(ram, vram, acc, gpu, disk=300.0):
    probe = {"ram_gb": ram, "vram_gb": vram, "accelerator": acc, "accelerator_status": "usable"}
    return capacity_bucket_for_machine(probe=probe, free_disk_gb=disk, gpu_usable=gpu)


def test_capacity_bucket_usable_gpu_sized_by_vram_not_ram() -> None:
    # THE fix: a live-verified GPU sizes by VRAM and is never dragged to a tiny tier by
    # modest system RAM (the "24GB card in a 16GB box gets 4B models" bug).
    assert _bucket(16, 24, "cuda", True) == "E"
    assert _bucket(32, 24, "cuda", True) == "E"
    assert _bucket(16, 16, "cuda", True) == "D"
    assert _bucket(16, 12, "cuda", True) == "C"
    assert _bucket(16, 8, "cuda", True) == "B"


def test_capacity_bucket_detected_nvidia_gpu_is_vram_sized_for_ollama() -> None:
    # Ollama-on-GPU: a compute-cap-`usable` NVIDIA card (accelerator_status "usable", which the
    # _bucket helper sets) is VRAM-sized even with NO live llama.cpp verdict (gpu=False), because
    # Ollama auto-offloads into CUDA VRAM. The tier follows VRAM, not modest host RAM.
    assert _bucket(8, 8, "cuda", False) == "B"    # 1080: 8GB RAM alone -> A; the 8GB card lifts it to B
    assert _bucket(16, 24, "cuda", False) == "E"  # 24GB card in a 16GB box -> E, not RAM-bound B


def test_capacity_bucket_unusable_or_small_gpu_is_ram_sized() -> None:
    # A card NOT classified usable (status unset/legacy) makes no GPU-serving promise -> RAM-sized.
    assert capacity_bucket_for_machine(
        probe={"ram_gb": 16.0, "vram_gb": 24.0, "accelerator": "cuda", "accelerator_status": ""},
        free_disk_gb=300.0,
    ) == "B"
    # A <6GB GPU can't hold the daily bundle model -> fall back to RAM sizing even if verified.
    assert _bucket(16, 4, "cuda", True) == "B"


def test_capacity_bucket_cpu_only_scales_with_ram() -> None:
    # Old bug: every CPU-only machine collapsed to bucket A. Now a strong CPU host scales.
    assert _bucket(8, 0, "cpu", False) == "A"
    assert _bucket(16, 0, "cpu", False) == "B"
    assert _bucket(32, 0, "cpu", False) == "D"
    assert _bucket(64, 0, "cpu", False) == "E"


def test_capacity_bucket_free_disk_caps_the_tier() -> None:
    assert _bucket(64, 24, "cuda", True, disk=15.0) == "A"  # <20GB free
    assert _bucket(64, 24, "cuda", True, disk=50.0) == "C"  # <80GB free
    assert _bucket(64, 24, "cuda", True, disk=300.0) == "E"


def test_capacity_bucket_mps_uses_unified_ram() -> None:
    # Unified memory is shared with the OS -> RAM-style headroom (a 24GB Mac is C, not E).
    assert _bucket(8, 8, "mps", False) == "A"
    assert _bucket(16, 16, "mps", False) == "B"
    assert _bucket(24, 24, "mps", False) == "C"
    assert _bucket(64, 64, "mps", False) == "E"


def test_cuda_detection_prefers_nvidia_smi_and_never_touches_torch(monkeypatch) -> None:
    # torch.cuda init can hang (AV scanning CUDA DLLs) — it must NOT run when the bounded
    # nvidia-smi path already found the GPU. This is the fix for the step-6 install freeze.
    torch_calls = {"n": 0}

    def _torch_spy():
        torch_calls["n"] += 1
        return ()

    smi_dev = GPUDevice(
        index=0, name="NVIDIA GeForce RTX 4090", vendor="nvidia", vram_gb=24.0,
        backend="cuda", status="usable", source="nvidia-smi",
    )
    monkeypatch.setattr("core.hardware_tier._try_torch_cuda_devices", _torch_spy)
    monkeypatch.setattr("core.hardware_tier._try_nvidia_smi_devices", lambda: (smi_dev,))
    devices = _try_cuda_devices()
    assert [d.name for d in devices] == ["NVIDIA GeForce RTX 4090"]
    assert torch_calls["n"] == 0  # torch (the hang risk) was never imported/called


def test_cuda_detection_env_can_skip_torch_entirely(monkeypatch) -> None:
    torch_calls = {"n": 0}

    def _torch_spy():
        torch_calls["n"] += 1
        return ()

    monkeypatch.setattr("core.hardware_tier._try_nvidia_smi_devices", lambda: ())
    monkeypatch.setattr("core.hardware_tier._try_torch_cuda_devices", _torch_spy)
    monkeypatch.setenv("VOOL_SKIP_TORCH_GPU_PROBE", "1")
    assert _try_cuda_devices() == ()
    assert torch_calls["n"] == 0  # env escape hatch skips torch even with no nvidia-smi


def test_windows_multi_gpu_inventory_selects_best_usable_cuda_device(monkeypatch) -> None:
    monkeypatch.delenv("VOOL_OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("VOOL_FORCE_OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("VOOL_ALLOW_LEGACY_CUDA", raising=False)
    monkeypatch.setattr("core.hardware_tier._try_torch_cuda_devices", lambda: ())

    def fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=(
                "0, NVIDIA GeForce GTX 1080, 8192\n"
                "1, NVIDIA GeForce RTX 4090, 24576\n"
            ),
            stderr="",
        )

    monkeypatch.setattr("core.hardware_tier.subprocess.run", fake_run)
    with mock.patch("core.hardware_tier.platform.system", return_value="Windows"):
        devices = detect_gpu_devices()
        gpu_name, vram_gb, accelerator, status, advice = _select_accelerator(devices)

    assert [device.name for device in devices] == ["NVIDIA GeForce GTX 1080", "NVIDIA GeForce RTX 4090"]
    # Both are first-class GPUs now (the 1080 is no longer downgraded); selection still
    # prefers the higher-VRAM 4090.
    assert devices[0].status == "usable"
    assert devices[1].status == "usable"
    assert gpu_name == "NVIDIA GeForce RTX 4090"
    assert vram_gb == 24.0
    assert accelerator == "cuda"
    assert status == "usable"
    assert advice == ""

    summary = tier_summary(
        MachineProbe(
            cpu_cores=16,
            ram_gb=64.0,
            gpu_name=gpu_name,
            vram_gb=vram_gb,
            accelerator=accelerator,
            accelerator_status=status,
            accelerator_advice=advice,
            gpu_devices=devices,
        )
    )

    assert summary["gpu_count"] == 2
    assert summary["gpu_devices"][0]["selected"] is False
    assert summary["gpu_devices"][1]["selected"] is True
    assert summary["gpu_devices"][1]["active_accelerator"] is True


def test_windows_gpu_inventory_adds_nonduplicate_directml_devices(monkeypatch) -> None:
    monkeypatch.delenv("VOOL_ALLOW_LEGACY_CUDA", raising=False)
    monkeypatch.setattr("core.hardware_tier._try_torch_cuda_devices", lambda: ())

    def fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        command = args[0]
        if command[0] == "nvidia-smi":
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="0, NVIDIA GeForce RTX 4090, 24576\n",
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=(
                "Node,AdapterRAM,Name\n"
                "DESKTOP,25769803776,NVIDIA GeForce RTX 4090\n"
                "DESKTOP,17179869184,AMD Radeon RX 7900 XTX\n"
            ),
            stderr="",
        )

    monkeypatch.setattr("core.hardware_tier.subprocess.run", fake_run)
    with mock.patch("core.hardware_tier.platform.system", return_value="Windows"):
        devices = detect_gpu_devices()

    assert [device.name for device in devices] == [
        "NVIDIA GeForce RTX 4090",
        "AMD Radeon RX 7900 XTX",
    ]
    assert [device.backend for device in devices] == ["cuda", "directml"]
