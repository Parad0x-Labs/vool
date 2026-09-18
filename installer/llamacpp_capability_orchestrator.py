"""Tries llama.cpp GPU backends in priority order and returns the first one that
measures as genuinely usable — vendor-optimized backend first (CUDA/HIP/SYCL),
Vulkan as the universal no-toolkit-install fallback, CPU if nothing pans out.

This is the actual "just make GPU acceleration work" entry point a one-click
installer calls. Backend selection, binary download/verify, and the live timed
probe are all separately testable building blocks; this module is only the glue
that tries them in order and stops at the first real, measured win — never a
guess based on GPU name alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.hardware_tier import MachineProbe
from core.llamacpp_capability_probe import CapabilityProbeResult, probe_llamacpp_capability
from installer.llamacpp_backend_select import DEFAULT_RELEASE_TAG, select_backend_candidates
from installer.llamacpp_runtime_bootstrap import (
    BootstrapError,
    download_probe_fixture_model,
    install_llamacpp_backend,
)

_NO_GPU_DETAIL = "No GPU detected; nothing to probe."


@dataclass(frozen=True)
class OrchestrationAttempt:
    backend: str
    outcome: str  # "usable" | "rejected" | "install_failed" | "probe_failed"
    detail: str


@dataclass(frozen=True)
class OrchestrationResult:
    result: CapabilityProbeResult
    attempts: tuple[OrchestrationAttempt, ...] = field(default_factory=tuple)


def detect_and_verify_best_backend(
    *,
    probe: MachineProbe,
    runtime_home: str | Path,
    tag: str = DEFAULT_RELEASE_TAG,
    force: bool = False,
) -> OrchestrationResult:
    """Tries every backend candidate for this machine's GPU, in priority order,
    stopping at the first one a live probe confirms usable. If every candidate is
    rejected or fails to install/launch, returns the last measured result (still
    an honest, real verdict — just a negative one) so callers always get a
    trustworthy answer, never a guess."""
    selection = select_backend_candidates(probe)
    if not selection.candidates:
        return OrchestrationResult(result=_no_gpu_result(tag, runtime_home), attempts=())

    attempts: list[OrchestrationAttempt] = []

    try:
        fixture_path = download_probe_fixture_model(runtime_home=runtime_home, force=force)
    except BootstrapError as exc:
        attempts.append(OrchestrationAttempt(backend="n/a", outcome="install_failed", detail=f"probe fixture download failed: {exc}"))
        return OrchestrationResult(result=_no_gpu_result(tag, runtime_home), attempts=tuple(attempts))

    last_result: CapabilityProbeResult | None = None
    for candidate in selection.candidates:
        try:
            installed = install_llamacpp_backend(candidate=candidate, runtime_home=runtime_home, tag=tag, force=force)
        except BootstrapError as exc:
            attempts.append(OrchestrationAttempt(backend=candidate.backend, outcome="install_failed", detail=str(exc)))
            continue

        result = probe_llamacpp_capability(
            gpu_name=selection.gpu_name,
            gpu_vendor=selection.gpu_vendor,
            binary_path=installed.server_exe_path,
            tiny_model_path=str(fixture_path),
            backend_tested=candidate.backend,
            binary_release_tag=tag,
            runtime_home=runtime_home,
            force=force,
        )
        last_result = result

        if result.usable:
            attempts.append(OrchestrationAttempt(backend=candidate.backend, outcome="usable", detail=result.detail))
            return OrchestrationResult(result=result, attempts=tuple(attempts))

        outcome = "probe_failed" if result.status == "gpu_launch_failed" else "rejected"
        attempts.append(OrchestrationAttempt(backend=candidate.backend, outcome=outcome, detail=result.detail))

    final_result = last_result if last_result is not None else _no_gpu_result(tag, runtime_home)
    return OrchestrationResult(result=final_result, attempts=tuple(attempts))


def _no_gpu_result(tag: str, runtime_home: str | Path) -> CapabilityProbeResult:
    # Reuses probe_llamacpp_capability's own tested "no GPU" path (empty gpu_name
    # always short-circuits to skipped_no_gpu before any filesystem access) instead
    # of hand-building a result, so this stays consistent with the single source of
    # truth for that status.
    return probe_llamacpp_capability(
        gpu_name="",
        gpu_vendor="",
        binary_path="",
        tiny_model_path="",
        backend_tested="cpu",
        binary_release_tag=tag,
        runtime_home=runtime_home,
    )


__all__ = [
    "OrchestrationAttempt",
    "OrchestrationResult",
    "detect_and_verify_best_backend",
]
