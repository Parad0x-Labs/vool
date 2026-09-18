from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.provider_env import merge_provider_env

DEFAULT_MLX_MODEL_ID = "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit"
DEFAULT_MLX_PORT = 8096
DEFAULT_MLX_HOST = "127.0.0.1"
DEFAULT_MLX_CONTEXT_WINDOW = 32768
DEFAULT_MLX_BACKEND = "mlx-lm"
DEFAULT_MLX_MAX_TOKENS = 4096
_CONFIG_RELATIVE_PATH = Path("config") / "mlx-local.json"


@dataclass(frozen=True)
class MlxLocalConfig:
    schema: str
    backend: str
    model_id: str
    base_url: str
    host: str
    port: int
    context_window: int
    max_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def env_exports(self) -> dict[str, str]:
        return {
            "MLX_BASE_URL": self.base_url,
            "VOOL_MLX_BASE_URL": self.base_url,
            "VOOL_MLX_MODEL": self.model_id,
            "MLX_CONTEXT_WINDOW": str(self.context_window),
            "VOOL_MLX_CONTEXT_WINDOW": str(self.context_window),
            "VOOL_MLX_HOST": self.host,
            "VOOL_MLX_PORT": str(self.port),
            "VOOL_MLX_MAX_TOKENS": str(self.max_tokens),
        }


def build_mlx_local_config(
    *,
    runtime_home: str | Path,
    env: dict[str, str] | None = None,
) -> MlxLocalConfig:
    runtime_root = Path(runtime_home).expanduser().resolve()
    env_map = merge_provider_env(runtime_root, env=env)
    base_url = str(env_map.get("MLX_BASE_URL") or env_map.get("VOOL_MLX_BASE_URL") or "").strip()
    if not base_url:
        host = str(env_map.get("VOOL_MLX_HOST") or DEFAULT_MLX_HOST).strip()
        port = _env_int(env_map, "VOOL_MLX_PORT", default=DEFAULT_MLX_PORT)
        base_url = f"http://{host}:{port}/v1"
    else:
        parsed = urlparse(base_url)
        host = parsed.hostname or DEFAULT_MLX_HOST
        port = int(parsed.port or DEFAULT_MLX_PORT)
    model_id = str(env_map.get("VOOL_MLX_MODEL") or DEFAULT_MLX_MODEL_ID).strip()
    context_window = _env_int(
        env_map,
        "MLX_CONTEXT_WINDOW",
        "VOOL_MLX_CONTEXT_WINDOW",
        default=DEFAULT_MLX_CONTEXT_WINDOW,
    )
    max_tokens = _env_int(env_map, "VOOL_MLX_MAX_TOKENS", default=DEFAULT_MLX_MAX_TOKENS)
    return MlxLocalConfig(
        schema="vool.mlx_local.v1",
        backend=DEFAULT_MLX_BACKEND,
        model_id=model_id,
        base_url=base_url,
        host=host,
        port=port,
        context_window=context_window,
        max_tokens=max_tokens,
    )


def write_mlx_local_config(
    *,
    runtime_home: str | Path,
    env: dict[str, str] | None = None,
) -> tuple[MlxLocalConfig, Path]:
    config = build_mlx_local_config(runtime_home=runtime_home, env=env)
    target = Path(runtime_home).expanduser().resolve() / _CONFIG_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return config, target


def _env_int(env: dict[str, str], *keys: str, default: int) -> int:
    for key in keys:
        value = str(env.get(key) or "").strip()
        if not value:
            continue
        try:
            return max(1, int(value))
        except Exception:
            continue
    return max(1, int(default))


__all__ = [
    "DEFAULT_MLX_BACKEND",
    "DEFAULT_MLX_CONTEXT_WINDOW",
    "DEFAULT_MLX_HOST",
    "DEFAULT_MLX_MAX_TOKENS",
    "DEFAULT_MLX_MODEL_ID",
    "DEFAULT_MLX_PORT",
    "MlxLocalConfig",
    "build_mlx_local_config",
    "write_mlx_local_config",
]
