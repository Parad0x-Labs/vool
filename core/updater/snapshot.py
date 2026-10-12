"""Consistent runtime DB/config and file-history backups at the update boundary."""
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
    files = []
    for directory, dirs, names in os.walk(data, followlinks=False):
        if Path(directory) == data:
            dirs[:] = [name for name in dirs if name != 'update_v2']
        if any((Path(directory) / name).is_symlink() for name in [*dirs, *names]):
            raise ValueError('runtime data contains a symlink; backup refused')
        files.extend(Path(directory) / name for name in names)
    databases = [p for p in files if p.suffix.lower() in ('.db', '.sqlite', '.sqlite3')]
    if len(databases) > 64 or len(files) > 100000 or sum(p.stat().st_size for p in files) > 8 * 1024**3:
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
    sidecars = {Path(str(path) + suffix) for path in databases for suffix in ('-wal', '-shm', '-journal')}
    for path in sorted(set(files) - set(databases) - sidecars):
        if not path.is_file():
            raise ValueError('runtime history backup requires regular files')
        relative = path.relative_to(home)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        os.chmod(target, 0o600)
        entries.append({'path': relative.as_posix(), 'kind': 'file', 'present': True})
    (destination / "index.json").write_text(json.dumps(entries, sort_keys=True) + "\n", encoding="utf-8")


def restore_runtime(data_dir: Path, source: Path) -> None:
    """Restore this snapshot's DB/config/history. Caller must have stopped the app.

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
            target.parent.mkdir(parents=True, exist_ok=True)
            staged = target.with_name(target.name + ".vool-restore")
            shutil.copy2(saved, staged)
            if item['kind'] == 'sqlite':
                for suffix in ("-wal", "-shm", '-journal'):
                    Path(str(target) + suffix).unlink(missing_ok=True)
            os.replace(staged, target)
