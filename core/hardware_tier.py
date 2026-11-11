"""Hardware probe, and the tier name for the local model this machine gets.

Which model a machine gets is decided in ONE place, `core.local_model_suggestions` (Apple Silicon
sized by RAM, a usable NVIDIA card by its VRAM, a CPU machine for speed, free disk as the cap).
This module probes the hardware and names the tier of that pick, so the installer, the runtime
backbone and the logs all report the same model the Settings Download button offers.

Tier names, smallest to largest (the daily model for each):
  nano  – qwen3.5:0.8b  (under ~6 GB of memory)
  lite  – qwen3.5:2b    (8 GB machines)
  base  – qwen3.5:4b    (12-16 GB Macs, CPU-only PCs up to 44 GB, 6-10 GB NVIDIA cards)
  mid   – qwen3.5:9b    (18-44 GB Macs, 10-22 GB NVIDIA cards)
  heavy – qwen3.8:27b   (22 GB+ NVIDIA cards)
  titan – qwen3.6:35b   (44 GB+ Macs and CPU machines; a 35B mixture of experts, 3B active)
An Ollama older than 0.40 gets the qwen3 tag of the same tier instead.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class GPUDevice:
    index: int
    name: str
    vendor: str
    vram_gb: float | None
    backend: str  # cuda | mps | directml
    status: str  # usable | legacy_cuda_cpu_recommended | blocked
    advice: str = ""
    source: str = ""
    # driver_version + vram_free_gb are best-effort signals for the live GPU-inference
    # gate: driver_version catches the "driver too old for the Ollama CUDA build" (PTX
    # toolchain) case, vram_free_gb sizes against ACTUAL free VRAM (memory.total is not
    # enough — an 8B model won't fit ~5.9 GB free even on an 8 GB card). Older drivers may
    # omit these columns, so both default to unset.
    driver_version: str = ""
    vram_free_gb: float | None = None
    # Parsed GPU compute capability (e.g. 6.1 for Pascal). Surfaced so the static driver-compat
    # detector can judge CUDA-runner support from the probe without re-running nvidia-smi. Older
    # drivers omit the column, so it defaults to unset.
    compute_cap: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "name": self.name,
            "vendor": self.vendor,
            "vram_gb": round(self.vram_gb, 1) if self.vram_gb is not None else None,
            "backend": self.backend,
            "status": self.status,
            "advice": self.advice,
            "source": self.source,
            "driver_version": self.driver_version,
            "vram_free_gb": round(self.vram_free_gb, 1) if self.vram_free_gb is not None else None,
            "compute_cap": self.compute_cap,
        }


@dataclass
class MachineProbe:
    cpu_cores: int
    ram_gb: float
    gpu_name: str | None
    vram_gb: float | None
    accelerator: str  # cuda | mps | directml | vulkan | cpu
    accelerator_status: str = ""
    accelerator_advice: str = ""
    gpu_devices: tuple[GPUDevice, ...] = ()
    # Threaded from the selected accelerator device (see probe_machine); best-effort so
    # the router/installer can gate on the actual driver + free VRAM without re-probing.
    driver_version: str = ""
    vram_free_gb: float | None = None


@dataclass
class QwenTier:
    tier_name: str
    ollama_tag: str
    param_billions: float
    min_vram_gb: float
    min_ram_gb: float


#: Reference rows of the suggestion table (core.local_model_suggestions holds the ladders that
#: decide). min_ram_gb is the least machine memory any ladder gives this model as the daily one,
#: min_vram_gb the least NVIDIA card memory; 0.0 where that ladder never picks it.
TIERS: list[QwenTier] = [
    QwenTier("titan", "qwen3.6:35b",  35.0,  0.0, 44.0),
    QwenTier("heavy", "qwen3.8:27b",  27.0, 22.0,  0.0),
    QwenTier("mid",   "qwen3.5:9b",    9.0, 10.0, 17.0),
    QwenTier("base",  "qwen3.5:4b",    4.0,  6.0, 11.0),
    QwenTier("lite",  "qwen3.5:2b",    2.0,  0.0,  5.5),
    QwenTier("nano",  "qwen3.5:0.8b",  0.8,  0.0,  0.0),
]

#: Apple Silicon rows (unified memory): min_ram_gb is the Mac's total memory for the row.
MPS_TIERS: list[QwenTier] = [
    QwenTier("titan", "qwen3.6:35b",  35.0, 0.0, 44.0),
    QwenTier("mid",   "qwen3.5:9b",    9.0, 0.0, 17.0),
    QwenTier("base",  "qwen3.5:4b",    4.0, 0.0, 11.0),
    QwenTier("lite",  "qwen3.5:2b",    2.0, 0.0,  5.5),
    QwenTier("nano",  "qwen3.5:0.8b",  0.8, 0.0,  0.0),
]

#: The same tiers on an Ollama older than 0.40 (core.local_model_suggestions previous_generation).
_TIER_NAME_BY_TAG: dict[str, str] = {
    **{tier.ollama_tag: tier.tier_name for tier in TIERS},
    "qwen3:0.6b": "nano",
    "qwen3:1.7b": "lite",
    "qwen3:4b": "base",
    "qwen3:8b": "mid",
    "qwen3:32b": "heavy",
    "qwen3:30b": "titan",
}


def probe_machine() -> MachineProbe:
    cpu_cores = os.cpu_count() or 2
    ram_gb = _detect_ram_gb()
    gpu_devices = detect_gpu_devices()
    gpu_name, vram_gb, accelerator, accelerator_status, accelerator_advice = _select_accelerator(gpu_devices)
    driver_version, vram_free_gb = _selected_device_signals(gpu_devices, gpu_name)
    return MachineProbe(
        cpu_cores=cpu_cores,
        ram_gb=ram_gb,
        gpu_name=gpu_name,
        vram_gb=vram_gb,
        accelerator=accelerator,
        accelerator_status=accelerator_status,
        accelerator_advice=accelerator_advice,
        gpu_devices=gpu_devices,
        driver_version=driver_version,
        vram_free_gb=vram_free_gb,
    )


def select_qwen_tier(probe: MachineProbe | None = None) -> QwenTier:
    """The tier of the daily model this machine gets (see core.local_model_suggestions)."""
    override_tag = _override_model_tag()
    if override_tag:
        for tier in TIERS:
            if tier.ollama_tag == override_tag:
                return tier
        return QwenTier("override", override_tag, 0.0, 0.0, 0.0)

    if probe is None:
        probe = probe_machine()

    from core.local_model_suggestions import suggest_local_models

    tag = suggest_local_models(probe).daily
    for tier in TIERS:
        if tier.ollama_tag == tag:
            return tier
    name = _TIER_NAME_BY_TAG.get(tag, "base")
    reference = next((tier for tier in TIERS if tier.tier_name == name), TIERS[-1])
    try:
        from core.local_model_bundles import model_total_parameter_billions

        params = float(model_total_parameter_billions(tag))
    except Exception:
        params = reference.param_billions
    return QwenTier(name, tag, params, reference.min_vram_gb, reference.min_ram_gb)


def recommended_ollama_model(probe: MachineProbe | None = None) -> str:
    """Return the Ollama model tag string for the best tier."""
    return select_qwen_tier(probe).ollama_tag


def tier_summary(probe: MachineProbe | None = None) -> dict:
    """Human-readable summary for installers/logs."""
    if probe is None:
        probe = probe_machine()
    tier = select_qwen_tier(probe)
    accelerator = str(probe.accelerator or "").strip().lower()
    accelerator_status = str(getattr(probe, "accelerator_status", "") or "").strip()
    if not accelerator_status:
        accelerator_status = "cpu" if accelerator == "cpu" else "usable"
    gpu_devices = _gpu_device_rows(probe)
    vram_free_gb = getattr(probe, "vram_free_gb", None)
    return {
        "cpu_cores": probe.cpu_cores,
        "ram_gb": round(probe.ram_gb, 1),
        "gpu": probe.gpu_name or "none",
        "vram_gb": round(probe.vram_gb, 1) if probe.vram_gb is not None else None,
        "gpu_count": len(gpu_devices),
        "gpu_devices": gpu_devices,
        "accelerator": probe.accelerator,
        "accelerator_status": accelerator_status,
        "accelerator_advice": str(getattr(probe, "accelerator_advice", "") or "").strip(),
        "driver_version": str(getattr(probe, "driver_version", "") or "").strip(),
        "vram_free_gb": round(vram_free_gb, 1) if vram_free_gb is not None else None,
        "selected_tier": tier.tier_name,
        "ollama_model": tier.ollama_tag,
        "param_billions": tier.param_billions,
    }


# ---------------------------------------------------------------------------
# Internal probes
# ---------------------------------------------------------------------------

def _detect_ram_gb() -> float:
    try:
        import psutil  # type: ignore
        return float(psutil.virtual_memory().total) / (1024.0 ** 3)
    except Exception:
        pass
    # Fallback: Windows wmic
    if platform.system().lower() == "windows":
        try:
            import subprocess
            out = subprocess.check_output(
                ["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"],
                text=True, timeout=5,
            )
            for line in out.strip().splitlines():
                line = line.strip()
                if line.isdigit():
                    return float(line) / (1024.0 ** 3)
        except Exception:
            pass
    return 4.0  # conservative fallback


def _override_model_tag() -> str | None:
    raw = str(
        os.environ.get("VOOL_OLLAMA_MODEL")
        or os.environ.get("VOOL_FORCE_OLLAMA_MODEL")
        or ""
    ).strip()
    if not raw:
        return None
    if "/" in raw:
        raw = raw.split("/", 1)[-1].strip()
    return raw or None


def detect_gpu_devices() -> tuple[GPUDevice, ...]:
    """Return every usable GPU backend candidate the installer can reason about."""
    devices: list[GPUDevice] = list(_try_cuda_devices())
    devices.extend(_new_backend_devices(devices, _try_mps_devices()))
    devices.extend(_new_backend_devices(devices, _try_directml_devices()))
    return tuple(devices)


def _detect_gpu() -> tuple[str | None, float | None, str, str, str]:
    """Returns (gpu_name, vram_gb, accelerator, accelerator_status, accelerator_advice)."""
    return _select_accelerator(detect_gpu_devices())


def _try_cuda() -> tuple[str | None, float | None, str, str, str]:
    return _select_accelerator(_try_cuda_devices())


def _try_cuda_devices() -> tuple[GPUDevice, ...]:
    # nvidia-smi FIRST: it is bounded (timeout=5) and now reports name + VRAM + compute_cap,
    # so it fully covers CUDA detection without importing torch. `torch.cuda.is_available()`
    # can hang for a very long time (no interruptible timeout) while it initializes a CUDA
    # context — most often when antivirus/Defender real-time-scans the CUDA DLLs on first
    # load, which froze at least one install at the hardware-detection step. So torch is only
    # a fallback when nvidia-smi is unavailable, and can be disabled with
    # VOOL_SKIP_TORCH_GPU_PROBE=1.
    devices = _try_nvidia_smi_devices()
    if devices:
        return devices
    if _env_truthy("VOOL_SKIP_TORCH_GPU_PROBE"):
        return ()
    return _try_torch_cuda_devices()


def _try_torch_cuda_devices() -> tuple[GPUDevice, ...]:
    try:
        import torch  # type: ignore
        if not torch.cuda.is_available():
            return ()
        devices: list[GPUDevice] = []
        for index in range(int(torch.cuda.device_count() or 0)):
            name = str(torch.cuda.get_device_name(index) or "").strip()
            if not name:
                continue
            vram_gb: float | None = None
            vram_free_gb: float | None = None
            try:
                free, total = torch.cuda.mem_get_info(index)
                vram_gb = float(total) / (1024.0 ** 3)
                vram_free_gb = float(free) / (1024.0 ** 3)
            except Exception:
                try:
                    props = torch.cuda.get_device_properties(index)
                    vram_gb = float(props.total_memory) / (1024.0 ** 3)
                except Exception:
                    vram_gb = None
            compute_cap: float | None = None
            try:
                major, minor = torch.cuda.get_device_capability(index)
                compute_cap = float(f"{int(major)}.{int(minor)}")
            except Exception:
                compute_cap = None
            devices.append(
                _cuda_device_result(
                    index=index,
                    gpu_name=name,
                    vram_gb=vram_gb,
                    source="torch",
                    compute_cap=compute_cap,
                    vram_free_gb=vram_free_gb,
                )
            )
        return tuple(devices)
    except Exception:
        return ()


def _try_nvidia_smi_devices() -> tuple[GPUDevice, ...]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                # Column order is load-bearing (parsing is positional):
                #   0 index, 1 name, 2 memory.total, 3 memory.free, 4 driver_version, 5 compute_cap
                # memory.free sizes against ACTUAL free VRAM (memory.total alone lets an 8B model
                # be "recommended" for a card with only ~5.9 GB free). driver_version catches the
                # too-old-driver PTX/toolchain case. compute_cap is the capability signal. Older
                # drivers omit any of the trailing columns, so each is guarded by len(parts) below.
                "--query-gpu=index,name,memory.total,memory.free,driver_version,compute_cap",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return ()
        devices: list[GPUDevice] = []
        for line in (result.stdout or "").strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                index = int(parts[0])
            except ValueError:
                index = len(devices)
            name = parts[1]
            try:
                vram_gb = float(parts[2]) / 1024.0
            except ValueError:
                vram_gb = None
            # memory.free (MiB) — guarded so a short row from an older driver (or the legacy
            # 3-column query used by some tests) parses cleanly to None instead of raising.
            vram_free_gb: float | None = None
            if len(parts) >= 4:
                try:
                    vram_free_gb = float(parts[3]) / 1024.0
                except ValueError:
                    vram_free_gb = None
            driver_version = parts[4] if len(parts) >= 5 else ""
            compute_cap = _parse_compute_cap(parts[5]) if len(parts) >= 6 else None
            if name:
                devices.append(
                    _cuda_device_result(
                        index=index,
                        gpu_name=name,
                        vram_gb=vram_gb,
                        source="nvidia-smi",
                        compute_cap=compute_cap,
                        driver_version=driver_version,
                        vram_free_gb=vram_free_gb,
                    )
                )
        return tuple(devices)
    except Exception:
        return ()


def _parse_compute_cap(raw: str) -> float | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _try_mps() -> tuple[str | None, float | None, str, str, str]:
    return _select_accelerator(_try_mps_devices())


def _try_mps_devices() -> tuple[GPUDevice, ...]:
    if platform.system().lower() != "darwin":
        return ()
    if platform.machine().lower() not in {"arm64", "aarch64"}:
        return ()
    try:
        import torch  # type: ignore
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            # MPS shares unified memory; report total RAM as proxy
            ram = _detect_ram_gb()
            return (
                GPUDevice(
                    index=0,
                    name="Apple Silicon (MPS)",
                    vendor="apple",
                    vram_gb=ram,
                    backend="mps",
                    status="usable",
                    source="torch",
                ),
            )
    except Exception:
        pass
    # Even without torch, Apple Silicon has unified memory
    ram = _detect_ram_gb()
    return (
        GPUDevice(
            index=0,
            name="Apple Silicon",
            vendor="apple",
            vram_gb=ram,
            backend="mps",
            status="usable",
            source="platform",
        ),
    )


def _try_directml() -> tuple[str | None, float | None, str, str, str]:
    return _select_accelerator(_try_directml_devices())


def _try_directml_devices() -> tuple[GPUDevice, ...]:
    """Detect AMD/Intel GPUs on Windows via WMI."""
    if platform.system().lower() != "windows":
        return ()
    try:
        result = subprocess.run(
            ["wmic", "path", "Win32_VideoController", "get",
             "Name,AdapterRAM", "/format:csv"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return ()
        devices: list[GPUDevice] = []
        for line in (result.stdout or "").strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                vram_bytes = int(parts[1])
            except ValueError:
                continue
            name = parts[2]
            if vram_bytes > 512 * 1024 * 1024 and name:  # >512 MB = real GPU
                devices.append(
                    GPUDevice(
                        index=len(devices),
                        name=name,
                        vendor=_gpu_vendor(name),
                        vram_gb=float(vram_bytes) / (1024.0 ** 3),
                        backend="directml",
                        status="usable",
                        source="wmic",
                    )
                )
        return tuple(devices)
    except Exception:
        return ()


def _cuda_probe_result(gpu_name: str, vram_gb: float) -> tuple[str | None, float | None, str, str, str]:
    return _select_accelerator(
        (_cuda_device_result(index=0, gpu_name=gpu_name, vram_gb=vram_gb, source="manual"),)
    )


def _cuda_device_result(
    *,
    index: int,
    gpu_name: str,
    vram_gb: float | None,
    source: str,
    compute_cap: float | None = None,
    driver_version: str = "",
    vram_free_gb: float | None = None,
) -> GPUDevice:
    advice = ""
    status = "usable"
    if _cuda_device_is_legacy(gpu_name, compute_cap):
        status = "legacy_cuda_cpu_recommended"
        cc_note = f" (compute capability {compute_cap})" if compute_cap is not None else ""
        advice = (
            f"Legacy NVIDIA CUDA device{cc_note}; VOOL sizes local models as CPU-only "
            "unless VOOL_ALLOW_LEGACY_CUDA=1 is set after a successful Ollama warmup."
        )
    return GPUDevice(
        index=index,
        name=gpu_name,
        vendor="nvidia",
        vram_gb=vram_gb,
        backend="cuda",
        status=status,
        advice=advice,
        source=source,
        driver_version=str(driver_version or ""),
        vram_free_gb=vram_free_gb,
        compute_cap=compute_cap,
    )


def _select_accelerator(devices: tuple[GPUDevice, ...]) -> tuple[str | None, float | None, str, str, str]:
    for backend in ("mps", "cuda", "directml"):
        usable = [device for device in devices if device.backend == backend and device.status == "usable"]
        if usable:
            selected = max(usable, key=lambda item: (float(item.vram_gb or 0.0), -int(item.index or 0)))
            return selected.name, selected.vram_gb, selected.backend, selected.status, selected.advice
    if devices:
        selected = max(devices, key=lambda item: (float(item.vram_gb or 0.0), -int(item.index or 0)))
        return selected.name, selected.vram_gb, "cpu", selected.status or "blocked", selected.advice
    return None, None, "cpu", "cpu", ""


def _selected_device_signals(
    devices: tuple[GPUDevice, ...], selected_name: str | None
) -> tuple[str, float | None]:
    """Best-effort driver_version + free VRAM for the accelerator _select_accelerator picked.

    Matches the device by name (the same key _select_accelerator returns) so the probe-level
    fields describe the selected accelerator. Returns neutral defaults ("", None) when there
    is no match, no devices, or the columns were never populated (older drivers).
    """
    if not devices or not selected_name:
        return "", None
    for device in devices:
        if str(device.name or "") == str(selected_name):
            return str(device.driver_version or ""), device.vram_free_gb
    return "", None


def _gpu_device_rows(probe: MachineProbe) -> list[dict[str, object]]:
    devices = tuple(getattr(probe, "gpu_devices", ()) or ())
    rows: list[dict[str, object]] = []
    for device in devices:
        if isinstance(device, GPUDevice):
            row = device.to_dict()
        elif isinstance(device, Mapping):
            row = dict(device)
        else:
            continue
        selected = str(row.get("name") or "") == str(probe.gpu_name or "")
        row["selected"] = selected
        row["active_accelerator"] = (
            selected
            and str(row.get("backend") or "").strip().lower() == str(probe.accelerator or "").strip().lower()
            and str(row.get("status") or "").strip().lower() == "usable"
        )
        rows.append(row)
    return rows


def _gpu_vendor(gpu_name: str) -> str:
    clean = str(gpu_name or "").strip().lower()
    if "nvidia" in clean or "geforce" in clean or "quadro" in clean or "tesla" in clean or "rtx" in clean:
        return "nvidia"
    if "amd" in clean or "radeon" in clean:
        return "amd"
    if "intel" in clean or "arc" in clean or "iris" in clean or "uhd" in clean:
        return "intel"
    if "apple" in clean:
        return "apple"
    return "unknown"


def _new_backend_devices(
    existing_devices: list[GPUDevice],
    candidate_devices: tuple[GPUDevice, ...],
) -> list[GPUDevice]:
    existing = {_gpu_identity(device) for device in existing_devices}
    added: list[GPUDevice] = []
    for candidate in candidate_devices:
        identity = _gpu_identity(candidate)
        if identity in existing:
            continue
        existing.add(identity)
        added.append(candidate)
    return added


def _gpu_identity(device: GPUDevice) -> tuple[str, str]:
    return (
        str(device.vendor or "").strip().lower(),
        re.sub(r"[^a-z0-9]+", " ", str(device.name or "").strip().lower()).strip(),
    )


# llama.cpp CUDA (and modern Ollama) support compute capability 5.0+ (Maxwell and newer:
# GTX 900 series, GTX 10 series/Pascal, RTX 20/30/40, etc). Only genuinely old
# architectures (Kepler/Fermi, CC < 5.0) are treated as legacy CPU-recommended.
_MIN_USABLE_CUDA_COMPUTE_CAP = 5.0


def _cuda_device_is_legacy(gpu_name: str, compute_cap: float | None) -> bool:
    """True if this NVIDIA card should be sized CPU-only.

    Prefers the measured compute capability (architecture-accurate); a card at CC >= 5.0
    (Maxwell+) is a first-class GPU. Falls back to the name-based heuristic only when the
    compute capability is unknown (very old driver, or torch/nvidia-smi didn't report it).
    """
    if platform.system().lower() != "windows":
        return False
    if _env_truthy("VOOL_ALLOW_LEGACY_CUDA"):
        return False
    if compute_cap is not None:
        return compute_cap < _MIN_USABLE_CUDA_COMPUTE_CAP
    return _windows_legacy_cuda_cpu_fallback(gpu_name)


def _windows_legacy_cuda_cpu_fallback(gpu_name: str) -> bool:
    """Name-based legacy heuristic, used only when compute capability is unknown.

    Kept conservative and pre-Pascal: GTX 9xx and older by name. Pascal (GTX 10xx) and
    newer are NOT flagged here — when the compute capability is unavailable we still trust
    the 10-series and up, and the live llama.cpp probe remains the final capability gate.
    """
    if platform.system().lower() != "windows":
        return False
    if _env_truthy("VOOL_ALLOW_LEGACY_CUDA"):
        return False
    clean = re.sub(r"[^a-z0-9]+", " ", str(gpu_name or "").strip().lower())
    if not clean:
        return False
    return bool(re.search(r"\bgtx\s*(?:9|7|6|5)\d{2}\b", clean))


def _env_truthy(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}
