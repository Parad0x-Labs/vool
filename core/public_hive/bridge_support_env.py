from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from core.public_hive.config import (
    _clean_token,
    _json_env_object,
    _json_env_write_grants,
    _load_json_file,
    _normalize_base_url,
    _resolve_local_tls_ca_file,
    _split_csv,
)
from core.runtime_paths import PROJECT_ROOT


def load_json_file(path: Path) -> dict[str, Any]:
    return _load_json_file(path)


def split_csv(value: str) -> list[str]:
    return _split_csv(value)


def json_env_object(value: str) -> dict[str, str]:
    return _json_env_object(value)


def json_env_write_grants(value: str) -> dict[str, dict[str, dict[str, Any]]]:
    return _json_env_write_grants(value)


def merge_auth_tokens_by_base_url(raw: dict[str, Any]) -> dict[str, str]:
    merged = {
        _normalize_base_url(base): str(token).strip()
        for base, token in dict(raw.get("auth_tokens_by_base_url") or {}).items()
        if str(base).strip() and _clean_token(str(token or "").strip())
    }
    merged.update(_json_env_object(os.environ.get("VOOL_MEET_AUTH_TOKENS_JSON", "")))
    return merged


def merge_write_grants_by_base_url(raw: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    merged: dict[str, dict[str, dict[str, Any]]] = {}
    raw_value = dict(raw.get("write_grants_by_base_url") or {})
    for base_url, routes in raw_value.items():
        normalized_base = _normalize_base_url(str(base_url or "").strip())
        if not normalized_base or not isinstance(routes, dict):
            continue
        merged[normalized_base] = {
            (str(route or "").rstrip("/") or "/"): dict(grant)
            for route, grant in routes.items()
            if str(route or "").strip() and isinstance(grant, dict)
        }
    env_value = _json_env_write_grants(os.environ.get("VOOL_MEET_WRITE_GRANTS_JSON", ""))
    for base_url, routes in env_value.items():
        merged[base_url] = dict(routes)
    return merged


def clean_token(value: str) -> str | None:
    return _clean_token(value)


def normalize_base_url(url: str) -> str:
    return _normalize_base_url(url)


def resolve_local_tls_ca_file(tls_ca_file: str | None, *, project_root: str | Path | None = None) -> str | None:
    return _resolve_local_tls_ca_file(tls_ca_file, project_root=project_root or PROJECT_ROOT)
