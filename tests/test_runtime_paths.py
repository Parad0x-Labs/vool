from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

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
    with mock.patch.dict("os.environ", {"VOOL_WORKSPACE_ROOT": "", "VOOL_PROJECT_ROOT": "", "VOOL_HOME": "", "NULLA_HOME": ""}, clear=False), mock.patch(
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


@pytest.mark.parametrize("variable", ["VOOL_HOME", "NULLA_HOME"])
def test_custom_profile_never_discovers_login_home_plugins(tmp_path, monkeypatch, variable) -> None:
    from core import plugin_catalog
    from core.runtime_paths import active_config_home_dir, active_data_dir, native_lock_base, native_state_dir

    login = tmp_path / "login"
    for name in ("Vool-skills-plugins", "Nulla-skills-plugins"):
        (login / "Desktop" / name / "plugins").mkdir(parents=True)
    fresh = tmp_path / "fresh"
    monkeypatch.setattr(Path, "home", lambda: login)
    monkeypatch.setenv("VOOL_HOME", "")
    monkeypatch.setenv("NULLA_HOME", "")
    monkeypatch.setenv(variable, str(fresh))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", "")
    monkeypatch.setenv("NULLA_PLUGINS_DIR", "")
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", "")
    monkeypatch.setenv("NULLA_WORKSPACE_ROOT", "")

    assert plugin_catalog.configured_plugins_root() == fresh / "plugins"
    assert plugin_catalog.plugins_root() is None
    assert active_data_dir() == fresh / "data"
    assert active_config_home_dir() == fresh / "config"
    assert active_workspace_dir() == fresh / "workspace"
    assert resolve_workspace_root() == fresh / "workspace"
    assert native_state_dir() == fresh / "logs"
    assert native_lock_base() == fresh / "run" / "native"
    # An operator's deliberately selected external plugin source still wins.
    selected = tmp_path / "selected"
    (selected / "plugins").mkdir(parents=True)
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(selected))
    assert plugin_catalog.plugins_root() == selected


@pytest.mark.parametrize("name", [".vool_runtime", ".nulla_runtime"])
def test_default_upgrade_profiles_keep_desktop_discovery(tmp_path, monkeypatch, name) -> None:
    from core import plugin_catalog
    from core.runtime_paths import runtime_home_is_isolated

    login = tmp_path / "login"
    legacy = login / "Desktop" / "Nulla-skills-plugins"
    (legacy / "plugins").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: login)
    monkeypatch.setenv("VOOL_HOME", str(login / name))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", "")
    monkeypatch.setenv("NULLA_PLUGINS_DIR", "")
    assert not runtime_home_is_isolated()
    assert plugin_catalog.plugins_root() == legacy
    current = login / "Desktop" / "Vool-skills-plugins"
    (current / "plugins").mkdir(parents=True)
    assert plugin_catalog.plugins_root() == current


def test_home_authority_moves_every_auxiliary_store_together(tmp_path, monkeypatch) -> None:
    from core import (
        generated_files,
        honesty_receipt,
        native_skill_library,
        plugin_catalog,
        runtime_paths,
        x_voice_profile,
    )
    from core.evidence_kernel import receipts
    from core.kernel import consensus, repl
    from core.media_studio_service import _preview_cache_dir
    from storage import db

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "environment"))
    monkeypatch.setenv("VOOL_REVIEW_BUS", "")
    monkeypatch.setenv("NULLA_REVIEW_BUS", "")
    previous = runtime_paths._VOOL_HOME_OVERRIDE
    prior_env = runtime_paths._VOOL_HOME_ENV_AT_OVERRIDE
    previous_db = db._DEFAULT_DB_PATH_OVERRIDE
    try:
        from storage.db import configure_default_db_path

        configure_default_db_path(None)
        for name in ("first", "second"):
            home = tmp_path / name
            runtime_paths.configure_runtime_home(home)
            assert generated_files._index_path() == home / "data" / "generated_files_index.json"
            assert honesty_receipt._ledger_dir() == home / "data" / "honesty_receipts"
            assert receipts._ledger_dir() == home / "data" / "receipts_v2"
            assert native_skill_library._enabled_store_path() == home / "config" / "skills_enabled.json"
            assert plugin_catalog._enabled_store_path() == home / "config" / "plugins_enabled.json"
            assert _preview_cache_dir("project") == home / "cache" / "media_preview_cache" / "project"
            assert consensus._bus_root() == home / "review-bus"
            assert repl._session_log_path() == home / "repl_sessions.jsonl"
            assert x_voice_profile._store_path() == home / "config" / "x_voice_profiles.json"
            assert Path(db.active_default_db_path()) == home / "data" / "vool_web0_v2.db"
            assert generated_files._default_roots()[0] == home / "generated"
    finally:
        runtime_paths.configure_runtime_home(previous)
        runtime_paths._VOOL_HOME_ENV_AT_OVERRIDE = prior_env
        configure_default_db_path(previous_db)


def test_blank_canonical_home_preserves_explicit_legacy_home(tmp_path) -> None:
    home = tmp_path / "legacy"
    assert active_vool_home({"VOOL_HOME": "  ", "NULLA_HOME": str(home)}) == home


def test_native_default_reuses_legacy_profile_before_creating_state(tmp_path, monkeypatch) -> None:
    from core.runtime_paths import native_runtime_home

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    base = tmp_path / "Library" / "Application Support"
    legacy = base / "NULLA" / "runtime"
    legacy.mkdir(parents=True)
    assert native_runtime_home({}) == legacy
    assert not (base / "VOOL").exists()
    current = base / "VOOL" / "runtime"
    current.mkdir(parents=True)
    assert native_runtime_home({}) == current
    explicit = tmp_path / "fresh"
    assert native_runtime_home({"NULLA_HOME": str(explicit)}) == explicit


def test_native_singleton_has_real_per_profile_locks(tmp_path, monkeypatch) -> None:
    from installer.bundle import vool_window

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "first"))
    code = (
        "import sys; from installer.bundle.vool_window import _single_instance; "
        "print(_single_instance(), flush=True); sys.stdin.readline()"
    )
    child = subprocess.Popen([sys.executable, "-c", code], cwd=PROJECT_ROOT,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "True"
        assert vool_window._single_instance() is False
        monkeypatch.setenv("VOOL_HOME", str(tmp_path / "second"))
        assert vool_window._single_instance() is True
        if os.name == "posix":
            assert (tmp_path / "second" / "run" / "native" / "VOOL" / "window.lock").is_file()
            assert (tmp_path / "second" / "run" / "native" / "NULLA" / "window.lock").is_file()
        assert vool_window._state_dir() == str(tmp_path / "second" / "logs")
    finally:
        child.communicate(input="done\n", timeout=10)
        for handle in vool_window._LOCK_HANDLES:
            os.close(handle)
        vool_window._LOCK_HANDLES.clear()


def test_profile_temp_failure_cannot_fall_back_to_global_temp(tmp_path, monkeypatch) -> None:
    from core.runtime_paths import runtime_temp_dir

    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("VOOL_HOME", str(blocked))
    with pytest.raises(OSError):
        runtime_temp_dir()


def test_unavailable_native_profile_lock_refuses_a_second_runtime(tmp_path, monkeypatch) -> None:
    from installer.bundle import vool_window

    blocked = tmp_path / "profile"
    blocked.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("VOOL_HOME", str(blocked))
    if os.name == "posix":
        assert vool_window._single_instance() is False
    with pytest.raises(OSError):
        vool_window._state_dir()


def test_later_environment_profile_cannot_be_pinned_by_an_earlier_bootstrap(tmp_path, monkeypatch) -> None:
    from core import runtime_paths

    previous = runtime_paths._VOOL_HOME_OVERRIDE
    prior_env = runtime_paths._VOOL_HOME_ENV_AT_OVERRIDE
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "original"))
    try:
        runtime_paths.configure_runtime_home(tmp_path / "intentional-import")
        assert active_vool_home() == tmp_path / "intentional-import"
        generation = runtime_paths.runtime_home_generation()
        monkeypatch.setenv("VOOL_HOME", str(tmp_path / "fresh"))
        assert active_vool_home() == tmp_path / "fresh"
        assert runtime_paths.runtime_home_generation() == generation + 1
    finally:
        runtime_paths.configure_runtime_home(previous)
        runtime_paths._VOOL_HOME_ENV_AT_OVERRIDE = prior_env


def test_native_launcher_resolves_authority_before_first_log() -> None:
    script = (PROJECT_ROOT / "installer" / "bundle" / "build_macos_app.sh").read_text(encoding="utf-8")
    launcher = script.split("<<'LAUNCHER'", 1)[1].split("\nLAUNCHER", 1)[0]
    assert launcher.index("from core.runtime_paths import native_runtime_home") < launcher.index('mkdir -p "${SUPPORT}"')
    assert launcher.index("from core.runtime_paths import native_state_dir") < launcher.index('exec >>"${SUPPORT}/app.log"')
    assert launcher.index('export VOOL_HOME=') < launcher.index('vool_ensure_bundled_ollama')


def test_mcp_config_cache_cannot_survive_a_profile_switch(tmp_path, monkeypatch) -> None:
    from core.execution import mcp_bridge

    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "mcp_servers.json").write_text(json.dumps({"servers": [{"name": "first-server", "command": "local-command"}]}))
    monkeypatch.setenv("VOOL_MCP_CONFIG", "")
    monkeypatch.setenv("VOOL_HOME", str(first))
    try:
        assert [row["name"] for row in mcp_bridge.load_mcp_server_configs()] == ["first-server"]
        monkeypatch.setenv("VOOL_HOME", str(second))
        assert mcp_bridge.load_mcp_server_configs() == []
        assert mcp_bridge.mcp_contracts() == []
    finally:
        mcp_bridge.reset_mcp_state()


@pytest.mark.parametrize("saved, outbound, web", [
    (None, False, False),
    ("not: [valid", False, False),
    ("network:\n  outbound_enabled: false\nsystem:\n  allow_web_fallback: false\n", False, False),
    ("network:\n  outbound_enabled: false\nsystem:\n  allow_web_fallback: true\n", False, True),
    ("network:\n  outbound_enabled: true\n", True, False),
    ("network:\n  outbound_enabled: 'true'\nsystem:\n  allow_web_fallback: 1\n", False, False),
])
def test_automatic_source_sidecars_require_saved_consent(tmp_path, monkeypatch, saved, outbound, web) -> None:
    from core.runtime_guard import automatic_launch_network_allowed

    home = tmp_path / "profile"
    monkeypatch.setenv("VOOL_HOME", str(home))
    if saved is not None:
        path = home / "config" / "default_policy.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(saved, encoding="utf-8")
    assert automatic_launch_network_allowed("outbound") is outbound
    assert automatic_launch_network_allowed("web") is web
    assert automatic_launch_network_allowed("unknown") is False


def test_confined_child_scratch_is_profile_scoped_outside_its_key_home(tmp_path, monkeypatch) -> None:
    from core.runtime_paths import runtime_child_scratch_root

    first, second = tmp_path / "first", tmp_path / "second"
    monkeypatch.setenv("VOOL_HOME", str(first))
    first_scratch = runtime_child_scratch_root("plugin")
    assert first not in first_scratch.parents
    monkeypatch.setenv("VOOL_HOME", str(second))
    assert runtime_child_scratch_root("plugin") != first_scratch
    assert runtime_child_scratch_root("mcp") != runtime_child_scratch_root("plugin")
    monkeypatch.setenv("VOOL_HOME", str(first))
    assert runtime_child_scratch_root("plugin") == first_scratch


def test_runtime_health_stamp_identifies_profile_without_exposing_its_path(tmp_path, monkeypatch) -> None:
    from core.runtime_paths import runtime_home_identity
    from core.web.api.runtime import build_runtime_version_stamp

    identities = []
    for name in ("first", "second"):
        home = tmp_path / name
        monkeypatch.setenv("VOOL_HOME", str(home))
        stamp = build_runtime_version_stamp(project_root=PROJECT_ROOT, runtime_model_tag="local", workstation_version="test")
        assert stamp["runtime_home_id"] == runtime_home_identity()
        assert str(home) not in json.dumps(stamp)
        identities.append(stamp["runtime_home_id"])
    assert identities[0] != identities[1]


@pytest.mark.parametrize("saved, variable, outbound, web", [
    ("system:\n  local_only_mode: true\n  allow_web_fallback: true\nnetwork:\n  outbound_enabled: true\n", None, False, False),
    ("system:\n  local_only_mode: false\n  allow_web_fallback: false\nnetwork:\n  outbound_enabled: true\n", None, True, False),
    ("system:\n  local_only_mode: false\n  allow_web_fallback: true\nnetwork:\n  outbound_enabled: false\n", "VOOL_DISABLE_WEB", False, False),
    ("system:\n  local_only_mode: false\n  allow_web_fallback: true\nnetwork:\n  outbound_enabled: false\n", "VOOL_NO_WEB", False, False),
    ("system:\n  local_only_mode: false\n  allow_web_fallback: true\nnetwork:\n  outbound_enabled: true\n", None, True, True),
])
def test_source_sidecar_consent_obeys_local_only_and_web_vetoes(tmp_path, monkeypatch, saved, variable, outbound, web) -> None:
    from core.runtime_guard import automatic_launch_network_allowed

    home = tmp_path / "profile"
    path = home / "config" / "default_policy.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(saved)
    monkeypatch.setenv("VOOL_HOME", str(home))
    for name in ("VOOL_DISABLE_WEB", "VOOL_NO_WEB", "NULLA_DISABLE_WEB", "NULLA_NO_WEB"):
        monkeypatch.delenv(name, raising=False)
    if variable is not None:
        monkeypatch.setenv(variable, "1")
    assert automatic_launch_network_allowed("outbound") is outbound
    assert automatic_launch_network_allowed("web") is web


@pytest.mark.parametrize("enabled, allow_network, expected_enabled, expected_network", [
    (..., None, True, False),
    (None, True, False, False),
    (True, True, True, True),
    (True, False, True, False),
    (True, "false", True, False),
    (True, "true", True, False),
    (True, 1, True, False),
    (False, True, False, False),
    ("false", True, False, False),
    ("true", True, False, False),
    (1, True, False, False),
])
def test_saved_mcp_configuration_requires_boolean_authority(tmp_path, monkeypatch, enabled, allow_network,
                                                          expected_enabled, expected_network) -> None:
    from core.execution import mcp_bridge

    home = tmp_path / "profile"
    home.mkdir()
    entry = {"name": "configured", "command": "local-command"}
    if enabled is not ...:
        entry["enabled"] = enabled
    if allow_network is not None:
        entry["allow_network"] = allow_network
    (home / "mcp_servers.json").write_text(json.dumps({"servers": [entry]}))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_MCP_CONFIG", "")
    try:
        configs = mcp_bridge.load_mcp_server_configs()
        assert bool(configs) is expected_enabled
        if configs:
            assert configs[0]["allow_network"] is expected_network
    finally:
        mcp_bridge.reset_mcp_state()
