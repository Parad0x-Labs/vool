"""Shared isolation for the native skill library suite.

Every test here drives REAL loaders and the REAL tool door against generated trees — the
repository's own `skills/` packages are read as shipped, never copied or stubbed, while anything
written (plugin trees, workspaces, homes) goes under tmp_path.
"""
from __future__ import annotations

import pytest

from core import runtime_paths


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    """A scratch VOOL_HOME so no test reads or writes the operator's live runtime state."""
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


@pytest.fixture(autouse=True)
def _capability_graph(monkeypatch):
    """The capability graph must be populated for the selection gate to judge declared capabilities.

    `bootstrap_from_registry` is explicit, deterministic and idempotent: every supported builtin
    contract is indexed exactly once. Without it a selection test would confuse "the graph has not
    been built" with "the capability is absent from this runtime" — two different absences.

    The runtime's contract availability is POLICY-relative (`filesystem.allow_read_workspace`,
    `execution.allow_sandbox_execution`, `system.allow_web_fallback`, ...), and a scratch test home
    disables most lanes by default. This suite tests the LIBRARY, so it grants the lanes the
    packages declare — availability-relative refusal is proven separately, by turning a lane off.
    """
    import core.policy_engine as policy_engine
    from core.capability_graph import bootstrap_from_registry

    granted = {
        "filesystem.allow_read_workspace": True,
        "filesystem.allow_write_workspace": True,
        "execution.allow_sandbox_execution": True,
        "system.allow_web_fallback": True,
    }
    real_get = policy_engine.get
    monkeypatch.setattr(
        policy_engine,
        "get",
        lambda path, default=None: granted[path] if path in granted else real_get(path, default),
    )
    bootstrap_from_registry()
    yield


@pytest.fixture()
def plugin_tree(tmp_path, monkeypatch):
    """A temporary installed-plugins root, the way an operator's VOOL_PLUGINS_DIR points at one."""
    root = tmp_path / "plugins-repo"
    (root / "plugins").mkdir(parents=True)
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(root))
    return root
