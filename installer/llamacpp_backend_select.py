"""Selects which prebuilt native-Windows llama.cpp backend to try for a given GPU.

Deliberately separate from core/llamacpp_capability_probe.py: this module only picks
*which binary is worth downloading and attempting*. It never promises that backend
will actually be fast — only probe_llamacpp_capability()'s live, measured benchmark
earns that. Vulkan is always included as the universal fallback: it needs no vendor
toolkit install (no CUDA Toolkit, no ROCm), works across NVIDIA/AMD/Intel GPUs, and is
a ~32MB download — this is what actually rescues GPUs Ollama's own blocklist rejects
(e.g. old NVIDIA Pascal cards), since llama.cpp's CUDA binary still needs a compatible
driver/toolkit pairing that legacy setups sometimes lack, while Vulkan just needs a
normal graphics driver.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.hardware_tier import MachineProbe, _gpu_vendor
from core.llamacpp_capability_probe import (
    LlamaCppBackendCandidate,
    gpu_vendor_to_backend_candidates,
)

DEFAULT_RELEASE_TAG = "b9856"
GITHUB_REPO = "ggml-org/llama.cpp"


@dataclass(frozen=True)
class BackendSelection:
    candidates: tuple[LlamaCppBackendCandidate, ...]
    gpu_name: str
    gpu_vendor: str


def select_backend_candidates(probe: MachineProbe) -> BackendSelection:
    gpu_name = str(probe.gpu_name or "").strip()
    vendor = _gpu_vendor(gpu_name) if gpu_name else "unknown"
    candidates = gpu_vendor_to_backend_candidates(vendor, gpu_name) if gpu_name else ()
    return BackendSelection(candidates=candidates, gpu_name=gpu_name, gpu_vendor=vendor)


def asset_name_for_tag(candidate: LlamaCppBackendCandidate, *, tag: str) -> str:
    return candidate.asset_name.format(tag=tag)


__all__ = [
    "DEFAULT_RELEASE_TAG",
    "GITHUB_REPO",
    "BackendSelection",
    "asset_name_for_tag",
    "select_backend_candidates",
]
