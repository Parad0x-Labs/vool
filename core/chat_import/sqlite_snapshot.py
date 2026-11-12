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
import time
from collections.abc import Iterator
from pathlib import Path

#: WAL holds committed new pages; a rollback journal holds the committed pages an
#: active transaction replaced. Both are needed to read only committed state.
_COMPANIONS = ("-wal", "-journal")
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
            signature.append((path.name, None, None, None, None))
            continue
        signature.append((path.name, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns))
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
        # Never open the source with SQLite: even mode=ro may change its shared-memory file.
        # Apply the captured WAL in our private copy, then use SQLite's backup API to obtain
        # one normalized database. The only writable connections belong to this temp folder.
        connection = None
        captured = None
        try:
            captured = sqlite3.connect(copy, timeout=1.0)
            captured.execute("PRAGMA query_only = ON")
            normalized = Path(temp_root) / "snapshot.db"
            connection = sqlite3.connect(normalized, timeout=1.0)
            deadline = time.monotonic() + 5.0
            page_size = int(captured.execute("PRAGMA page_size").fetchone()[0])

            def progress(status: int, remaining: int, total: int) -> None:
                if time.monotonic() > deadline:
                    raise SnapshotError("store_busy")
                if total * page_size > max_bytes:
                    raise SnapshotError("store_too_large")

            captured.backup(connection, pages=256, progress=progress, sleep=0.01)
            captured.close()
            captured = None
            if normalized.stat().st_size > max_bytes:
                raise SnapshotError("store_too_large")
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("PRAGMA query_only = ON")
            connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        except (sqlite3.DatabaseError, SnapshotError) as exc:
            if connection is not None:
                connection.close()
            if isinstance(exc, SnapshotError):
                raise
            raise SnapshotError("store_unreadable") from None
        finally:
            if captured is not None:
                captured.close()
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
