from __future__ import annotations

from pathlib import Path
from unittest import mock

from core.runtime_context import apply_runtime_context, build_runtime_context
from core.runtime_install_profiles import persist_install_profile_record


def test_build_runtime_context_centralizes_runtime_and_workspace_paths(tmp_path: Path) -> None:
    runtime_home = tmp_path / "runtime-home"
    workspace_root = tmp_path / "workspace-root"
    with mock.patch.dict(
        "os.environ",
        {
            "VOOL_HOME": str(runtime_home),
            "VOOL_WORKSPACE_ROOT": str(workspace_root),
            "VOOL_PUBLIC_HIVE_ENABLED": "0",
        },
        clear=False,
    ):
        context = build_runtime_context(mode="api_server")

    assert context.mode == "api_server"
    assert context.paths.runtime_home == runtime_home.resolve()
    assert context.paths.workspace_root == workspace_root.resolve()
    assert context.paths.db_path == (runtime_home / "data" / "vool_web0_v2.db").resolve()
    assert context.feature_flags.public_hive_enabled is False


def test_apply_runtime_context_configures_runtime_home_and_default_db_path(tmp_path: Path) -> None:
    runtime_home = tmp_path / "runtime-home"
    context = build_runtime_context(
        mode="cli",
        workspace_root=tmp_path / "workspace",
        db_path=runtime_home / "db" / "vool.db",
        env={"VOOL_HOME": str(runtime_home)},
    )

    with mock.patch("core.runtime_context.configure_runtime_home") as configure_home, mock.patch(
        "core.runtime_context.configure_default_db_path"
    ) as configure_db:
        applied = apply_runtime_context(context)

    assert applied is context
    configure_home.assert_called_once_with(context.paths.runtime_home)
    configure_db.assert_called_once_with(context.paths.db_path)


def test_build_runtime_context_uses_install_receipt_runtime_home_when_env_is_unset(tmp_path: Path) -> None:
    runtime_home = tmp_path / "receipt-runtime"

    with mock.patch(
        "core.runtime_context.active_vool_home",
        return_value=runtime_home.resolve(),
    ), mock.patch.dict(
        "os.environ",
        {"VOOL_HOME": "", "VOOL_WORKSPACE_ROOT": ""},
        clear=False,
    ):
        context = build_runtime_context(mode="cli")

    assert context.paths.runtime_home == runtime_home.resolve()
    assert context.paths.db_path == (runtime_home / "data" / "vool_web0_v2.db").resolve()


def test_build_runtime_context_marks_local_only_install_profiles_as_local_first(tmp_path: Path) -> None:
    runtime_home = tmp_path / "runtime-home"
    persist_install_profile_record(runtime_home, "local-only", selected_model="qwen3:8b")

    context = build_runtime_context(
        mode="api_server",
        env={"VOOL_HOME": str(runtime_home)},
    )

    assert context.feature_flags.local_only_mode is True
    assert context.feature_flags.allow_remote_only_without_backend is False
