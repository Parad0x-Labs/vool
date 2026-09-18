"""The installed .app bundle must be IMMUTABLE at runtime.

Live-matrix evidence (2026-09-02): a self-contained VOOL.app accumulated 69 files under
``Contents/Resources/app`` while running — ``TOOLS.md``, ``control/*``, ``templates/*``,
``memory/README.md`` (the control-plane workspace materialized into the SOURCE root because
``active_workspace_dir`` treated the launchers' ``VOOL_PROJECT_ROOT`` as a workspace location),
and ``.vool_local/config/agent-bootstrap.json`` (public-hive writers defaulting to the
import-time ``CONFIG_HOME_DIR`` constant, frozen to the source root at import).

The law under test: runtime-generated state resolves beneath the canonical active data home for
the isolated user — never the cwd, the source root, ``Resources/app`` or the bundle root — so a
freshly built bundle launches and operates READ-ONLY and stays byte-identical across launches,
while state persists per isolated home across restarts.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------------------------------
# The workspace authority: a packaged runtime's source root is never a workspace location
# ------------------------------------------------------------------------------------------


def test_workspace_never_resolves_into_a_packaged_source_root(tmp_path: Path, monkeypatch) -> None:
    import core.runtime_paths
    from core.runtime_paths import active_workspace_dir

    bundle = tmp_path / "VOOL.app"
    app_root = bundle / "Contents" / "Resources" / "app"
    app_root.mkdir(parents=True)
    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setattr(core.runtime_paths, "_VOOL_HOME_OVERRIDE", None)
    env = {
        "VOOL_WORKSPACE_ROOT": "",
        "VOOL_PROJECT_ROOT": str(app_root),
        "VOOL_HOME": str(home),
    }
    with mock.patch.dict(os.environ, env, clear=False):
        resolved = active_workspace_dir()
    assert resolved == (home / "workspace").resolve(), (
        "a packaged runtime (VOOL_PROJECT_ROOT exported) must keep its workspace under the "
        "active vool home, not inside the bundle"
    )


def test_control_plane_workspace_materializes_outside_a_packaged_bundle(tmp_path: Path, monkeypatch) -> None:
    """The live control/, templates/ writer, driven against a fake read-only bundle."""
    import core.runtime_paths
    from core.control_plane_workspace import sync_control_plane_workspace

    app_root = tmp_path / "VOOL.app" / "Contents" / "Resources" / "app"
    app_root.mkdir(parents=True)
    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setattr(core.runtime_paths, "_VOOL_HOME_OVERRIDE", None)
    env = {
        "VOOL_WORKSPACE_ROOT": "",
        "VOOL_PROJECT_ROOT": str(app_root),
        "VOOL_HOME": str(home),
    }
    with mock.patch.dict(os.environ, env, clear=False):
        sync_control_plane_workspace(db_path=tmp_path / "state.db")
    stray = [p for p in app_root.rglob("*") if p.is_file()]
    assert stray == [], f"runtime materialized files inside the bundle: {stray[:5]}"
    assert (home / "workspace" / "control").is_dir(), "workspace state belongs in the writable home"
    assert (home / "workspace" / "templates").is_dir()


def test_openclaw_tools_md_seeds_into_the_agent_workspace_never_the_source_root(
    tmp_path: Path, monkeypatch
) -> None:
    """RED for the live TOOLS.md-in-bundle write: register() seeded the grounded TOOLS.md into
    project_root — the packaged app's read-only source tree — instead of the agent workspace."""
    import contextlib
    import io

    app_root = tmp_path / "VOOL.app" / "Contents" / "Resources" / "app"
    app_root.mkdir(parents=True)
    openclaw_home = tmp_path / "openclaw-home"
    openclaw_home.mkdir()
    monkeypatch.chdir(tmp_path)  # no cwd escape hatch either

    from installer.register_openclaw_agent import register

    with contextlib.redirect_stdout(io.StringIO()):
        register(
            project_root=str(app_root),
            vool_home=str(tmp_path / "vool-home"),
            openclaw_home=str(openclaw_home),
        )
    stray = list(app_root.rglob("*"))
    assert stray == [], f"registration wrote into the bundle source tree: {stray[:5]}"
    seeded = list(openclaw_home.rglob("TOOLS.md"))
    assert seeded, "the grounded TOOLS.md must be seeded into the agent workspace"


def test_dev_checkout_workspace_default_is_unchanged(tmp_path: Path, monkeypatch) -> None:
    """No VOOL_PROJECT_ROOT in play: the dev source checkout keeps its gitignored workspace."""
    import core.runtime_paths
    from core.runtime_paths import WORKSPACE_DIR, active_workspace_dir

    monkeypatch.setattr(core.runtime_paths, "_VOOL_HOME_OVERRIDE", None)
    env = {"VOOL_WORKSPACE_ROOT": "", "VOOL_PROJECT_ROOT": "", "VOOL_HOME": ""}
    with mock.patch.dict(os.environ, env, clear=False):
        resolved = active_workspace_dir()
    assert resolved == WORKSPACE_DIR.resolve()


# ------------------------------------------------------------------------------------------
# The config-home authority: writers must resolve at call time, never the import-time constant
# ------------------------------------------------------------------------------------------


def test_agent_bootstrap_writer_resolves_under_the_active_config_home(
    tmp_path: Path, monkeypatch
) -> None:
    import core.runtime_paths

    """RED for the live .vool_local-in-bundle write: the writer must resolve its target from the
    active config home at CALL time (VOOL_HOME), not the CONFIG_HOME_DIR frozen at import."""
    from core.public_hive import bootstrap as hive_bootstrap
    from core.runtime_paths import active_config_home_dir

    home = tmp_path / "isolated-home"
    home.mkdir()
    # Pre-create the stale frozen-constant target so the RED run early-returns there instead of
    # clobbering real developer state in the repo checkout.
    frozen_target = REPO / ".vool_local" / "config" / "agent-bootstrap.json"
    frozen_target.parent.mkdir(parents=True, exist_ok=True)
    frozen_target.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(core.runtime_paths, "_VOOL_HOME_OVERRIDE", None)
    try:
        monkeypatch.setenv("VOOL_HOME", str(home))
        monkeypatch.setenv("VOOL_MEET_SEED_URLS", "https://seed.example")
        written = hive_bootstrap.ensure_public_hive_agent_bootstrap(
            split_csv_fn=lambda v: [part for part in str(v or "").split(",") if part],
            load_agent_bootstrap_fn=lambda include_runtime: {},
            discover_local_cluster_bootstrap_fn=lambda project_root: {},
        )
        assert written is not None, "seeded env must produce a bootstrap write"
        assert written == active_config_home_dir() / "agent-bootstrap.json"
        assert written.parent == home / "config", "writer resolved outside the active config home"
        assert json.loads(written.read_text(encoding="utf-8"))["meet_seed_urls"] == ["https://seed.example"]
    finally:
        if frozen_target.read_text(encoding="utf-8") == "{}\n":
            frozen_target.unlink()
            with __import__("contextlib").suppress(OSError):
                frozen_target.parent.rmdir()


def test_public_hive_auth_wrapper_resolves_config_home_at_call_time(
    tmp_path: Path, monkeypatch
) -> None:
    """The auth wrapper must not inject the import-time constant over the fixed default."""
    from core.public_hive import auth as hive_auth
    from core.runtime_paths import active_config_home_dir

    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    captured: dict = {}

    def fake_impl(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(
        hive_auth.public_hive_bootstrap, "ensure_public_hive_agent_bootstrap", fake_impl
    )
    hive_auth.ensure_public_hive_agent_bootstrap(
        split_csv_fn=lambda v: [],
        clean_token_fn=lambda v: "",
        json_env_object_fn=lambda v: {},
        json_env_write_grants_fn=lambda v: {},
        load_agent_bootstrap_fn=lambda include_runtime: {},
        discover_local_cluster_bootstrap_fn=lambda project_root: {},
        merge_write_grants_by_base_url_fn=lambda sample: {},
    )
    assert captured["config_home_dir"] == active_config_home_dir(), (
        "the wrapper must pass the call-time config home, not the import-time constant"
    )


def test_trainable_base_policy_writes_under_the_active_config_home(
    tmp_path: Path, monkeypatch
) -> None:
    from core import trainable_base_manager as tbm
    from core.runtime_paths import active_config_home_dir

    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    assert tbm._default_policy_path() == active_config_home_dir() / "default_policy.yaml"


# ------------------------------------------------------------------------------------------
# Read-only source tree: boot-time dir creation must be best-effort outside the writable home
# ------------------------------------------------------------------------------------------


def test_get_connection_never_materializes_the_frozen_default_db_path(
    tmp_path: Path, monkeypatch
) -> None:
    """RED (read-only bundle boot crash): get_connection created the import-time DEFAULT_DB_PATH's
    parent (.vool_local/data INSIDE the bundle) merely to compare the default sentinel —
    PermissionError on a read-only source root, and a database inside the .app on a writable one.
    The sentinel comparison must not touch the filesystem; the real DB belongs to the active home."""
    from pathlib import Path as _Path

    import core.runtime_paths
    from storage import db as storage_db

    app_root = tmp_path / "VOOL.app" / "Contents" / "Resources" / "app"
    app_root.mkdir(parents=True)
    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setattr(core.runtime_paths, "_VOOL_HOME_OVERRIDE", None)
    env = {"VOOL_WORKSPACE_ROOT": "", "VOOL_PROJECT_ROOT": str(app_root), "VOOL_HOME": str(home)}
    try:
        with mock.patch.dict(os.environ, env, clear=False):
            conn = storage_db.get_connection(storage_db.DEFAULT_DB_PATH)
            conn.execute("CREATE TABLE IF NOT EXISTS immutability_probe (x INTEGER)")
            conn.commit()
            conn.close()
            resolved = _Path(storage_db.active_default_db_path()).resolve()
        # The active authority (override/env chain) owns the answer; this test's session may carry
        # its own DB override, so assert the LAW rather than one literal path: the effective
        # database never lives inside the packaged source tree, and the frozen default
        # (.vool_local under the source root) is never materialized.
        assert not resolved.is_relative_to(app_root), (
            f"the default database resolved inside the packaged source tree: {resolved}"
        )
        assert conn is not None
        assert not (app_root / ".vool_local").exists(), (
            "the frozen source-root default path must never be materialized"
        )
    finally:
        os.chmod(app_root, stat.S_IRWXU)


def test_ensure_runtime_dirs_survives_a_read_only_source_root(tmp_path: Path, monkeypatch) -> None:
    """The real read-only bundle case: docs/ (and anything else under the locked source root) is
    MISSING and cannot be created. Boot-time dir creation must be best-effort there — only the
    writable home is load-bearing."""
    import core.runtime_paths
    from core.runtime_paths import ensure_runtime_dirs

    app_root = tmp_path / "VOOL.app" / "Contents" / "Resources" / "app"
    app_root.mkdir(parents=True)
    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setattr(core.runtime_paths, "_VOOL_HOME_OVERRIDE", None)
    os.chmod(app_root, stat.S_IRUSR | stat.S_IXUSR)  # read-only source root, docs/ absent
    env = {"VOOL_WORKSPACE_ROOT": "", "VOOL_PROJECT_ROOT": str(app_root), "VOOL_HOME": str(home)}
    try:
        with mock.patch.dict(os.environ, env, clear=False):
            ensure_runtime_dirs()  # must not raise on the read-only source root
        assert (home / "workspace").is_dir(), "the writable home dirs must still be ensured"
        assert not (app_root / "docs").exists()
    finally:
        os.chmod(app_root, stat.S_IRWXU)


def test_explicit_frozen_db_path_argument_reroutes_to_the_active_home(
    tmp_path: Path, monkeypatch
) -> None:
    """Live read-only launch (2026-09-02): a caller passed the import-time DEFAULT_DB_PATH as an
    EXPLICIT argument, and _resolve_db_path_cached mkdir'd its parent inside the bundle. The
    deepest choke point must reroute the frozen sentinel to the active data dir."""
    from pathlib import Path as _Path

    import core.runtime_paths
    from storage import db as storage_db

    storage_db._resolve_db_path_cached.cache_clear()  # earlier tests may hold another home's entry
    # This test proves the no-override reroute (sentinel -> runtime home). The session-wide
    # test override would legitimately win otherwise — the sentinel means "the active default".
    monkeypatch.setattr(storage_db, "_DEFAULT_DB_PATH_OVERRIDE", None)
    app_root = tmp_path / "VOOL.app" / "Contents" / "Resources" / "app"
    app_root.mkdir(parents=True)
    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setattr(core.runtime_paths, "_VOOL_HOME_OVERRIDE", None)
    monkeypatch.setenv("VOOL_HOME", str(home))
    env = {"VOOL_WORKSPACE_ROOT": "", "VOOL_PROJECT_ROOT": str(app_root)}
    try:
        os.chmod(app_root, stat.S_IRUSR | stat.S_IXUSR)  # read-only source root
        with mock.patch.dict(os.environ, env, clear=False):
            resolved = _Path(storage_db._resolve_db_path(storage_db.DEFAULT_DB_PATH))
        assert resolved == (home / "data" / "vool_web0_v2.db").resolve(), (
            f"the frozen sentinel must reroute to the active data dir, got {resolved}"
        )
        assert not (app_root / ".vool_local").exists()
    finally:
        os.chmod(app_root, stat.S_IRWXU)
