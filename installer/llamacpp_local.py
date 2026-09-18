from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from core.local_specialist_lane import (
    DEFAULT_SECONDARY_LOCAL_BACKEND,
    DEFAULT_SECONDARY_LOCAL_CONTEXT_WINDOW,
    DEFAULT_SECONDARY_LOCAL_PORT,
    secondary_local_base_url,
    secondary_local_context_window,
    secondary_local_model,
    secondary_local_model_path,
)
from core.provider_env import merge_provider_env

DEFAULT_LLAMACPP_REPO_ID = "Qwen/Qwen2.5-Coder-14B-Instruct-GGUF"
DEFAULT_LLAMACPP_FILENAME = "qwen2.5-coder-14b-instruct-q4_k_m.gguf"
DEFAULT_LLAMACPP_CHAT_FORMAT = "chatml"
DEFAULT_LLAMACPP_N_GPU_LAYERS = -1
DEFAULT_LLAMACPP_CACHE = True
DEFAULT_LLAMACPP_CACHE_TYPE = "ram"
DEFAULT_LLAMACPP_DRAFT_MODEL = "prompt-lookup-decoding"
DEFAULT_LLAMACPP_DRAFT_MODEL_NUM_PRED_TOKENS = 10
_CONFIG_RELATIVE_PATH = Path("config") / "llamacpp-local.json"
_MODEL_DIR_RELATIVE_PATH = Path("models") / "llamacpp"


@dataclass(frozen=True)
class LlamacppLocalConfig:
    schema: str
    profile_id: str
    backend: str
    model_id: str
    repo_id: str
    filename: str
    model_path: str
    base_url: str
    host: str
    port: int
    context_window: int
    chat_format: str
    n_gpu_layers: int
    cache: bool
    cache_type: str
    draft_model: str
    draft_model_num_pred_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def env_exports(self) -> dict[str, str]:
        return {
            "LLAMACPP_BASE_URL": self.base_url,
            "VOOL_LLAMACPP_MODEL": self.model_id,
            "LLAMACPP_CONTEXT_WINDOW": str(self.context_window),
            "VOOL_LLAMACPP_MODEL_PATH": self.model_path,
            "VOOL_LLAMACPP_HOST": self.host,
            "VOOL_LLAMACPP_PORT": str(self.port),
            "VOOL_LLAMACPP_CHAT_FORMAT": self.chat_format,
            "VOOL_LLAMACPP_N_GPU_LAYERS": str(self.n_gpu_layers),
            "VOOL_LLAMACPP_CACHE": "1" if self.cache else "0",
            "VOOL_LLAMACPP_CACHE_TYPE": self.cache_type,
            "VOOL_LLAMACPP_DRAFT_MODEL": self.draft_model,
            "VOOL_LLAMACPP_DRAFT_MODEL_NUM_PRED_TOKENS": str(self.draft_model_num_pred_tokens),
            "VOOL_LLAMACPP_REPO_ID": self.repo_id,
            "VOOL_LLAMACPP_FILENAME": self.filename,
        }


def build_llamacpp_local_config(
    *,
    runtime_home: str | Path,
    env: dict[str, str] | None = None,
) -> LlamacppLocalConfig:
    runtime_root = Path(runtime_home).expanduser().resolve()
    env_map = merge_provider_env(runtime_root, env=env)
    base_url = secondary_local_base_url(env_map)
    model_id = secondary_local_model(env_map)
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or DEFAULT_SECONDARY_LOCAL_PORT)
    model_path = str(
        Path(secondary_local_model_path(env_map) or (runtime_root / _MODEL_DIR_RELATIVE_PATH / DEFAULT_LLAMACPP_FILENAME))
        .expanduser()
        .resolve()
    )
    return LlamacppLocalConfig(
        schema="vool.llamacpp_local.v1",
        profile_id="local-max",
        backend=DEFAULT_SECONDARY_LOCAL_BACKEND,
        model_id=model_id,
        repo_id=str(env_map.get("VOOL_LLAMACPP_REPO_ID") or DEFAULT_LLAMACPP_REPO_ID).strip(),
        filename=str(env_map.get("VOOL_LLAMACPP_FILENAME") or DEFAULT_LLAMACPP_FILENAME).strip(),
        model_path=model_path,
        base_url=base_url,
        host=host,
        port=port,
        context_window=secondary_local_context_window(env_map) or DEFAULT_SECONDARY_LOCAL_CONTEXT_WINDOW,
        chat_format=str(env_map.get("VOOL_LLAMACPP_CHAT_FORMAT") or DEFAULT_LLAMACPP_CHAT_FORMAT).strip(),
        n_gpu_layers=_env_int(env_map, "VOOL_LLAMACPP_N_GPU_LAYERS", default=DEFAULT_LLAMACPP_N_GPU_LAYERS),
        cache=_env_bool(env_map, "VOOL_LLAMACPP_CACHE", default=DEFAULT_LLAMACPP_CACHE),
        cache_type=str(env_map.get("VOOL_LLAMACPP_CACHE_TYPE") or DEFAULT_LLAMACPP_CACHE_TYPE).strip(),
        draft_model=str(env_map.get("VOOL_LLAMACPP_DRAFT_MODEL") or DEFAULT_LLAMACPP_DRAFT_MODEL).strip(),
        draft_model_num_pred_tokens=_env_int(
            env_map,
            "VOOL_LLAMACPP_DRAFT_MODEL_NUM_PRED_TOKENS",
            default=DEFAULT_LLAMACPP_DRAFT_MODEL_NUM_PRED_TOKENS,
        ),
    )


def write_llamacpp_local_config(
    *,
    runtime_home: str | Path,
    env: dict[str, str] | None = None,
) -> tuple[LlamacppLocalConfig, Path]:
    config = build_llamacpp_local_config(runtime_home=runtime_home, env=env)
    target = Path(runtime_home).expanduser().resolve() / _CONFIG_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return config, target


def download_llamacpp_model(
    *,
    runtime_home: str | Path,
    env: dict[str, str] | None = None,
) -> LlamacppLocalConfig:
    config, _ = write_llamacpp_local_config(runtime_home=runtime_home, env=env)
    model_path = Path(config.model_path)
    if model_path.exists():
        return config
    model_path.parent.mkdir(parents=True, exist_ok=True)
    if _download_public_hf_file(config=config, model_path=model_path):
        return config
    _download_via_hf_hub(config=config, model_path=model_path)
    return config


def _download_public_hf_file(*, config: LlamacppLocalConfig, model_path: Path) -> bool:
    curl_binary = shutil.which("curl")
    if not curl_binary:
        return False
    partial_path = model_path.with_suffix(f"{model_path.suffix}.partial")
    download_url = _huggingface_resolve_url(repo_id=config.repo_id, filename=config.filename)
    completed = subprocess.run(
        [
            curl_binary,
            "-fL",
            "--retry",
            "5",
            "--retry-delay",
            "2",
            "--continue-at",
            "-",
            "-o",
            str(partial_path),
            download_url,
        ],
        check=False,
    )
    if completed.returncode != 0 or not partial_path.exists() or partial_path.stat().st_size <= 0:
        return False
    partial_path.replace(model_path)
    return True


def _download_via_hf_hub(*, config: LlamacppLocalConfig, model_path: Path) -> None:
    try:
        from huggingface_hub import hf_hub_download
    except Exception as exc:  # pragma: no cover - runtime dependency issue
        raise RuntimeError("huggingface_hub is required to download the optional llama.cpp model.") from exc
    previous_disable_xet = os.environ.get("HF_HUB_DISABLE_XET")
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    try:
        downloaded = hf_hub_download(
            repo_id=config.repo_id,
            filename=config.filename,
            local_dir=str(model_path.parent),
        )
    finally:
        if previous_disable_xet is None:
            os.environ.pop("HF_HUB_DISABLE_XET", None)
        else:
            os.environ["HF_HUB_DISABLE_XET"] = previous_disable_xet
    downloaded_path = Path(downloaded).expanduser().resolve()
    if downloaded_path != model_path:
        shutil.copyfile(downloaded_path, model_path)


def _huggingface_resolve_url(*, repo_id: str, filename: str) -> str:
    repo_path = "/".join(quote(part, safe="") for part in str(repo_id).split("/"))
    file_path = "/".join(quote(part, safe="") for part in Path(str(filename)).parts)
    return f"https://huggingface.co/{repo_path}/resolve/main/{file_path}?download=1"


def _env_int(env: dict[str, str], key: str, *, default: int) -> int:
    value = str(env.get(key) or "").strip()
    if not value:
        return default
    try:
        return int(value)
    except Exception:
        return default


def _env_bool(env: dict[str, str], key: str, *, default: bool) -> bool:
    value = str(env.get(key) or "").strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "on", "enabled"}:
        return True
    if value in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


__all__ = [
    "DEFAULT_LLAMACPP_CACHE",
    "DEFAULT_LLAMACPP_CACHE_TYPE",
    "DEFAULT_LLAMACPP_CHAT_FORMAT",
    "DEFAULT_LLAMACPP_DRAFT_MODEL",
    "DEFAULT_LLAMACPP_DRAFT_MODEL_NUM_PRED_TOKENS",
    "DEFAULT_LLAMACPP_FILENAME",
    "DEFAULT_LLAMACPP_N_GPU_LAYERS",
    "DEFAULT_LLAMACPP_REPO_ID",
    "LlamacppLocalConfig",
    "build_llamacpp_local_config",
    "download_llamacpp_model",
    "write_llamacpp_local_config",
]
