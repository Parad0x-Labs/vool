"""core/web0_settings — the one off-by-default Web0 switch.

Off means off: a missing file, an unreadable file, a corrupt file and a wrong
version are ALL the off state. Only a persisted `{"version": 1, "enabled": true}`
is on. Reading never creates anything.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import runtime_paths
from core.web0_settings import set_web0_enabled, web0_enabled, web0_settings_status


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _settings_file(tmp_path: Path) -> Path:
    return tmp_path / "data" / "web0" / "settings.json"


def test_fresh_home_is_off_and_read_has_no_side_effects(tmp_path: Path) -> None:
    assert web0_enabled() is False
    assert web0_settings_status() == {"enabled": False}
    # Reading did not create the data tree.
    assert not (tmp_path / "data").exists()


def test_enable_persists_and_reads_back(tmp_path: Path) -> None:
    result = set_web0_enabled(True)
    assert result == {"enabled": True}
    assert web0_enabled() is True
    raw = json.loads(_settings_file(tmp_path).read_text(encoding="utf-8"))
    assert raw == {"version": 1, "enabled": True}
    # 0600 on the persisted switch — daemon-owned state, like the companion store.
    assert (_settings_file(tmp_path).stat().st_mode & 0o777) == 0o600


def test_disable_persists(tmp_path: Path) -> None:
    set_web0_enabled(True)
    assert set_web0_enabled(False) == {"enabled": False}
    assert web0_enabled() is False


def test_corrupt_file_fails_closed(tmp_path: Path) -> None:
    set_web0_enabled(True)
    _settings_file(tmp_path).write_text("{not json", encoding="utf-8")
    assert web0_enabled() is False


def test_wrong_version_fails_closed(tmp_path: Path) -> None:
    target = _settings_file(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"version": 99, "enabled": True}), encoding="utf-8")
    assert web0_enabled() is False


def test_non_boolean_enabled_fails_closed(tmp_path: Path) -> None:
    target = _settings_file(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"version": 1, "enabled": "yes"}), encoding="utf-8")
    assert web0_enabled() is False
