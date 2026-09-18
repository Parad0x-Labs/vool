from __future__ import annotations

from pathlib import Path

from . import auth as public_hive_auth
from .bridge_facade_compat_shared import compat_module
from .config import PublicHiveBridgeConfig


def public_hive_write_enabled_impl(config: PublicHiveBridgeConfig | None = None) -> bool:
    compat = compat_module()
    return public_hive_auth.public_hive_write_enabled(
        config,
        load_public_hive_bridge_config_fn=compat.load_public_hive_bridge_config,
    )


def ensure_public_hive_auth_impl(
    *,
    project_root: str | Path | None = None,
    target_path: Path | None = None,
    watch_host: str | None = None,
    watch_user: str = "root",
    remote_config_path: str = "",
    require_auth: bool = False,
) -> dict[str, object]:
    compat = compat_module()
    return public_hive_auth.ensure_public_hive_auth(
        project_root=project_root,
        target_path=target_path,
        watch_host=watch_host,
        watch_user=watch_user,
        remote_config_path=remote_config_path,
        require_auth=require_auth,
        load_json_file_fn=compat._load_json_file,
        discover_local_cluster_bootstrap_fn=compat._discover_local_cluster_bootstrap,
        load_agent_bootstrap_fn=compat._load_agent_bootstrap,
        clean_token_fn=compat._clean_token,
        json_env_object_fn=compat._json_env_object,
        normalize_base_url_fn=compat._normalize_base_url,
        public_hive_has_auth_fn=compat.public_hive_has_auth,
        public_hive_write_requires_auth_fn=compat.public_hive_write_requires_auth,
        write_public_hive_agent_bootstrap_fn=compat.write_public_hive_agent_bootstrap,
        find_public_hive_ssh_key_fn=compat.find_public_hive_ssh_key,
        sync_public_hive_auth_from_ssh_fn=compat.sync_public_hive_auth_from_ssh,
    )
