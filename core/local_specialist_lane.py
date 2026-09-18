from __future__ import annotations

from collections.abc import Mapping

DEFAULT_SECONDARY_LOCAL_MODEL = "qwen2.5:14b-gguf"
DEFAULT_SECONDARY_LOCAL_PROFILE = "local-max"
DEFAULT_SECONDARY_LOCAL_BACKEND = "llama.cpp"
DEFAULT_SECONDARY_LOCAL_BASE_URL = "http://127.0.0.1:8090/v1"
DEFAULT_SECONDARY_LOCAL_CONTEXT_WINDOW = 32768
DEFAULT_SECONDARY_LOCAL_PORT = 8090

# Deep lane vars take precedence: when a separate deep/quality llamacpp server is
# configured (VOOL_LLAMACPP_DEEP_*), it should be used as the verifier/secondary,
# not the fast lane primary.
SECONDARY_LOCAL_MODEL_ENV_KEYS = (
    "VOOL_LLAMACPP_DEEP_MODEL",
    "LLAMACPP_DEEP_MODEL",
    "VOOL_LLAMACPP_MODEL",
    "LLAMACPP_MODEL",
    "VOOL_LLAMA_CPP_MODEL",
    "LLAMA_CPP_MODEL",
)
SECONDARY_LOCAL_BASE_URL_ENV_KEYS = (
    "VOOL_LLAMACPP_DEEP_BASE_URL",
    "LLAMACPP_DEEP_BASE_URL",
    "LLAMACPP_BASE_URL",
    "VOOL_LLAMACPP_BASE_URL",
    "LLAMA_CPP_BASE_URL",
    "VOOL_LLAMA_CPP_BASE_URL",
)
SECONDARY_LOCAL_CONTEXT_WINDOW_ENV_KEYS = (
    "LLAMACPP_CONTEXT_WINDOW",
    "VOOL_LLAMACPP_CONTEXT_WINDOW",
    "LLAMA_CPP_CONTEXT_WINDOW",
    "VOOL_LLAMA_CPP_CONTEXT_WINDOW",
)
SECONDARY_LOCAL_MODEL_PATH_ENV_KEYS = (
    "LLAMACPP_MODEL_PATH",
    "VOOL_LLAMACPP_MODEL_PATH",
    "LLAMA_CPP_MODEL_PATH",
    "VOOL_LLAMA_CPP_MODEL_PATH",
)


def secondary_local_model(env: Mapping[str, str]) -> str:
    for key in SECONDARY_LOCAL_MODEL_ENV_KEYS:
        value = str(env.get(key) or "").strip()
        if value:
            return value
    return DEFAULT_SECONDARY_LOCAL_MODEL


def secondary_local_base_url(env: Mapping[str, str]) -> str:
    for key in SECONDARY_LOCAL_BASE_URL_ENV_KEYS:
        value = str(env.get(key) or "").strip()
        if value:
            return value
    return DEFAULT_SECONDARY_LOCAL_BASE_URL


def secondary_local_context_window(env: Mapping[str, str]) -> int:
    for key in SECONDARY_LOCAL_CONTEXT_WINDOW_ENV_KEYS:
        value = str(env.get(key) or "").strip()
        if not value:
            continue
        try:
            return max(1, int(value))
        except Exception:
            continue
    return DEFAULT_SECONDARY_LOCAL_CONTEXT_WINDOW


def secondary_local_model_path(env: Mapping[str, str]) -> str:
    for key in SECONDARY_LOCAL_MODEL_PATH_ENV_KEYS:
        value = str(env.get(key) or "").strip()
        if value:
            return value
    return ""


def secondary_local_provider_id(env: Mapping[str, str]) -> str:
    return f"llamacpp-local:{secondary_local_model(env)}"


# ---------------------------------------------------------------------------
# MLX tertiary lane (port 8096)
# ---------------------------------------------------------------------------

DEFAULT_MLX_MODEL = "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit"
DEFAULT_MLX_BACKEND = "mlx-lm"
DEFAULT_MLX_BASE_URL = "http://127.0.0.1:8096/v1"
DEFAULT_MLX_CONTEXT_WINDOW = 32768
DEFAULT_MLX_PORT = 8096

MLX_MODEL_ENV_KEYS = ("VOOL_MLX_MODEL", "MLX_MODEL")
MLX_BASE_URL_ENV_KEYS = ("VOOL_MLX_BASE_URL", "MLX_BASE_URL")
MLX_CONTEXT_WINDOW_ENV_KEYS = ("VOOL_MLX_CONTEXT_WINDOW", "MLX_CONTEXT_WINDOW")


def mlx_model(env: Mapping[str, str]) -> str:
    for key in MLX_MODEL_ENV_KEYS:
        value = str(env.get(key) or "").strip()
        if value:
            return value
    return DEFAULT_MLX_MODEL


def mlx_base_url(env: Mapping[str, str]) -> str:
    for key in MLX_BASE_URL_ENV_KEYS:
        value = str(env.get(key) or "").strip()
        if value:
            return value
    return DEFAULT_MLX_BASE_URL


def mlx_context_window(env: Mapping[str, str]) -> int:
    for key in MLX_CONTEXT_WINDOW_ENV_KEYS:
        value = str(env.get(key) or "").strip()
        if not value:
            continue
        try:
            return max(1, int(value))
        except Exception:
            continue
    return DEFAULT_MLX_CONTEXT_WINDOW


def mlx_provider_id(env: Mapping[str, str]) -> str:
    return f"mlx-local:{mlx_model(env)}"


__all__ = [
    "DEFAULT_MLX_BACKEND",
    "DEFAULT_MLX_BASE_URL",
    "DEFAULT_MLX_CONTEXT_WINDOW",
    "DEFAULT_MLX_MODEL",
    "DEFAULT_MLX_PORT",
    "DEFAULT_SECONDARY_LOCAL_BACKEND",
    "DEFAULT_SECONDARY_LOCAL_BASE_URL",
    "DEFAULT_SECONDARY_LOCAL_CONTEXT_WINDOW",
    "DEFAULT_SECONDARY_LOCAL_MODEL",
    "DEFAULT_SECONDARY_LOCAL_PORT",
    "DEFAULT_SECONDARY_LOCAL_PROFILE",
    "MLX_BASE_URL_ENV_KEYS",
    "MLX_CONTEXT_WINDOW_ENV_KEYS",
    "MLX_MODEL_ENV_KEYS",
    "SECONDARY_LOCAL_BASE_URL_ENV_KEYS",
    "SECONDARY_LOCAL_CONTEXT_WINDOW_ENV_KEYS",
    "SECONDARY_LOCAL_MODEL_ENV_KEYS",
    "SECONDARY_LOCAL_MODEL_PATH_ENV_KEYS",
    "mlx_base_url",
    "mlx_context_window",
    "mlx_model",
    "mlx_provider_id",
    "secondary_local_base_url",
    "secondary_local_context_window",
    "secondary_local_model",
    "secondary_local_model_path",
    "secondary_local_provider_id",
]
