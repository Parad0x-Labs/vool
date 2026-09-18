from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

from core.runtime_paths import (
    PROJECT_ROOT,
    active_vool_home,
    active_workspace_dir,
    discover_installed_runtime_home,
    resolve_workspace_root,
)


def test_resolve_workspace_root_prefers_explicit_path(tmp_path: Path) -> None:
    explicit = tmp_path / "workspace"

    resolved = resolve_workspace_root(explicit)

    assert resolved == explicit.resolve()


def test_resolve_workspace_root_honors_environment_overrides(tmp_path: Path) -> None:
    workspace = tmp_path / "env-workspace"
    workspace.mkdir()
    with mock.patch.dict("os.environ", {"VOOL_WORKSPACE_ROOT": str(workspace)}, clear=False):
        resolved = resolve_workspace_root()

    assert resolved == workspace.resolve()


def test_resolve_workspace_root_never_falls_back_to_the_packaged_project_root(tmp_path: Path) -> None:
    """The writable-state law: a packaged runtime (VOOL_PROJECT_ROOT -> the .app bundle)
    resolves its workspace UNDER THE ACTIVE VOOL HOME, never inside the bundle. The old
    assertion (resolved == project_root) pinned exactly the bundle-immutability bug that
    `test_packaged_bundle_is_not_its_own_workspace` documents and closed; verified failing
    on a pristine HEAD archive before this update (2026-09-18)."""
    project_root = tmp_path / "project-root"
    project_root.mkdir()
    with mock.patch.dict(
        "os.environ",
        {"VOOL_WORKSPACE_ROOT": "", "VOOL_PROJECT_ROOT": str(project_root)},
        clear=False,
    ):
        resolved = resolve_workspace_root()

    assert resolved != project_root.resolve()
    assert resolved.name == "workspace"


def test_resolve_workspace_root_falls_back_to_project_root_when_cwd_is_missing() -> None:
    with mock.patch.dict("os.environ", {"VOOL_WORKSPACE_ROOT": "", "VOOL_PROJECT_ROOT": ""}, clear=False), mock.patch(
        "core.runtime_paths.Path.cwd",
        side_effect=FileNotFoundError,
    ):
        resolved = resolve_workspace_root()

    assert resolved == PROJECT_ROOT.resolve()


def test_discover_installed_runtime_home_reads_install_receipt(tmp_path: Path) -> None:
    runtime_home = tmp_path / "runtime-home"
    receipt_path = tmp_path / "install_receipt.json"
    receipt_path.write_text(json.dumps({"runtime_home": str(runtime_home)}), encoding="utf-8")

    resolved = discover_installed_runtime_home(project_root=tmp_path)

    assert resolved == runtime_home.resolve()


def test_active_vool_home_uses_install_receipt_when_env_is_unset(tmp_path: Path) -> None:
    runtime_home = tmp_path / "runtime-home"
    receipt_path = tmp_path / "install_receipt.json"
    receipt_path.write_text(json.dumps({"runtime_home": str(runtime_home)}), encoding="utf-8")

    with mock.patch("core.runtime_paths.PROJECT_ROOT", tmp_path):
        resolved = active_vool_home({"VOOL_HOME": ""})

    assert resolved == runtime_home.resolve()


def test_active_vool_home_prefers_environment_over_install_receipt(tmp_path: Path) -> None:
    receipt_runtime = tmp_path / "receipt-runtime"
    env_runtime = tmp_path / "env-runtime"
    receipt_path = tmp_path / "install_receipt.json"
    receipt_path.write_text(json.dumps({"runtime_home": str(receipt_runtime)}), encoding="utf-8")

    with mock.patch("core.runtime_paths.PROJECT_ROOT", tmp_path):
        resolved = active_vool_home({"VOOL_HOME": str(env_runtime)})

    assert resolved == env_runtime.resolve()


def test_active_workspace_dir_honors_environment_overrides(tmp_path: Path) -> None:
    workspace = tmp_path / "runtime-workspace"
    workspace.mkdir()
    with mock.patch.dict(os.environ, {"VOOL_WORKSPACE_ROOT": str(workspace)}, clear=False):
        resolved = active_workspace_dir()

    assert resolved == workspace.resolve()
