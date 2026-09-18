from __future__ import annotations

from pathlib import Path

from . import auth as public_hive_auth


def ensure_public_hive_auth(
    *,
    project_root: str | Path | None = None,
    target_path: Path | None = None,
    watch_host: str | None = None,
    watch_user: str = "root",
    remote_config_path: str = "",
    require_auth: bool = False,
    load_json_file_fn,
    discover_local_cluster_bootstrap_fn,
    load_agent_bootstrap_fn,
    clean_token_fn,
    json_env_object_fn,
    normalize_base_url_fn,
    public_hive_has_auth_fn,
    public_hive_write_requires_auth_fn,
    write_public_hive_agent_bootstrap_fn,
    find_public_hive_ssh_key_fn,
    sync_public_hive_auth_from_ssh_fn,
) -> dict[str, object]:
    return public_hive_auth.ensure_public_hive_auth(
        project_root=project_root,
        target_path=target_path,
        watch_host=watch_host,
        watch_user=watch_user,
        remote_config_path=remote_config_path,
        require_auth=require_auth,
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
