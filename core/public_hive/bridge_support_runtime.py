from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from core.public_hive import bootstrap as public_hive_bootstrap
from core.runtime_paths import PROJECT_ROOT


def discover_local_cluster_bootstrap(
    *,
    project_root: str | Path | None = None,
    load_json_file_fn: Any,
    clean_token_fn: Any,
    normalize_base_url_fn: Any,
) -> dict[str, Any]:
    return public_hive_bootstrap.discover_local_cluster_bootstrap(
        project_root=project_root,
        project_root_default=PROJECT_ROOT,
        load_json_file_fn=load_json_file_fn,
        clean_token_fn=clean_token_fn,
        normalize_base_url_fn=normalize_base_url_fn,
    )


def find_public_hive_ssh_key(
    project_root: str | Path | None = None,
    *,
    project_root_default: str | Path | None = None,
    env: Any | None = None,
) -> Path | None:
    return public_hive_bootstrap.find_public_hive_ssh_key(
        project_root=project_root,
        project_root_default=project_root_default,
        env=env if env is not None else os.environ,
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
        runner=runner,
        clean_token_fn=clean_token_fn,
        write_public_hive_agent_bootstrap_fn=write_public_hive_agent_bootstrap_fn,
    )
