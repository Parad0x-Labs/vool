"""Final Repair 1 (Mnemosyne final review, 2026-08-07) -- BLOCKING.

`storage/migrations.py` placed `CREATE UNIQUE INDEX ... retry_idempotency_key` inside `SCHEMA_SQL`,
executed as one `executescript()` batch. On a LEGACY database that already has `runtime_attempts`
(from before this column existed -- e.g. the reviewed tip at 7ff50e8d), `CREATE TABLE IF NOT
EXISTS` is a no-op, so the index statement immediately raises `OperationalError: no such column:
retry_idempotency_key` and aborts the WHOLE script -- silently skipping every table/index defined
after it AND every `_add_column_if_missing()` call in `run_migrations()` (which runs after the
script, in the same try block, and would otherwise have added the missing column). The attempt lane
becomes permanently inert on any pre-existing installation.

Fixed by removing the premature index statement from `SCHEMA_SQL` -- the correctly-ordered version
already existed later in `run_migrations()`, immediately after `_add_column_if_missing` guarantees
the column exists.

The legacy database fixture here is built from the REAL historical schema at the reviewed tip
(`git show 7ff50e8d...:storage/migrations.py`), not a hand-approximated shape -- that file has ZERO
occurrences of `retry_idempotency_key`, confirmed directly, so this reproduces the exact upgrade
path a real pre-existing installation goes through.
"""

from __future__ import annotations

import ast
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from core.runtime_continuity import (
    configure_runtime_continuity_db_path,
    create_runtime_attempt,
    get_runtime_attempt,
    latest_runtime_attempt,
    reset_runtime_continuity_state,
)
from storage.migrations import run_migrations

_LEGACY_COMMIT = "7ff50e8d42ed13bbe9fc888ac649d6d942731202"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_legacy_schema_sql_cache: str | None = None


def _legacy_schema_sql() -> str:
    """The REAL `SCHEMA_SQL` string as it existed at the reviewed tip, extracted via `ast` (no
    import, no execution of the historical file -- just its one string literal)."""
    global _legacy_schema_sql_cache
    if _legacy_schema_sql_cache is not None:
        return _legacy_schema_sql_cache
    result = subprocess.run(
        ["git", "show", f"{_LEGACY_COMMIT}:storage/migrations.py"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, check=True,
    )
    tree = ast.parse(result.stdout)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "SCHEMA_SQL" for t in node.targets):
            _legacy_schema_sql_cache = ast.literal_eval(node.value)
            return _legacy_schema_sql_cache
    raise RuntimeError("SCHEMA_SQL not found in the historical migrations.py")


def _build_legacy_database(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_legacy_schema_sql())
        conn.commit()
    finally:
        conn.close()


def _column_exists(db_path: Path, table: str, column: str) -> bool:
    conn = sqlite3.connect(str(db_path))
    try:
        cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        return column in cols
    finally:
        conn.close()


def _index_exists(db_path: Path, index_name: str) -> bool:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?", (index_name,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


class _MigrationCaseBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "migration.db"

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def _activate(self) -> None:
        """Point the runtime-continuity module at this test's db WITHOUT wiping it --
        `reset_runtime_continuity_state()` deletes `runtime_attempts` rows (it's a per-test
        isolation reset, not a "connect" call), which would destroy exactly the pre-existing data
        a legacy-upgrade test needs to verify survived."""
        configure_runtime_continuity_db_path(str(self._db_path))

    def _assert_column_and_index_present(self) -> None:
        self.assertTrue(_column_exists(self._db_path, "runtime_attempts", "retry_idempotency_key"))
        self.assertTrue(_index_exists(self._db_path, "idx_runtime_attempts_retry_idempotency"))

    def _assert_lane_functional(self) -> None:
        self._activate()
        first = create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")
        self.assertTrue(first["attempt_id"])
        found = latest_runtime_attempt("s1")
        self.assertEqual(found["attempt_id"], first["attempt_id"])
        retry_a = create_runtime_attempt(
            session_id="s1", original_request="req", answer_mode="LIVE_DATA",
            parent_attempt_id=first["attempt_id"], root_attempt_id=first["attempt_id"],
            trigger_user_turn_id="turn-migration-check", execution_generation=2,
        )
        retry_b = create_runtime_attempt(
            session_id="s1", original_request="req", answer_mode="LIVE_DATA",
            parent_attempt_id=first["attempt_id"], root_attempt_id=first["attempt_id"],
            trigger_user_turn_id="turn-migration-check", execution_generation=2,
        )
        self.assertEqual(retry_a["attempt_id"], retry_b["attempt_id"], "duplicate retry identity must still be rejected")


class CaseAFreshEmptyDatabaseTests(_MigrationCaseBase):
    def test_fresh_database_migrates_cleanly(self) -> None:
        run_migrations(db_path=self._db_path)
        self._assert_column_and_index_present()
        self._assert_lane_functional()

    def test_second_migration_run_is_a_no_op(self) -> None:
        run_migrations(db_path=self._db_path)
        run_migrations(db_path=self._db_path)  # must not raise, must not duplicate/destroy anything
        self._assert_column_and_index_present()
        self._assert_lane_functional()


class CaseBHistoricalSchemaDatabaseTests(_MigrationCaseBase):
    """Database built using the EXACT schema at the reviewed tip (7ff50e8d), no data yet."""

    def test_upgrading_the_historical_schema_succeeds(self) -> None:
        _build_legacy_database(self._db_path)
        self.assertFalse(_column_exists(self._db_path, "runtime_attempts", "retry_idempotency_key"))

        run_migrations(db_path=self._db_path)  # must not raise

        self._assert_column_and_index_present()
        self._assert_lane_functional()


class CaseCLegacyDatabaseWithExistingRowsTests(_MigrationCaseBase):
    """Legacy database with a real original attempt, a retry child, and terminal attempts --
    built and populated entirely through the columns the historical schema actually had (no
    retry_idempotency_key column at all)."""

    def _seed_legacy_rows(self) -> tuple[str, str]:
        conn = sqlite3.connect(str(self._db_path))
        try:
            now = "2026-08-01T00:00:00+00:00"
            original_id = "attempt-legacy-original"
            child_id = "attempt-legacy-child"
            terminal_id = "attempt-legacy-terminal"
            for attempt_id, parent_id, root_id, generation, state in (
                (original_id, "", original_id, 1, "PARTIAL_SUCCESS"),
                (child_id, original_id, original_id, 2, "SUCCEEDED"),
                (terminal_id, "", terminal_id, 1, "FAILED_TOOL"),
            ):
                conn.execute(
                    """
                    INSERT INTO runtime_attempts (
                        attempt_id, session_id, checkpoint_id,
                        origin_user_turn_id, trigger_user_turn_id, origin_conversation_event_id,
                        root_attempt_id, parent_attempt_id, execution_generation,
                        original_request_snapshot, original_request_hash, original_request_bytes,
                        original_request_truncated,
                        answer_mode, plan_id, lifecycle_state, terminal_reason,
                        retryable, retry_reason, retry_from_stage, refresh_required, refresh_reason,
                        created_daemon_sha, last_updated_daemon_sha, process_instance_id,
                        created_at, updated_at, completed_at
                    ) VALUES (?, 'legacy-session', '', '', '', '', ?, ?, ?, 'legacy request', 'hash', 13, 0,
                        'LIVE_DATA', 'plan-legacy', ?, '', 0, '', '', 0, '', '', '', '', ?, ?, ?)
                    """,
                    (attempt_id, root_id, parent_id, generation, state, now, now, now),
                )
            conn.commit()
        finally:
            conn.close()
        return original_id, child_id

    def test_legacy_rows_survive_the_upgrade_untouched(self) -> None:
        _build_legacy_database(self._db_path)
        original_id, child_id = self._seed_legacy_rows()

        run_migrations(db_path=self._db_path)  # must not raise, must not destroy existing rows

        self._assert_column_and_index_present()
        self._activate()
        original = get_runtime_attempt(original_id)
        child = get_runtime_attempt(child_id)
        self.assertIsNotNone(original)
        self.assertIsNotNone(child)
        self.assertEqual(original["lifecycle_state"], "PARTIAL_SUCCESS")
        self.assertEqual(child["lifecycle_state"], "SUCCEEDED")
        self.assertEqual(child["parent_attempt_id"], original_id)
        # A legacy row's retry_idempotency_key defaults to '' -- excluded by the partial index, so
        # it does not collide with anything and does not need a backfilled value.
        self.assertEqual(child.get("retry_idempotency_key"), "")

        latest = latest_runtime_attempt("legacy-session")
        self.assertIn(latest["attempt_id"], (original_id, child_id))
        self._assert_lane_functional()


class CaseDColumnExistsIndexDoesNotTests(_MigrationCaseBase):
    """Simulates a previously PARTIAL migration attempt: the column was added (by some earlier,
    possibly-interrupted run) but the index creation never happened."""

    def test_migration_still_creates_the_missing_index(self) -> None:
        _build_legacy_database(self._db_path)
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("ALTER TABLE runtime_attempts ADD COLUMN retry_idempotency_key TEXT NOT NULL DEFAULT ''")
            conn.commit()
        finally:
            conn.close()
        self.assertTrue(_column_exists(self._db_path, "runtime_attempts", "retry_idempotency_key"))
        self.assertFalse(_index_exists(self._db_path, "idx_runtime_attempts_retry_idempotency"))

        run_migrations(db_path=self._db_path)

        self._assert_column_and_index_present()
        self._assert_lane_functional()


class CaseEBothAlreadyExistTests(_MigrationCaseBase):
    """Database where both the column and the index already exist -- the fully-current shape."""

    def test_migration_is_idempotent_when_everything_already_exists(self) -> None:
        run_migrations(db_path=self._db_path)
        self._assert_column_and_index_present()
        self._activate()
        parent = create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")

        run_migrations(db_path=self._db_path)  # second run against an already-current schema

        self._assert_column_and_index_present()
        reloaded = get_runtime_attempt(parent["attempt_id"])
        self.assertIsNotNone(reloaded, "an existing row must survive a redundant migration run")
        self._assert_lane_functional()


class SabotageMigrationOrderingTests(_MigrationCaseBase):
    """Sabotage: move the index creation back before the column addition -- the legacy-upgrade
    case must reproduce the exact confirmed defect."""

    def test_sabotage_index_before_column_reproduces_the_missing_column_error(self) -> None:
        _build_legacy_database(self._db_path)
        conn = sqlite3.connect(str(self._db_path))
        try:
            with self.assertRaises(sqlite3.OperationalError) as ctx:
                conn.executescript(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_attempts_retry_idempotency "
                    "ON runtime_attempts(retry_idempotency_key) WHERE retry_idempotency_key != '';"
                )
            self.assertIn("no such column", str(ctx.exception))
            self.assertIn("retry_idempotency_key", str(ctx.exception))
        finally:
            conn.close()

    def test_control_the_real_migration_does_not_reproduce_the_error(self) -> None:
        _build_legacy_database(self._db_path)
        run_migrations(db_path=self._db_path)  # must not raise
        self._assert_column_and_index_present()


if __name__ == "__main__":
    unittest.main()
