from __future__ import annotations

from pathlib import Path
from typing import Any

from core.runtime_paths import PROJECT_ROOT, config_path

from . import auth as public_hive_auth
from . import bridge_support as _public_hive_bridge_support
from .bridge_facade_compat_shared import compat_module


def write_public_hive_agent_bootstrap_impl(
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
) -> Path | None:
    compat = compat_module()
    return public_hive_auth.write_public_hive_agent_bootstrap(
        target_path=target_path,
        project_root=project_root,
        meet_seed_urls=meet_seed_urls,
        auth_token=auth_token,
        auth_tokens_by_base_url=auth_tokens_by_base_url,
        write_grants_by_base_url=write_grants_by_base_url,
        home_region=home_region,
        tls_ca_file=tls_ca_file,
        tls_insecure_skip_verify=tls_insecure_skip_verify,
        config_path_fn=config_path,
        project_root_default=PROJECT_ROOT,
        load_json_file_fn=compat._load_json_file,
        load_agent_bootstrap_fn=compat._load_agent_bootstrap,
        discover_local_cluster_bootstrap_fn=compat._discover_local_cluster_bootstrap,
        resolve_local_tls_ca_file_fn=compat._resolve_local_tls_ca_file,
        normalize_base_url_fn=compat._normalize_base_url,
        clean_token_fn=compat._clean_token,
        merge_write_grants_by_base_url_fn=compat._merge_write_grants_by_base_url,
    )


def sync_public_hive_auth_from_ssh_impl(
    *,
    ssh_key_path: str,
    project_root: str | Path | None = None,
    watch_host: str = "",
    watch_user: str = "root",
    remote_config_path: str = "",
    target_path: Path | None = None,
    runner: Any | None = None,
) -> dict[str, Any]:
    compat = compat_module()
    return public_hive_auth.sync_public_hive_auth_from_ssh(
        ssh_key_path=ssh_key_path,
        project_root=project_root,
        watch_host=watch_host,
        watch_user=watch_user,
        remote_config_path=remote_config_path,
        target_path=target_path,
        runner=runner or compat.subprocess.run,
        clean_token_fn=compat._clean_token,
        write_public_hive_agent_bootstrap_fn=compat.write_public_hive_agent_bootstrap,
    )


def discover_local_cluster_bootstrap_impl(*, project_root: str | Path | None = None) -> dict[str, Any]:
    compat = compat_module()
    return _public_hive_bridge_support.discover_local_cluster_bootstrap(
        project_root=project_root,
        load_json_file_fn=compat._load_json_file,
        clean_token_fn=compat._clean_token,
        normalize_base_url_fn=compat._normalize_base_url,
    )
