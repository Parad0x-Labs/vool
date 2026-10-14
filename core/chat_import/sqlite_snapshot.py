"""Read another app's live SQLite store without ever writing to it.

The other app may be running and writing in WAL mode. Opening its file directly -- even with
``mode=ro`` -- can touch its shared-memory file, and ``immutable=1`` ignores the write-ahead log
so recent rows go missing. Instead the database and its ``-wal`` log are copied together into a
private temporary folder (owner-only), the copy is opened there, and the folder is removed when
the read ends. The ``-shm`` file is only an index of the log; it is not copied, so SQLite rebuilds
it from the copied log instead of trusting an index another process was updating. The copy is taken again if the source changed while it was copied; a
store that keeps changing is reported as busy rather than read half-written.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import sqlite3
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path

#: The write-ahead log SQLite keeps beside a WAL database; it holds the newest rows.
_COMPANIONS = ("-wal",)
_ATTEMPTS = 3


class SnapshotError(Exception):
    """A store could not be copied safely. ``code`` is a ReadResult problem code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _signature(paths: list[Path]) -> tuple:
    signature = []
    for path in paths:
        try:
            info = path.stat()
        except FileNotFoundError:
            signature.append((path.name, None, None))
            continue
        signature.append((path.name, info.st_size, info.st_mtime_ns))
    return tuple(signature)


def store_size(path: Path) -> int:
    """Bytes of the database plus its companions (missing companions count zero)."""
    total = 0
    for candidate in [path] + [path.with_name(path.name + suffix) for suffix in _COMPANIONS]:
        with contextlib.suppress(OSError):
            total += candidate.stat().st_size
    return total


def _is_regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


@contextlib.contextmanager
def snapshot_connection(path: Path, *, max_bytes: int, work_dir: str | os.PathLike[str] | None = None) -> Iterator[sqlite3.Connection]:
    """A read-only connection to a private, consistent copy of the SQLite store at ``path``.

    The source files are only ever opened for reading (``shutil.copyfile``). Raises
    ``SnapshotError`` with a problem code when the store is missing, too large, a link, keeps
    changing during the copy, or is not a readable SQLite database.
    """
    source = Path(path)
    if not _is_regular_file(source):
        raise SnapshotError("store_missing")
    if store_size(source) > max_bytes:
        raise SnapshotError("store_too_large")
    temp_root = tempfile.mkdtemp(prefix="vool-chat-import-", dir=os.fspath(work_dir) if work_dir else None)
    try:
        os.chmod(temp_root, 0o700)
        copy = Path(temp_root) / "store.db"
        sources = [source] + [source.with_name(source.name + suffix) for suffix in _COMPANIONS]
        copied = False
        for _attempt in range(_ATTEMPTS):
            before = _signature(sources)
            for item in sources:
                target = copy.with_name(copy.name + item.name[len(source.name):])
                with contextlib.suppress(FileNotFoundError):
                    target.unlink()
                if item is not source and not item.exists():
                    continue
                if item is not source and not _is_regular_file(item):
                    raise SnapshotError("store_unreadable")
                try:
                    shutil.copyfile(item, target)
                except FileNotFoundError:
                    if item is source:
                        raise SnapshotError("store_missing") from None
                except OSError:
                    raise SnapshotError("store_unreadable") from None
            if _signature(sources) == before:
                copied = True
                break
        if not copied:
            raise SnapshotError("store_busy")
        # The copy is ours: opening it read-write lets SQLite apply the copied write-ahead log
        # (that is how the newest rows become visible); the connection is then made read-only.
        try:
            connection = sqlite3.connect(copy, timeout=1.0)
            connection.execute("PRAGMA query_only = ON")
            connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        except sqlite3.DatabaseError:
            raise SnapshotError("store_unreadable") from None
        try:
            yield connection
        finally:
            connection.close()
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    """Column names of ``table`` (empty when the table does not exist)."""
    try:
        rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    except sqlite3.DatabaseError:
        return set()
    return {str(row[1]) for row in rows}


def table_names(connection: sqlite3.Connection) -> set[str]:
    try:
        return {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    except sqlite3.DatabaseError:
        return set()
