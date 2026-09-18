"""STORE_USER_VERSION is owned by the migration authority's version ledger.

Pre-demo candidate blocker (2026-09-02): slice 8 made ``run_migrations`` a versioned no-op — a
database whose ``PRAGMA user_version`` already equals the binary's contract is left untouched —
but the contract stayed at 3, the value stamped since ``19762f15``. Every schema object added
between that stamp and slice 8 (``obligation_sets``, ``semantic_admissions``,
``runtime_attempts.attempt_role`` …) had been applied by the old unconditional migration pass;
under the new guard a database stamped 3 by an older binary skips all of them.

The repair moves version ownership to ``storage.migrations.STORE_VERSION_LEDGER``: an ordered
ledger whose head IS the contract, asserted equal to ``storage.db.STORE_USER_VERSION`` when the
authority module imports. Adding a gated migration without a ledger entry, or bumping one side
without the other, refuses to load rather than silently skipping.

Proven here on real historical shapes: the version-3 stamp's own schema, the previous
candidate's schema, an upgrade interrupted at the stamp boundary and resumed, a repeated upgrade,
and a fresh install.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

V3_STAMP_SHA = "19762f15"  # the commit that introduced the user_version stamp — stamped 3
PREVIOUS_CANDIDATE_SHA = "a6c8e3c4"  # the last binary whose migration pass ran unconditionally
OLD_STAMP = 3

# Objects that only exist because a migration ran AFTER the version-3 stamp was introduced.
POST_STAMP_TABLES = ("obligation_sets", "semantic_admissions", "a8_migration_markers", "executions")
POST_STAMP_COLUMNS = (
    ("runtime_attempts", "attempt_role"),
    ("runtime_attempts", "execution_slot"),
    ("finalized_responses", "finalization_id"),
)


def _schema_sql_from_git(spec: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{spec}:storage/migrations.py"],
        capture_output=True, text=True, check=True,
    )
    match = re.search(r'SCHEMA_SQL = """(.*?)"""', done.stdout, re.DOTALL)
    assert match, f"no SCHEMA_SQL in {spec}"
    return match.group(1)


def _stamped_old_db(tmp_path: Path, spec: str, name: str) -> tuple[Path, dict[str, int]]:
    """A database exactly as an older binary left it: its schema, sanitized rows, stamped 3."""
    from storage.db import STORE_APPLICATION_ID

    db = tmp_path / name
    conn = sqlite3.connect(db)
    survivors: dict[str, int] = {}
    try:
        conn.executescript(_schema_sql_from_git(spec))
        for table in ("runtime_sessions", "runtime_attempts", "peers"):
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone():
                continue
            cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
            values = {}
            for _cid, col, ctype, notnull, default, pk in cols:
                if pk:
                    values[col] = f"sanitized-{table}-0"
                elif notnull and not str(default or "").strip():
                    values[col] = f"sanitized-{table}-{col}" if "TEXT" in ctype.upper() else 0
            conn.execute(
                f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
                tuple(values.values()),
            )
            survivors[table] = 1
        conn.execute(f"PRAGMA application_id = {STORE_APPLICATION_ID};")
        conn.execute(f"PRAGMA user_version = {OLD_STAMP};")
        conn.commit()
    finally:
        conn.close()
    return db, survivors


def _missing_post_stamp_objects(db: Path) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        missing = [
            f"table:{t}"
            for t in POST_STAMP_TABLES
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone()
        ]
        for table, column in POST_STAMP_COLUMNS:
            names = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in names:
                missing.append(f"column:{table}.{column}")
        return missing
    finally:
        conn.close()


def _version(db: Path) -> int:
    conn = sqlite3.connect(db)
    try:
        return int(conn.execute("PRAGMA user_version;").fetchone()[0])
    finally:
        conn.close()


def _rows(db: Path, table: str) -> int:
    conn = sqlite3.connect(db)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def _semantic_dump(db: Path) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        objects = sorted(
            conn.execute("SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        )
        dump = [("schema", *obj) for obj in objects]
        for kind, name in objects:
            if kind != "table":
                continue
            dump.extend((f"rows:{name}", r) for r in sorted(map(repr, conn.execute(f"SELECT * FROM {name}").fetchall())))
        return dump
    finally:
        conn.close()


def test_the_ledger_owns_the_gate_constant(monkeypatch) -> None:
    from storage import db as sdb
    from storage import migrations

    migrations.assert_store_version_ownership()
    versions = [version for version, _note in migrations.STORE_VERSION_LEDGER]
    assert versions == sorted(set(versions)), "ledger versions must be strictly increasing"
    assert versions[-1] == migrations.ledger_head_version() == sdb.STORE_USER_VERSION
    assert versions[-1] > OLD_STAMP, "slice 8's version-gated migration pass needs a version of its own"
    # A gate constant that drifts from the ledger head is refused, not tolerated.
    monkeypatch.setattr(sdb, "STORE_USER_VERSION", OLD_STAMP)
    with pytest.raises(migrations.StoreVersionOwnershipError) as err:
        migrations.assert_store_version_ownership()
    assert err.value.code == "VOOL_E_STORE_VERSION_OWNERSHIP"


def test_old_stamped_database_cannot_skip_new_migrations(tmp_path: Path) -> None:
    from storage.db import STORE_USER_VERSION, get_connection
    from storage.migrations import run_migrations

    db, survivors = _stamped_old_db(tmp_path, V3_STAMP_SHA, "stamped-3-at-the-stamp-commit.db")
    assert _missing_post_stamp_objects(db), "precondition: the version-3 shape lacks later objects"

    run_migrations(db_path=db)

    assert _missing_post_stamp_objects(db) == [], "a database stamped 3 skipped the later migrations"
    assert _version(db) == STORE_USER_VERSION
    for table, count in survivors.items():
        assert _rows(db, table) == count, f"{table}: data lost across the upgrade"
    get_connection(db).execute("SELECT 1")  # opens through the gate


def test_previous_candidate_database_upgrades_and_restamps(tmp_path: Path) -> None:
    from storage.db import STORE_USER_VERSION, get_connection
    from storage.migrations import run_migrations

    db, survivors = _stamped_old_db(tmp_path, PREVIOUS_CANDIDATE_SHA, "stamped-3-previous-candidate.db")
    run_migrations(db_path=db)
    assert _version(db) == STORE_USER_VERSION
    assert _version(db) != OLD_STAMP, "the previous candidate's stamp must not equal the new contract"
    assert _missing_post_stamp_objects(db) == []
    for table, count in survivors.items():
        assert _rows(db, table) == count
    get_connection(db).execute("SELECT 1")


def test_interrupted_upgrade_leaves_old_bytes_and_resumes_on_next_boot(tmp_path: Path, monkeypatch) -> None:
    from storage import migrations

    db, survivors = _stamped_old_db(tmp_path, V3_STAMP_SHA, "interrupted.db")
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    real_get_connection = migrations.get_connection

    def stamp_boom_conn(db_path=None):
        real = real_get_connection(db_path)

        class StampRaisingConn:
            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, *a, **k):
                if "PRAGMA application_id" in sql:
                    raise RuntimeError("injected: process died at the stamp boundary")
                return self._conn.execute(sql, *a, **k)

            def __getattr__(self, name):
                return getattr(self._conn, name)

        return StampRaisingConn(real)

    monkeypatch.setattr(migrations, "get_connection", stamp_boom_conn)
    with pytest.raises(migrations.StoreMigrationError) as err:
        migrations.run_migrations(db_path=db)
    assert err.value.code == "VOOL_E_MIGRATION_FAILED"
    monkeypatch.setattr(migrations, "get_connection", real_get_connection)

    assert hashlib.sha256(db.read_bytes()).hexdigest() == before, "interrupted upgrade drifted the old bytes"
    assert _version(db) == OLD_STAMP

    migrations.run_migrations(db_path=db)  # next boot
    assert _missing_post_stamp_objects(db) == []
    assert _version(db) == migrations.ledger_head_version()
    for table, count in survivors.items():
        assert _rows(db, table) == count


def test_repeated_upgrade_is_stable(tmp_path: Path) -> None:
    from storage.migrations import ledger_head_version, run_migrations

    db, _survivors = _stamped_old_db(tmp_path, V3_STAMP_SHA, "repeated.db")
    run_migrations(db_path=db)
    first = _semantic_dump(db)
    run_migrations(db_path=db)  # stamped current: versioned no-op
    assert _semantic_dump(db) == first
    run_migrations(db_path=db, force=True)  # forced rerun: idempotent
    assert _semantic_dump(db) == first
    assert _version(db) == ledger_head_version()


def test_fresh_install_stamps_the_ledger_head(tmp_path: Path) -> None:
    from storage.db import STORE_USER_VERSION
    from storage.migrations import ledger_head_version, run_migrations

    db = tmp_path / "fresh.db"
    run_migrations(db_path=db)
    assert _version(db) == ledger_head_version() == STORE_USER_VERSION
    assert _missing_post_stamp_objects(db) == []
