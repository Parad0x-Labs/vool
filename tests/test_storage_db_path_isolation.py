"""Regression: get_connection must not create the project-root default DB
directory as a side effect when a runtime-home override is active.

Measured: storage/db.get_connection() resolved (and mkdir'ed) the base
DEFAULT_DB_PATH under <project_root>/.vool_local before choosing the
override — a write outside the configured runtime home. Under a confined
runtime that PermissionError propagated into seal_provider_invocation and
silently degraded every embedding request to hash fallback.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from core.runtime_paths import configure_runtime_home
from storage import db


@pytest.fixture()
def isolated_paths(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        fake_base = str(Path(tmp) / "never" / "created" / "web0.db")
        monkeypatch.setattr(db, "DEFAULT_DB_PATH", fake_base)
        # main recognises "the default database" by its import-frozen sentinel, so the fake base is that too
        monkeypatch.setattr(db, "_FROZEN_DEFAULT_DB_PATH", str(Path(fake_base).expanduser().resolve()))
        # clear caches so the patched constant is used
        db._resolve_db_path_cached.cache_clear()
        db._resolve_db_path_no_create.cache_clear()
        yield Path(tmp), fake_base
        db._resolve_db_path_cached.cache_clear()
        db._resolve_db_path_no_create.cache_clear()


def test_base_default_dir_not_created_when_override_active(isolated_paths, monkeypatch):
    tmp, fake_base = isolated_paths
    runtime_home = tmp / "runtime-home"
    runtime_home.mkdir()
    configure_runtime_home(runtime_home)
    try:
        conn = db.get_connection()
        conn.execute("CREATE TABLE IF NOT EXISTS probe (x INTEGER)")
        conn.commit()
        active = db.active_default_db_path()
        conn.close()
    finally:
        configure_runtime_home(None)
        db.reset_default_connection()
    assert Path(fake_base).exists() is False, "base default DB must never materialize"
    assert (Path(tmp) / "never").exists() is False, "no base-path parents created"
    # a write happened through the ACTIVE default path, wherever the runtime
    # layer (possibly conftest) points it
    assert Path(active).exists(), f"active default DB materialized at {active}"
    assert not str(active).startswith(str(Path(fake_base).parent)), \
        "active DB must not live under the never-created base dir"


def test_explicit_base_default_request_is_not_materialized_either(isolated_paths):
    """The default argument used to bind the real project path at definition time, so patching
    DEFAULT_DB_PATH never reached get_connection() and the test above could not see the write.
    Asking explicitly for the (patched) base default must resolve to the active default without
    creating the base path's parents."""
    tmp, fake_base = isolated_paths
    runtime_home = tmp / "runtime-home-explicit"
    runtime_home.mkdir()
    configure_runtime_home(runtime_home)
    try:
        conn = db.get_connection(db.DEFAULT_DB_PATH)
        conn.execute("CREATE TABLE IF NOT EXISTS probe (x INTEGER)")
        conn.commit()
        conn.close()
    finally:
        configure_runtime_home(None)
        db.reset_default_connection()
    assert (Path(tmp) / "never").exists() is False, "explicit base-default request created its parents"
