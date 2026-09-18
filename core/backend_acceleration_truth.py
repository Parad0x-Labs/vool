from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any

import requests

from core.provider_invocation_gateway import (
    seal_direct_provider_invocation,
)


@dataclass(frozen=True)
class BackendAccelerationProof:
    backend: str
    kv_cache_status: str
    backend_cache_proof: dict[str, Any]
    speculative_status: str
    speculative_proof: dict[str, Any]
    eagle_status: str
    eagle_proof: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def backend_acceleration_proof(
    *,
    provider_id: str = "",
    model_id: str = "",
    backend: str = "",
    env: dict[str, str] | None = None,
    probe: bool = False,
    timeout_seconds: float = 2.0,
) -> BackendAccelerationProof:
    env_map = os.environ if env is None else env
    resolved_backend = _resolve_backend(provider_id=provider_id, model_id=model_id, backend=backend)
    if resolved_backend == "ollama":
        return BackendAccelerationProof(
            backend="ollama",
            kv_cache_status="ollama=not_supported_keep_alive_only",
            backend_cache_proof={
                "backend": "ollama",
                "status": "not_supported",
                "reason": "Ollama keep_alive keeps a model resident but does not expose portable KV or prefix-cache handles.",
            },
            speculative_status="inactive",
            speculative_proof={
                "backend": "ollama",
                "status": "inactive",
                "reason": "Ollama does not expose a proven draft/speculative decoding control through this runtime.",
            },
            eagle_status="unsupported_by_backend",
            eagle_proof={
                "backend": "ollama",
                "status": "unsupported_by_backend",
                "reason": "No EAGLE/draft-model backend handle is exposed for Ollama.",
            },
        )
    if resolved_backend != "llama.cpp":
        return BackendAccelerationProof(
            backend=resolved_backend or "unknown",
            kv_cache_status=f"{resolved_backend or 'unknown'}=unsupported",
            backend_cache_proof={
                "backend": resolved_backend or "unknown",
                "status": "unsupported",
                "reason": "No backend-specific cache probe is configured.",
            },
            speculative_status="inactive",
            speculative_proof={
                "backend": resolved_backend or "unknown",
                "status": "inactive",
                "reason": "No proven draft/speculative decoding configuration is active.",
            },
            eagle_status="unsupported_by_backend",
            eagle_proof={
                "backend": resolved_backend or "unknown",
                "status": "unsupported_by_backend",
                "reason": "No EAGLE-compatible backend handle is configured.",
            },
        )

    base_url = _first_env(
        env_map,
        "LLAMACPP_BASE_URL",
        "VOOL_LLAMACPP_BASE_URL",
        "LLAMA_CPP_BASE_URL",
        "VOOL_LLAMA_CPP_BASE_URL",
    )
    cache_enabled = _env_flag(env_map, "VOOL_LLAMACPP_CACHE", "LLAMACPP_CACHE", default=False)
    spec_type = _first_env(env_map, "VOOL_LLAMACPP_SPEC_TYPE", "LLAMACPP_SPEC_TYPE")
    draft_model_path = _first_env(env_map, "VOOL_LLAMACPP_DRAFT_MODEL", "LLAMACPP_DRAFT_MODEL")
    draft_n_max = _first_env(env_map, "VOOL_LLAMACPP_DRAFT_N_MAX", "VOOL_LLAMACPP_DRAFT_MODEL_NUM_PRED_TOKENS") or "8"
    draft_p_min = _first_env(env_map, "VOOL_LLAMACPP_DRAFT_P_MIN") or "0.5"
    flash_attn = _env_flag(env_map, "VOOL_LLAMACPP_FLASH_ATTN", "LLAMACPP_FLASH_ATTN", default=False)
    model = _first_env(env_map, "VOOL_LLAMACPP_MODEL", "LLAMACPP_MODEL") or str(model_id or "").strip()
    probe_result = _probe_llamacpp_generation(
        base_url=base_url,
        model=model,
        timeout_seconds=timeout_seconds,
    ) if probe else {"ok": False, "status": "not_probed"}
    proved = bool(probe_result.get("ok"))

    if cache_enabled and proved:
        kv_status = "llama.cpp=cache_active"
        cache_status = "active"
    elif cache_enabled:
        kv_status = "llama.cpp=configured_not_proven"
        cache_status = "configured_not_proven"
    else:
        kv_status = "llama.cpp=supported_not_active"
        cache_status = "supported_not_active"

    is_eagle3 = spec_type == "draft-eagle3"
    # A draft only counts as present when it is an ABSOLUTE, existing file (issue #20 guard). The old
    # check treated any non-"/" string as existing, so a relative path, a repo id, or a sentinel like
    # "prompt-lookup-decoding" flipped eagle_status to active/configured_not_proven with no GGUF behind
    # it — and on Windows, where absolute paths start with a drive letter (not "/"), EVERY string passed.
    # os.path.isabs handles both POSIX ("/x") and Windows ("C:\\x"); os.path.isfile requires it to exist.
    draft_model_exists = (
        bool(draft_model_path)
        and os.path.isabs(draft_model_path)
        and os.path.isfile(draft_model_path)
    )

    if is_eagle3 and draft_model_exists and proved:
        speculative_status = "active"
        speculative_state = "active"
        eagle_status = "active"
        eagle_state = "active"
    elif is_eagle3 and draft_model_exists:
        speculative_status = "configured_not_proven"
        speculative_state = "configured_not_proven"
        eagle_status = "configured_not_proven"
        eagle_state = "configured_not_proven"
    elif spec_type and proved:
        speculative_status = "active"
        speculative_state = "active"
        eagle_status = "unsupported_by_config"
        eagle_state = "unsupported_by_config"
    elif spec_type:
        speculative_status = "configured_not_proven"
        speculative_state = "configured_not_proven"
        eagle_status = "unsupported_by_config"
        eagle_state = "unsupported_by_config"
    else:
        speculative_status = "supported_not_configured"
        speculative_state = "supported_not_configured"
        eagle_status = "supported_not_configured"
        eagle_state = "supported_not_configured"

    return BackendAccelerationProof(
        backend="llama.cpp",
        kv_cache_status=kv_status,
        backend_cache_proof={
            "backend": "llama.cpp",
            "status": cache_status,
            "configured": cache_enabled,
            "flash_attn": flash_attn,
            "probe": probe_result,
        },
        speculative_status=speculative_status,
        speculative_proof={
            "backend": "llama.cpp",
            "status": speculative_state,
            "spec_type": spec_type or "none",
            "draft_model_path": draft_model_path or "",
            "draft_model_exists": draft_model_exists,
            "n_max": draft_n_max,
            "probe": probe_result,
        },
        eagle_status=eagle_status,
        eagle_proof={
            "backend": "llama.cpp",
            "status": eagle_state,
            "spec_type": spec_type or "none",
            "draft_model": draft_model_path or "",
            "draft_model_exists": draft_model_exists,
            "n_max": draft_n_max,
            "p_min": draft_p_min,
            "probe": probe_result,
        },
    )


def _probe_llamacpp_generation(*, base_url: str, model: str, timeout_seconds: float) -> dict[str, Any]:
    clean_base = str(base_url or "").rstrip("/")
    clean_model = str(model or "").strip()
    if not clean_base:
        return {"ok": False, "status": "missing_base_url"}
    if not clean_model:
        return {"ok": False, "status": "missing_model"}
    try:
        models_response = requests.get(f"{clean_base}/models", timeout=timeout_seconds)
        models_response.raise_for_status()
        payload = {
            "model": clean_model,
            "messages": [{"role": "user", "content": "Reply with ok."}],
            "temperature": 0,
            "max_tokens": 1,
            "stream": False,
        }
        permit = seal_direct_provider_invocation(
            provider_id="llamacpp:acceleration-probe",
            model_id=clean_model,
            operation="generation_probe",
            payload=payload,
            request_id=f"llamacpp-probe-{clean_model}",
            max_output_tokens=1,
            header_names=("Content-Type",),
        )
        response = requests.post(
            f"{clean_base}/chat/completions",
            json=permit.consume(),
            timeout=max(timeout_seconds, 8.0),
        )
        response.raise_for_status()
        payload = response.json()
        return {
            "ok": True,
            "status": "generation_probe_ok",
            "models_status": models_response.status_code,
            "completion_status": response.status_code,
            "usage_present": isinstance(payload.get("usage"), dict),
        }
    except Exception as exc:
        return {"ok": False, "status": "probe_failed", "error": str(exc)}


def _resolve_backend(*, provider_id: str, model_id: str, backend: str) -> str:
    clean_backend = str(backend or "").strip().lower()
    if clean_backend in {"llama.cpp", "llamacpp", "llama-cpp"}:
        return "llama.cpp"
    if clean_backend in {"ollama", "mlx", "vllm"}:
        return clean_backend
    lowered = f"{provider_id}:{model_id}".lower()
    if "llamacpp" in lowered or "llama.cpp" in lowered:
        return "llama.cpp"
    if "ollama" in lowered:
        return "ollama"
    if "mlx" in lowered:
        return "mlx"
    if "vllm" in lowered:
        return "vllm"
    return clean_backend or "unknown"


def _first_env(env: dict[str, str] | os._Environ[str], *names: str) -> str:
    for name in names:
        value = str(env.get(name) or "").strip()
        if value:
            return value
    return ""


def _env_flag(env: dict[str, str] | os._Environ[str], *names: str, default: bool = False) -> bool:
    raw = _first_env(env, *names).lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on", "enabled"}:
        return True
    if raw in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


__all__ = ["BackendAccelerationProof", "backend_acceleration_proof"]
