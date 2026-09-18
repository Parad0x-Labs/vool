from __future__ import annotations

from pathlib import Path
from typing import Any


def sync_public_hive_auth_from_ssh(
    *,
    ssh_key_path: str,
    project_root: str | Path | None = None,
    watch_host: str = "",
    watch_user: str = "root",
    remote_config_path: str = "",
    target_path: Path | None = None,
    runner: Any | None = None,
    clean_token_fn,
    write_public_hive_agent_bootstrap_fn,
    sync_public_hive_auth_from_ssh_impl,
) -> dict[str, Any]:
    return sync_public_hive_auth_from_ssh_impl(
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
