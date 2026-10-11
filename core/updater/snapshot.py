"""Consistent runtime DB/config backups at the updater's migration boundary."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote


def snapshot_runtime(data_dir: Path, destination: Path) -> None:
    home = Path(data_dir).resolve().parent
    data = Path(data_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    os.chmod(destination, 0o700)
    entries = []
    config = home / "config"
    if config.is_symlink():
        raise ValueError("runtime config is a symlink; refusing an ambiguous backup")
    entries.append({"path": "config", "kind": "config", "present": config.exists()})
    if config.exists():
        if any(p.is_symlink() for p in config.rglob("*")):
            raise ValueError("runtime config contains a symlink; backup refused")
        shutil.copytree(config, destination / "config")
    databases = [p for p in data.rglob("*")
                 if p.suffix.lower() in (".db", ".sqlite", ".sqlite3")
                 and "update_v2" not in p.relative_to(data).parts]
    if len(databases) > 64 or sum(p.stat().st_size for p in databases) > 8 * 1024**3:
        raise ValueError("runtime database backup exceeds limits")
    for database in sorted(databases):
        if database.is_symlink() or not database.is_file():
            raise ValueError("runtime DB backup requires regular files")
        relative = database.relative_to(home)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 60

        def bounded(_status, _remaining, _total, deadline=deadline):
            if time.monotonic() > deadline:
                raise TimeoutError("SQLite snapshot did not finish in time")

        with sqlite3.connect(f"file:{quote(str(database))}?mode=ro", uri=True, timeout=5) as source:
            with sqlite3.connect(target) as backup:
                source.backup(backup, pages=256, progress=bounded, sleep=0.05)
                if backup.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise ValueError("SQLite snapshot failed its integrity check")
        os.chmod(target, 0o600)
        entries.append({"path": relative.as_posix(), "kind": "sqlite", "present": True})
    (destination / "index.json").write_text(json.dumps(entries, sort_keys=True) + "\n", encoding="utf-8")


def restore_runtime(data_dir: Path, source: Path) -> None:
    """Restore only this snapshot's DB/config. Caller must have stopped the app.

    Keep backups intact if any operation fails; propagate the error to the status.
    WAL/SHM sidecars cannot accompany a restored database from another generation.
    """
    home = Path(data_dir).resolve().parent
    entries = json.loads((source / "index.json").read_text(encoding="utf-8"))
    for item in entries:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid snapshot index")
        target = home / relative
        target.resolve().relative_to(home)
        if target.is_symlink():
            raise ValueError("restore destination became a symlink")
        saved = source / relative
        if item["present"] and not saved.exists():
            raise FileNotFoundError(f"snapshot missing: {relative}")
    for item in entries:
        target = home / item["path"]
        saved = source / item["path"]
        if item["kind"] == "config":
            staged = target.with_name(".vool-update-config-restore")
            if staged.exists():
                shutil.rmtree(staged)
            if item["present"]:
                shutil.copytree(saved, staged)
            if target.exists():
                shutil.rmtree(target)
            if item["present"]:
                os.replace(staged, target)
        else:
            staged = target.with_name(target.name + ".vool-restore")
            shutil.copy2(saved, staged)
            for suffix in ("-wal", "-shm"):
                Path(str(target) + suffix).unlink(missing_ok=True)
            os.replace(staged, target)
