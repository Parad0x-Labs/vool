from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from pathlib import Path

_PROVIDER_ENV_RELATIVE_PATH = Path("config") / "provider-env.sh"


def provider_env_file_path(runtime_home: str | Path | None) -> Path:
    if runtime_home is None:
        return Path()
    return Path(runtime_home).expanduser().resolve() / _PROVIDER_ENV_RELATIVE_PATH


def load_provider_env_overrides(runtime_home: str | Path | None) -> dict[str, str]:
    path = provider_env_file_path(runtime_home)
    if not path or not path.exists():
        return {}
    overrides: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        key = name.strip()
        if not key:
            continue
        overrides[key] = _parse_shell_value(raw_value)
    return overrides


def merge_provider_env(
    runtime_home: str | Path | None,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    merged = dict(os.environ if env is None else env)
    for key, value in load_provider_env_overrides(runtime_home).items():
        if str(merged.get(key) or "").strip():
            continue
        merged[key] = value
    return merged


def _parse_shell_value(raw_value: str) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    try:
        parts = shlex.split(value, posix=True)
    except Exception:
        return value.strip("\"'")
    return " ".join(parts) if parts else ""


__all__ = [
    "load_provider_env_overrides",
    "merge_provider_env",
    "provider_env_file_path",
]
