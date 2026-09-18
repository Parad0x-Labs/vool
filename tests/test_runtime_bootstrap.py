from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from core.runtime_bootstrap import (
    BootstrappedRuntime,
    RuntimeBackendSelection,
    bootstrap_runtime_environment,
    bootstrap_runtime_mode,
    bootstrap_storage_environment,
    resolve_backend_selection,
)


def test_bootstrap_storage_environment_runs_storage_bootstrap_sequence() -> None:
    fake_context = mock.Mock()
    fake_context.paths.db_path = "/tmp/vool-test.db"
    with mock.patch("core.runtime_bootstrap.build_runtime_context", return_value=fake_context), mock.patch(
        "core.runtime_bootstrap.apply_runtime_context",
        return_value=fake_context,
    ) as apply_context, mock.patch("core.runtime_bootstrap.ensure_runtime_dirs") as ensure_dirs, mock.patch(
        "core.runtime_bootstrap.run_migrations"
    ) as run_migrations, mock.patch(
        "core.runtime_bootstrap.healthcheck",
        return_value=True,
    ) as healthcheck:
        bootstrap_storage_environment()

    apply_context.assert_called_once_with(fake_context)
    ensure_dirs.assert_called_once_with()
    run_migrations.assert_called_once_with("/tmp/vool-test.db")
    healthcheck.assert_called_once_with("/tmp/vool-test.db")


def test_bootstrap_storage_environment_checks_explicit_db_path_when_provided() -> None:
    fake_context = mock.Mock()
    fake_context.paths.db_path = "/tmp/vool-test.db"
    with mock.patch("core.runtime_bootstrap.build_runtime_context", return_value=fake_context) as build_context, mock.patch(
        "core.runtime_bootstrap.apply_runtime_context",
        return_value=fake_context,
    ), mock.patch("core.runtime_bootstrap.ensure_runtime_dirs"), mock.patch(
        "core.runtime_bootstrap.run_migrations"
    ) as run_migrations, mock.patch(
        "core.runtime_bootstrap.healthcheck",
        return_value=True,
    ) as healthcheck:
        bootstrap_storage_environment(db_path="/tmp/vool-test.db")

    build_context.assert_called_once_with(mode="storage", db_path="/tmp/vool-test.db")
    run_migrations.assert_called_once_with("/tmp/vool-test.db")
    healthcheck.assert_called_once_with("/tmp/vool-test.db")


def test_bootstrap_storage_environment_raises_when_database_healthcheck_fails() -> None:
    with mock.patch("core.runtime_bootstrap.ensure_runtime_dirs"), mock.patch(
        "core.runtime_bootstrap.run_migrations"
    ), mock.patch("core.runtime_bootstrap.healthcheck", return_value=False):
        with pytest.raises(RuntimeError, match=r"Database healthcheck failed\."):
            bootstrap_storage_environment()


def test_bootstrap_runtime_environment_runs_canonical_startup_sequence() -> None:
    fake_context = mock.Mock()
    with mock.patch("core.runtime_bootstrap.bootstrap_storage_environment") as bootstrap_storage, mock.patch(
        "core.runtime_bootstrap.policy_engine.load"
    ) as load_policy:
        bootstrap_runtime_environment(context=fake_context, force_policy_reload=True)

    bootstrap_storage.assert_called_once_with(context=fake_context)
    load_policy.assert_called_once_with(force_reload=True)


def test_bootstrap_runtime_environment_raises_when_database_healthcheck_fails() -> None:
    with mock.patch(
        "core.runtime_bootstrap.bootstrap_storage_environment",
        side_effect=RuntimeError("Database healthcheck failed."),
    ), mock.patch(
        "core.runtime_bootstrap.policy_engine.load"
    ) as load_policy:
        with pytest.raises(RuntimeError, match=r"Database healthcheck failed\."):
            bootstrap_runtime_environment()

    load_policy.assert_not_called()


def test_bootstrap_runtime_mode_builds_context_and_optional_backend_selection() -> None:
    fake_context = mock.Mock()
    fake_context.log_level = "DEBUG"
    fake_context.json_logs = False
    fake_context.feature_flags.allow_remote_only_without_backend = True
    fake_context.feature_flags.local_only_mode = False
    fake_backend = RuntimeBackendSelection(
        backend_name="torch",
        device="cuda",
        reason="gpu_detected",
        hardware=SimpleNamespace(accelerator="cuda"),
    )
    with mock.patch("core.runtime_bootstrap.build_runtime_context", return_value=fake_context) as build_context, mock.patch(
        "core.runtime_bootstrap.bootstrap_runtime_environment",
        return_value=fake_context,
    ) as bootstrap_runtime, mock.patch(
        "core.runtime_bootstrap.setup_logging"
    ) as setup_logging, mock.patch(
        "core.runtime_bootstrap.resolve_backend_selection",
        return_value=fake_backend,
    ) as resolve_backend:
        state = bootstrap_runtime_mode(
            mode="api_server",
            workspace_root="/tmp/workspace",
            force_policy_reload=True,
            configure_logging=True,
            resolve_backend=True,
        )

    assert state == BootstrappedRuntime(context=fake_context, backend_selection=fake_backend)
    build_context.assert_called_once_with(mode="api_server", workspace_root="/tmp/workspace", db_path=None)
    bootstrap_runtime.assert_called_once_with(context=fake_context, force_policy_reload=True)
    setup_logging.assert_called_once_with(level="DEBUG", json_output=False)
    resolve_backend.assert_called_once_with(manager=None, local_only=False, allow_remote_only=True)


def test_resolve_backend_selection_returns_real_backend_when_healthy() -> None:
    manager = mock.Mock()
    hardware = SimpleNamespace(accelerator="cuda")
    selection = SimpleNamespace(backend_name="torch", device="cuda", reason="gpu_detected")
    manager.detect_hardware.return_value = hardware
    manager.select_backend.return_value = selection
    manager.healthcheck.return_value = True

    resolved = resolve_backend_selection(manager=manager)

    assert resolved == RuntimeBackendSelection(
        backend_name="torch",
        device="cuda",
        reason="gpu_detected",
        hardware=hardware,
    )


def test_resolve_backend_selection_falls_back_to_remote_only_when_allowed() -> None:
    manager = mock.Mock()
    hardware = SimpleNamespace(accelerator="cpu")
    selection = SimpleNamespace(backend_name="onnxruntime", device="cpu", reason="backend_missing")
    manager.detect_hardware.return_value = hardware
    manager.select_backend.return_value = selection
    manager.healthcheck.return_value = False

    resolved = resolve_backend_selection(manager=manager, allow_remote_only=True)

    assert resolved == RuntimeBackendSelection(
        backend_name="remote_only",
        device="cpu",
        reason="backend_missing",
        hardware=hardware,
    )


def test_resolve_backend_selection_raises_when_remote_only_not_allowed() -> None:
    manager = mock.Mock()
    manager.detect_hardware.return_value = SimpleNamespace(accelerator="cpu")
    manager.select_backend.return_value = SimpleNamespace(
        backend_name="onnxruntime",
        device="cpu",
        reason="backend_missing",
    )
    manager.healthcheck.return_value = False

    with pytest.raises(RuntimeError, match=r"No supported backend found\."):
        resolve_backend_selection(manager=manager, allow_remote_only=False)
