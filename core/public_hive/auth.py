from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from core.public_hive import bootstrap as public_hive_bootstrap
from core.public_hive import config as public_hive_config
from core.public_hive.config import PublicHiveBridgeConfig
from core.runtime_paths import PROJECT_ROOT, active_config_home_dir, config_path


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
    config_path_fn: Any = config_path,
    project_root: str | Path | None = None,
    env: Any | None = None,
) -> PublicHiveBridgeConfig:
    return public_hive_config.load_public_hive_bridge_config(
        ensure_public_hive_agent_bootstrap_fn=ensure_public_hive_agent_bootstrap_fn,
        load_json_file_fn=load_json_file_fn,
        load_agent_bootstrap_fn=load_agent_bootstrap_fn,
        discover_local_cluster_bootstrap_fn=discover_local_cluster_bootstrap_fn,
        split_csv_fn=split_csv_fn,
        json_env_object_fn=json_env_object_fn,
        merge_auth_tokens_by_base_url_fn=merge_auth_tokens_by_base_url_fn,
        json_env_write_grants_fn=json_env_write_grants_fn,
        merge_write_grants_by_base_url_fn=merge_write_grants_by_base_url_fn,
        clean_token_fn=clean_token_fn,
        config_path_fn=config_path_fn,
        project_root=project_root or PROJECT_ROOT,
        env=env if env is not None else os.environ,
    )


def ensure_public_hive_agent_bootstrap(
    *,
    config_home_dir: str | Path | None = None,
    project_root: str | Path | None = None,
    env: Any | None = None,
    split_csv_fn: Any,
    clean_token_fn: Any,
    json_env_object_fn: Any,
    json_env_write_grants_fn: Any,
    load_agent_bootstrap_fn: Any,
    discover_local_cluster_bootstrap_fn: Any,
    merge_write_grants_by_base_url_fn: Any,
) -> Path | None:
    return public_hive_bootstrap.ensure_public_hive_agent_bootstrap(
        config_home_dir=config_home_dir or active_config_home_dir(),
        project_root=project_root or PROJECT_ROOT,
        env=env if env is not None else os.environ,
        split_csv_fn=split_csv_fn,
        clean_token_fn=clean_token_fn,
        json_env_object_fn=json_env_object_fn,
        json_env_write_grants_fn=json_env_write_grants_fn,
        load_agent_bootstrap_fn=load_agent_bootstrap_fn,
        discover_local_cluster_bootstrap_fn=discover_local_cluster_bootstrap_fn,
        merge_write_grants_by_base_url_fn=merge_write_grants_by_base_url_fn,
    )


def load_agent_bootstrap(*, include_runtime: bool = True, agent_bootstrap_paths_fn: Any) -> dict[str, Any]:
    return public_hive_bootstrap.load_agent_bootstrap(
        include_runtime=include_runtime,
        agent_bootstrap_paths_fn=agent_bootstrap_paths_fn,
    )


def agent_bootstrap_paths(*, include_runtime: bool, config_path_fn: Any = config_path) -> tuple[Path, ...]:
    return public_hive_bootstrap.agent_bootstrap_paths(
        include_runtime=include_runtime,
        config_path_fn=config_path_fn,
    )


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
    resolve_local_tls_ca_file_fn: Any,
    normalize_base_url_fn: Any,
    clean_token_fn: Any,
    merge_write_grants_by_base_url_fn: Any,
) -> Path | None:
    return public_hive_bootstrap.write_public_hive_agent_bootstrap(
        target_path=target_path,
        project_root=project_root,
        meet_seed_urls=meet_seed_urls,
        auth_token=auth_token,
        auth_tokens_by_base_url=auth_tokens_by_base_url,
        write_grants_by_base_url=write_grants_by_base_url,
        home_region=home_region,
        tls_ca_file=tls_ca_file,
        tls_insecure_skip_verify=tls_insecure_skip_verify,
        config_path_fn=config_path_fn,
        project_root_default=project_root_default or PROJECT_ROOT,
        load_json_file_fn=load_json_file_fn,
        load_agent_bootstrap_fn=load_agent_bootstrap_fn,
        discover_local_cluster_bootstrap_fn=discover_local_cluster_bootstrap_fn,
        resolve_local_tls_ca_file_fn=resolve_local_tls_ca_file_fn,
        normalize_base_url_fn=normalize_base_url_fn,
        clean_token_fn=clean_token_fn,
        merge_write_grants_by_base_url_fn=merge_write_grants_by_base_url_fn,
    )


def sync_public_hive_auth_from_ssh(
    *,
    ssh_key_path: str,
    project_root: str | Path | None = None,
    watch_host: str = "",
    watch_user: str = "root",
    remote_config_path: str = "",
    target_path: Path | None = None,
    runner: Any | None = None,
    clean_token_fn: Any,
    write_public_hive_agent_bootstrap_fn: Any,
) -> dict[str, Any]:
    return public_hive_bootstrap.sync_public_hive_auth_from_ssh(
        ssh_key_path=ssh_key_path,
        project_root=project_root,
        watch_host=watch_host,
        watch_user=watch_user,
        remote_config_path=remote_config_path,
        target_path=target_path,
        runner=runner or subprocess.run,
        clean_token_fn=clean_token_fn,
        write_public_hive_agent_bootstrap_fn=write_public_hive_agent_bootstrap_fn,
    )


def public_hive_has_auth(config: PublicHiveBridgeConfig | None = None, *, payload: dict[str, Any] | None = None) -> bool:
    return public_hive_config.public_hive_has_auth(config, payload=payload)


def public_hive_write_requires_auth(
    config: PublicHiveBridgeConfig | None = None,
    *,
    seed_urls: list[str] | tuple[str, ...] | None = None,
    topic_target_url: str | None = None,
) -> bool:
    return public_hive_config.public_hive_write_requires_auth(
        config,
        seed_urls=seed_urls,
        topic_target_url=topic_target_url,
    )


def public_hive_write_enabled(
    config: PublicHiveBridgeConfig | None = None,
    *,
    load_public_hive_bridge_config_fn: Any,
) -> bool:
    return public_hive_config.public_hive_write_enabled(
        config,
        load_public_hive_bridge_config_fn=load_public_hive_bridge_config_fn,
    )


def resolve_local_tls_ca_file(tls_ca_file: str | None, *, project_root: str | Path | None = None) -> str | None:
    return public_hive_config._resolve_local_tls_ca_file(tls_ca_file, project_root=project_root or PROJECT_ROOT)


def find_public_hive_ssh_key(project_root: str | Path | None = None) -> Path | None:
    return public_hive_bootstrap.find_public_hive_ssh_key(
        project_root=project_root,
        project_root_default=PROJECT_ROOT,
        env=os.environ,
    )


def ensure_public_hive_auth(
    *,
    project_root: str | Path | None = None,
    target_path: Path | None = None,
    watch_host: str | None = None,
    watch_user: str = "root",
    remote_config_path: str = "",
    require_auth: bool = False,
    load_json_file_fn: Any,
    discover_local_cluster_bootstrap_fn: Any,
    load_agent_bootstrap_fn: Any,
    clean_token_fn: Any,
    json_env_object_fn: Any,
    normalize_base_url_fn: Any,
    public_hive_has_auth_fn: Any,
    public_hive_write_requires_auth_fn: Any,
    write_public_hive_agent_bootstrap_fn: Any,
    find_public_hive_ssh_key_fn: Any,
    sync_public_hive_auth_from_ssh_fn: Any,
) -> dict[str, Any]:
    return public_hive_bootstrap.ensure_public_hive_auth(
        project_root=project_root,
        target_path=target_path,
        watch_host=watch_host,
        watch_user=watch_user,
        remote_config_path=remote_config_path,
        require_auth=require_auth,
        env=os.environ,
        project_root_default=PROJECT_ROOT,
        config_home_dir=active_config_home_dir(),
        load_json_file_fn=load_json_file_fn,
        discover_local_cluster_bootstrap_fn=discover_local_cluster_bootstrap_fn,
        load_agent_bootstrap_fn=load_agent_bootstrap_fn,
        clean_token_fn=clean_token_fn,
        json_env_object_fn=json_env_object_fn,
        normalize_base_url_fn=normalize_base_url_fn,
        public_hive_has_auth_fn=public_hive_has_auth_fn,
        public_hive_write_requires_auth_fn=public_hive_write_requires_auth_fn,
        write_public_hive_agent_bootstrap_fn=write_public_hive_agent_bootstrap_fn,
        find_public_hive_ssh_key_fn=find_public_hive_ssh_key_fn,
        sync_public_hive_auth_from_ssh_fn=sync_public_hive_auth_from_ssh_fn,
    )


def discover_local_cluster_bootstrap(*, project_root: str | Path | None = None, load_json_file_fn: Any, clean_token_fn: Any, normalize_base_url_fn: Any) -> dict[str, Any]:
    return public_hive_bootstrap.discover_local_cluster_bootstrap(
        project_root=project_root,
        project_root_default=PROJECT_ROOT,
        load_json_file_fn=load_json_file_fn,
        clean_token_fn=clean_token_fn,
        normalize_base_url_fn=normalize_base_url_fn,
    )


def split_csv(value: str) -> list[str]:
    return public_hive_config._split_csv(value)


def load_json_file(path: Path) -> dict[str, Any]:
    return public_hive_config._load_json_file(path)


def json_env_object(value: str) -> dict[str, str]:
    return public_hive_config._json_env_object(value)


def json_env_write_grants(value: str) -> dict[str, dict[str, dict[str, Any]]]:
    return public_hive_config._json_env_write_grants(value)


def merge_auth_tokens_by_base_url(raw: dict[str, Any]) -> dict[str, str]:
    return public_hive_config._merge_auth_tokens_by_base_url(raw)


def merge_write_grants_by_base_url(raw: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    return public_hive_config._merge_write_grants_by_base_url(raw)


def clean_token(value: str) -> str | None:
    return public_hive_config._clean_token(value)


def url_requires_auth(url: str) -> bool:
    return public_hive_config._url_requires_auth(url)


def normalize_base_url(url: str) -> str:
    return public_hive_config._normalize_base_url(url)
