"""Concurrent-first-turn P0 (final amendment): run_migrations is a lifecycle singleton.

The empty-body HTTP 500 during simultaneous first post-restart turns, attributed
with per-request daemon generations (ops/restart_repro/evidence, final amendment):

    agent.py:1466  load_active_persona        <- lazy, on the FIRST turn
    identity_manager.py:50  run_migrations()
    migrations.py:1694  _snapshot_db_files
    migrations.py:1656  shutil.copy2(live, snap)   <- shared "<db>.pre-migration" path
    shutil.copystat -> FileNotFoundError

Two concurrent first turns both entered run_migrations; the finisher's
`finally: _discard_db_snapshot` unlinked the shared snapshot between the other
thread's copyfile and copystat, and the chat dispatch served the raw OS error as
a 500. The owner is run_migrations: a process-wide, run-once lifecycle step that
concurrent first turns must not race.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from storage.migrations import run_migrations


def _scratch_db(tmp_path: Path) -> Path:
    db = tmp_path / "concurrent-migrations.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    # A WAL sidecar triples the snapshot surface — three shared .pre-migration
    # paths per call instead of one.
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("INSERT INTO probe (id) VALUES (1)")
    conn.commit()
    conn.close()
    return db


def test_concurrent_run_migrations_never_corrupt_or_raise(tmp_path: Path) -> None:
    """Many threads, many rounds, one db: no thread may see another thread's
    snapshot disappear mid-copy. At base this raises (FileNotFoundError wrapped
    in StoreMigrationError) within rounds."""

    db = _scratch_db(tmp_path)
    errors: list[BaseException] = []

    def _worker(rounds: int) -> None:
        for _index in range(rounds):
            try:
                run_migrations(db, force=True)
            except BaseException as exc:  # recorded, not swallowed
                errors.append(exc)
                return

    workers = [threading.Thread(target=_worker, args=(24,)) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=60.0)
    assert not errors, errors[:3]
    residue = list(tmp_path.glob("*.pre-migration*"))
    assert not residue, f"snapshot residue after all rounds: {residue}"


def test_run_migrations_still_repairs_a_regressed_store(tmp_path: Path) -> None:
    """A completed migration must NOT become a no-op forever: repair flows
    (tests/test_migrations_backward_compat.py) regress the store deliberately
    and depend on a later call re-running the body. Pin both sides: the store
    version is restamped after regression, and the repair is race-free when a
    concurrent burst follows it."""

    import sqlite3

    from storage.db import STORE_USER_VERSION, get_connection

    db = _scratch_db(tmp_path)
    run_migrations(db)

    conn = sqlite3.connect(db)
    conn.execute(f"PRAGMA user_version = {STORE_USER_VERSION - 1};")
    conn.commit()
    conn.close()

    errors: list[BaseException] = []

    def _repair_worker() -> None:
        try:
            run_migrations(db)
        except BaseException as exc:  # recorded, not swallowed
            errors.append(exc)

    workers = [threading.Thread(target=_repair_worker) for _ in range(6)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=60.0)
    assert not errors, errors[:3]

    conn = get_connection(db)
    try:
        version = int(conn.execute("PRAGMA user_version;").fetchone()[0])
    finally:
        conn.close()
    assert version == STORE_USER_VERSION, "the regressed store was not repaired"


# ---------------------------------------------------------------------------
# Deterministic guard pins -- the burst above is the honest end-to-end check,
# but the race window is narrow; these two catch a silent guard revert exactly.
# ---------------------------------------------------------------------------


def test_the_singleton_lock_is_held_during_the_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lock-bypass sabotage (call the body without the lock) must be visible:
    the body observes the module lock as HELD for its whole duration."""

    import storage.migrations as migrations

    db = _scratch_db(tmp_path)
    observed: list[bool] = []
    original = migrations._run_migrations_locked

    def _observing_body(db_file: Path, *, db_path=None, force: bool = False) -> None:
        observed.append(migrations._RUN_MIGRATIONS_LOCK.locked())
        original(db_file, db_path=db_path, force=force)
        observed.append(migrations._RUN_MIGRATIONS_LOCK.locked())

    monkeypatch.setattr(migrations, "_run_migrations_locked", _observing_body)
    run_migrations(db)
    assert observed == [True, True], f"the migration body ran without the singleton lock: {observed}"


def test_snapshot_paths_are_private_per_invocation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Shared-path sabotage (every invocation snapshots to '<db>.pre-migration')
    must be visible: two OVERLAPPING invocations must produce disjoint snapshot
    paths. A bounded rendezvous inside copy2 makes the overlap deterministic in
    both directions -- under the fix the invocations serialize (lock) and the
    rendezvous times out harmlessly."""

    import shutil as shutil_module

    import storage.migrations as migrations

    db = _scratch_db(tmp_path)
    other_copying = threading.Event()
    first_saw_other = threading.Event()

    def _rendezvous_copy2(src, dst, **kwargs):
        real_copyfile = shutil_module.copyfile
        real_copyfile(src, dst, **kwargs)
        # Hold the window OPEN between copyfile and copystat until the other
        # invocation has also landed here (or 1s passes: the serialized case).
        other_copying.set()
        first_saw_other.wait(timeout=1.0)
        return shutil_module.copystat(src, dst)

    monkeypatch.setattr(shutil_module, "copy2", _rendezvous_copy2)

    results: dict[str, list[tuple[Path, Path]]] = {}

    def _snapshot_worker(key: str) -> None:
        results[key] = migrations._snapshot_db_files(db)

    workers = [
        threading.Thread(target=_snapshot_worker, args=("a",)),
        threading.Thread(target=_snapshot_worker, args=("b",)),
    ]
    for worker in workers:
        worker.start()
    other_copying.wait(timeout=2.0)
    first_saw_other.set()
    for worker in workers:
        worker.join(timeout=10.0)

    paths_a = {snap for snap, _ in results.get("a", [])}
    paths_b = {snap for snap, _ in results.get("b", [])}
    assert paths_a and paths_b, results
    assert not (paths_a & paths_b), (
        f"two overlapping invocations shared snapshot paths: {paths_a & paths_b}"
    )
    for snap in paths_a | paths_b:
        snap.unlink(missing_ok=True)


@pytest.mark.parametrize('target_mode', ['runtime_home', 'configured_database'])
def test_legacy_default_migration_snapshots_the_active_database(tmp_path, monkeypatch, target_mode):
    """The legacy source-path sentinel must never redirect a backup into the app bundle."""
    import storage.db as db
    import storage.migrations as migrations
    from core import runtime_paths

    source = tmp_path / 'packaged-source' / 'legacy.db'
    source.parent.mkdir()
    source.write_bytes(b'immutable packaged source marker')
    home = tmp_path / 'profile'
    active = home / 'data' / 'vool_web0_v2.db' if target_mode == 'runtime_home' else tmp_path / 'selected' / 'runtime.db'
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    monkeypatch.setattr(db, '_FROZEN_DEFAULT_DB_PATH', str(source))
    monkeypatch.setattr(db, '_DEFAULT_DB_PATH_OVERRIDE', None if target_mode == 'runtime_home' else str(active))
    snapshots = []
    snapshot = migrations._snapshot_db_files

    def observe_snapshot(path):
        snapshots.append(path.resolve())
        return snapshot(path)

    monkeypatch.setattr(migrations, '_snapshot_db_files', observe_snapshot)
    run_migrations(str(source))
    assert snapshots == [active.resolve()]
    assert source.read_bytes() == b'immutable packaged source marker'
    conn = sqlite3.connect(active)
    try:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == migrations.ledger_head_version()
    finally:
        conn.close()


def test_failed_legacy_default_migration_restores_the_selected_database(tmp_path, monkeypatch):
    import storage.db as db
    import storage.migrations as migrations

    active = _scratch_db(tmp_path)
    before = active.read_bytes()
    source = tmp_path / 'packaged-source.db'
    source.write_bytes(b'preserved source bytes')
    monkeypatch.setattr(db, '_FROZEN_DEFAULT_DB_PATH', str(source))
    monkeypatch.setattr(db, '_DEFAULT_DB_PATH_OVERRIDE', str(active))
    # executescript commits the preceding statements before the missing-table failure.
    # A rollback of the connection alone cannot replace the pre-migration snapshot.
    monkeypatch.setattr(migrations, 'SCHEMA_SQL',
        'CREATE TABLE migration_probe (value TEXT); '
        "INSERT INTO migration_probe VALUES ('partial migration'); "
        'SELECT * FROM absent_migration_table;')
    with pytest.raises(migrations.StoreMigrationError):
        run_migrations(str(source), force=True)
    assert active.read_bytes() == before
    assert source.read_bytes() == b'preserved source bytes'
    assert not list(tmp_path.glob('*.pre-migration*'))
