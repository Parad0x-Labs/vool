from __future__ import annotations

from pathlib import Path
from typing import Any

from core.public_hive import bootstrap as public_hive_bootstrap
from core.runtime_paths import config_path


def load_agent_bootstrap(*, include_runtime: bool = True) -> dict[str, Any]:
    return public_hive_bootstrap.load_agent_bootstrap(
        include_runtime=include_runtime,
        agent_bootstrap_paths_fn=agent_bootstrap_paths,
    )


def agent_bootstrap_paths(*, include_runtime: bool, config_path_fn: Any = config_path) -> tuple[Path, ...]:
    return public_hive_bootstrap.agent_bootstrap_paths(
        include_runtime=include_runtime,
        config_path_fn=config_path_fn,
    )
