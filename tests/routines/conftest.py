"""In-process isolation for My routines.

Each test gets its own person's home folder (Desktop, Downloads and Documents are real folders under
it), its own VOOL home and its own skills tree, so nothing a test saves, moves or notes reaches the
real ones.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest


@pytest.fixture
def person(tmp_path, monkeypatch):
    home = tmp_path / "person"
    for name in ("Desktop", "Downloads", "Documents"):
        (home / name).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "vool"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "addons"))
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "lifecycle.json"))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    from storage.migrations import run_migrations

    run_migrations()
    yield home
    from core.routines import watch

    watch.forget()


def put(folder: Path, name: str, data: bytes, day: int = 3, month: int = 11, year: int = 2026) -> Path:
    """A file with a fixed modification date (10:00 local time on that day)."""
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    stamp = time.mktime((year, month, day, 10, 0, 0, 0, 0, -1))
    os.utime(path, (stamp, stamp))
    return path


def tree(folder: Path) -> dict[str, bytes]:
    """Every file under ``folder`` with its bytes."""
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}
