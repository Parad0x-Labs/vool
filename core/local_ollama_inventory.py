from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class InstalledOllamaModel:
    name: str
    size_bytes: int = 0


def env_flag_enabled(env: Mapping[str, str], name: str, *, default: bool = False) -> bool:
    raw = str(env.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on", "enabled"}:
        return True
    if raw in {"0", "false", "no", "off", "disabled"}:
        return False
    return bool(default)


def installed_ollama_model_names(
    *,
    env: Mapping[str, str] | None = None,
    base_url: str | None = None,
    timeout_seconds: float = 2.0,
) -> tuple[str, ...]:
    return tuple(
        item.name
        for item in installed_ollama_model_inventory(
            env=env,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
    )


def installed_ollama_model_inventory(
    *,
    env: Mapping[str, str] | None = None,
    base_url: str | None = None,
    timeout_seconds: float = 2.0,
) -> tuple[InstalledOllamaModel, ...]:
    """Installed text-model identity plus Ollama's artifact bytes, when reported.

    Names alone are sufficient for registration, but not for residency decisions: MoE active
    parameters can be small while the resident weights are tens of gigabytes. Explicit inventory
    overrides do not carry byte truth and therefore return ``size_bytes=0``; callers can fall back
    to their static model metadata without inventing a measured size.
    """

    env_map = os.environ if env is None else env
    explicit = str(env_map.get("VOOL_INSTALLED_OLLAMA_MODELS") or "").strip()
    if explicit:
        return tuple(
            InstalledOllamaModel(name=name)
            for name in _dedupe_model_names(
                item.strip()
                for chunk in explicit.splitlines()
                for item in chunk.split(",")
            )
        )

    models = _ollama_models_payload(
        env=env_map,
        base_url=base_url,
        path="/api/tags",
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(models, list):
        return tuple()
    seen: set[str] = set()
    installed: list[InstalledOllamaModel] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        try:
            size_bytes = max(0, int(item.get("size") or 0))
        except (TypeError, ValueError):
            size_bytes = 0
        installed.append(InstalledOllamaModel(name=name, size_bytes=size_bytes))
    return tuple(installed)


def loaded_ollama_model_names(
    *,
    env: Mapping[str, str] | None = None,
    base_url: str | None = None,
    timeout_seconds: float = 2.0,
) -> tuple[str, ...]:
    env_map = os.environ if env is None else env
    explicit = str(env_map.get("VOOL_LOADED_OLLAMA_MODELS") or "").strip()
    if explicit:
        return _dedupe_model_names(item.strip() for chunk in explicit.splitlines() for item in chunk.split(","))
    models = _ollama_models_payload(
        env=env_map,
        base_url=base_url,
        path="/api/ps",
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(models, list):
        return tuple()
    return _dedupe_model_names(
        str(item.get("name") or item.get("model") or "").strip()
        for item in models
        if isinstance(item, dict)
    )


def _ollama_models_payload(
    *,
    env: Mapping[str, str],
    base_url: str | None,
    path: str,
    timeout_seconds: float,
) -> Any:
    # One resolution for every Ollama client: an explicit base_url wins, otherwise the mapping the caller gave
    # us decides (VOOL_RAW_OLLAMA_API_URL, OLLAMA_HOST, VOOL_OLLAMA_URL), then the process environment.
    from core.ollama_endpoint import ollama_base_url

    resolved = str(base_url or "").strip() or ollama_base_url({**os.environ, **dict(env)} if env is not os.environ else None)
    url = resolved.rstrip("/") + path
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=float(timeout_seconds)) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None
    return payload.get("models") if isinstance(payload, dict) else None


def is_text_generation_ollama_model(model_name: str) -> bool:
    clean = str(model_name or "").strip().lower()
    if not clean:
        return False
    non_text_markers = (
        # Embedding models — not chat.
        "all-minilm",
        "bge-",
        "clip",
        "embed",
        "embedding",
        "nomic-embed",
        "snowflake-arctic-embed",
        # Vision / multimodal models — they answer image prompts, not text chat, and the router
        # must never pick one for a plain message (moondream returned "A coffee please?" echoes for
        # casual turns before this filter).
        "moondream",
        "llava",
        "bakllava",
        "minicpm-v",
        "-vision",
        "-vl",
        "vl-",
    )
    return not any(marker in clean for marker in non_text_markers)


def _dedupe_model_names(values: Any) -> tuple[str, ...]:
    seen: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if not clean or clean in seen:
            continue
        seen.append(clean)
    return tuple(seen)


__all__ = [
    "InstalledOllamaModel",
    "env_flag_enabled",
    "installed_ollama_model_inventory",
    "installed_ollama_model_names",
    "is_text_generation_ollama_model",
    "loaded_ollama_model_names",
]
