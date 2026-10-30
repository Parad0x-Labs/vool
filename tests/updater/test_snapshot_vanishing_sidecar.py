"""An update's data backup survives SQLite tidying its own side files mid-backup.

Seen on the Intel live update journey (run 41073706227, 2026-10-30): VOOL paused its work, the
last database connection closed and SQLite removed ``vool_web0_v2.db-shm`` while the backup was
listing the data folder, so the update aborted with "No such file or directory" for that file.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from core.updater import snapshot


def _runtime(tmp_path: Path) -> Path:
    data = tmp_path / "home" / "data"
    data.mkdir(parents=True)
    (tmp_path / "home" / "config").mkdir()
    (tmp_path / "home" / "config" / "profile.json").write_text("{}")
    with sqlite3.connect(data / "memory.db") as connection:
        connection.execute("CREATE TABLE memory (value TEXT)")
        connection.execute("INSERT INTO memory VALUES ('kept')")
    (data / "memory.db-shm").write_bytes(b"\0" * 32)
    (data / "memory.db-wal").write_bytes(b"")
    (data / "notes.txt").write_text("history")
    (data / "scratch.tmp").write_text("short-lived")
    return data


def _vanishing_walk(names: tuple[str, ...]):
    real_walk = os.walk

    def walk(top, *args, **kwargs):
        for directory, dirs, files in real_walk(top, *args, **kwargs):
            yield directory, dirs, files
            # The listing is taken; SQLite and the app now tidy these files away.
            for name in names:
                (Path(directory) / name).unlink(missing_ok=True)

    return walk


def test_a_sidecar_removed_while_listing_does_not_abort_the_backup(tmp_path, monkeypatch) -> None:
    data = _runtime(tmp_path)
    monkeypatch.setattr(snapshot.os, "walk", _vanishing_walk(("memory.db-shm", "memory.db-wal")))

    snapshot.snapshot_runtime(data, tmp_path / "backup")

    with sqlite3.connect(tmp_path / "backup" / "data" / "memory.db") as backup:
        assert backup.execute("SELECT value FROM memory").fetchall() == [("kept",)]
    assert (tmp_path / "backup" / "data" / "notes.txt").read_text() == "history"


def test_a_plain_file_removed_while_listing_is_left_out_not_fatal(tmp_path, monkeypatch) -> None:
    data = _runtime(tmp_path)
    monkeypatch.setattr(snapshot.os, "walk", _vanishing_walk(("scratch.tmp",)))

    snapshot.snapshot_runtime(data, tmp_path / "backup")

    assert not (tmp_path / "backup" / "data" / "scratch.tmp").exists()
    assert (tmp_path / "backup" / "data" / "notes.txt").read_text() == "history"
    restored = tmp_path / "restored"
    (restored / "data").mkdir(parents=True)
    snapshot.restore_runtime(restored / "data", tmp_path / "backup")
    assert (restored / "data" / "notes.txt").read_text() == "history"
    assert not (restored / "data" / "scratch.tmp").exists()
