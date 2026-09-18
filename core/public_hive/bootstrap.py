from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from core.public_hive.config import (
    _clean_token,
    _json_env_object,
    _json_env_write_grants,
    _merge_write_grants_by_base_url,
    _normalize_base_url,
    _resolve_local_tls_ca_file,
    public_hive_has_auth,
    public_hive_write_requires_auth,
)
from core.runtime_paths import PROJECT_ROOT, active_config_home_dir, config_path

DEFAULT_PUBLIC_HIVE_REMOTE_CONFIG_PATH = "/etc/vool-hive-mind/watch-config.json"


def _watch_host_from_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return str(urlsplit(raw).hostname or "").strip()
    except Exception:
        return ""


def ensure_public_hive_agent_bootstrap(
    *,
    config_home_dir: str | Path | None = None,
    project_root: str | Path | None = None,
    env: Any | None = None,
    split_csv_fn: Any,
    clean_token_fn: Any = _clean_token,
    json_env_object_fn: Any = _json_env_object,
    json_env_write_grants_fn: Any = _json_env_write_grants,
    load_agent_bootstrap_fn: Any,
    discover_local_cluster_bootstrap_fn: Any,
    merge_write_grants_by_base_url_fn: Any = _merge_write_grants_by_base_url,
) -> Path | None:
    resolved_config_home = Path(config_home_dir).expanduser().resolve() if config_home_dir else active_config_home_dir()
    resolved_project_root = Path(project_root).expanduser().resolve() if project_root else PROJECT_ROOT.resolve()
    environ = env if env is not None else os.environ
    target_path = (resolved_config_home / "agent-bootstrap.json").resolve()
    if target_path.exists():
        return target_path

    seed_urls = split_csv_fn(environ.get("VOOL_MEET_SEED_URLS", ""))
    auth_token = clean_token_fn(str(environ.get("VOOL_MEET_AUTH_TOKEN", "")).strip())
    auth_tokens_by_base_url = json_env_object_fn(environ.get("VOOL_MEET_AUTH_TOKENS_JSON", ""))
    write_grants_by_base_url = json_env_write_grants_fn(environ.get("VOOL_MEET_WRITE_GRANTS_JSON", ""))
    sample = load_agent_bootstrap_fn(include_runtime=False)
    discovered = discover_local_cluster_bootstrap_fn(project_root=resolved_project_root)
    sample_seed_urls = [str(url).strip() for url in list(sample.get("meet_seed_urls") or []) if str(url).strip()]
    resolved_seed_urls = seed_urls or sample_seed_urls or list(discovered.get("meet_seed_urls") or [])
    if not resolved_seed_urls:
        return None

    payload: dict[str, Any] = {
        "home_region": str(
            environ.get("VOOL_HOME_REGION")
            or sample.get("home_region")
            or discovered.get("home_region")
            or "global"
        ).strip()
        or "global",
        "meet_seed_urls": resolved_seed_urls,
        "prefer_home_region_first": bool(sample.get("prefer_home_region_first", True)),
        "cross_region_summary_only": bool(sample.get("cross_region_summary_only", True)),
        "allow_local_fallback": bool(sample.get("allow_local_fallback", True)),
        "keep_local_cache": bool(sample.get("keep_local_cache", True)),
    }
    resolved_tls_ca_file = str(
        environ.get("VOOL_MEET_TLS_CA_FILE")
        or sample.get("tls_ca_file")
        or discovered.get("tls_ca_file")
        or ""
    ).strip()
    if resolved_tls_ca_file:
        payload["tls_ca_file"] = resolved_tls_ca_file
    resolved_tls_insecure = str(environ.get("VOOL_MEET_TLS_INSECURE_SKIP_VERIFY") or "").strip().lower()
    if resolved_tls_insecure in {"1", "true", "yes", "on"}:
        payload["tls_insecure_skip_verify"] = True
    if auth_token:
        payload["auth_token"] = auth_token
    if auth_tokens_by_base_url:
        payload["auth_tokens_by_base_url"] = dict(auth_tokens_by_base_url)
    merged_write_grants = merge_write_grants_by_base_url_fn(sample)
    merged_write_grants.update(write_grants_by_base_url)
    if merged_write_grants:
        payload["write_grants_by_base_url"] = merged_write_grants
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return target_path
    except Exception:
        return None


def load_agent_bootstrap(
    *,
    include_runtime: bool = True,
    agent_bootstrap_paths_fn: Any,
) -> dict[str, Any]:
    candidate_paths = agent_bootstrap_paths_fn(include_runtime=include_runtime)
    for path in candidate_paths:
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


def agent_bootstrap_paths(
    *,
    include_runtime: bool,
    config_path_fn: Any = config_path,
) -> tuple[Path, ...]:
    paths: list[Path] = []
    if include_runtime:
        paths.append(config_path_fn("agent-bootstrap.json"))
    paths.extend(
        [
            config_path_fn("meet_clusters/do_ip_first_4node/agent-bootstrap.sample.json"),
            config_path_fn("meet_clusters/separated_watch_4node/agent-bootstrap.sample.json"),
            config_path_fn("meet_clusters/global_3node/agent-bootstrap.sample.json"),
        ]
    )
    return tuple(paths)


def write_public_hive_agent_bootstrap(
    *,
    target_path: Path | None = None,
    project_root: str | Path | None = None,
    meet_seed_urls: list[str] | tuple[str, ...] | None = None,
    auth_token: str | None = None,
    auth_tokens_by_base_url: dict[str, str] | None = None,
    write_grants_by_base_url: dict[str, dict[str, dict[str, Any]]] | None = None,
    home_region: str | None = None,
    tls_ca_file: str | None = None,
    tls_insecure_skip_verify: bool | None = None,
    config_path_fn: Any = config_path,
    project_root_default: str | Path | None = None,
    load_json_file_fn: Any,
    load_agent_bootstrap_fn: Any,
    discover_local_cluster_bootstrap_fn: Any,
    resolve_local_tls_ca_file_fn: Any = _resolve_local_tls_ca_file,
    normalize_base_url_fn: Any = _normalize_base_url,
    clean_token_fn: Any = _clean_token,
    merge_write_grants_by_base_url_fn: Any = _merge_write_grants_by_base_url,
) -> Path | None:
    destination = (target_path or config_path_fn("agent-bootstrap.json")).resolve()
    default_root = project_root_default if project_root_default is not None else PROJECT_ROOT
    root = Path(project_root).expanduser().resolve() if project_root else Path(default_root).expanduser().resolve()
    existing = load_json_file_fn(destination) if destination.exists() else load_agent_bootstrap_fn(include_runtime=False)
    discovered = discover_local_cluster_bootstrap_fn(project_root=root)
    payload: dict[str, Any] = dict(existing or {})

    resolved_urls = [
        str(url).strip()
        for url in list(meet_seed_urls or payload.get("meet_seed_urls") or discovered.get("meet_seed_urls") or [])
        if str(url).strip()
    ]
    if not resolved_urls:
        return None
    payload["meet_seed_urls"] = resolved_urls
    payload["home_region"] = str(home_region or payload.get("home_region") or discovered.get("home_region") or "global").strip() or "global"
    payload["prefer_home_region_first"] = bool(payload.get("prefer_home_region_first", True))
    payload["cross_region_summary_only"] = bool(payload.get("cross_region_summary_only", True))
    payload["allow_local_fallback"] = bool(payload.get("allow_local_fallback", True))
    payload["keep_local_cache"] = bool(payload.get("keep_local_cache", True))
    resolved_tls_ca_file = resolve_local_tls_ca_file_fn(
        str(tls_ca_file or payload.get("tls_ca_file") or discovered.get("tls_ca_file") or "").strip() or None,
        project_root=root,
    )
    if resolved_tls_ca_file:
        resolved_tls_path = Path(resolved_tls_ca_file).expanduser()
        if resolved_tls_path.is_file():
            try:
                local_tls_path = resolved_tls_path.resolve()
                if destination.is_relative_to(root) and local_tls_path.is_relative_to(root):
                    payload["tls_ca_file"] = local_tls_path.relative_to(root).as_posix()
                else:
                    payload["tls_ca_file"] = str(local_tls_path)
            except Exception:
                payload["tls_ca_file"] = resolved_tls_ca_file
        else:
            payload["tls_ca_file"] = resolved_tls_ca_file
    else:
        payload.pop("tls_ca_file", None)
    if tls_insecure_skip_verify is None:
        resolved_tls_insecure = bool(payload.get("tls_insecure_skip_verify", discovered.get("tls_insecure_skip_verify", False)))
    else:
        resolved_tls_insecure = bool(tls_insecure_skip_verify)
    if resolved_tls_insecure:
        payload["tls_insecure_skip_verify"] = True
    else:
        payload.pop("tls_insecure_skip_verify", None)

    merged_tokens = {
        normalize_base_url_fn(base): str(token).strip()
        for base, token in dict(payload.get("auth_tokens_by_base_url") or {}).items()
        if str(base).strip() and clean_token_fn(str(token or "").strip())
    }
    for base, token in dict(auth_tokens_by_base_url or {}).items():
        normalized = normalize_base_url_fn(str(base or "").strip())
        clean_token = clean_token_fn(str(token or "").strip())
        if normalized and clean_token:
            merged_tokens[normalized] = clean_token
    if merged_tokens:
        payload["auth_tokens_by_base_url"] = merged_tokens
    elif "auth_tokens_by_base_url" in payload:
        payload.pop("auth_tokens_by_base_url", None)

    merged_write_grants = merge_write_grants_by_base_url_fn(payload)
    merged_write_grants.update(dict(discovered.get("write_grants_by_base_url") or {}))
    for base_url, routes in dict(write_grants_by_base_url or {}).items():
        normalized_base = normalize_base_url_fn(str(base_url or "").strip())
        if not normalized_base or not isinstance(routes, dict):
            continue
        normalized_routes = {
            (str(route or "").rstrip("/") or "/"): dict(grant)
            for route, grant in routes.items()
            if str(route or "").strip() and isinstance(grant, dict)
        }
        if normalized_routes:
            merged_write_grants[normalized_base] = normalized_routes
    if merged_write_grants:
        payload["write_grants_by_base_url"] = merged_write_grants
    elif "write_grants_by_base_url" in payload:
        payload.pop("write_grants_by_base_url", None)

    clean_auth_token = clean_token_fn(str(auth_token or "").strip()) or clean_token_fn(str(payload.get("auth_token") or "").strip())
    if clean_auth_token:
        payload["auth_token"] = clean_auth_token
    else:
        payload.pop("auth_token", None)

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return destination
    except Exception:
        return None


def sync_public_hive_auth_from_ssh(
    *,
    ssh_key_path: str,
    project_root: str | Path | None = None,
    watch_host: str = "",
    watch_user: str = "root",
    remote_config_path: str = "",
    target_path: Path | None = None,
    runner: Any | None = None,
    clean_token_fn: Any = _clean_token,
    write_public_hive_agent_bootstrap_fn: Any,
) -> dict[str, Any]:
    key_path = Path(str(ssh_key_path or "").strip()).expanduser().resolve()
    if not key_path.exists():
        raise FileNotFoundError(f"SSH key not found: {key_path}")

    remote_path = str(remote_config_path or "").strip()
    if not remote_path:
        raise ValueError("Remote config path is required.")

    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-i",
        str(key_path),
        "-o",
        "StrictHostKeyChecking=accept-new",
        f"{str(watch_user or 'root').strip() or 'root'}@{str(watch_host or '').strip()}",
        (
            "python3 -c "
            + shlex.quote(
                "import json, pathlib; "
                f"print(json.dumps(json.loads(pathlib.Path({remote_path!r}).read_text(encoding='utf-8'))))"
            )
        ),
    ]
    completed = (runner or subprocess.run)(
        command,
        capture_output=True,
        check=True,
        text=True,
        timeout=12,
    )
    remote_payload = json.loads(str(completed.stdout or "").strip() or "{}")
    auth_token = clean_token_fn(str(remote_payload.get("auth_token") or "").strip())
    if not auth_token:
        raise ValueError("Remote watch config does not contain a valid auth token.")

    seed_urls = [
        str(url).strip()
        for url in list(remote_payload.get("upstream_base_urls") or remote_payload.get("meet_seed_urls") or [])
        if str(url).strip()
    ]
    written = write_public_hive_agent_bootstrap_fn(
        target_path=target_path,
        project_root=project_root,
        meet_seed_urls=seed_urls,
        auth_token=auth_token,
        write_grants_by_base_url=dict(remote_payload.get("write_grants_by_base_url") or {}),
        tls_ca_file=str(remote_payload.get("tls_ca_file") or "").strip() or None,
        tls_insecure_skip_verify=bool(remote_payload.get("tls_insecure_skip_verify", False)),
    )
    if written is None:
        raise RuntimeError("Failed to write runtime agent-bootstrap.json")
    return {
        "path": str(written),
        "watch_host": str(watch_host or "").strip(),
        "seed_count": len(seed_urls),
        "auth_loaded": True,
    }


def _build_public_hive_auth_suggestion(
    *,
    watch_host: str,
    remote_config_path: str,
) -> str:
    host = str(watch_host or "").strip()
    remote_path = str(remote_config_path or "").strip()
    if not host:
        return ""
    command = [
        "python",
        "-m",
        "ops.ensure_public_hive_auth",
        "--watch-host",
        host,
    ]
    if remote_path:
        command.extend(["--remote-config-path", remote_path])
    return " ".join(shlex.quote(part) for part in command)


def find_public_hive_ssh_key(
    project_root: str | Path | None = None,
    *,
    project_root_default: str | Path | None = None,
    env: Any | None = None,
) -> Path | None:
    default_root = project_root_default if project_root_default is not None else PROJECT_ROOT
    root = Path(project_root).expanduser().resolve() if project_root else Path(default_root).expanduser().resolve()
    environ = env if env is not None else os.environ
    seen: set[Path] = set()
    env_path = str(environ.get("VOOL_PUBLIC_HIVE_SSH_KEY_PATH") or "").strip()
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.extend(
        [
            root / "ssh" / "vool-ssh" / "vool_do_ed25519_v2",
            root.parent / "ssh" / "vool-ssh" / "vool_do_ed25519_v2",
            Path.home() / ".ssh" / "vool_do_ed25519_v2",
            Path.home() / ".ssh" / "vool_do_ed25519",
            Path.home() / "Desktop" / "ssh" / "vool-ssh" / "vool_do_ed25519_v2",
        ]
    )
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    return None


def ensure_public_hive_auth(
    *,
    project_root: str | Path | None = None,
    target_path: Path | None = None,
    watch_host: str | None = None,
    watch_user: str = "root",
    remote_config_path: str = "",
    require_auth: bool = False,
    env: Any | None = None,
    project_root_default: str | Path | None = None,
    config_home_dir: str | Path | None = None,
    load_json_file_fn: Any,
    discover_local_cluster_bootstrap_fn: Any,
    load_agent_bootstrap_fn: Any,
    clean_token_fn: Any = _clean_token,
    json_env_object_fn: Any = _json_env_object,
    normalize_base_url_fn: Any = _normalize_base_url,
    public_hive_has_auth_fn: Any = public_hive_has_auth,
    public_hive_write_requires_auth_fn: Any = public_hive_write_requires_auth,
    write_public_hive_agent_bootstrap_fn: Any,
    find_public_hive_ssh_key_fn: Any,
    sync_public_hive_auth_from_ssh_fn: Any,
) -> dict[str, Any]:
    default_root = project_root_default if project_root_default is not None else PROJECT_ROOT
    resolved_config_home = Path(config_home_dir).expanduser().resolve() if config_home_dir else active_config_home_dir()
    root = Path(project_root).expanduser().resolve() if project_root else Path(default_root).expanduser().resolve()
    destination = (target_path or (resolved_config_home / "agent-bootstrap.json")).expanduser().resolve()
    environ = env if env is not None else os.environ
    existing = load_json_file_fn(destination) if destination.exists() else {}
    bundled = load_json_file_fn(root / "config" / "agent-bootstrap.json")
    discovered = discover_local_cluster_bootstrap_fn(project_root=root)
    sample = load_agent_bootstrap_fn(include_runtime=False)
    env_auth_token = clean_token_fn(str(environ.get("VOOL_MEET_AUTH_TOKEN", "")).strip())
    env_auth_tokens_by_base_url = json_env_object_fn(environ.get("VOOL_MEET_AUTH_TOKENS_JSON", ""))

    seed_urls = [
        str(url).strip()
        for url in list(
            existing.get("meet_seed_urls")
            or bundled.get("meet_seed_urls")
            or discovered.get("meet_seed_urls")
            or sample.get("meet_seed_urls")
            or []
        )
        if str(url).strip()
    ]
    if not seed_urls:
        return {"ok": True, "status": "disabled", "seed_count": 0, "target_path": str(destination)}

    merged_auth_tokens: dict[str, str] = {}
    for payload in (bundled, discovered, existing):
        for base, token in dict(payload.get("auth_tokens_by_base_url") or {}).items():
            normalized = normalize_base_url_fn(str(base or "").strip())
            clean_token = clean_token_fn(str(token or "").strip())
            if normalized and clean_token:
                merged_auth_tokens[normalized] = clean_token
    merged_auth_tokens.update(env_auth_tokens_by_base_url)
    merged_write_grants: dict[str, dict[str, dict[str, Any]]] = {}
    for payload in (sample, discovered, bundled, existing):
        for base_url, routes in dict(payload.get("write_grants_by_base_url") or {}).items():
            normalized_base = normalize_base_url_fn(str(base_url or "").strip())
            if not normalized_base or not isinstance(routes, dict):
                continue
            normalized_routes = {
                (str(route or "").rstrip("/") or "/"): dict(grant)
                for route, grant in routes.items()
                if str(route or "").strip() and isinstance(grant, dict)
            }
            if normalized_routes:
                merged_write_grants[normalized_base] = normalized_routes
    auth_token = (
        env_auth_token
        or clean_token_fn(str(existing.get("auth_token") or "").strip())
        or clean_token_fn(str(discovered.get("auth_token") or "").strip())
        or clean_token_fn(str(bundled.get("auth_token") or "").strip())
    )
    home_region = (
        str(existing.get("home_region") or "").strip()
        or str(bundled.get("home_region") or "").strip()
        or str(discovered.get("home_region") or "").strip()
        or str(sample.get("home_region") or "").strip()
        or "global"
    )
    tls_ca_file = (
        str(existing.get("tls_ca_file") or "").strip()
        or str(bundled.get("tls_ca_file") or "").strip()
        or str(discovered.get("tls_ca_file") or "").strip()
        or str(sample.get("tls_ca_file") or "").strip()
        or None
    )
    tls_insecure_skip_verify = bool(
        existing.get("tls_insecure_skip_verify")
        or bundled.get("tls_insecure_skip_verify")
        or discovered.get("tls_insecure_skip_verify")
        or sample.get("tls_insecure_skip_verify")
    )
    resolved_watch_host = (
        str(watch_host or "").strip()
        or str(environ.get("VOOL_PUBLIC_HIVE_WATCH_HOST") or "").strip()
        or str(existing.get("watch_host") or "").strip()
        or str(bundled.get("watch_host") or "").strip()
        or str(discovered.get("watch_host") or "").strip()
        or str(sample.get("watch_host") or "").strip()
    )
    suggested_remote_config_path = str(
        environ.get("VOOL_PUBLIC_HIVE_REMOTE_CONFIG")
        or remote_config_path
        or DEFAULT_PUBLIC_HIVE_REMOTE_CONFIG_PATH
    ).strip() or DEFAULT_PUBLIC_HIVE_REMOTE_CONFIG_PATH

    if auth_token or merged_auth_tokens:
        written = write_public_hive_agent_bootstrap_fn(
            target_path=destination,
            project_root=root,
            meet_seed_urls=seed_urls,
            auth_token=auth_token,
            auth_tokens_by_base_url=merged_auth_tokens,
            write_grants_by_base_url=merged_write_grants,
            home_region=home_region,
            tls_ca_file=tls_ca_file,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
        )
        if public_hive_has_auth_fn(payload=existing):
            status = "already_configured"
        elif public_hive_has_auth_fn(payload=discovered):
            status = "hydrated_from_local_cluster"
        else:
            status = "hydrated_from_bundle"
        return {
            "ok": written is not None,
            "status": status,
            "seed_count": len(seed_urls),
            "target_path": str(written or destination),
            "auth_loaded": True,
        }

    requires_auth = public_hive_write_requires_auth_fn(seed_urls=seed_urls)
    if not requires_auth:
        written = write_public_hive_agent_bootstrap_fn(
            target_path=destination,
            project_root=root,
            meet_seed_urls=seed_urls,
            auth_tokens_by_base_url=merged_auth_tokens,
            write_grants_by_base_url=merged_write_grants,
            home_region=home_region,
            tls_ca_file=tls_ca_file,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
        )
        return {
            "ok": written is not None,
            "status": "no_auth_required",
            "seed_count": len(seed_urls),
            "target_path": str(written or destination),
            "auth_loaded": False,
        }

    ssh_key = find_public_hive_ssh_key_fn(root)
    if ssh_key is None:
        return {
            "ok": False,
            "status": "missing_ssh_key" if not require_auth else "auth_required",
            "seed_count": len(seed_urls),
            "target_path": str(destination),
            "requires_auth": True,
            "watch_host": resolved_watch_host,
            "suggested_remote_config_path": suggested_remote_config_path,
            "suggested_command": _build_public_hive_auth_suggestion(
                watch_host=resolved_watch_host,
                remote_config_path=suggested_remote_config_path,
            ),
        }

    resolved_remote_config_path = str(
        remote_config_path
        or environ.get("VOOL_PUBLIC_HIVE_REMOTE_CONFIG")
        or suggested_remote_config_path
    ).strip()
    if not resolved_remote_config_path:
        return {
            "ok": False,
            "status": "missing_remote_config_path" if not require_auth else "auth_required",
            "seed_count": len(seed_urls),
            "target_path": str(destination),
            "requires_auth": True,
            "watch_host": resolved_watch_host,
            "suggested_remote_config_path": suggested_remote_config_path,
            "suggested_command": _build_public_hive_auth_suggestion(
                watch_host=resolved_watch_host,
                remote_config_path=suggested_remote_config_path,
            ),
        }

    if not resolved_watch_host:
        return {
            "ok": False,
            "status": "missing_watch_host" if not require_auth else "auth_required",
            "seed_count": len(seed_urls),
            "target_path": str(destination),
            "requires_auth": True,
            "suggested_remote_config_path": suggested_remote_config_path,
        }

    try:
        sync_result = sync_public_hive_auth_from_ssh_fn(
            ssh_key_path=str(ssh_key),
            project_root=root,
            watch_host=resolved_watch_host,
            watch_user=str(watch_user or "root").strip() or "root",
            remote_config_path=resolved_remote_config_path,
            target_path=destination,
        )
    except Exception as exc:
        return {
            "ok": False,
            "status": "sync_failed" if not require_auth else "auth_required",
            "seed_count": len(seed_urls),
            "target_path": str(destination),
            "requires_auth": True,
            "watch_host": resolved_watch_host,
            "suggested_remote_config_path": suggested_remote_config_path,
            "suggested_command": _build_public_hive_auth_suggestion(
                watch_host=resolved_watch_host,
                remote_config_path=resolved_remote_config_path,
            ),
            "error": str(exc),
        }
    sync_result["ok"] = True
    sync_result["status"] = "synced_from_ssh"
    sync_result["ssh_key_path"] = str(ssh_key)
    return sync_result


def discover_local_cluster_bootstrap(
    *,
    project_root: str | Path | None = None,
    project_root_default: str | Path | None = None,
    load_json_file_fn: Any,
    clean_token_fn: Any = _clean_token,
    normalize_base_url_fn: Any = _normalize_base_url,
) -> dict[str, Any]:
    default_root = project_root_default if project_root_default is not None else PROJECT_ROOT
    root = Path(project_root).expanduser().resolve() if project_root else Path(default_root).expanduser().resolve()
    cluster_dirs = ("do_ip_first_4node", "separated_watch_4node")
    region_map = {
        "seed-eu-1.json": "eu",
        "seed-us-1.json": "us",
        "seed-apac-1.json": "apac",
    }
    selected_cluster = ""
    selected_watch_urls: list[str] = []
    selected_watch_auth_token = ""
    discovered_urls: list[str] = []
    discovered_tokens: dict[str, str] = {}
    selected_urls_by_region: dict[str, str] = {}
    region_token_candidates: dict[str, str] = {}
    discovered_home_region = ""
    discovered_tls_ca_file = ""
    discovered_tls_insecure_skip_verify = False
    discovered_watch_host = ""

    for cluster_dir in cluster_dirs:
        raw = load_json_file_fn(root / "config" / "meet_clusters" / cluster_dir / "watch-edge-1.json")
        auth_token = clean_token_fn(str(raw.get("auth_token") or "").strip())
        upstream = [str(url).strip() for url in list(raw.get("upstream_base_urls") or []) if str(url).strip()]
        tls_ca_file = str(raw.get("tls_ca_file") or "").strip()
        tls_insecure_skip_verify = bool(raw.get("tls_insecure_skip_verify", False))
        watch_host = _watch_host_from_url(str(raw.get("public_url") or raw.get("public_base_url") or "").strip())
        if upstream or auth_token or tls_ca_file or tls_insecure_skip_verify:
            if not selected_cluster:
                selected_cluster = cluster_dir
                selected_watch_urls = upstream
                selected_watch_auth_token = auth_token or ""
            if watch_host and not discovered_watch_host:
                discovered_watch_host = watch_host
            if tls_ca_file and not discovered_tls_ca_file:
                discovered_tls_ca_file = tls_ca_file
            if tls_insecure_skip_verify:
                discovered_tls_insecure_skip_verify = True

    for cluster_dir in cluster_dirs:
        for filename, region in region_map.items():
            raw = load_json_file_fn(root / "config" / "meet_clusters" / cluster_dir / filename)
            public_base_url = str(raw.get("public_base_url") or "").strip()
            auth_token = clean_token_fn(str(raw.get("auth_token") or "").strip())
            tls_ca_file = str(raw.get("tls_ca_file") or "").strip()
            tls_insecure_skip_verify = bool(raw.get("tls_insecure_skip_verify", False))
            if cluster_dir == selected_cluster and public_base_url and region not in selected_urls_by_region:
                selected_urls_by_region[region] = public_base_url
            if auth_token and (region not in region_token_candidates or cluster_dir == selected_cluster):
                region_token_candidates[region] = auth_token
                if not discovered_home_region:
                    discovered_home_region = region
            if tls_ca_file and not discovered_tls_ca_file:
                discovered_tls_ca_file = tls_ca_file
            if tls_insecure_skip_verify:
                discovered_tls_insecure_skip_verify = True

    ordered_regions = [region_map[name] for name in region_map]
    region_by_selected_url = {
        normalize_base_url_fn(url): region
        for region, url in selected_urls_by_region.items()
        if url
    }

    if selected_watch_urls:
        discovered_urls = [str(url).strip() for url in selected_watch_urls if str(url).strip()]
        for idx, url in enumerate(discovered_urls):
            normalized = normalize_base_url_fn(url)
            region = region_by_selected_url.get(normalized)
            if not region and idx < len(ordered_regions):
                region = ordered_regions[idx]
            token = region_token_candidates.get(str(region or "").strip())
            if token:
                discovered_tokens[normalized] = token
    else:
        for region in ordered_regions:
            url = str(selected_urls_by_region.get(region) or "").strip()
            if not url:
                continue
            discovered_urls.append(url)
            token = region_token_candidates.get(region)
            if token:
                discovered_tokens[normalize_base_url_fn(url)] = token

    payload = {}
    if discovered_urls:
        payload["meet_seed_urls"] = discovered_urls
    if discovered_tokens:
        payload["auth_tokens_by_base_url"] = discovered_tokens
        if len(set(discovered_tokens.values())) == 1 and not selected_watch_auth_token:
            payload["auth_token"] = next(iter(discovered_tokens.values()))
    if selected_watch_auth_token:
        payload["auth_token"] = selected_watch_auth_token
    if discovered_home_region:
        payload["home_region"] = discovered_home_region
    if discovered_tls_ca_file:
        payload["tls_ca_file"] = discovered_tls_ca_file
    if discovered_tls_insecure_skip_verify:
        payload["tls_insecure_skip_verify"] = True
    if discovered_watch_host:
        payload["watch_host"] = discovered_watch_host
    return payload
