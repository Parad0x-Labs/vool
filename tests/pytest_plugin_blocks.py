"""Third-party pytest plugins VOOL's test sessions never load, and how child sessions inherit that.

pyproject addopts blocks them for a session started in this repository, and the gate's children
(ops/pytest_manifest.py, ops/pytest_execution.py) block them in their own arguments. A child pytest
session that a test starts in a scratch directory reads neither, so it gets the block through
PYTEST_ADDOPTS, set by the root conftest.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, MutableMapping

#: anchorpy (installed by the optional `pay` extra, through Solana pay-kit) registers this plugin. It
#: imports pytest-asyncio and pytest-xprocess and stops any pytest run at startup without them; VOOL
#: uses none of it.
BLOCKED_PLUGINS = ("pytest_anchorpy",)


def installed_pytest_plugins() -> set[str]:
    from importlib.metadata import entry_points

    return {entry_point.name for entry_point in entry_points(group="pytest11")}


def block_for_child_sessions(
    environ: MutableMapping[str, str] | None = None, *, installed: Iterable[str] | None = None
) -> None:
    """Add ``-p no:<plugin>`` to PYTEST_ADDOPTS for each blocked plugin that is installed. Without one
    the environment is untouched, so a run without the `pay` extra is exactly what it was."""
    environ = os.environ if environ is None else environ
    present = set(installed_pytest_plugins() if installed is None else installed)
    current = environ.get("PYTEST_ADDOPTS", "")
    blocks = [f"-p no:{name}" for name in BLOCKED_PLUGINS if name in present and f"no:{name}" not in current]
    if blocks:
        environ["PYTEST_ADDOPTS"] = " ".join([current, *blocks]).strip()
