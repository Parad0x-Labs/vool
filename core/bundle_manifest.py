"""Read the immutable model selection written into a self-contained VOOL bundle."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

BUNDLE_MANIFEST_FILENAME = "bundle_manifest.json"


def bundle_manifest_path(*, env: Mapping[str, str] | None = None) -> Path | None:
    env_map = os.environ if env is None else env
    explicit = str(env_map.get("VOOL_BUNDLE_MANIFEST") or "").strip()
    root = str(env_map.get("VOOL_BUNDLE_ROOT") or env_map.get("VOOL_ROOT") or "").strip()
    candidate = Path(explicit).expanduser() if explicit else (Path(root).expanduser() / BUNDLE_MANIFEST_FILENAME if root else None)
    if candidate is None:
        return None
    if candidate.is_dir():
        candidate = candidate / BUNDLE_MANIFEST_FILENAME
    return candidate


def load_bundle_manifest(*, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    path = bundle_manifest_path(env=env)
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def selected_bundle_model(*, env: Mapping[str, str] | None = None) -> str:
    payload = load_bundle_manifest(env=env)
    model = str(payload.get("selected_model") or payload.get("model") or "").strip()
    return model


def bundle_model_store_path(
    *,
    env: Mapping[str, str] | None = None,
    runtime_home: str | Path | None = None,
) -> Path | None:
    """Resolve the model store declared by a bundle manifest.

    Bundle manifests use Windows environment-style placeholders so the same staged artifact can
    be installed per-user. ``runtime_home`` is accepted by doctor/runtime callers so a test or an
    alternate install can resolve ``%LOCALAPPDATA%\\VOOL`` to the actual writable runtime home.
    """
    env_map = os.environ if env is None else env
    raw = str(load_bundle_manifest(env=env_map).get("model_store") or "").strip()
    if not raw:
        return None

    local_app_data = Path(str(env_map.get("LOCALAPPDATA") or Path.home())).expanduser()
    runtime = Path(
        runtime_home
        or str(env_map.get("VOOL_HOME") or "").strip()
        or local_app_data / "VOOL"
    ).expanduser()
    normalized = raw.replace("\\", "/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    local_prefix = "%LOCALAPPDATA%/VOOL"
    home_prefix = "%VOOL_HOME%"
    normalized_lower = normalized.lower()
    if normalized_lower == local_prefix.lower() or normalized_lower.startswith(local_prefix.lower() + "/"):
        suffix = normalized[len(local_prefix) :].lstrip("/")
        return runtime / suffix if suffix else runtime
    if normalized_lower == home_prefix.lower() or normalized_lower.startswith(home_prefix.lower() + "/"):
        suffix = normalized[len(home_prefix) :].lstrip("/")
        return runtime / suffix if suffix else runtime

    expanded = normalized.replace("%LOCALAPPDATA%", str(local_app_data).replace("\\", "/"))
    expanded = expanded.replace("%VOOL_HOME%", str(runtime).replace("\\", "/"))
    return Path(expanded).expanduser()


__all__ = [
    "BUNDLE_MANIFEST_FILENAME",
    "bundle_manifest_path",
    "bundle_model_store_path",
    "load_bundle_manifest",
    "selected_bundle_model",
]
