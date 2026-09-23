"""First use of one store by several PROCESSES at once.

`run_migrations` is a run-once lifecycle step guarded by a per-process lock
(`tests/test_run_migrations_singleton.py`). Processes do not share that lock.
Measured at the pinned base: six fresh processes running their first-use
migration while a seventh commits rows lost committed rows — a migrator that
failed on another migrator's concurrent ALTER restored its pre-migration byte
snapshot over the live database and unlinked the WAL under everyone else —
and left the writer failing every later open with "disk I/O error".

For money this is not cosmetic: a restored snapshot rewinds committed
reservations, which reopens money that was held.

Each process here is an independent `python -B` interpreter; nothing is a
thread standing in for a process.

The lock is the repo's one cross-process lock authority (`core.cross_process_lock`),
which never waits and never yields unlocked: a contended migration retries up to a
wall-clock bound and is then refused before any snapshot, and a store already at
this binary's contract never takes the lock at all.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="these independent-process proofs run on POSIX only; the lock itself is the shared authority's msvcrt branch on Windows",
)

_MIGRATOR = """
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from storage.migrations import run_migrations
go = Path(sys.argv[3])
Path(sys.argv[3] + ".ready-" + sys.argv[4]).write_text("ready")
while not go.exists():
    time.sleep(0.001)
try:
    run_migrations(sys.argv[2])
    print(json.dumps({"migrated": True}))
except Exception as exc:
    print(json.dumps({"migrated": False, "error": type(exc).__name__, "detail": str(exc)[:200]}))
"""

_WRITER = """
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from storage.db import get_connection
go = Path(sys.argv[3])
Path(sys.argv[3] + ".ready-writer").write_text("ready")
while not go.exists():
    time.sleep(0.001)
committed, errors = 0, []
deadline = time.monotonic() + float(sys.argv[4])
while time.monotonic() < deadline:
    conn = None
    try:
        conn = get_connection(sys.argv[2])
        # The caller owns the transaction (storage.db.get_connection's contract).
        # Measured on the runner (instrumented capture, run 35622837193, SQLite
        # 3.45.1): the bare autocommit CREATE TABLE below raised
        # OperationalError SQLITE_SCHEMA (17, "database schema has changed")
        # exactly once per round, in all four rounds; runs 35615031134 and
        # 35626506389 leg A showed the same failing assertion. The interleaving
        # was NOT traced directly: SQLITE_SCHEMA's documented meaning is that
        # the schema changed after the statement was prepared, so "a concurrent
        # first-use migration committed while this statement was in flight" is
        # an inference from that documented semantics. BEGIN IMMEDIATE takes
        # the database's write lock before the iteration's schema-touching work
        # (documented SQLite locking -- the production stores already serialize
        # their write paths on it), so each migration commit lands entirely
        # before or after the iteration.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TABLE IF NOT EXISTS committed_probe (v INTEGER NOT NULL)")
        conn.execute("INSERT INTO committed_probe (v) VALUES (?)", (committed,))
        conn.commit()
        conn.close()
        conn = None
        committed += 1
    except Exception as exc:
        errors.append(type(exc).__name__ + ": " + str(exc)[:80])
        # The iteration failed: undo its own partial statements and release the
        # handle. Rollback or close problems are themselves recorded, never
        # swallowed -- nothing here hides the original finding.
        if conn is not None:
            try:
                conn.rollback()
            except Exception as rollback_exc:
                errors.append(type(rollback_exc).__name__ + ": " + str(rollback_exc)[:80])
            try:
                conn.close()
            except Exception as close_exc:
                errors.append(type(close_exc).__name__ + ": " + str(close_exc)[:80])
    time.sleep(0.003)
print(json.dumps({"committed": committed, "errors": len(errors), "error_kinds": sorted(set(errors))[:5]}))
"""


def _round(root: Path, index: int, *, migrators: int = 8, write_seconds: float = 4.0) -> dict:
    home = root / f"round-{index}"
    home.mkdir(parents=True)
    db = home / "store.db"
    barrier = home / "go"
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "VOOL_HOME": str(home / "vool-home"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    processes = [
        subprocess.Popen(
            [sys.executable, "-B", "-c", _MIGRATOR, str(REPO_ROOT), str(db), str(barrier), str(n)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        for n in range(migrators)
    ]
    writer = subprocess.Popen(
        [sys.executable, "-B", "-c", _WRITER, str(REPO_ROOT), str(db), str(barrier), str(write_seconds)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    deadline = time.monotonic() + 120.0
    while len(list(home.glob("go.ready-*"))) < migrators + 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    barrier.write_text("go")
    migrated = []
    for process in processes:
        out, err = process.communicate(timeout=240)
        line = (out.strip().splitlines() or ["{}"])[-1]
        try:
            result = json.loads(line)
        except ValueError:
            result = {"migrated": False, "error": "no_result", "detail": err[-300:]}
        result["returncode"] = process.returncode
        migrated.append(result)
    out, err = writer.communicate(timeout=240)
    try:
        written = json.loads((out.strip().splitlines() or ["{}"])[-1])
    except ValueError:
        written = {"committed": -1, "errors": -1, "stderr": err[-300:]}
    written["returncode"] = writer.returncode
    sys.path.insert(0, str(REPO_ROOT))
    from storage.db import get_connection

    table_present = True
    try:
        conn = get_connection(db)
        try:
            table_present = (
                conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='committed_probe'").fetchone()
                is not None
            )
            # a table the writer created and committed that is now absent was rewound
            # with everything in it: that reads as zero rows present, and says so
            present = int(conn.execute("SELECT COUNT(*) FROM committed_probe").fetchone()[0]) if table_present else 0
            integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            conn.close()
    except Exception as exc:  # a store the parent cannot even open is itself the finding
        present, integrity = -1, f"{type(exc).__name__}: {exc}"
    return {
        "migrators": migrated,
        "writer": written,
        "present": present,
        "committed_table_present": table_present,
        "integrity": integrity,
        "snapshot_residue": sorted(path.name for path in home.glob("*pre-migration*")),
    }


def test_concurrent_first_use_migrations_lose_no_committed_row_and_poison_no_process(tmp_path: Path) -> None:
    for index in range(4):
        outcome = _round(tmp_path, index)
        assert all(item.get("migrated") is True and item["returncode"] == 0 for item in outcome["migrators"]), outcome
        assert outcome["writer"]["returncode"] == 0 and outcome["writer"]["errors"] == 0, outcome
        assert outcome["writer"]["committed"] > 0, outcome
        assert outcome["present"] == outcome["writer"]["committed"], f"committed rows were rewound: {outcome}"
        assert outcome["integrity"] == "ok", outcome
        assert outcome["snapshot_residue"] == [], outcome


def test_a_failed_caller_transaction_rolls_back_its_own_changes_and_committed_rows_survive(
    tmp_path: Path,
) -> None:
    """The caller-owned transaction boundary cuts both ways: a statement that fails
    inside an explicit BEGIN IMMEDIATE must undo the WHOLE caller transaction --
    including that transaction's own earlier statements -- while rows committed by
    earlier transactions survive untouched and the store stays openable."""
    import sqlite3

    from storage.db import get_connection

    db = tmp_path / "caller-tx.db"
    committed_conn = get_connection(db)
    try:
        committed_conn.execute("BEGIN IMMEDIATE")
        committed_conn.execute("CREATE TABLE IF NOT EXISTS committed_probe (v INTEGER NOT NULL)")
        committed_conn.execute("INSERT INTO committed_probe (v) VALUES (1)")
        committed_conn.commit()
    finally:
        committed_conn.close()

    conn = get_connection(db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO committed_probe (v) VALUES (2)")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO committed_probe (v) VALUES (NULL)")
        conn.rollback()
    finally:
        conn.close()

    after = get_connection(db)
    try:
        assert int(after.execute("SELECT COUNT(*) FROM committed_probe").fetchone()[0]) == 1
        assert int(after.execute("SELECT v FROM committed_probe").fetchone()[0]) == 1
        assert str(after.execute("PRAGMA integrity_check").fetchone()[0]) == "ok"
    finally:
        after.close()


def test_an_up_to_date_store_is_never_snapshotted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The byte snapshot exists to undo a FAILED upgrade. A store already at
    this binary's contract has nothing to upgrade, so copying its live bytes
    (and being able to restore them over later writes) is pure hazard."""
    from storage import migrations

    db = tmp_path / "current.db"
    migrations.run_migrations(db)
    calls: list[Path] = []
    original = migrations._snapshot_db_files

    def _recording(db_file: Path):
        calls.append(db_file)
        return original(db_file)

    monkeypatch.setattr(migrations, "_snapshot_db_files", _recording)
    migrations.run_migrations(db)
    assert calls == [], "an up-to-date store was snapshotted"
    migrations.run_migrations(db, force=True)
    assert calls == [db.resolve()] or calls == [db], "force still runs the guarded body"


_HOLDER = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from core.cross_process_lock import PublicationLock
ready, release, hold = Path(sys.argv[3]), Path(sys.argv[4]), float(sys.argv[5])
with PublicationLock(sys.argv[2]):
    ready.write_text("held")
    deadline = time.monotonic() + hold
    while time.monotonic() < deadline and not release.exists():
        time.sleep(0.01)
    released_at = time.time()
print(released_at)
"""


class _LockHolder:
    """An independent process holding one store's migration lock through the shared authority."""

    def __init__(self, root: Path, db: Path, *, hold_seconds: float) -> None:
        self.ready = root / f"{db.name}.holder-ready"
        self.release = root / f"{db.name}.holder-release"
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(root),
            "VOOL_HOME": str(root / "vool-home"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-c",
                _HOLDER,
                str(REPO_ROOT),
                str(db) + ".migration.lock",
                str(self.ready),
                str(self.release),
                str(hold_seconds),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        deadline = time.monotonic() + 60.0
        while not self.ready.exists():
            if self.process.poll() is not None or time.monotonic() > deadline:
                _out, err = self.process.communicate(timeout=10)
                raise AssertionError(f"the lock holder never took the lock (rc={self.process.returncode}): {err[-400:]}")
            time.sleep(0.01)

    def finish(self) -> float:
        self.release.write_text("release")
        out, err = self.process.communicate(timeout=60)
        assert self.process.returncode == 0, err[-400:]
        return float(out.strip().splitlines()[-1])


def _user_version(db: Path) -> int:
    import sqlite3

    conn = sqlite3.connect(db)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def test_the_migration_lock_is_the_shared_fail_closed_authority() -> None:
    """One lock authority: storage migrations acquire through core.cross_process_lock and
    import no private locking primitive."""
    import inspect

    from storage import migrations

    assert "import fcntl" not in inspect.getsource(migrations), "storage.migrations imports fcntl directly"
    assert "PublicationLock" in inspect.getsource(migrations._cross_process_migration_lock)


def test_a_store_that_cannot_be_locked_is_never_migrated_unlocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No locking primitive importable (the shape of a platform without fcntl): the migration
    is refused with a typed error and leaves no stamped store and no snapshot behind."""
    import builtins

    from storage import migrations

    real_import = builtins.__import__

    def refusing_import(name, *args, **kwargs):
        if name in ("fcntl", "msvcrt"):
            raise ImportError(f"{name} is not available here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(migrations, "MIGRATION_LOCK_WAIT_SECONDS", 0.3, raising=False)
    db = tmp_path / "unlockable.db"
    monkeypatch.setattr(builtins, "__import__", refusing_import)
    try:
        with pytest.raises(migrations.StoreMigrationError) as refused:
            migrations.run_migrations(db)
    finally:
        monkeypatch.setattr(builtins, "__import__", real_import)
    assert refused.value.code == "VOOL_E_MIGRATION_LOCK_UNAVAILABLE", refused.value
    assert not db.exists() or _user_version(db) == 0, "the store was migrated without the lock"
    assert list(tmp_path.glob("*pre-migration*")) == []


def test_a_held_migration_lock_is_refused_after_the_wait_and_the_store_is_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from storage import migrations

    db = tmp_path / "contended.db"
    monkeypatch.setattr(migrations, "MIGRATION_LOCK_WAIT_SECONDS", 0.5, raising=False)
    holder = _LockHolder(tmp_path, db, hold_seconds=10.0)
    try:
        started = time.monotonic()
        with pytest.raises(migrations.StoreMigrationError) as refused:
            migrations.run_migrations(db)
        waited = time.monotonic() - started
        holder_still_held = holder.process.poll() is None
    finally:
        holder.finish()
    assert refused.value.code == "VOOL_E_MIGRATION_LOCK_UNAVAILABLE", refused.value
    assert holder_still_held and waited >= 0.5, f"refused after {waited:.3f}s (holder alive: {holder_still_held})"
    assert not db.exists(), "a refused migration created the store"
    assert list(tmp_path.glob("*pre-migration*")) == []
    monkeypatch.setattr(migrations, "MIGRATION_LOCK_WAIT_SECONDS", 30.0, raising=False)
    migrations.run_migrations(db)
    assert _user_version(db) == migrations.ledger_head_version(), "the next use after the holder left did not migrate"


def test_a_migration_waits_for_another_process_to_release_the_lock_and_then_runs(tmp_path: Path) -> None:
    from storage import migrations

    db = tmp_path / "waited.db"
    holder = _LockHolder(tmp_path, db, hold_seconds=0.8)
    migrations.run_migrations(db)
    finished_at = time.time()
    released_at = holder.finish()
    assert finished_at >= released_at, "the migration completed while another process still held its lock"
    assert _user_version(db) == migrations.ledger_head_version()


def test_a_current_store_never_waits_on_the_migration_lock(tmp_path: Path) -> None:
    from storage import migrations

    db = tmp_path / "current-held.db"
    migrations.run_migrations(db)
    holder = _LockHolder(tmp_path, db, hold_seconds=20.0)
    try:
        started = time.monotonic()
        migrations.run_migrations(db)
        elapsed = time.monotonic() - started
        still_held = holder.process.poll() is None
    finally:
        holder.finish()
    assert still_held and elapsed < 5.0, f"a current store waited {elapsed:.3f}s on another process's migration lock"


def _vanishing_live_file_during_copystat(monkeypatch: pytest.MonkeyPatch, suffix: str) -> None:
    """shutil.copy2 is copyfile then copystat; this removes the live file in between, the moment
    another process's last connection close unlinks a sidecar."""
    import shutil

    real_copystat = shutil.copystat

    def copystat(src, dst, *args, **kwargs):
        if str(src).endswith(suffix):
            Path(src).unlink()
        return real_copystat(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copystat", copystat)


def test_a_sidecar_that_vanishes_mid_copy_leaves_no_snapshot_behind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Observed in the cross-process race as a stray store.db-wal.pre-migration-<pid>-<thread>: the
    partial snapshot of a sidecar that vanished after copyfile was in no returned pair, so nothing ever
    removed it."""
    from storage import migrations

    db = tmp_path / "store.db"
    for name, payload in (("store.db", b"main"), ("store.db-wal", b"wal"), ("store.db-shm", b"shm")):
        (tmp_path / name).write_bytes(payload)
    _vanishing_live_file_during_copystat(monkeypatch, "-wal")
    pairs = migrations._snapshot_db_files(db)
    assert [live.name for _snap, live in pairs] == ["store.db", "store.db-shm"]
    assert sorted(path.name for path in tmp_path.glob("store.db-wal.pre-migration*")) == []
    migrations._discard_db_snapshot(pairs)
    assert sorted(path.name for path in tmp_path.glob("*pre-migration*")) == []


def test_a_migration_whose_shm_vanishes_mid_snapshot_still_migrates_and_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    from storage import migrations

    db = tmp_path / "vanishing.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE seed (v INTEGER)")
    conn.commit()
    conn.close()
    (tmp_path / "vanishing.db-wal").write_bytes(b"")
    (tmp_path / "vanishing.db-shm").write_bytes(b"")
    _vanishing_live_file_during_copystat(monkeypatch, "-shm")
    migrations.run_migrations(db)
    monkeypatch.undo()
    assert _user_version(db) == migrations.ledger_head_version()
    assert sorted(path.name for path in tmp_path.glob("*pre-migration*")) == []
