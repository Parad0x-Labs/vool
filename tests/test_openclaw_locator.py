"""Retirement non-interference contracts for third-party OpenClaw state.

The locator module (and the registration bridge behind it) is retired: VOOL product
paths must neither import it nor read any third-party OpenClaw state, and cleanup
tooling must stay precise to VOOL-owned artifacts. These contracts replaced this
suite's original locator-behavior tests when the module was deleted.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PRODUCT_TREES = ("core", "installer", "apps", "ops", "scripts", "tools", "relay", "adapters")

# Third-party state-locator env names: reading any of them from product code would
# reintroduce a dependency on a foreign OpenClaw installation.
STATE_ENV_NAMES = ("OPENCLAW_CONFIG_PATH", "OPENCLAW_HOME", "OPENCLAW_STATE_DIR")


def test_openclaw_locator_module_is_retired() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("core.openclaw_locator")


def test_no_product_python_references_the_retired_locator() -> None:
    offenders: list[str] = []
    for tree in PRODUCT_TREES:
        for path in (PROJECT_ROOT / tree).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "openclaw_locator" in text:
                offenders.append(path.relative_to(PROJECT_ROOT).as_posix())
    assert offenders == [], f"retired locator referenced from product code: {offenders}"


def test_no_product_python_reads_third_party_openclaw_state_env() -> None:
    offenders: list[str] = []
    for tree in PRODUCT_TREES:
        for path in (PROJECT_ROOT / tree).rglob("*.py"):
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if line.strip().startswith("#"):
                    continue
                for name in STATE_ENV_NAMES:
                    if name in line:
                        offenders.append(f"{path.relative_to(PROJECT_ROOT).as_posix()}:{lineno}: {line.strip()[:100]}")
    assert offenders == [], f"product code reads third-party OpenClaw state: {offenders}"


def test_uninstaller_never_removes_the_users_whole_openclaw_tree() -> None:
    """The local uninstaller may trash VOOL-owned bridge artifacts, but it must never
    delete the user's entire ~/.openclaw tree or any OpenClaw file it did not create."""
    uninstaller = (PROJECT_ROOT / "installer" / "uninstall_vool_local.sh").read_text(encoding="utf-8")

    assert 'rm -rf "${HOME}/.openclaw"' not in uninstaller
    assert '"${OPENCLAW_HOME}"' in uninstaller  # only the VOOL-created isolated home
    # The agent-dir target is the VOOL bridge directory, not the OpenClaw root.
    assert 'OPENCLAW_AGENT_DIR="${VOOL_OPENCLAW_AGENT_DIR:-$HOME/.openclaw/agents/main/agent/vool}"' in uninstaller
    assert 'remove_targets' in uninstaller
