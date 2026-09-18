from __future__ import annotations

from pathlib import Path
from typing import Any

from . import auth as public_hive_auth


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
    config_path_fn=None,
    project_root_default: Path | None = None,
    load_json_file_fn=None,
    load_agent_bootstrap_fn=None,
    discover_local_cluster_bootstrap_fn=None,
    resolve_local_tls_ca_file_fn=None,
    normalize_base_url_fn=None,
    clean_token_fn=None,
    merge_write_grants_by_base_url_fn=None,
) -> Path | None:
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
        config_path_fn=config_path_fn,
        project_root_default=project_root_default,
        load_json_file_fn=load_json_file_fn,
        load_agent_bootstrap_fn=load_agent_bootstrap_fn,
        discover_local_cluster_bootstrap_fn=discover_local_cluster_bootstrap_fn,
        resolve_local_tls_ca_file_fn=resolve_local_tls_ca_file_fn,
        normalize_base_url_fn=normalize_base_url_fn,
        clean_token_fn=clean_token_fn,
        merge_write_grants_by_base_url_fn=merge_write_grants_by_base_url_fn,
    )
