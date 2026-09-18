"""The storage lifecycle contract for the runtime home's SQLite store.

P0 chat-lifecycle defect (checkpoint D, 2026-09-03): the previous thread-local connection
pool kept WAL-mode connections attached across operations while short-lived helper processes
(seed scripts, CLI tools, test rigs) legitimately checkpoint + DELETE the ``-wal``/``-shm``
at their own last close. SQLite's cross-process guard for that deletion is per-process fcntl
state, which a multithreaded churning daemon cannot keep intact, so helpers pulled live WAL
generations out from under idle pooled connections: committed bytes landed in an unlinked WAL
no other process could read, and every fresh open then failed process-wide with
``sqlite3.OperationalError: disk I/O error`` at ``_make_connection`` (``PRAGMA
synchronous=NORMAL`` — ``_enable_wal``'s deliberate fail-soft swallows the first hit) until
the process restarted.

The owning contract asserted here: connections are PER-USE. No idle attached state outlives
an operation, so a helper's last-close always lands in a healthy state — SQLite skips the
deletion whenever any connection is live mid-operation (its locks are held) and checkpoints
committed bytes into the main database before deleting otherwise — and every new open
resolves the store fresh, so no stale connection and no stale WAL generation can survive a
home/path replacement.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

from core.runtime_paths import configure_runtime_home
from storage.db import (
    active_default_db_path,
    configure_default_db_path,
    get_connection,
    reset_default_connection,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _isolated_home(tmp_path: Path) -> None:
    configure_default_db_path(None)
    reset_default_connection()
    configure_runtime_home(tmp_path / "home")


def _restore_home(original_db: str) -> None:
    reset_default_connection()
    configure_runtime_home(None)
    configure_default_db_path(original_db)


def _read_rows_from_fresh_process(db_path: Path, table: str) -> list[str]:
    """Ground truth from OUTSIDE this process: a brand-new reader with no shared fds."""
    code = (
        "import json, sqlite3, sys\n"
        "conn = sqlite3.connect(sys.argv[1])\n"
        "rows = [r[0] for r in conn.execute(f'SELECT v FROM {sys.argv[2]} ORDER BY id')]\n"
        "print(json.dumps(rows))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code, str(db_path), table],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return list(json.loads(completed.stdout.strip().splitlines()[-1]))


def test_store_generation_survives_helper_processes_and_writes_stay_externally_visible(
    tmp_path: Path,
) -> None:
    """A holder in this process plus helper processes that open/write/close the same store:
    the live generation stays coherent and every committed write is externally visible."""
    original_db = active_default_db_path()
    _isolated_home(tmp_path)
    try:
        db_path = Path(active_default_db_path())

        holder_thread_result: dict[str, object] = {}

        def hold() -> None:
            conn = get_connection()
            conn.execute("CREATE TABLE wal_generation_probe (id INTEGER PRIMARY KEY, v TEXT NOT NULL)")
            conn.execute("INSERT INTO wal_generation_probe (v) VALUES ('from-runtime')")
            conn.commit()
            holder_thread_result["conn"] = conn

        holder = threading.Thread(target=hold)
        holder.start()
        holder.join(30)
        assert "conn" in holder_thread_result

        # A helper process the way the runtime spawns them: storage.db end to end.
        helper = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    f"import sys; sys.path.insert(0, {str(REPO_ROOT)!r})\n"
                    "from core.runtime_paths import configure_runtime_home\n"
                    f"configure_runtime_home({str(Path(active_default_db_path()).parent.parent)!r})\n"
                    "from storage.db import get_connection\n"
                    "conn = get_connection()\n"
                    "conn.execute('INSERT INTO wal_generation_probe (v) VALUES (\"helper\")')\n"
                    "conn.commit()\n"
                    "conn.close()\n"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        )
        assert helper.returncode == 0, helper.stderr[-400:]

        # The same store still answers this process, with the helper's committed write.
        def read_back() -> None:
            conn = get_connection()
            rows = conn.execute("SELECT v FROM wal_generation_probe ORDER BY id").fetchall()
            holder_thread_result["rows"] = [str(row[0]) for row in rows]

        reader = threading.Thread(target=read_back)
        reader.start()
        reader.join(30)
        assert holder_thread_result.get("rows") == ["from-runtime", "helper"], holder_thread_result

        # Externally visible: a brand-new process reads both committed writes.
        assert _read_rows_from_fresh_process(db_path, "wal_generation_probe") == [
            "from-runtime",
            "helper",
        ]
    finally:
        _restore_home(original_db)


def test_store_replaced_at_the_same_path_never_keeps_the_old_connection(tmp_path: Path) -> None:
    """Home/path replacement: a restore over the same absolute path must be picked up by the
    very next connection — a per-use open resolves the store fresh every call."""
    original_db = active_default_db_path()
    _isolated_home(tmp_path)
    try:
        db_path = Path(active_default_db_path())

        first = get_connection()
        first.execute("CREATE TABLE replaced_probe (id INTEGER PRIMARY KEY, v TEXT NOT NULL)")
        first.execute("INSERT INTO replaced_probe (v) VALUES ('old-store')")
        first.commit()
        first.close()

        # External replacement: old bytes gone, a DIFFERENT store takes the same path.
        for suffix in ("-wal", "-shm", ""):
            candidate = Path(str(db_path) + suffix)
            if candidate.exists():
                candidate.unlink()
        replacement = sqlite3.connect(str(db_path))
        replacement.execute("PRAGMA journal_mode=WAL")
        replacement.execute("CREATE TABLE replaced_probe (id INTEGER PRIMARY KEY, v TEXT NOT NULL)")
        replacement.execute("INSERT INTO replaced_probe (v) VALUES ('new-store')")
        replacement.commit()
        replacement.close()

        conn = get_connection()
        rows = conn.execute("SELECT v FROM replaced_probe ORDER BY id").fetchall()
        conn.close()
        assert [str(row[0]) for row in rows] == ["new-store"], rows
    finally:
        _restore_home(original_db)


def test_a_committed_write_is_visible_to_a_later_connection_in_another_thread(
    tmp_path: Path,
) -> None:
    """Commit-before-close is the visibility boundary: a committed row reaches every later
    connection, in any thread and any process; nothing rides on connection reuse."""
    original_db = active_default_db_path()
    _isolated_home(tmp_path)
    try:
        db_path = Path(active_default_db_path())
        writer_result: dict[str, object] = {}

        started = threading.Event()

        def write_and_close() -> None:
            conn = get_connection()
            conn.execute("CREATE TABLE visibility_probe (id INTEGER PRIMARY KEY, v TEXT NOT NULL)")
            conn.execute("INSERT INTO visibility_probe (v) VALUES ('committed')")
            conn.commit()
            conn.close()
            writer_result["done"] = True
            started.set()

        writer = threading.Thread(target=write_and_close)
        writer.start()
        assert started.wait(30)

        conn = get_connection()
        rows = conn.execute("SELECT v FROM visibility_probe ORDER BY id").fetchall()
        conn.close()
        assert [str(row[0]) for row in rows] == ["committed"]
        assert _read_rows_from_fresh_process(db_path, "visibility_probe") == ["committed"]
    finally:
        _restore_home(original_db)
