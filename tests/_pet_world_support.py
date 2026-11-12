from __future__ import annotations

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    from core import runtime_continuity, runtime_paths
    from storage import db
    from storage.migrations import run_migrations

    monkeypatch.setenv('VOOL_HOME', str(tmp_path/'home'))
    monkeypatch.setenv('VOOL_WORKSPACE_ROOT', str(tmp_path/'workspace'))
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', None)
    monkeypatch.setattr(db, '_DEFAULT_DB_PATH_OVERRIDE', None)
    monkeypatch.setattr(runtime_continuity, '_DB_PATH_OVERRIDE', None)
    run_migrations()
    return tmp_path/'home'
