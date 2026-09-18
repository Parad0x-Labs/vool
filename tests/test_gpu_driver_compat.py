from __future__ import annotations

from types import SimpleNamespace

from core.gpu_driver_compat import (
    GpuDriverCompatVerdict,
    detect_gpu_driver_compat,
    evaluate_driver_compat_for_probe,
    evaluate_gpu_driver_compat,
    gpu_driver_compat_advisory,
)

# The exact nvidia-smi facts observed live on the working GTX 1080 box (driver 582.66, Pascal cc 6.1).
# This is the tricky case the detector must NOT flag as broken: CUDA 13 dropped Pascal, but the
# 580-branch driver still ships Pascal kernels and Ollama serves it on its cuda_v12 runner.
_BOX_NAME = "NVIDIA GeForce GTX 1080"
_BOX_DRIVER = "582.66"
_BOX_COMPUTE_CAP = "6.1"
_BOX_SMI_CSV = f"{_BOX_NAME}, {_BOX_DRIVER}, {_BOX_COMPUTE_CAP}\n"

# Words the repo forbids in user-facing copy (hype / drama). Advisories must never contain them.
_BANNED_HYPE_WORDS = (
    "bulletproof",
    "blazing",
    "supercharge",
    "unleash",
    "revolutionary",
    "game-changer",
    "seamless",
    "effortless",
)


def _assert_no_hype(message: str) -> None:
    lowered = message.lower()
    for banned in _BANNED_HYPE_WORDS:
        assert banned not in lowered, f"advisory leaked hype word: {banned!r}"


# --- evaluate_gpu_driver_compat: the anchor cases ------------------------------------------------


def test_pascal_on_580_branch_driver_uses_cuda_v12_and_is_compatible() -> None:
    verdict = evaluate_gpu_driver_compat(
        gpu_name=_BOX_NAME,
        driver_version=_BOX_DRIVER,
        compute_cap=_BOX_COMPUTE_CAP,
        os_name="Windows",
    )
    assert isinstance(verdict, GpuDriverCompatVerdict)
    assert verdict.outcome == "uses_cuda_v12"
    assert verdict.compatible is True
    assert verdict.recommended_runner == "cuda_v12"
    assert verdict.gpu_generation == "Pascal"
    assert verdict.suggested_action == "none"


def test_pascal_on_too_old_driver_is_driver_too_old() -> None:
    # 516.94 is a CUDA 11.7-era Windows driver, below the 527.41 CUDA-12 floor.
    verdict = evaluate_gpu_driver_compat(
        gpu_name=_BOX_NAME, driver_version="516.94", compute_cap="6.1", os_name="Windows"
    )
    assert verdict.outcome == "driver_too_old"
    assert verdict.compatible is False
    assert verdict.suggested_action == "update_driver"


def test_pascal_on_610_branch_driver_is_driver_dropped_gpu() -> None:
    # 610.62 is the branch known to have dropped Pascal support entirely.
    verdict = evaluate_gpu_driver_compat(
        gpu_name=_BOX_NAME, driver_version="610.62", compute_cap="6.1", os_name="Windows"
    )
    assert verdict.outcome == "driver_dropped_gpu"
    assert verdict.compatible is False
    assert verdict.suggested_action == "install_supported_driver"


def test_modern_turing_plus_gpu_with_current_driver_is_ok() -> None:
    verdict = evaluate_gpu_driver_compat(
        gpu_name="NVIDIA GeForce RTX 4090", driver_version="560.94", compute_cap="8.9", os_name="Windows"
    )
    assert verdict.outcome == "ok"
    assert verdict.compatible is True
    assert verdict.recommended_runner == "cuda_v13"
    assert verdict.gpu_generation == "Ada Lovelace"


def test_turing_75_is_covered_by_cuda_v13_runner() -> None:
    verdict = evaluate_gpu_driver_compat(
        gpu_name="NVIDIA GeForce RTX 2060", driver_version="560.94", compute_cap="7.5", os_name="Windows"
    )
    assert verdict.outcome == "ok"
    assert verdict.recommended_runner == "cuda_v13"


def test_pre_maxwell_gpu_is_too_old_for_ollama() -> None:
    # Kepler (cc 3.5) is below Ollama's 5.0 floor regardless of driver.
    verdict = evaluate_gpu_driver_compat(
        gpu_name="NVIDIA GeForce GTX 780", driver_version="560.94", compute_cap="3.5", os_name="Windows"
    )
    assert verdict.outcome == "gpu_too_old"
    assert verdict.compatible is False
    assert verdict.suggested_action == "use_cpu"


# --- evaluate_gpu_driver_compat: absent / unparseable inputs -------------------------------------


def test_no_gpu_when_not_present() -> None:
    verdict = evaluate_gpu_driver_compat(gpu_present=False, os_name="Windows")
    assert verdict.outcome == "no_gpu"
    assert verdict.compatible is False
    assert verdict.suggested_action == "none"


def test_no_gpu_when_name_empty() -> None:
    verdict = evaluate_gpu_driver_compat(gpu_name="", driver_version="582.66", compute_cap="6.1")
    assert verdict.outcome == "no_gpu"


def test_unknown_when_compute_cap_missing() -> None:
    verdict = evaluate_gpu_driver_compat(gpu_name=_BOX_NAME, driver_version="582.66", compute_cap="")
    assert verdict.outcome == "unknown"
    assert verdict.suggested_action == "none"  # nothing scary; the live probe verifies


def test_unknown_when_driver_missing_but_compute_cap_ok() -> None:
    verdict = evaluate_gpu_driver_compat(gpu_name=_BOX_NAME, driver_version="", compute_cap="6.1", os_name="Windows")
    assert verdict.outcome == "unknown"


def test_cuda_max_version_below_12_is_authoritative_too_old() -> None:
    # Even if the driver number somehow parsed, a reported max CUDA < 12 means the CUDA 12 runner cannot load.
    verdict = evaluate_gpu_driver_compat(
        gpu_name=_BOX_NAME, driver_version="530.00", compute_cap="6.1", cuda_max_version="11.8", os_name="Windows"
    )
    assert verdict.outcome == "driver_too_old"
    assert verdict.suggested_action == "update_driver"


# --- OS-specific driver thresholds ---------------------------------------------------------------


def test_linux_pascal_at_cuda12_floor_uses_cuda_v12() -> None:
    verdict = evaluate_gpu_driver_compat(
        gpu_name=_BOX_NAME, driver_version="525.60.13", compute_cap="6.1", os_name="Linux"
    )
    assert verdict.outcome == "uses_cuda_v12"
    assert verdict.compatible is True


def test_linux_pascal_below_cuda12_floor_is_too_old() -> None:
    verdict = evaluate_gpu_driver_compat(
        gpu_name=_BOX_NAME, driver_version="520.61.05", compute_cap="6.1", os_name="Linux"
    )
    assert verdict.outcome == "driver_too_old"


def test_dropped_gpu_heuristic_is_windows_only() -> None:
    # A very new Linux driver number does not trigger the Windows-anchored "dropped" branch.
    verdict = evaluate_gpu_driver_compat(
        gpu_name=_BOX_NAME, driver_version="610.62", compute_cap="6.1", os_name="Linux"
    )
    assert verdict.outcome != "driver_dropped_gpu"


# --- advisories: plain, factual, no hype ---------------------------------------------------------


def test_advisory_driver_too_old_names_fix_and_cpu() -> None:
    verdict = evaluate_gpu_driver_compat(
        gpu_name=_BOX_NAME, driver_version="516.94", compute_cap="6.1", os_name="Windows"
    )
    message = gpu_driver_compat_advisory(verdict)
    lowered = message.lower()
    assert "driver" in lowered
    assert "update" in lowered
    assert "cpu" in lowered
    assert "516.94" in message
    _assert_no_hype(message)


def test_advisory_driver_dropped_pascal_points_at_580_branch() -> None:
    verdict = evaluate_gpu_driver_compat(
        gpu_name=_BOX_NAME, driver_version="610.62", compute_cap="6.1", os_name="Windows"
    )
    message = gpu_driver_compat_advisory(verdict)
    assert "580" in message  # the fix for Pascal is a 580-branch driver
    assert "582" in message  # names a concrete supported version
    assert "Pascal" in message
    assert "610.62" in message
    _assert_no_hype(message)


def test_advisory_uses_cuda_v12_is_neutral_and_mentions_cuda_12() -> None:
    verdict = evaluate_gpu_driver_compat(
        gpu_name=_BOX_NAME, driver_version=_BOX_DRIVER, compute_cap="6.1", os_name="Windows"
    )
    message = gpu_driver_compat_advisory(verdict)
    assert "cuda 12" in message.lower()
    _assert_no_hype(message)


def test_advisory_ok_is_nonempty_and_no_gpu_is_empty() -> None:
    ok = evaluate_gpu_driver_compat(
        gpu_name="NVIDIA GeForce RTX 4090", driver_version="560.94", compute_cap="8.9", os_name="Windows"
    )
    no_gpu = evaluate_gpu_driver_compat(gpu_present=False, os_name="Windows")
    assert gpu_driver_compat_advisory(ok)  # neutral one-line status
    assert gpu_driver_compat_advisory(no_gpu) == ""


def test_advisory_gpu_too_old_mentions_cpu() -> None:
    verdict = evaluate_gpu_driver_compat(
        gpu_name="NVIDIA GeForce GTX 780", driver_version="560.94", compute_cap="3.5", os_name="Windows"
    )
    assert "cpu" in gpu_driver_compat_advisory(verdict).lower()


# --- detect_gpu_driver_compat: injected nvidia-smi runner ----------------------------------------


def _smi_ok(args, timeout):
    return SimpleNamespace(returncode=0, stdout=_BOX_SMI_CSV)


def _smi_no_gpu(args, timeout):
    return SimpleNamespace(returncode=9, stdout="")


def _smi_raises(args, timeout):
    raise FileNotFoundError("nvidia-smi not found")


def test_detect_parses_live_smi_line() -> None:
    verdict = detect_gpu_driver_compat(smi_runner=_smi_ok, os_name="Windows")
    assert verdict.gpu_name == _BOX_NAME
    assert verdict.driver_version == _BOX_DRIVER
    assert verdict.compute_cap == _BOX_COMPUTE_CAP
    assert verdict.outcome == "uses_cuda_v12"


def test_detect_no_gpu_on_nonzero_returncode() -> None:
    assert detect_gpu_driver_compat(smi_runner=_smi_no_gpu, os_name="Windows").outcome == "no_gpu"


def test_detect_never_raises_when_smi_absent() -> None:
    assert detect_gpu_driver_compat(smi_runner=_smi_raises, os_name="Windows").outcome == "no_gpu"


# --- evaluate_driver_compat_for_probe: duck-typed MachineProbe -----------------------------------


def _fake_device(**kw):
    base = {"name": _BOX_NAME, "backend": "cuda", "compute_cap": 6.1, "driver_version": _BOX_DRIVER}
    base.update(kw)
    return SimpleNamespace(**base)


def _fake_probe(**kw):
    base = {
        "accelerator": "cuda",
        "gpu_name": _BOX_NAME,
        "driver_version": _BOX_DRIVER,
        "gpu_devices": (_fake_device(),),
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_probe_adapter_reads_selected_cuda_device() -> None:
    verdict = evaluate_driver_compat_for_probe(_fake_probe(), os_name="Windows")
    assert verdict.outcome == "uses_cuda_v12"
    assert verdict.gpu_name == _BOX_NAME
    assert verdict.compute_cap == "6.1"


def test_probe_adapter_returns_no_gpu_for_non_cuda_accelerator() -> None:
    verdict = evaluate_driver_compat_for_probe(_fake_probe(accelerator="cpu"), os_name="Windows")
    assert verdict.outcome == "no_gpu"


def test_probe_adapter_prefers_device_with_compute_cap() -> None:
    devices = (_fake_device(compute_cap=None), _fake_device(name="NVIDIA GeForce RTX 4090", compute_cap=8.9))
    verdict = evaluate_driver_compat_for_probe(
        _fake_probe(gpu_devices=devices, driver_version="560.94"), os_name="Windows"
    )
    assert verdict.compute_cap == "8.9"
    assert verdict.outcome == "ok"


def test_probe_adapter_never_raises_on_garbage_probe() -> None:
    verdict = evaluate_driver_compat_for_probe(object(), os_name="Windows")
    assert verdict.outcome == "no_gpu"
