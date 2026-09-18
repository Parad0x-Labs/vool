"""Live, measurement-based capability probing for GPU-accelerated llama.cpp backends.

core/hardware_tier.py's _windows_legacy_cuda_cpu_fallback() is a name-based blocklist
tuned for Ollama's bundled runtime (it demotes e.g. "GTX 1080" to CPU-only sizing).
That heuristic stays as-is for Ollama's own sizing decisions, but it is NOT allowed to
be the final word on whether a GPU-accelerated llama.cpp backend can serve requests
well: llama.cpp built with CUDA/Vulkan/HIP support routinely runs old "legacy" NVIDIA
cards (and non-NVIDIA cards Ollama never considers) far faster than Ollama's own CPU
fallback. This module never trusts a GPU name string for that call: it launches the
actual llama-server binary briefly against a tiny fixture model, offloaded fully to
GPU vs fully to CPU, and only a measured speedup earns a "usable" verdict.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from core.provider_invocation_gateway import (
    seal_direct_provider_invocation,
)

BackendId = Literal["cuda", "hip", "vulkan", "sycl", "cpu"]

PROBE_SCHEMA = "vool.llamacpp_capability_probe.v1"
PROBE_VERSION = 1
PROBE_PORT = 8095
PROBE_PROMPT = "Explain what a binary search tree is in one short paragraph."
PROBE_PREDICT_TOKENS = 64
PROBE_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60

# Ratio-based thresholds (robust to whatever CPU speed this exact host has) plus an
# absolute sanity floor so a near-zero-vs-near-zero ratio never reads as "fast".
_FAST_SPEEDUP_RATIO = 2.5
_MARGINAL_SPEEDUP_RATIO = 1.3
_ABSOLUTE_USABLE_FLOOR_TOKENS_PER_SECOND = 3.0

_CACHE_RELATIVE_PATH = Path("config") / "llamacpp-capability-probe.json"

ProbeStatus = Literal[
    "gpu_confirmed_fast",
    "gpu_confirmed_marginal",
    "gpu_rejected_slow",
    "gpu_launch_failed",
    "skipped_no_gpu",
    "skipped_cached",
]


@dataclass(frozen=True)
class LlamaCppBackendCandidate:
    backend: BackendId
    vendor: str
    asset_name: str
    requires_runtime_asset: str
    priority: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapabilityProbeResult:
    schema: str
    probed_at_epoch: float
    probe_version: int
    gpu_name: str
    gpu_vendor: str
    backend_tested: str
    binary_release_tag: str
    cpu_baseline_tokens_per_second: float
    gpu_tokens_per_second: float
    speedup_ratio: float
    status: str
    verdict_backend: str
    detail: str

    @property
    def usable(self) -> bool:
        return self.status in {"gpu_confirmed_fast", "gpu_confirmed_marginal"}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def gpu_vendor_to_backend_candidates(vendor: str, gpu_name: str = "") -> tuple[LlamaCppBackendCandidate, ...]:
    """Ordered backends worth trying for this GPU. Vulkan is always included as the
    universal, no-toolkit-install fallback (works across NVIDIA/AMD/Intel) — it is
    what actually rescues GPUs Ollama's blocklist rejects, since it needs no CUDA
    Toolkit and no vendor driver beyond a normal graphics driver."""
    clean_vendor = str(vendor or "").strip().lower()
    candidates: list[LlamaCppBackendCandidate] = []
    if clean_vendor == "nvidia":
        candidates.append(
            LlamaCppBackendCandidate(
                backend="cuda",
                vendor="nvidia",
                asset_name="llama-{tag}-bin-win-cuda-12.4-x64.zip",
                requires_runtime_asset="cudart-llama-bin-win-cuda-12.4-x64.zip",
                priority=0,
            )
        )
    elif clean_vendor == "amd":
        candidates.append(
            LlamaCppBackendCandidate(
                backend="hip",
                vendor="amd",
                asset_name="llama-{tag}-bin-win-hip-radeon-x64.zip",
                requires_runtime_asset="",
                priority=0,
            )
        )
    elif clean_vendor == "intel":
        candidates.append(
            LlamaCppBackendCandidate(
                backend="sycl",
                vendor="intel",
                asset_name="llama-{tag}-bin-win-sycl-x64.zip",
                requires_runtime_asset="",
                priority=0,
            )
        )
    candidates.append(
        LlamaCppBackendCandidate(
            backend="vulkan",
            vendor=clean_vendor or "unknown",
            asset_name="llama-{tag}-bin-win-vulkan-x64.zip",
            requires_runtime_asset="",
            priority=1 if candidates else 0,
        )
    )
    return tuple(candidates)


def probe_llamacpp_capability(
    *,
    gpu_name: str,
    gpu_vendor: str,
    binary_path: str | Path,
    tiny_model_path: str | Path,
    backend_tested: str,
    binary_release_tag: str,
    runtime_home: str | Path,
    force: bool = False,
    timeout_seconds: float = 45.0,
) -> CapabilityProbeResult:
    """Entry point: checks a cached verdict first (keyed on GPU identity + binary
    release tag + probe logic version), else runs a live timed benchmark."""
    if not gpu_name:
        return _result(
            gpu_name=gpu_name,
            gpu_vendor=gpu_vendor,
            backend_tested="cpu",
            binary_release_tag=binary_release_tag,
            cpu_tps=0.0,
            gpu_tps=0.0,
            status="skipped_no_gpu",
            detail="No GPU detected; nothing to probe.",
        )

    cache_path = Path(runtime_home).expanduser().resolve() / _CACHE_RELATIVE_PATH
    cache_key = _cache_key(gpu_name=gpu_name, release_tag=binary_release_tag, backend=backend_tested)
    if not force:
        cached = _read_cache(cache_path, cache_key)
        if cached is not None:
            return cached

    result = _run_live_benchmark(
        gpu_name=gpu_name,
        gpu_vendor=gpu_vendor,
        binary_path=Path(binary_path),
        tiny_model_path=Path(tiny_model_path),
        backend_tested=backend_tested,
        binary_release_tag=binary_release_tag,
        timeout_seconds=timeout_seconds,
    )
    _write_cache(cache_path, cache_key, result)
    return result


def _run_live_benchmark(
    *,
    gpu_name: str,
    gpu_vendor: str,
    binary_path: Path,
    tiny_model_path: Path,
    backend_tested: str,
    binary_release_tag: str,
    timeout_seconds: float,
) -> CapabilityProbeResult:
    if not binary_path.exists() or not tiny_model_path.exists():
        return _result(
            gpu_name=gpu_name,
            gpu_vendor=gpu_vendor,
            backend_tested=backend_tested,
            binary_release_tag=binary_release_tag,
            cpu_tps=0.0,
            gpu_tps=0.0,
            status="gpu_launch_failed",
            detail=f"Missing binary or probe model (binary={binary_path.exists()}, model={tiny_model_path.exists()}).",
        )

    per_run_timeout = max(5.0, min(20.0, timeout_seconds / 2.0))
    try:
        gpu_tps = _timed_single_run(
            binary_path=binary_path,
            model_path=tiny_model_path,
            n_gpu_layers=999,
            timeout_seconds=per_run_timeout,
        )
        cpu_tps = _timed_single_run(
            binary_path=binary_path,
            model_path=tiny_model_path,
            n_gpu_layers=0,
            timeout_seconds=per_run_timeout,
        )
    except _ProbeLaunchError as exc:
        return _result(
            gpu_name=gpu_name,
            gpu_vendor=gpu_vendor,
            backend_tested=backend_tested,
            binary_release_tag=binary_release_tag,
            cpu_tps=0.0,
            gpu_tps=0.0,
            status="gpu_launch_failed",
            detail=str(exc),
        )

    speedup_ratio = gpu_tps / max(cpu_tps, 0.1)
    if gpu_tps < _ABSOLUTE_USABLE_FLOOR_TOKENS_PER_SECOND or speedup_ratio < _MARGINAL_SPEEDUP_RATIO:
        status: ProbeStatus = "gpu_rejected_slow"
        verdict_backend = "cpu"
    elif speedup_ratio < _FAST_SPEEDUP_RATIO:
        status = "gpu_confirmed_marginal"
        verdict_backend = backend_tested
    else:
        status = "gpu_confirmed_fast"
        verdict_backend = backend_tested

    return CapabilityProbeResult(
        schema=PROBE_SCHEMA,
        probed_at_epoch=time.time(),
        probe_version=PROBE_VERSION,
        gpu_name=gpu_name,
        gpu_vendor=gpu_vendor,
        backend_tested=backend_tested,
        binary_release_tag=binary_release_tag,
        cpu_baseline_tokens_per_second=round(cpu_tps, 2),
        gpu_tokens_per_second=round(gpu_tps, 2),
        speedup_ratio=round(speedup_ratio, 2),
        status=status,
        verdict_backend=verdict_backend,
        detail=(
            f"Measured {gpu_tps:.1f} tok/s at ngl=999 vs {cpu_tps:.1f} tok/s at ngl=0 "
            f"({speedup_ratio:.2f}x) on {gpu_name} via {backend_tested}."
        ),
    )


class _ProbeLaunchError(RuntimeError):
    pass


def _timed_single_run(*, binary_path: Path, model_path: Path, n_gpu_layers: int, timeout_seconds: float) -> float:
    """Launch llama-server briefly on the dedicated probe port, wait for /health,
    time one /completion call for PROBE_PREDICT_TOKENS tokens, then kill it. Reads
    llama-server's own reported tokens/sec from the response's "timings" field
    (predicted_per_second) rather than wall-clock timing on our side, so process
    spawn/model-load overhead never pollutes the measurement."""
    argv = [
        str(binary_path),
        "-m", str(model_path),
        "--host", "127.0.0.1",
        "--port", str(PROBE_PORT),
        "-ngl", str(n_gpu_layers),
        "-c", "512",
        "--no-mmap",
    ]
    process = subprocess.Popen(
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + timeout_seconds
        if not _wait_for_health(deadline=deadline):
            raise _ProbeLaunchError(f"llama-server did not become healthy within {timeout_seconds:.0f}s (ngl={n_gpu_layers}).")
        remaining = max(1.0, deadline - time.time())
        return _post_completion_tokens_per_second(remaining_seconds=remaining)
    finally:
        _terminate(process)


def _wait_for_health(*, deadline: float) -> bool:
    url = f"http://127.0.0.1:{PROBE_PORT}/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError, TimeoutError):
            pass
        time.sleep(0.5)
    return False


def _post_completion_tokens_per_second(*, remaining_seconds: float) -> float:
    # Use the OpenAI-compatible chat endpoint (not the raw /completion endpoint) so
    # llama-server applies the fixture model's own chat template. Without it, a raw
    # un-templated prompt can make an instruct-tuned model immediately emit an
    # end-of-sequence token (predicted_n=1), which silently produces a division-by-
    # near-zero "tokens per second" figure that looks plausible but is garbage.
    payload = {
        "messages": [{"role": "user", "content": PROBE_PROMPT}],
        "max_tokens": PROBE_PREDICT_TOKENS,
        "temperature": 0.0,
    }
    permit = seal_direct_provider_invocation(
        provider_id="llamacpp:capability-probe",
        model_id="probe-fixture",
        operation="generation_probe",
        payload=payload,
        request_id="llamacpp-capability-probe",
        max_output_tokens=PROBE_PREDICT_TOKENS,
        header_names=("Content-Type",),
    )
    request = urllib.request.Request(
        f"http://127.0.0.1:{PROBE_PORT}/v1/chat/completions",
        data=json.dumps(permit.consume()).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=remaining_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise _ProbeLaunchError(f"probe completion request failed: {exc}") from exc
    timings = body.get("timings") or {}
    predicted_n = int(timings.get("predicted_n") or 0)
    predicted_ms = float(timings.get("predicted_ms") or 0.0)
    if predicted_n < 8 or predicted_ms < 5.0:
        # Too few tokens / too little elapsed time to trust a per-second rate at all
        # (e.g. the model stopped almost immediately) — treat as no usable signal
        # rather than propagate a divide-by-near-zero number.
        raise _ProbeLaunchError(
            f"probe generation ended too early to measure (predicted_n={predicted_n}, predicted_ms={predicted_ms})"
        )
    return float(timings.get("predicted_per_second") or 0.0)


def _terminate(process: subprocess.Popen) -> None:
    try:
        process.terminate()
        process.wait(timeout=5.0)
    except Exception:
        with contextlib.suppress(Exception):
            process.kill()


def _result(
    *,
    gpu_name: str,
    gpu_vendor: str,
    backend_tested: str,
    binary_release_tag: str,
    cpu_tps: float,
    gpu_tps: float,
    status: str,
    detail: str,
) -> CapabilityProbeResult:
    return CapabilityProbeResult(
        schema=PROBE_SCHEMA,
        probed_at_epoch=time.time(),
        probe_version=PROBE_VERSION,
        gpu_name=gpu_name,
        gpu_vendor=gpu_vendor,
        backend_tested=backend_tested,
        binary_release_tag=binary_release_tag,
        cpu_baseline_tokens_per_second=round(cpu_tps, 2),
        gpu_tokens_per_second=round(gpu_tps, 2),
        speedup_ratio=0.0,
        status=status,
        verdict_backend="cpu",
        detail=detail,
    )


def has_any_verified_gpu_backend(runtime_home: str | Path) -> bool:
    """True when the cache holds at least one usable (non-expired) probe verdict for
    this runtime home. Used to decide whether the aux llama.cpp provider lane should
    be allowed regardless of install profile — a live measurement, not a profile
    setting or a GPU name guess, is what unlocks it."""
    cache_path = Path(runtime_home).expanduser().resolve() / _CACHE_RELATIVE_PATH
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(raw, dict):
        return False
    for cache_key in raw:
        cached = _read_cache(cache_path, cache_key)
        if cached is not None and cached.usable:
            return True
    return False


def _cache_key(*, gpu_name: str, release_tag: str, backend: str) -> str:
    # backend MUST be part of the key: an orchestrator trying multiple backends
    # against the same GPU (e.g. cuda rejected, then vulkan) must not have the
    # vulkan attempt silently served the cuda attempt's cached verdict.
    identity = str(gpu_name or "").strip().lower()
    clean_backend = str(backend or "").strip().lower()
    return f"{identity}|{clean_backend}|{release_tag}|{PROBE_VERSION}"


def _read_cache(cache_path: Path, cache_key: str) -> CapabilityProbeResult | None:
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entry = raw.get(cache_key) if isinstance(raw, dict) else None
    if not isinstance(entry, dict):
        return None
    probed_at = float(entry.get("probed_at_epoch") or 0.0)
    if time.time() - probed_at > PROBE_CACHE_TTL_SECONDS:
        return None
    if int(entry.get("probe_version") or 0) != PROBE_VERSION:
        return None
    try:
        return CapabilityProbeResult(
            schema=str(entry.get("schema") or PROBE_SCHEMA),
            probed_at_epoch=probed_at,
            probe_version=int(entry.get("probe_version") or PROBE_VERSION),
            gpu_name=str(entry.get("gpu_name") or ""),
            gpu_vendor=str(entry.get("gpu_vendor") or ""),
            backend_tested=str(entry.get("backend_tested") or "cpu"),
            binary_release_tag=str(entry.get("binary_release_tag") or ""),
            cpu_baseline_tokens_per_second=float(entry.get("cpu_baseline_tokens_per_second") or 0.0),
            gpu_tokens_per_second=float(entry.get("gpu_tokens_per_second") or 0.0),
            speedup_ratio=float(entry.get("speedup_ratio") or 0.0),
            status=str(entry.get("status") or "gpu_rejected_slow"),
            verdict_backend=str(entry.get("verdict_backend") or "cpu"),
            detail=str(entry.get("detail") or ""),
        )
    except (TypeError, ValueError):
        return None


def _write_cache(cache_path: Path, cache_key: str, result: CapabilityProbeResult) -> None:
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except (OSError, json.JSONDecodeError):
        raw = {}
    raw[cache_key] = result.to_dict()
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        pass


__all__ = [
    "PROBE_CACHE_TTL_SECONDS",
    "PROBE_PORT",
    "PROBE_SCHEMA",
    "PROBE_VERSION",
    "BackendId",
    "CapabilityProbeResult",
    "LlamaCppBackendCandidate",
    "gpu_vendor_to_backend_candidates",
    "has_any_verified_gpu_backend",
    "probe_llamacpp_capability",
]
