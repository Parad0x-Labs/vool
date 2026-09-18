from __future__ import annotations

import os
from pathlib import Path

from core.runtime_paths import PROJECT_ROOT, config_path

from . import auth as public_hive_auth
from .bridge_facade_compat_shared import compat_module
from .config import PublicHiveBridgeConfig


def load_public_hive_bridge_config_impl() -> PublicHiveBridgeConfig:
    compat = compat_module()
    return public_hive_auth.load_public_hive_bridge_config(
        ensure_public_hive_agent_bootstrap_fn=compat.ensure_public_hive_agent_bootstrap,
        load_json_file_fn=compat._load_json_file,
        load_agent_bootstrap_fn=compat._load_agent_bootstrap,
        discover_local_cluster_bootstrap_fn=compat._discover_local_cluster_bootstrap,
        split_csv_fn=compat._split_csv,
        json_env_object_fn=compat._json_env_object,
        merge_auth_tokens_by_base_url_fn=compat._merge_auth_tokens_by_base_url,
        json_env_write_grants_fn=compat._json_env_write_grants,
        merge_write_grants_by_base_url_fn=compat._merge_write_grants_by_base_url,
        clean_token_fn=compat._clean_token,
        config_path_fn=config_path,
        project_root=PROJECT_ROOT,
        env=os.environ,
    )


def ensure_public_hive_agent_bootstrap_impl() -> Path | None:
    compat = compat_module()
    return public_hive_auth.ensure_public_hive_agent_bootstrap(
        split_csv_fn=compat._split_csv,
        clean_token_fn=compat._clean_token,
        json_env_object_fn=compat._json_env_object,
        json_env_write_grants_fn=compat._json_env_write_grants,
        load_agent_bootstrap_fn=compat._load_agent_bootstrap,
        discover_local_cluster_bootstrap_fn=compat._discover_local_cluster_bootstrap,
        merge_write_grants_by_base_url_fn=compat._merge_write_grants_by_base_url,
    )
