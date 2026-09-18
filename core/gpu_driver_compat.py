"""Static GPU driver / CUDA / compute-capability compatibility check for VOOL's Ollama lane.

VOOL statically detects an NVIDIA GPU and sizes a "GPU-served" model, and the live GPU-inference
probe (``core/gpu_inference_probe.py``) proves a real token can be generated on the GPU. This module
adds the missing *upfront, static* verdict: from nvidia-smi facts alone (driver version + GPU compute
capability), predict whether the local CUDA inference backend can use the GPU, and if not, say plainly
what is wrong and how to fix it -- *before* any model is pulled or any generate is attempted.

Why a static check in addition to the live probe:
  * The live probe needs Ollama running and a model pulled; this static check works during a cold
    install and in ``/status`` even when the backend is down.
  * It turns an opaque "GPU run crashed" into a specific, actionable message ("driver too old ->
    update", "driver dropped your Pascal card -> install a 580-branch driver").

Domain facts this encodes (all public, verified 2026-07):
  * Ollama's CUDA lane requires compute capability >= 5.0 (Maxwell and newer).
  * Ollama ships two CUDA runners: cuda_v12 (built against CUDA 12) and cuda_v13 (CUDA 13). CUDA 13
    dropped every pre-Turing architecture (Maxwell 5.x, Pascal 6.x, Volta 7.0), so a compute
    capability < 7.5 can only run on the cuda_v12 runner -- which is fine, Ollama selects it
    automatically.
  * A CUDA 12 runner needs at least a CUDA-12-capable driver: >= 527.41 on Windows, >= 525.60 on
    Linux. Below that, GPU runs crash with a PTX / "unsupported toolchain" error.
  * Newer NVIDIA driver branches drop legacy GPUs entirely: for Pascal, the 580 branch is the last
    that still ships kernels for it (driver 582.66 runs a GTX 1080), while the 610 branch (610.62)
    removed Pascal support. So a legacy GPU on a very new driver needs an *older*, still-supporting
    driver branch.

Everything here is stdlib-only, fully typed, injectable (a subprocess runner), bounded (timeout), and
fail-safe: no function raises. Style mirrors ``core/gpu_inference_probe.py``.
"""
from __future__ import annotations

import platform
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

# Injectable subprocess runner: (args, timeout) -> a CompletedProcess-like with .returncode/.stdout.
SmiRunner = Callable[..., "subprocess.CompletedProcess[str]"]

_SCHEMA = "vool.gpu_driver_compat.v1"

# Ollama's CUDA lane floor: Maxwell (GTX 900) and newer.
OLLAMA_MIN_COMPUTE_CAP = 5.0
# CUDA 13 dropped pre-Turing; a compute capability below this is not in the cuda_v13 runner.
CUDA13_MIN_COMPUTE_CAP = 7.5
# Minimum driver for a CUDA-12 runtime (the runner Ollama uses for legacy archs).
CUDA12_MIN_DRIVER_WINDOWS = 527.41
CUDA12_MIN_DRIVER_LINUX = 525.60
# Driver branch at/above which recent NVIDIA Windows drivers have dropped legacy (pre-Turing) GPUs.
# Anchored on known points: 582.66 still runs Pascal; 610.62 dropped it. Kept conservative (only warn
# from the 600 branch up) so a working 580-branch driver is never flagged; the live probe is the
# authoritative backstop for anything in between.
LEGACY_DRIVER_DROP_FLOOR_WINDOWS = 600.0


def _parse_compute_cap(value: object) -> float | None:
    """Parse a compute-capability / CUDA-version string ("6.1", "13.0") to a float, or None."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _parse_driver_version(value: object) -> float | None:
    """Parse an nvidia-smi driver string ("582.66", "525.60.13") to a comparable major.minor float."""
    text = str(value or "").strip()
    if not text:
        return None
    parts = text.split(".")
    try:
        if len(parts) >= 2:
            return float(f"{int(parts[0])}.{int(parts[1])}")
        return float(int(parts[0]))
    except (TypeError, ValueError):
        return None


def _gpu_generation(compute_cap: float | None) -> str:
    """Map a compute capability onto its NVIDIA architecture name (best-effort, for advisories)."""
    if compute_cap is None:
        return ""
    if compute_cap < 3.0:
        return "Fermi"
    if compute_cap < 5.0:
        return "Kepler"
    if compute_cap < 6.0:
        return "Maxwell"
    if compute_cap < 7.0:
        return "Pascal"
    if compute_cap < 7.5:
        return "Volta"
    if compute_cap < 8.0:
        return "Turing"
    if compute_cap < 8.9:
        return "Ampere"
    if compute_cap < 9.0:
        return "Ada Lovelace"
    if compute_cap < 10.0:
        return "Hopper"
    return "Blackwell"


def _recommended_runner(compute_cap: float | None) -> str:
    """Which Ollama CUDA runner covers this compute capability: cuda_v12 | cuda_v13 | none."""
    if compute_cap is None or compute_cap < OLLAMA_MIN_COMPUTE_CAP:
        return "none"
    if compute_cap < CUDA13_MIN_COMPUTE_CAP:
        return "cuda_v12"
    return "cuda_v13"


@dataclass(frozen=True)
class GpuDriverCompatVerdict:
    """Outcome of a static GPU driver / compute-capability compatibility check.

    outcome: ok | uses_cuda_v12 | driver_too_old | driver_dropped_gpu | gpu_too_old | no_gpu | unknown
    suggested_action: update_driver | install_supported_driver | use_cpu | none
    ``compatible`` is True only when GPU inference is expected to work (ok / uses_cuda_v12).
    """

    outcome: str
    reason: str
    gpu_name: str
    driver_version: str
    compute_cap: str
    gpu_generation: str
    recommended_runner: str
    compatible: bool
    suggested_action: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": _SCHEMA,
            "outcome": self.outcome,
            "reason": self.reason,
            "gpu_name": self.gpu_name,
            "driver_version": self.driver_version,
            "compute_cap": self.compute_cap,
            "gpu_generation": self.gpu_generation,
            "recommended_runner": self.recommended_runner,
            "compatible": self.compatible,
            "suggested_action": self.suggested_action,
        }


def evaluate_gpu_driver_compat(
    *,
    gpu_name: str = "",
    driver_version: str = "",
    compute_cap: object = "",
    cuda_max_version: object = "",
    gpu_present: bool = True,
    os_name: str = "",
) -> GpuDriverCompatVerdict:
    """Judge GPU/driver compatibility from static facts. Never raises.

    ``cuda_max_version`` is the driver's maximum supported CUDA (the "CUDA Version: X.Y" nvidia-smi
    header); when supplied and below 12 it is an authoritative too-old signal. ``os_name`` overrides
    the host OS (default: ``platform.system()``) so the driver thresholds are deterministic in tests.
    """
    system = (os_name or platform.system() or "").strip().lower()
    name = str(gpu_name or "").strip()
    driver_text = str(driver_version or "").strip()
    cc = _parse_compute_cap(compute_cap)
    cc_text = str(compute_cap or "").strip()
    generation = _gpu_generation(cc)
    runner = _recommended_runner(cc)

    def _mk(outcome: str, reason: str, compatible: bool, action: str) -> GpuDriverCompatVerdict:
        return GpuDriverCompatVerdict(
            outcome=outcome,
            reason=reason,
            gpu_name=name,
            driver_version=driver_text,
            compute_cap=cc_text,
            gpu_generation=generation,
            recommended_runner=runner,
            compatible=compatible,
            suggested_action=action,
        )

    if not gpu_present or not name:
        return _mk("no_gpu", "No NVIDIA GPU detected; nothing to check.", False, "none")

    if cc is None:
        return _mk(
            "unknown",
            "GPU compute capability unavailable; deferring to the live GPU check.",
            False,
            "none",
        )

    if cc < OLLAMA_MIN_COMPUTE_CAP:
        return _mk(
            "gpu_too_old",
            f"Compute capability {cc_text} is below the {OLLAMA_MIN_COMPUTE_CAP:g} minimum the CUDA backend supports.",
            False,
            "use_cpu",
        )

    cuda_max = _parse_compute_cap(cuda_max_version)
    if cuda_max is not None and cuda_max < 12.0:
        return _mk(
            "driver_too_old",
            f"Driver {driver_text or '(unknown)'} supports at most CUDA {cuda_max:g}; the CUDA 12 runner needs a newer driver.",
            False,
            "update_driver",
        )

    driver = _parse_driver_version(driver_text)
    if driver is None:
        return _mk(
            "unknown",
            "GPU driver version unavailable; deferring to the live GPU check.",
            False,
            "none",
        )

    floor = CUDA12_MIN_DRIVER_WINDOWS if system == "windows" else CUDA12_MIN_DRIVER_LINUX
    if driver < floor:
        return _mk(
            "driver_too_old",
            f"Driver {driver_text} is below the {floor:g} minimum for the CUDA 12 runner.",
            False,
            "update_driver",
        )

    is_legacy_arch = cc < CUDA13_MIN_COMPUTE_CAP  # Maxwell / Pascal / Volta -> cuda_v12 only
    if is_legacy_arch:
        if system == "windows" and driver >= LEGACY_DRIVER_DROP_FLOOR_WINDOWS:
            return _mk(
                "driver_dropped_gpu",
                f"Driver {driver_text} is new enough to have dropped {generation or 'legacy'} GPU support.",
                False,
                "install_supported_driver",
            )
        return _mk(
            "uses_cuda_v12",
            f"{generation or 'This'} GPU is not in the CUDA 13 runner; Ollama uses its CUDA 12 runner, which supports it.",
            True,
            "none",
        )

    return _mk("ok", "GPU driver and compute capability are compatible with the CUDA backend.", True, "none")


def gpu_driver_compat_advisory(verdict: GpuDriverCompatVerdict) -> str:
    """Plain, factual user-facing message for a driver-compat verdict. No hype, no drama, no emoji.

    Warnings for the actionable outcomes; a short neutral line for ok / uses_cuda_v12; empty for
    no_gpu / unknown (nothing the user can act on).
    """
    outcome = str(getattr(verdict, "outcome", "") or "")
    name = str(getattr(verdict, "gpu_name", "") or "").strip()
    driver = str(getattr(verdict, "driver_version", "") or "").strip()
    generation = str(getattr(verdict, "gpu_generation", "") or "").strip()
    name_clause = f" ({name})" if name else ""
    driver_clause = f" {driver}" if driver else ""

    if outcome == "driver_too_old":
        return (
            f"Your NVIDIA driver{driver_clause} is too old for the local inference backend, so GPU runs "
            "crash. Update your NVIDIA driver to use the GPU. VOOL runs on CPU until then."
        )
    if outcome == "driver_dropped_gpu":
        gen = generation or "your GPU's"
        if generation == "Pascal":
            fix = "install a 580-branch driver (such as 582.xx), the last branch that supports Pascal"
        else:
            fix = f"install an older driver branch that still supports {gen} GPUs"
        return (
            f"Your NVIDIA driver{driver_clause} no longer includes support for your {gen} GPU{name_clause}; "
            f"{fix}. VOOL runs on CPU until then."
        )
    if outcome == "gpu_too_old":
        return f"Your GPU{name_clause} is older than the local inference backend supports, so VOOL runs on CPU."
    if outcome == "uses_cuda_v12":
        gen = generation or "Your GPU"
        return (
            f"{gen}{name_clause} is not covered by the newer CUDA 13 runner, but Ollama's CUDA 12 runner "
            "supports it, so GPU inference should work."
        )
    if outcome == "ok":
        return "GPU driver and compute capability look compatible with GPU inference."
    # no_gpu / unknown: nothing actionable to warn about.
    return ""


def _select_cuda_device(probe: object) -> object | None:
    """Pick the primary CUDA device from a MachineProbe-like object (prefers one with a compute cap)."""
    devices = list(getattr(probe, "gpu_devices", ()) or ())
    cuda_devices = [d for d in devices if str(getattr(d, "backend", "") or "").lower() == "cuda"]
    candidates = cuda_devices or devices
    for device in candidates:
        if getattr(device, "compute_cap", None) is not None:
            return device
    return candidates[0] if candidates else None


def evaluate_driver_compat_for_probe(probe: object, *, os_name: str = "") -> GpuDriverCompatVerdict:
    """Adapt a MachineProbe-like object to a driver-compat verdict (duck-typed; never raises)."""
    try:
        accelerator = str(getattr(probe, "accelerator", "") or "").strip().lower()
        if accelerator != "cuda":
            return evaluate_gpu_driver_compat(gpu_present=False, os_name=os_name)
        device = _select_cuda_device(probe)
        name = str(
            (getattr(device, "name", "") if device is not None else "") or getattr(probe, "gpu_name", "") or ""
        ).strip()
        driver = str(
            (getattr(device, "driver_version", "") if device is not None else "")
            or getattr(probe, "driver_version", "")
            or ""
        ).strip()
        compute_cap = getattr(device, "compute_cap", "") if device is not None else ""
        return evaluate_gpu_driver_compat(
            gpu_name=name,
            driver_version=driver,
            compute_cap=compute_cap,
            gpu_present=bool(name),
            os_name=os_name,
        )
    except Exception:
        return evaluate_gpu_driver_compat(gpu_present=False, os_name=os_name)


def _default_smi_runner(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def detect_gpu_driver_compat(
    *,
    smi_runner: SmiRunner | None = None,
    timeout_seconds: float = 5.0,
    os_name: str = "",
) -> GpuDriverCompatVerdict:
    """Run nvidia-smi directly and return a driver-compat verdict. Never raises; no GPU -> no_gpu.

    Standalone entry point (CLI / diagnostics). The install and /status paths reuse the existing
    MachineProbe via ``evaluate_driver_compat_for_probe`` instead of running nvidia-smi again.
    """
    runner = smi_runner or _default_smi_runner
    try:
        proc = runner(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,compute_cap",
                "--format=csv,noheader,nounits",
            ],
            timeout_seconds,
        )
    except Exception:
        return evaluate_gpu_driver_compat(gpu_present=False, os_name=os_name)

    if int(getattr(proc, "returncode", 1) or 0) != 0:
        return evaluate_gpu_driver_compat(gpu_present=False, os_name=os_name)

    lines = str(getattr(proc, "stdout", "") or "").strip().splitlines()
    if not lines:
        return evaluate_gpu_driver_compat(gpu_present=False, os_name=os_name)
    parts = [p.strip() for p in lines[0].split(",")]
    if len(parts) < 3:
        return evaluate_gpu_driver_compat(gpu_present=False, os_name=os_name)
    return evaluate_gpu_driver_compat(
        gpu_name=parts[0],
        driver_version=parts[1],
        compute_cap=parts[2],
        gpu_present=bool(parts[0]),
        os_name=os_name,
    )


__all__ = [
    "GpuDriverCompatVerdict",
    "detect_gpu_driver_compat",
    "evaluate_driver_compat_for_probe",
    "evaluate_gpu_driver_compat",
    "gpu_driver_compat_advisory",
]
