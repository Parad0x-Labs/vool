from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from core import policy_engine
from core.runtime_install_profiles import active_install_profile_id, install_profile_runs_local_only
from core.runtime_paths import (
    DOCS_DIR,
    PROJECT_CONFIG_DIR,
    PROJECT_ROOT,
    active_vool_home,
    configure_runtime_home,
    resolve_workspace_root,
)
from storage.db import resolve_runtime_db_filename as _resolve_runtime_db
from storage.db import configure_default_db_path


@dataclass(frozen=True)
class RuntimePaths:
    project_root: Path
    runtime_home: Path
    data_dir: Path
    config_home_dir: Path
    docs_dir: Path
    project_config_dir: Path
    workspace_root: Path
    db_path: Path


@dataclass(frozen=True)
class RuntimeFeatureFlags:
    local_only_mode: bool
    public_hive_enabled: bool
    helper_mesh_enabled: bool
    allow_workspace_writes: bool
    allow_sandbox_execution: bool
    allow_remote_only_without_backend: bool


@dataclass(frozen=True)
class RuntimeContext:
    mode: str
    paths: RuntimePaths
    log_level: str
    json_logs: bool
    feature_flags: RuntimeFeatureFlags
    env_overrides: dict[str, str] = field(default_factory=dict)


def build_runtime_context(
    *,
    mode: str,
    workspace_root: str | Path | None = None,
    db_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> RuntimeContext:
    env_map = os.environ if env is None else env
    runtime_home = active_vool_home(env_map)
    data_dir = (runtime_home / "data").resolve()
    config_home_dir = (runtime_home / "config").resolve()
    resolved_workspace = resolve_workspace_root(workspace_root)
    resolved_db_path = (
        Path(db_path).expanduser().resolve()
        if db_path is not None
        else _resolve_runtime_db(data_dir)
    )

    # Policy-backed defaults are read here so entrypoints do not each rediscover
    # the same capability truth and logging policy.
    log_level = str(policy_engine.get("observability.log_level", "INFO"))
    json_logs = bool(policy_engine.get("observability.json_logs", True))
    active_profile = active_install_profile_id(runtime_home=runtime_home, env=env_map)
    profile_local_only = install_profile_runs_local_only(active_profile)
    feature_flags = RuntimeFeatureFlags(
        local_only_mode=bool(policy_engine.local_only_mode()) or profile_local_only,
        public_hive_enabled=str(env_map.get("VOOL_PUBLIC_HIVE_ENABLED", "1")).strip().lower() not in {"0", "false", "no", "off"},
        helper_mesh_enabled=bool(policy_engine.get("assist_mesh.enabled", True)),
        allow_workspace_writes=bool(policy_engine.get("filesystem.allow_write_workspace", False)),
        allow_sandbox_execution=bool(policy_engine.get("execution.allow_sandbox_execution", False)),
        allow_remote_only_without_backend=bool(policy_engine.allow_remote_only_without_backend()) and not profile_local_only,
    )
    env_overrides = {
        key: str(env_map.get(key) or "").strip()
        for key in (
            "VOOL_HOME",
            "VOOL_INSTALL_PROFILE",
            "VOOL_WORKSPACE_ROOT",
            "VOOL_PROJECT_ROOT",
            "VOOL_PUBLIC_HIVE_ENABLED",
            "VOOL_MEET_SEED_URLS",
            "VOOL_MEET_AUTH_TOKEN",
        )
        if str(env_map.get(key) or "").strip()
    }
    return RuntimeContext(
        mode=str(mode or "runtime").strip() or "runtime",
        paths=RuntimePaths(
            project_root=PROJECT_ROOT.resolve(),
            runtime_home=runtime_home,
            data_dir=data_dir,
            config_home_dir=config_home_dir,
            docs_dir=DOCS_DIR.resolve(),
            project_config_dir=PROJECT_CONFIG_DIR.resolve(),
            workspace_root=resolved_workspace,
            db_path=resolved_db_path,
        ),
        log_level=log_level,
        json_logs=json_logs,
        feature_flags=feature_flags,
        env_overrides=env_overrides,
    )


def apply_runtime_context(context: RuntimeContext) -> RuntimeContext:
    configure_runtime_home(context.paths.runtime_home)
    configure_default_db_path(context.paths.db_path)
    return context
