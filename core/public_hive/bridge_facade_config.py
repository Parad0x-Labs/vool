from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from . import auth as public_hive_auth
from .config import PublicHiveBridgeConfig


def load_public_hive_bridge_config(
    *,
    ensure_public_hive_agent_bootstrap_fn,
    load_json_file_fn,
    load_agent_bootstrap_fn,
    discover_local_cluster_bootstrap_fn,
    split_csv_fn,
    json_env_object_fn,
    merge_auth_tokens_by_base_url_fn,
    json_env_write_grants_fn,
    merge_write_grants_by_base_url_fn,
    clean_token_fn,
    config_path_fn,
    project_root: Path,
    env: Mapping[str, str],
) -> PublicHiveBridgeConfig:
    return public_hive_auth.load_public_hive_bridge_config(
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
        project_root=project_root,
        env=env,
    )
