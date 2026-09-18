from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_PLACEHOLDER_TOKEN_RE = re.compile(r"(?:replace|set|change).*(?:token|secret)", re.IGNORECASE)


@dataclass(frozen=True)
class PublicHiveBridgeConfig:
    enabled: bool = True
    meet_seed_urls: tuple[str, ...] = ()
    topic_target_url: str | None = None
    home_region: str = "global"
    request_timeout_seconds: int = 8
    auth_token: str | None = None
    auth_tokens_by_base_url: dict[str, str] = field(default_factory=dict)
    write_grants_by_base_url: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    tls_ca_file: str | None = None
    tls_insecure_skip_verify: bool = False


def load_public_hive_bridge_config(
    *,
    ensure_public_hive_agent_bootstrap_fn: Any,
    load_json_file_fn: Any,
    load_agent_bootstrap_fn: Any,
    discover_local_cluster_bootstrap_fn: Any,
    split_csv_fn: Any,
    json_env_object_fn: Any,
    merge_auth_tokens_by_base_url_fn: Any,
    json_env_write_grants_fn: Any,
    merge_write_grants_by_base_url_fn: Any,
    clean_token_fn: Any,
    config_path_fn: Any,
    project_root: str | Path | None,
    env: Any,
) -> PublicHiveBridgeConfig:
    ensure_public_hive_agent_bootstrap_fn()
    runtime = load_json_file_fn(config_path_fn("agent-bootstrap.json"))
    sample = load_agent_bootstrap_fn(include_runtime=False)
    discovered = discover_local_cluster_bootstrap_fn(project_root=project_root)
    env_urls = split_csv_fn(env.get("VOOL_MEET_SEED_URLS", ""))
    runtime_seed_urls = [str(url).strip() for url in list(runtime.get("meet_seed_urls") or []) if str(url).strip()]
    sample_seed_urls = [str(url).strip() for url in list(sample.get("meet_seed_urls") or []) if str(url).strip()]
    seed_urls = tuple(env_urls or runtime_seed_urls or sample_seed_urls or list(discovered.get("meet_seed_urls") or []))
    env_auth_tokens_by_base_url = json_env_object_fn(env.get("VOOL_MEET_AUTH_TOKENS_JSON", ""))
    auth_tokens_by_base_url = env_auth_tokens_by_base_url or merge_auth_tokens_by_base_url_fn(runtime)
    env_write_grants_by_base_url = json_env_write_grants_fn(env.get("VOOL_MEET_WRITE_GRANTS_JSON", ""))
    write_grants_by_base_url = env_write_grants_by_base_url or merge_write_grants_by_base_url_fn(runtime)
    if not write_grants_by_base_url:
        write_grants_by_base_url = merge_write_grants_by_base_url_fn(sample)
    env_auth_token = clean_token_fn(str(env.get("VOOL_MEET_AUTH_TOKEN", "")).strip())
    raw_auth_token = clean_token_fn(str(runtime.get("auth_token") or "").strip())
    env_tls_insecure = str(env.get("VOOL_MEET_TLS_INSECURE_SKIP_VERIFY") or "").strip().lower()
    if env_tls_insecure:
        tls_insecure_skip_verify = env_tls_insecure in {"1", "true", "yes", "on"}
    else:
        tls_insecure_skip_verify = bool(runtime.get("tls_insecure_skip_verify", False))
    enabled_raw = str(env.get("VOOL_PUBLIC_HIVE_ENABLED", "0")).strip().lower()
    enabled = enabled_raw in {"1", "true", "yes", "on"} and bool(seed_urls)
    topic_target_url = seed_urls[0] if seed_urls else None
    return PublicHiveBridgeConfig(
        enabled=enabled,
        meet_seed_urls=seed_urls,
        topic_target_url=topic_target_url,
        home_region=str(
            env.get("VOOL_HOME_REGION")
            or runtime.get("home_region")
            or discovered.get("home_region")
            or sample.get("home_region")
            or "global"
        ).strip()
        or "global",
        request_timeout_seconds=max(
            3,
            int(
                float(
                    env.get("VOOL_MEET_TIMEOUT_SECONDS")
                    or runtime.get("request_timeout_seconds")
                    or sample.get("request_timeout_seconds")
                    or 8
                )
            ),
        ),
        auth_token=env_auth_token or raw_auth_token,
        auth_tokens_by_base_url=auth_tokens_by_base_url,
        write_grants_by_base_url=write_grants_by_base_url,
        tls_ca_file=str(
            env.get("VOOL_MEET_TLS_CA_FILE")
            or runtime.get("tls_ca_file")
            or discovered.get("tls_ca_file")
            or sample.get("tls_ca_file")
            or ""
        ).strip()
        or None,
        tls_insecure_skip_verify=tls_insecure_skip_verify,
    )


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _load_json_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def public_hive_has_auth(config: PublicHiveBridgeConfig | None = None, *, payload: dict[str, Any] | None = None) -> bool:
    if config is not None:
        if _clean_token(str(config.auth_token or "").strip()):
            return True
        return any(_clean_token(str(token or "").strip()) for token in dict(config.auth_tokens_by_base_url or {}).values())
    raw = dict(payload or {})
    if _clean_token(str(raw.get("auth_token") or "").strip()):
        return True
    return any(_clean_token(str(token or "").strip()) for token in dict(raw.get("auth_tokens_by_base_url") or {}).values())


def public_hive_write_requires_auth(
    config: PublicHiveBridgeConfig | None = None,
    *,
    seed_urls: list[str] | tuple[str, ...] | None = None,
    topic_target_url: str | None = None,
) -> bool:
    urls = [str(url).strip() for url in list(seed_urls or []) if str(url).strip()]
    if config is not None:
        urls.extend(str(url).strip() for url in list(config.meet_seed_urls or ()) if str(url).strip())
        if str(config.topic_target_url or "").strip():
            urls.append(str(config.topic_target_url or "").strip())
    if str(topic_target_url or "").strip():
        urls.append(str(topic_target_url or "").strip())
    return any(_url_requires_auth(url) for url in urls)


def public_hive_write_enabled(
    config: PublicHiveBridgeConfig | None = None,
    *,
    load_public_hive_bridge_config_fn: Any,
) -> bool:
    cfg = config or load_public_hive_bridge_config_fn()
    if not cfg.enabled or not cfg.meet_seed_urls:
        return False
    if not public_hive_write_requires_auth(cfg):
        return True
    return public_hive_has_auth(cfg)


def _resolve_local_tls_ca_file(tls_ca_file: str | None, *, project_root: str | Path | None = None) -> str | None:
    raw = str(tls_ca_file or "").strip()
    if not raw:
        return None

    root = Path(project_root).expanduser().resolve() if project_root else Path.cwd().resolve()
    candidate = Path(raw).expanduser()
    if candidate.is_absolute() and candidate.is_file():
        return str(candidate.resolve())
    if not candidate.is_absolute():
        rooted_candidate = (root / candidate).resolve()
        if rooted_candidate.is_file():
            return str(rooted_candidate)

    normalized = raw.replace("\\", "/")
    relative_from_config = ""
    marker = "/config/"
    if marker in normalized:
        relative_from_config = "config/" + normalized.split(marker, 1)[1].lstrip("/")

    local_candidates: list[Path] = []
    if relative_from_config:
        local_candidates.append(root / relative_from_config)
    if candidate.name:
        local_candidates.extend(
            [
                root / "config" / "meet_clusters" / "do_ip_first_4node" / "tls" / candidate.name,
                root / "config" / "meet_clusters" / "separated_watch_4node" / "tls" / candidate.name,
                root / "config" / "tls" / candidate.name,
            ]
        )
    for local_path in local_candidates:
        if local_path.is_file():
            return str(local_path.resolve())
    return raw


def _json_env_object(value: str) -> dict[str, str]:
    try:
        raw = json.loads(str(value or "").strip() or "{}")
    except Exception:
        raw = {}
    out: dict[str, str] = {}
    for base, token in dict(raw or {}).items():
        clean_token = _clean_token(str(token or "").strip())
        clean_base = _normalize_base_url(str(base or "").strip())
        if clean_base and clean_token:
            out[clean_base] = clean_token
    return out


def _json_env_write_grants(value: str) -> dict[str, dict[str, dict[str, Any]]]:
    try:
        raw = json.loads(str(value or "").strip() or "{}")
    except Exception:
        raw = {}
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for base_url, routes in dict(raw or {}).items():
        normalized_base = _normalize_base_url(str(base_url or "").strip())
        if not normalized_base or not isinstance(routes, dict):
            continue
        normalized_routes: dict[str, dict[str, Any]] = {}
        for route, grant in dict(routes).items():
            clean_route = str(route or "").rstrip("/") or "/"
            if not clean_route or not isinstance(grant, dict):
                continue
            normalized_routes[clean_route] = dict(grant)
        if normalized_routes:
            out[normalized_base] = normalized_routes
    return out


def _merge_auth_tokens_by_base_url(raw: dict[str, Any]) -> dict[str, str]:
    merged = {
        _normalize_base_url(base): str(token).strip()
        for base, token in dict(raw.get("auth_tokens_by_base_url") or {}).items()
        if str(base).strip() and _clean_token(str(token or "").strip())
    }
    merged.update(_json_env_object(os.environ.get("VOOL_MEET_AUTH_TOKENS_JSON", "")))
    return merged


def _merge_write_grants_by_base_url(raw: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
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


def _clean_token(value: str) -> str | None:
    cleaned = str(value or "").strip()
    if not cleaned or _PLACEHOLDER_TOKEN_RE.search(cleaned):
        return None
    return cleaned


def _url_requires_auth(url: str) -> bool:
    parsed = urlsplit(str(url or "").strip())
    host = str(parsed.hostname or "").strip().lower()
    if host in {"", "localhost", "127.0.0.1", "::1"}:
        return False
    return bool(host)


def _normalize_base_url(url: str) -> str:
    parsed = urlsplit(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return str(url or "").rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), "", "", "")).rstrip("/")
