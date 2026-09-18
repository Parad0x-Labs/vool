from __future__ import annotations

import json
from pathlib import Path

from core.model_registry import ModelRegistry
from core.runtime_provider_defaults import _ensure_mlx_provider
from installer.mlx_local import (
    build_mlx_local_config,
    write_mlx_local_config,
)


def test_build_mlx_local_config_returns_correct_defaults(tmp_path: Path) -> None:
    config = build_mlx_local_config(runtime_home=tmp_path, env={})

    assert config.model_id == "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit"
    assert config.base_url == "http://127.0.0.1:8096/v1"
    assert config.port == 8096
    assert config.context_window == 32768
    assert config.backend == "mlx-lm"
    assert config.schema == "vool.mlx_local.v1"


def test_build_mlx_local_config_respects_env_overrides(tmp_path: Path) -> None:
    env = {
        "MLX_BASE_URL": "http://192.168.1.10:9000/v1",
        "VOOL_MLX_MODEL": "mlx-community/custom-model-4bit",
        "VOOL_MLX_CONTEXT_WINDOW": "65536",
    }
    config = build_mlx_local_config(runtime_home=tmp_path, env=env)

    assert config.base_url == "http://192.168.1.10:9000/v1"
    assert config.model_id == "mlx-community/custom-model-4bit"
    assert config.context_window == 65536


def test_write_mlx_local_config_writes_valid_json(tmp_path: Path) -> None:
    _config, target = write_mlx_local_config(runtime_home=tmp_path, env={})

    assert target == tmp_path / "config" / "mlx-local.json"
    assert target.exists()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["backend"] == "mlx-lm"
    assert data["schema"] == "vool.mlx_local.v1"
    assert data["port"] == 8096
    assert data["context_window"] == 32768
    assert isinstance(data["model_id"], str) and data["model_id"]


def test_mlx_local_config_env_exports_returns_required_keys(tmp_path: Path) -> None:
    config = build_mlx_local_config(runtime_home=tmp_path, env={})
    exports = config.env_exports()

    assert "MLX_BASE_URL" in exports
    assert "VOOL_MLX_BASE_URL" in exports
    assert "VOOL_MLX_MODEL" in exports
    assert "MLX_CONTEXT_WINDOW" in exports
    assert "VOOL_MLX_CONTEXT_WINDOW" in exports
    assert "VOOL_MLX_HOST" in exports
    assert "VOOL_MLX_PORT" in exports
    assert "VOOL_MLX_MAX_TOKENS" in exports


def test_ensure_mlx_provider_returns_empty_when_base_url_not_set() -> None:
    registry = ModelRegistry()
    result = _ensure_mlx_provider(registry, env={})

    assert result == ""


def test_ensure_mlx_provider_registers_manifest_when_base_url_set() -> None:
    registry = ModelRegistry()
    env = {"MLX_BASE_URL": "http://127.0.0.1:8096/v1"}
    result = _ensure_mlx_provider(registry, env=env)

    assert result != ""
    manifest = registry.get_manifest("mlx-local", "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit")
    assert manifest is not None
    assert manifest.provider_name == "mlx-local"
    assert manifest.enabled is True
