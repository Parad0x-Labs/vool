"""Shared fixtures for the Blackbox coverage pack: isolated store, isolated workspace, isolated
capability registry. No test here touches another lane's worktree or the operator's data."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_cas_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh, deterministic CAS keyring per test through the explicit key-file channel (same
    schema, same crypto as the canonical authority path). Sabotage tests replace or unset it."""
    from core.blackbox.coverage.cas_keys import CasKeyring

    keys_file = tmp_path / "cas-keys.json"
    keys_file.write_text(CasKeyring.mint().to_json(), encoding="utf-8")
    monkeypatch.setenv("VOOL_BLACKBOX_CAS_KEYS_FILE", str(keys_file))
    yield keys_file
    monkeypatch.delenv("VOOL_BLACKBOX_CAS_KEYS_FILE", raising=False)


@pytest.fixture(autouse=True)
def _isolated_capability_registry():
    from core.blackbox.coverage import registry as coverage_registry

    coverage_registry.reset_registered()
    yield
    coverage_registry.reset_registered()


@pytest.fixture(autouse=True)
def _isolated_tool_registry():
    from core.tool_registry import reset

    reset()
    yield
    reset()


@pytest.fixture
def store_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "blackbox-store"
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(root))
    from core.blackbox import store as store_module

    store_module.reset_default_store()
    yield root
    store_module.reset_default_store()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root


from tests.blackbox_coverage._ctx import ctx  # noqa: F401  (tests import ctx by package path)
