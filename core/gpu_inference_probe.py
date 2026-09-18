"""Live GPU-inference verification for VOOL's local Ollama lane.

VOOL statically detects a CUDA/DirectML/MPS accelerator and sizes a "GPU-served" model, but never
proves a real token can be generated on the GPU. On some hosts (old NVIDIA driver vs. the Ollama CUDA
build, or a model that does not fit free VRAM) every ``num_gpu > 0`` run crashes, so the install
"succeeds" and then the agent cannot answer. This module runs one small real ``/api/chat`` generation
with the GPU lane forced, classifies any failure, and returns a verdict that recommends a CPU fallback
plus a plain, factual user-facing advisory.

Everything here is stdlib-only, fully typed, injectable (``http_post_fn``), bounded (timeout), and
fail-safe: ``verify_gpu_inference`` never raises. Style mirrors ``core/machine_diagnostics.py``.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from core.provider_invocation_gateway import (
    seal_direct_provider_invocation,
)

# Injectable HTTP POST: (url, payload, timeout) -> (http_status, parsed_json_or_None, error_text).
HttpPostFn = Callable[[str, dict, float], "tuple[int, dict | None, str]"]

# --- Error keyword groups (case-insensitive substring match) -------------------------------------
# driver_too_old: the local NVIDIA driver is too old for the Ollama CUDA build; PTX/toolchain crash.
# Only the toolchain-specific phrases are used (not a bare "ptxas"/" ptx ") so a stray ptxas warning
# line emitted alongside a genuine OOM cannot override the more actionable vram_insufficient verdict.
_DRIVER_TOO_OLD_KEYWORDS = (
    "unsupported toolchain",
    "provided ptx",
    "compiled with an unsupported",
)
# vram_insufficient: the model / KV cache does not fit the free VRAM (out-of-memory on the device).
# Keywords are qualified (CUDA/buffer) so benign "failed to allocate ..." or "kv cache" log lines that
# are not device-OOM do not get misclassified as VRAM exhaustion.
_VRAM_INSUFFICIENT_KEYWORDS = (
    "out of memory",
    "cudamalloc",
    "failed to allocate cuda",
    "failed to allocate buffer",
    "buffer for kv cache",
    "cuda_error_out_of_memory",
    "cudaerroroutofmemory",
    "cuda out of memory",
)
# Transient / not-a-GPU-failure signals: a connection error or a missing model must NOT durably pin the
# host to CPU, so an unrecognized failure carrying one of these is reported "inconclusive" (no fallback).
_TRANSIENT_KEYWORDS = (
    "connection refused",
    "actively refused",
    "cannot connect",
    "failed to connect",
    "connection reset",
    "max retries",
    "not found",
    "no such model",
    "model not found",
    "name or service not known",
)
# gpu_unsupported: the GPU could not run the inference backend at all (generic CUDA/backend failure).
_GPU_UNSUPPORTED_KEYWORDS = (
    "cuda error",
    "no cuda-capable device",
    "no kernel image",
    "device kernel image is invalid",
    "ggml_cuda",
    "0xc0000005",
    "0xc0000409",
    "llama-server process has terminated",
)

_EXCERPT_LIMIT = 400


def classify_gpu_error(text: str) -> str:
    """Classify GPU failure text into one of the recognized outcomes.

    Returns "driver_too_old" | "vram_insufficient" | "gpu_unsupported" | "" (empty = not a recognized
    GPU failure). Matching is a case-insensitive substring/keyword check on ``text``.

    Precedence when multiple groups match:
      * ``vram_insufficient`` wins over ``gpu_unsupported`` when both an OOM phrase and a bare
        "cuda error" appear -- OOM is the more specific, more actionable failure.
      * ``driver_too_old`` wins over ``gpu_unsupported`` when a PTX/toolchain phrase appears -- the
        driver-update guidance is more specific than a generic backend failure.
    Order of checks below encodes that precedence: driver_too_old, then vram_insufficient, then the
    generic gpu_unsupported fallback.
    """
    lowered = str(text or "").lower()
    if not lowered:
        return ""
    if any(keyword in lowered for keyword in _DRIVER_TOO_OLD_KEYWORDS):
        return "driver_too_old"
    if any(keyword in lowered for keyword in _VRAM_INSUFFICIENT_KEYWORDS):
        return "vram_insufficient"
    if any(keyword in lowered for keyword in _GPU_UNSUPPORTED_KEYWORDS):
        return "gpu_unsupported"
    return ""


def _looks_transient(text: str) -> bool:
    """True when the failure text looks transient (Ollama down / model not pulled), not a GPU crash."""
    lowered = str(text or "").lower()
    return any(keyword in lowered for keyword in _TRANSIENT_KEYWORDS)


@dataclass
class GpuInferenceVerdict:
    """Outcome of a live GPU-inference verification.

    outcome: ok | driver_too_old | vram_insufficient | gpu_unsupported | no_gpu | skipped
    suggested_action: update_driver | free_vram_or_smaller_model | use_cpu | none
    recommended_num_gpu is 0 whenever recommend_cpu_fallback is True.
    """

    outcome: str
    reason: str
    raw_excerpt: str
    recommend_cpu_fallback: bool
    recommended_num_gpu: int
    suggested_action: str

    def to_dict(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "reason": self.reason,
            "raw_excerpt": self.raw_excerpt,
            "recommend_cpu_fallback": self.recommend_cpu_fallback,
            "recommended_num_gpu": self.recommended_num_gpu,
            "suggested_action": self.suggested_action,
        }


# Map a classified GPU-failure outcome onto its user-facing action.
_ACTION_BY_OUTCOME = {
    "driver_too_old": "update_driver",
    "vram_insufficient": "free_vram_or_smaller_model",
    "gpu_unsupported": "use_cpu",
}
_REASON_BY_OUTCOME = {
    "driver_too_old": "The GPU driver is too old for the local inference backend, so GPU runs crash.",
    "vram_insufficient": "The model does not fit the free GPU memory, so GPU runs run out of memory.",
    "gpu_unsupported": "The GPU could not run the local inference backend.",
}


def _excerpt(text: str) -> str:
    return str(text or "").strip()[:_EXCERPT_LIMIT]


def _default_http_post(url: str, payload: dict, timeout: float) -> tuple[int, dict | None, str]:
    """POST ``payload`` as JSON to ``url``; return (status, parsed_json_or_None, error_text)."""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 0) or 200)
            body = response.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError):
            parsed = None
        return status, parsed if isinstance(parsed, dict) else None, "" if status == 200 else body
    except urllib.error.HTTPError as exc:  # non-2xx with a body
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = str(exc)
        return int(getattr(exc, "code", 0) or 0), None, body or str(exc)
    except Exception as exc:  # timeout, connection refused, DNS, etc.
        return 0, None, str(exc)


def _fallback_verdict(outcome: str, error_text: str) -> GpuInferenceVerdict:
    """Build a CPU-fallback verdict for a classified GPU failure ``outcome``."""
    reason = _REASON_BY_OUTCOME.get(outcome, "The GPU could not run the local inference backend.")
    return GpuInferenceVerdict(
        outcome=outcome,
        reason=reason,
        raw_excerpt=_excerpt(error_text),
        recommend_cpu_fallback=True,
        recommended_num_gpu=0,
        suggested_action=_ACTION_BY_OUTCOME.get(outcome, "use_cpu"),
    )


def verify_gpu_inference(
    *,
    model: str,
    gpu_present: bool = True,
    num_gpu: int = 999,
    num_ctx: int = 4096,
    base_url: str | None = None,
    http_post_fn: HttpPostFn | None = None,
    timeout_seconds: float = 90.0,
) -> GpuInferenceVerdict:
    """Prove a real token can be generated on the GPU, or classify why it cannot.

    Sends one small ``/api/chat`` generation with the GPU lane forced (``num_gpu``). Never raises.

    * No GPU present -> outcome "no_gpu" (CPU is already the plan, so nothing to warn about).
    * No model -> outcome "skipped".
    * HTTP 200 with non-empty ``message.content`` -> outcome "ok".
    * Non-200 / error body -> classify the error text; unrecognized failures default to
      "gpu_unsupported".
    * Exception / timeout -> classify the exception text; default to "gpu_unsupported" with a reason
      that mentions the hang.
    """
    # The canonical LocalModelPolicy: proving the GPU means GENERATING a token on a local
    # model. Under a disabled policy that proof is out of scope — report the same honest
    # "skipped" verdict as a machine with no model, without opening a socket.
    from core.local_model_policy import local_models_enabled

    if not local_models_enabled():
        return GpuInferenceVerdict(
            outcome="skipped",
            reason="local models disabled by policy",
            raw_excerpt="",
            recommend_cpu_fallback=False,
            recommended_num_gpu=999,
            suggested_action="Local models are disabled; GPU inference was not probed.",
        )
    if not gpu_present:
        return GpuInferenceVerdict(
            outcome="no_gpu",
            reason="No usable GPU detected; VOOL is already planning to run on CPU.",
            raw_excerpt="",
            recommend_cpu_fallback=False,
            recommended_num_gpu=0,
            suggested_action="none",
        )
    if not str(model or "").strip():
        return GpuInferenceVerdict(
            outcome="skipped",
            reason="No model provided; GPU inference check skipped.",
            raw_excerpt="",
            recommend_cpu_fallback=False,
            recommended_num_gpu=0,
            suggested_action="none",
        )

    post = http_post_fn or _default_http_post
    from core.ollama_endpoint import ollama_base_url

    url = f"{(str(base_url or '').strip() or ollama_base_url()).rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "keep_alive": 0,
        "options": {
            "num_gpu": int(num_gpu),
            "num_ctx": int(num_ctx),
            "num_predict": 8,
        },
        "messages": [{"role": "user", "content": "Reply with the single word: OK"}],
    }

    try:
        permit = seal_direct_provider_invocation(
            provider_id="ollama:gpu-inference-probe",
            model_id=model,
            operation="generation_probe",
            payload=payload,
            request_id=f"gpu-probe-{model}-{int(num_gpu)}",
            max_output_tokens=8,
            header_names=("Content-Type",),
        )
        status, parsed, error_text = post(
            url,
            permit.consume(),
            float(timeout_seconds),
        )
    except Exception as exc:  # a broken injected http_post_fn must not crash the probe
        outcome = classify_gpu_error(str(exc)) or "gpu_unsupported"
        verdict = _fallback_verdict(outcome, str(exc))
        verdict.reason = f"{verdict.reason} The GPU inference check raised or timed out before completing."
        return verdict

    if status == 200 and isinstance(parsed, dict):
        message = parsed.get("message") if isinstance(parsed.get("message"), dict) else {}
        content = str((message or {}).get("content") or "").strip()
        if content:
            return GpuInferenceVerdict(
                outcome="ok",
                reason="GPU inference produced a live token.",
                raw_excerpt="",
                recommend_cpu_fallback=False,
                recommended_num_gpu=int(num_gpu),
                suggested_action="none",
            )
        # 200 but no content: treat the JSON error field (if any) as the failure text.
        error_text = str(parsed.get("error") or error_text or "empty model response").strip()

    # Prefer an explicit JSON "error" field when present on a non-200 body.
    if isinstance(parsed, dict) and str(parsed.get("error") or "").strip():
        error_text = str(parsed.get("error")).strip()

    combined = str(error_text or "").strip()
    outcome = classify_gpu_error(combined)
    if outcome:
        return _fallback_verdict(outcome, combined)
    # No recognized GPU-failure signature. A 404 / model-missing / connection error is transient and
    # must NOT durably pin the host to CPU, so it is reported inconclusive (no fallback). Any other
    # non-transient failure with an unrecognized body is treated as a real GPU-backend failure.
    if status == 404 or _looks_transient(combined):
        return GpuInferenceVerdict(
            outcome="inconclusive",
            reason=(
                "GPU inference check was inconclusive (no GPU-failure signature; a transient error "
                "or the model was unavailable)."
            ),
            raw_excerpt=_excerpt(combined),
            recommend_cpu_fallback=False,
            recommended_num_gpu=int(num_gpu),
            suggested_action="none",
        )
    verdict = _fallback_verdict("gpu_unsupported", combined)
    if not combined:
        verdict.reason = f"{verdict.reason} The GPU inference check timed out or returned no usable response."
    return verdict


def gpu_capability_advisory(
    verdict: GpuInferenceVerdict,
    *,
    driver_version: str = "",
    vram_free_gb: float | None = None,
    cpu_fallback_model: str = "",
) -> str:
    """Plain, factual user-facing message for a verdict. No hype, no drama, no emoji.

    Each message is 1-3 short sentences. ok/no_gpu/skipped return an empty or one-line neutral status.
    """
    outcome = str(getattr(verdict, "outcome", "") or "")
    cpu_model = str(cpu_fallback_model or "").strip()
    cpu_clause = f" on CPU with {cpu_model}" if cpu_model else " on CPU"

    if outcome == "driver_too_old":
        driver_note = f" (installed driver {driver_version.strip()})" if str(driver_version or "").strip() else ""
        return (
            f"Your NVIDIA GPU driver{driver_note} is too old for the local inference backend, so GPU "
            "runs crash. Update your NVIDIA driver to use the GPU. VOOL is running"
            f"{cpu_clause} in the meantime."
        )
    if outcome == "vram_insufficient":
        vram_note = ""
        if vram_free_gb is not None:
            try:
                vram_note = f" (about {float(vram_free_gb):.1f} GB free)"
            except (TypeError, ValueError):
                vram_note = ""
        return (
            f"The selected model does not fit your GPU's free memory{vram_note}, so GPU runs run out "
            f"of memory. VOOL is running{cpu_clause} instead."
        )
    if outcome == "gpu_unsupported":
        return f"Your GPU could not run the local inference backend, so VOOL is running{cpu_clause}."
    if outcome == "ok":
        return "GPU inference is working."
    # no_gpu / skipped / anything else: nothing actionable to warn about.
    return ""


__all__ = [
    "GpuInferenceVerdict",
    "classify_gpu_error",
    "gpu_capability_advisory",
    "verify_gpu_inference",
]
