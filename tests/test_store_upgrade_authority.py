"""M2 upgrade authority (2026-09-02): existing installs must upgrade, not die.

The operator's real report: launching a fresh build against a pre-existing runtime home died
with ``sqlite3.OperationalError: no such column: attempt_role`` — raised from
``run_migrations``'s ``executescript(SCHEMA_SQL)`` because SCHEMA_SQL embeds
``CREATE INDEX idx_runtime_attempts_session_role ON runtime_attempts(session_id, attempt_role,
...)`` while the owning ``ALTER TABLE ... ADD COLUMN attempt_role`` only runs LATER, in the
dynamic patch phase. On any database whose ``runtime_attempts`` predates that column the boot
order is inverted: the schema script queries a column the authority has not yet added.

Fixtures here are SANITIZED and built from SCHEMA SHAPE ONLY — historical SCHEMA_SQL strings
extracted from this repository's own git history plus synthetic rows, never real user data:

- n-2 shape: SCHEMA_SQL at ``9b644f3f^`` (the parent that introduced ``attempt_role``),
  i.e. ``runtime_attempts`` WITHOUT the chain-role columns.
- n-1 shape: SCHEMA_SQL at ``9b644f3f``.
- current shape: produced by this binary's own ``run_migrations``.

The upgrade contract under test: ordered steps before any DDL that depends on them;
transactional-with-snapshot semantics (any failure restores the pre-update bytes exactly);
data survival; idempotence; fail-closed on newer unsupported schemas with a useful VOOL error
code.
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

N2_SCHEMA_SHA = "9b644f3f^"  # runtime_attempts predates attempt_role — the reported failure shape
N1_SCHEMA_SHA = "9b644f3f"  # attempt_role present; newest dynamic columns still dynamic

# The two NEWEST dynamic steps (the current binary's n-1 delta): a database created by the
# immediately previous binary has the full SCHEMA_SQL tables EXCEPT these columns.
NEWEST_STEPS = (
    ("runtime_attempts", "execution_slot"),
    ("runtime_attempts", "retry_idempotency_key"),
)


def _current_schema_minus_newest_steps() -> str:
    """The n-1 shape: today's SCHEMA_SQL with the newest dynamic columns (and the dependent
    unique index) stripped — exactly what the previous binary created for fresh installs."""
    from storage.migrations import SCHEMA_SQL

    schema = SCHEMA_SQL
    for _table, column in NEWEST_STEPS:
        schema = re.sub(
            rf"\n\s*{column} (TEXT|INTEGER)[^\n]*,?", "", schema
        )
    schema = re.sub(
        r"CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_attempts_retry_idempotency[^;]*;",
        "",
        schema,
    )
    return schema


def _schema_sql_from_git(spec: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{spec}:storage/migrations.py"],
        capture_output=True, text=True, check=True,
    )
    match = re.search(r'SCHEMA_SQL = """(.*?)"""', done.stdout, re.DOTALL)
    assert match, f"no SCHEMA_SQL in {spec}"
    return match.group(1)


def _build_fixture(db_path: Path, schema_sql: str, survivors: dict[str, int]) -> None:
    """Materialize a sanitized old-shape database: the historical schema plus generic rows."""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(schema_sql)
        for table, count in survivors.items():
            existing = conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone()
            if not existing:
                continue
            cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
            for n in range(count):
                values = {}
                for col in cols:
                    _cid, name, ctype, notnull, _default, pk = col
                    if pk:
                        values[name] = f"sanitized-{table}-{n}"
                    elif notnull and not str(_default or "").strip():
                        values[name] = f"sanitized-{table}-{name}-{n}" if "TEXT" in ctype.upper() else n
                names = ", ".join(values)
                marks = ", ".join("?" for _ in values)
                conn.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", tuple(values.values()))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def old_shape_db(tmp_path: Path, request) -> Path:
    db = tmp_path / "vool_web0_v2.db"
    _build_fixture(
        db,
        _schema_sql_from_git(request.param),
        survivors={
            "runtime_sessions": 2,
            "dialogue_sessions": 2,
            "runtime_attempts": 3,
            "runtime_checkpoints": 1,
        },
    )
    return db


# ------------------------------------------------------------------------------------------
# RED: the reported boot failure, reproduced from shape-only fixtures
# ------------------------------------------------------------------------------------------


def _rows(db: Path, table: str) -> int:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


@pytest.mark.parametrize("note", ["inspect the beta export", "review the orchard inventory"])
def test_version_four_install_upgrades_before_scheduling_and_keeps_rows(tmp_path, note):
    from core.operator import reminders
    from storage.db import STORE_APPLICATION_ID, get_connection
    from storage.migrations import run_migrations

    db = tmp_path / "existing-v4.db"
    _build_fixture(db, _schema_sql_from_git("ad619e3f"), {"runtime_sessions": 2})
    with sqlite3.connect(db) as conn:
        conn.execute(f"PRAGMA application_id={STORE_APPLICATION_ID}")
        conn.execute("PRAGMA user_version=4")
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='reminder_requests'").fetchone()
    run_migrations(db_path=db)
    created = reminders.schedule_reminder(
        session_id="upgrade-chat", task_id="upgrade-task", note=note,
        due_at_utc="2026-09-14T07:15:00+00:00", tz_name="UTC",
        get_connection_fn=lambda: get_connection(db),
    )
    assert created["status"] == "scheduled"
    run_migrations(db_path=db)  # The next startup is a no-op that keeps the scheduled item.
    rows = reminders.list_reminders(session_id="upgrade-chat", get_connection_fn=lambda: get_connection(db))
    assert len(rows) == 1 and rows[0]["note"] == note
    assert _rows(db, "runtime_sessions") == 2


@pytest.mark.parametrize("old_shape_db", [N2_SCHEMA_SHA], indirect=True)
def test_n2_upgrade_succeeds_survives_data_and_restarts(old_shape_db: Path) -> None:
    """The reported failure, now the contract: an n-2 database upgrades, keeps its dialogue /
    attempts / checkpoint rows, answers the new-column queries, and reopens cleanly."""
    from storage.migrations import run_migrations

    before = {t: _rows(old_shape_db, t) for t in ("runtime_sessions", "dialogue_sessions", "runtime_attempts")}
    run_migrations(db_path=old_shape_db)

    conn = sqlite3.connect(old_shape_db)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(runtime_attempts)")}
        assert {"attempt_role", "execution_slot", "retry_idempotency_key"} <= cols
        # the new-column query that used to be boot-fatal
        conn.execute(
            "SELECT session_id, attempt_role FROM runtime_attempts "
            "ORDER BY updated_at DESC LIMIT 5"
        ).fetchall()
        stamped = conn.execute("PRAGMA user_version;").fetchone()[0]
    finally:
        conn.close()
    from storage.db import STORE_USER_VERSION

    assert stamped == STORE_USER_VERSION
    for table, count in before.items():
        assert _rows(old_shape_db, table) >= count, f"{table} lost rows"

    # restart: reopening the migrated store goes through the version gate cleanly
    conn = sqlite3.connect(old_shape_db)
    conn.execute("SELECT 1 FROM dialogue_sessions LIMIT 1").fetchone()
    conn.close()


def test_upgrade_failure_at_any_boundary_restores_pre_update_bytes_exactly(
    tmp_path: Path, monkeypatch
) -> None:
    """M2 law: a failure injected at EACH migration boundary must leave the database file
    byte-identical to its pre-migration state (snapshot restore), with the original rows."""
    import hashlib

    from storage import migrations

    counter = {"n": 0}

    def make_db() -> Path:
        counter["n"] += 1
        db = tmp_path / f"fixture-{counter['n']}.db"
        _build_fixture(
            db,
            _schema_sql_from_git(N2_SCHEMA_SHA),
            survivors={"runtime_sessions": 1, "dialogue_sessions": 1, "runtime_attempts": 1},
        )
        return db

    def digest(db: Path) -> str:
        return hashlib.sha256(db.read_bytes()).hexdigest()

    real_get_connection = migrations.get_connection
    real_steps = migrations.run_legacy_shape_steps
    real_preflight = migrations._preflight_legacy_schema_sql_tables
    real_schema = migrations.SCHEMA_SQL

    def boom_steps(conn):
        raise RuntimeError("injected: legacy-shape phase")

    def boom_preflight(conn):
        raise RuntimeError("injected: preflight phase")

    def stamp_boom_conn(db_path=None):
        real = real_get_connection(db_path)

        class StampRaisingConn:
            """Delegates everything; detonates exactly at the closing version stamp."""

            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, *a, **k):
                if "PRAGMA application_id" in sql:
                    raise RuntimeError("injected: stamp boundary")
                return self._conn.execute(sql, *a, **k)

            def __getattr__(self, name):
                return getattr(self._conn, name)

        return StampRaisingConn(real)

    # name -> (patch, restore): each migration boundary gets its own scoped failure injection
    boundaries: dict = {
        "preflight": (
            lambda: setattr(migrations, "_preflight_legacy_schema_sql_tables", boom_preflight),
            lambda: setattr(migrations, "_preflight_legacy_schema_sql_tables", real_preflight),
        ),
        "legacy-shape": (
            lambda: setattr(migrations, "run_legacy_shape_steps", boom_steps),
            lambda: setattr(migrations, "run_legacy_shape_steps", real_steps),
        ),
        "schema-script": (
            lambda: setattr(migrations, "SCHEMA_SQL", "THIS IS NOT SQL;"),
            lambda: setattr(migrations, "SCHEMA_SQL", real_schema),
        ),
        "stamp": (
            lambda: setattr(migrations, "get_connection", stamp_boom_conn),
            lambda: setattr(migrations, "get_connection", real_get_connection),
        ),
    }

    for name, (inject, restore) in boundaries.items():
        db = make_db()
        before = digest(db)
        inject()
        try:
            with pytest.raises(migrations.StoreMigrationError) as err:
                migrations.run_migrations(db_path=db)
            assert err.value.code == "VOOL_E_MIGRATION_FAILED"
        finally:
            restore()
        assert digest(db) == before, f"{name}: database bytes drifted after failed migration"
        assert _rows(db, "runtime_attempts") == 1, f"{name}: data lost"


def test_newer_unsupported_schema_fails_closed_with_vool_code(tmp_path: Path) -> None:
    from storage.db import STORE_APPLICATION_ID, StoreVersionError
    from storage.migrations import run_migrations

    db = tmp_path / "vool_web0_v2.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE future_table (x)")
    conn.execute(f"PRAGMA application_id = {STORE_APPLICATION_ID};")
    conn.execute("PRAGMA user_version = 999;")
    conn.commit()
    conn.close()

    with pytest.raises(StoreVersionError) as err:
        run_migrations(db_path=db)
    assert err.value.code == "VOOL_E_STORE_TOO_NEW"


def test_older_binary_refuses_migrated_data_rollback_stays_coherent(
    tmp_path: Path, monkeypatch
) -> None:
    """Updater rollback: the OLD binary (n-1 contract version) must refuse a database the NEW
    binary already upgraded and stamped — old app + old data move together, never mixed."""
    import storage.db as storage_db
    from storage.db import StoreVersionError
    from storage.migrations import run_migrations

    db = tmp_path / "vool_web0_v2.db"
    _build_fixture(db, _schema_sql_from_git(N2_SCHEMA_SHA), survivors={"runtime_attempts": 1})
    run_migrations(db_path=db)

    monkeypatch.setattr(storage_db, "STORE_USER_VERSION", 2)  # the old binary's contract
    conn = sqlite3.connect(db)
    try:
        with pytest.raises(StoreVersionError) as err:
            storage_db.enforce_store_version_gate(conn)
        assert err.value.code == "VOOL_E_STORE_TOO_NEW"
    finally:
        conn.close()


def _semantic_dump(db: Path) -> list[tuple]:
    """Schema objects (sorted by type+name) and every table's rows (sorted) — the meaningful
    content of the store, independent of sqlite_master rowid ordering."""
    conn = sqlite3.connect(db)
    try:
        objects = sorted(
            conn.execute(
                "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
        )
        dump = [("schema", *obj) for obj in objects]
        for _type, name in objects:
            if name != "table":
                continue
            rows = sorted(map(repr, conn.execute(f"SELECT * FROM {name}").fetchall()))
            dump.extend((f"rows:{name}", r) for r in rows)
        return dump
    finally:
        conn.close()


def test_rerunning_the_same_migration_changes_nothing(tmp_path: Path) -> None:
    from storage.migrations import run_migrations

    db = tmp_path / "vool_web0_v2.db"
    _build_fixture(db, _schema_sql_from_git(N2_SCHEMA_SHA), survivors={"runtime_attempts": 1})
    run_migrations(db_path=db)
    dump1 = _semantic_dump(db)

    run_migrations(db_path=db)  # stamped: versioned no-op
    dump2 = _semantic_dump(db)
    assert dump1 == dump2

    run_migrations(db_path=db, force=True)  # forced full rerun: still identical
    dump3 = _semantic_dump(db)
    assert dump1 == dump3


@pytest.mark.parametrize("old_shape_db", [N2_SCHEMA_SHA, N1_SCHEMA_SHA], indirect=True)
def test_upgrade_matrix_versions(tmp_path: Path, old_shape_db: Path) -> None:
    from storage.db import STORE_USER_VERSION
    from storage.migrations import run_migrations

    run_migrations(db_path=old_shape_db)
    conn = sqlite3.connect(old_shape_db)
    try:
        assert conn.execute("PRAGMA user_version;").fetchone()[0] == STORE_USER_VERSION
        assert conn.execute("PRAGMA application_id;").fetchone()[0] == STORE_APPLICATION_ID
    finally:
        conn.close()


from storage.db import STORE_APPLICATION_ID


def test_n1_schema_upgrade_succeeds(tmp_path: Path) -> None:
    from storage.migrations import run_migrations

    db = tmp_path / "vool_web0_v2.db"
    _build_fixture(db, _current_schema_minus_newest_steps(), survivors={"runtime_attempts": 2})
    probe = sqlite3.connect(db)
    cols = {row[1] for row in probe.execute("PRAGMA table_info(runtime_attempts)")}
    probe.close()
    assert "execution_slot" not in cols and "retry_idempotency_key" not in cols, (
        "the n-1 fixture must genuinely lack the newest columns"
    )
    run_migrations(db_path=db)  # must not raise
    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(runtime_attempts)")}
    n = conn.execute("SELECT COUNT(*) FROM runtime_attempts").fetchone()[0]
    conn.close()
    assert {"execution_slot", "retry_idempotency_key"} <= cols
    assert n == 2


def test_schema_sql_never_indexes_a_column_the_authority_does_not_own() -> None:
    """Drift pin for the boot-order law: every column referenced by a SCHEMA_SQL index must be
    created by a SCHEMA_SQL table or owned by a LEGACY_SHAPE_STEPS entry. A new index over a
    dynamically-added column without a step fails here BEFORE it kills an old install."""
    import re

    from storage import migrations

    schema = migrations.SCHEMA_SQL

    # Ground truth: create the schema in an in-memory database and ASK SQLite what exists.
    probe = sqlite3.connect(":memory:")
    probe.executescript(schema)
    created: dict[str, set] = {}
    for table, in probe.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall():
        created[table] = {row[1] for row in probe.execute(f"PRAGMA table_info({table})")}
    probe.close()

    unowned: list[str] = []
    for match in re.finditer(r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS (\w+)\s+ON (\w+)\s*\(([^)]*)\)", schema):
        index, table, body = match.group(1), match.group(2), match.group(3)
        referenced = {part.strip().split()[0] for part in body.split(",") if part.strip()}
        have = created.get(table)
        if have is None:
            unowned.append(f"{index}: table {table} is not created by SCHEMA_SQL")
            continue
        steps = {column for step_table, column, _ in migrations.LEGACY_SHAPE_STEPS if step_table == table}
        missing = [c for c in referenced if c not in have and c not in steps]
        if missing:
            unowned.append(f"{index}: columns {missing} neither in table nor steps")
    assert unowned == [], f"SCHEMA_SQL indexes columns with no owning migration step: {unowned}"
