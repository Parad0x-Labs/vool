"""P0 AMENDMENT — false rollback must be impossible (review DB_SKILL_REVIEW_20260903 §5-6).

RED at 3061c7fe (review reproduction, driven here through the REAL confined executor):

    CREATE TABLE t(n INTEGER)
    apply [INSERT INTO t VALUES (1), COMMIT, INSERT INTO missing_table VALUES (2)]
    -> status apply_failed_rolled_back, rolled_back=True ... and a FRESH connection
       reads [(1,)]. The caller's COMMIT ended the wrapper transaction; the later
       ROLLBACK had nothing to roll back; the failure path reported rollback anyway.

A backup existing is not a rollback having happened. These tests pin the transaction
truth contract: the wrapper owns the transaction; caller-controlled transaction/
attachment/engine-configuration verbs are refused before any effect (engine
authorization + comment/whitespace-aware parsing, not a phrase blacklist); preview
and apply share one policy; every outcome names what actually happened
(rolled_back_verified / committed / restored / recovery_needed), verified from a
FRESH connection; a failed rollback preserves the original SQL error; automatic
backup identities never overwrite a same-second or concurrent preimage.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests._toolchain_fixtures import executor_kwargs, internal_scope, reset_toolchain_state
from tests._vool_database_pack import DB_PLUGIN_ID, databases_root, isolated_db_world


@pytest.fixture()
def db_world(tmp_path, monkeypatch):
    from core.mode_permission_policy import reset_mode_permission_state
    from core.runtime_flags import override

    isolated_db_world(tmp_path, monkeypatch)
    reset_toolchain_state()
    reset_mode_permission_state()
    with override("plugin_runtime_tools", True):
        from core import plugin_tools

        _loaded, errors = plugin_tools.load_all(tmp_path)
        assert errors == (), errors
        yield tmp_path
    reset_mode_permission_state()
    reset_toolchain_state()


T_DDL = "CREATE TABLE t (n INTEGER)"
APPLY_SCOPE_ARGS = ("test.apply", "create_files", "modify_files")


def _scope(label: str, *actions: str, intents: tuple[str, ...] = ()) -> dict:
    return internal_scope(label, *actions, intents=intents)


def _run(intent: str, arguments: dict, session: str = "tx", **context):
    from core.tool_intent_executor import execute_tool_intent

    return execute_tool_intent({"intent": intent, "arguments": arguments}, **executor_kwargs(session, **context))


def _create(db_world, *, ddl: str = T_DDL, seed: str = "", name: str = "txdb") -> None:
    out = _run(
        f"{DB_PLUGIN_ID}.db.create",
        {"name": name, "schema_sql": ddl, "seed_sql": seed},
        **_scope("tx.create", "create_files", intents=(f"{DB_PLUGIN_ID}.db.create",)),
    )
    assert out.ok, (out.status, out.response_text)


def _approved_apply(db_world, statements: list[str], *, name: str = "txdb") -> dict:
    from core.mode_permission_policy import resolve_approval

    pending = _run(f"{DB_PLUGIN_ID}.migrate.apply", {"name": name, "statements": statements})
    assert pending.status == "pending_approval", (pending.status, pending.response_text[:200])
    token = str(pending.details["approval_request"]["approval_id"])
    assert resolve_approval(token, decision="allow") is not None
    return _run(
        f"{DB_PLUGIN_ID}.migrate.apply",
        {"name": name, "statements": statements},
        mode_approval_token=token,
    )


def _fresh_rows(db_world, sql: str = "SELECT n FROM t ORDER BY n", *, name: str = "txdb"):
    """Ground truth from a connection the handler never saw."""
    path = databases_root(db_world) / f"{name}.sqlite"
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The review's exact reproduction, and the variants of the same escape
# ---------------------------------------------------------------------------


def test_review_reproduction_commit_escape_is_impossible(db_world) -> None:
    _create(db_world)
    failed = _approved_apply(
        db_world,
        ["INSERT INTO t VALUES (1)", "COMMIT", "INSERT INTO missing_table VALUES (2)"],
    )
    # The refusal happens BEFORE any effect: no statement of this migration may run.
    assert failed.status in {"refused_transaction_control", "refused_statement"}, failed.status
    assert _fresh_rows(db_world) == [], "a refused migration must leave the database untouched"
    # Refusal BEFORE effect, mechanically: no pre-apply preimage was ever taken.
    backups = databases_root(db_world) / "backups" / "txdb"
    assert not any(backups.glob("pre-apply-*")) if backups.is_dir() else True


def test_control_without_commit_still_rolls_back_verified(db_world) -> None:
    _create(db_world)
    failed = _approved_apply(
        db_world, ["INSERT INTO t VALUES (1)", "INSERT INTO missing_table VALUES (2)"]
    )
    assert not failed.ok
    assert failed.status == "apply_failed_rolled_back"
    observation = failed.details["observation"]
    assert observation["outcome"] == "rolled_back_verified"
    assert observation["rolled_back"] is True
    assert _fresh_rows(db_world) == []


@pytest.mark.parametrize(
    "verb",
    [
        "COMMIT",
        "commit",
        "  COMMIT  ",
        "COMMIT;",
        "END",
        "ROLLBACK",
        "BEGIN",
        "BEGIN IMMEDIATE",
        "SAVEPOINT sp1",
        "RELEASE sp1",
        "ROLLBACK TO sp1",
        "-- nothing to see\nCOMMIT",
        "/* block comment */ COMMIT",
    ],
)
def test_transaction_and_attachment_verbs_are_refused_before_effect(db_world, verb: str) -> None:
    _create(db_world)
    failed = _approved_apply(db_world, ["INSERT INTO t VALUES (1)", verb])
    assert failed.status in {"refused_transaction_control", "refused_statement", "refused_attachment", "refused_pragma"}, (
        verb, failed.status, failed.response_text[:200]
    )
    assert _fresh_rows(db_world) == [], f"{verb!r} must not leave committed data"


@pytest.mark.parametrize(
    "statement",
    [
        "ATTACH DATABASE '/tmp/evil.sqlite' AS evil",
        "-- x\nATTACH DATABASE ':memory:' AS evil",
        "PRAGMA writable_schema = 1",
        "/* c */ PRAGMA journal_mode = OFF",
        "PRAGMA foreign_keys = OFF",
    ],
)
def test_attachment_and_unsafe_pragma_are_refused(db_world, statement: str) -> None:
    _create(db_world)
    failed = _approved_apply(db_world, [statement])
    assert failed.status in {"refused_attachment", "refused_pragma", "refused_statement"}, (
        statement, failed.status
    )
    assert failed.details["executed"] is False or failed.details["observation"].get("executed_statements", 0) == 0


def test_engine_authorizer_is_the_backstop_layer() -> None:
    """The authorizer's OWN contract, pinned at the engine (sabotage finding, 2026-09-03).

    Disarming the parser (sabotage) leaves the authorizer holding persistence;
    disarming the AUTHORIZER leaves the parser holding the typed refusals, and
    the pack stays green -- so each layer needs its own pin. This one drives the
    engine directly: with the handler's authorizer armed, COMMIT / ROLLBACK /
    SAVEPOINT / PRAGMA / ATTACH are refused at prepare time and ordinary DML is
    not. If this layer silently disappears, the parser becomes the only guard.
    """
    import importlib.util

    handler_path = Path("plugins/vool-database/bin/db_handler")
    spec = importlib.util.spec_from_loader("db_handler_probe", loader=None)
    module = importlib.util.module_from_spec(spec)
    body = handler_path.read_text(encoding="utf-8")
    # Execute only the policy section (through _apply_deadline): importing the
    # whole script would run tool code; the policy core is what this test pins.
    cut = body.index("def scratch_root")
    exec(compile(body[:cut], str(handler_path), "exec"), module.__dict__)

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (n INTEGER)")
    module._arm_authorizer(conn)
    for denied_sql in (
        "COMMIT",
        "ROLLBACK",
        "SAVEPOINT sp",
        "BEGIN",
        "PRAGMA journal_mode = OFF",
        "ATTACH DATABASE ':memory:' AS evil",
    ):
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(denied_sql)
    conn.set_authorizer(None)
    conn.execute("BEGIN")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.execute("COMMIT")
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    conn.close()


def test_a_second_statement_hidden_in_one_string_is_refused(db_world) -> None:
    _create(db_world)
    failed = _approved_apply(db_world, ["INSERT INTO t VALUES (1); COMMIT"])
    assert not failed.ok
    assert _fresh_rows(db_world) == []


def test_non_nesting_block_comment_variant_cannot_commit(db_world) -> None:
    """SQLite ends block comments at the first */, so this text is a syntax error,
    not a classified transaction verb. The invariant is unchanged: no persistence."""
    _create(db_world)
    failed = _approved_apply(db_world, ["/* nested /* still comment */ */ COMMIT"])
    assert not failed.ok
    assert _fresh_rows(db_world) == []


# ---------------------------------------------------------------------------
# Outcome truth
# ---------------------------------------------------------------------------


def test_successful_apply_reports_committed_and_verifies_from_a_fresh_connection(db_world) -> None:
    _create(db_world)
    ok = _approved_apply(db_world, ["INSERT INTO t VALUES (7)", "CREATE INDEX i_t ON t(n)"])
    assert ok.ok
    observation = ok.details["observation"]
    assert observation["outcome"] == "committed"
    assert observation["integrity"] == "ok"
    assert observation["verified_from"] == "fresh_connection"
    assert _fresh_rows(db_world) == [(7,)]


def test_preview_shares_the_same_policy_as_apply(db_world) -> None:
    _create(db_world)
    preview = _run(f"{DB_PLUGIN_ID}.migrate.preview", {"name": "txdb", "statements": ["INSERT INTO t VALUES (1)", "COMMIT"]})
    assert not preview.ok
    assert preview.status in {"refused_transaction_control", "refused_statement"}
    assert _fresh_rows(db_world) == []
    # And db.create shares it too.
    made = _run(
        f"{DB_PLUGIN_ID}.db.create",
        {"name": "txdb2", "schema_sql": T_DDL + "; COMMIT"},
        **_scope("tx.create2", "create_files", intents=(f"{DB_PLUGIN_ID}.db.create",)),
    )
    assert not made.ok
    assert not (databases_root(db_world) / "txdb2.sqlite").exists()


def test_outcome_vocabulary_never_labels_a_backup_as_a_rollback(db_world) -> None:
    """The failure receipt names the recovery backup AND the actual state separately."""
    _create(db_world)
    failed = _approved_apply(db_world, ["INSERT INTO t VALUES (1)", "INSERT INTO missing_table VALUES (2)"])
    observation = failed.details["observation"]
    assert observation["outcome"] == "rolled_back_verified"
    # A recovery backup exists (pre-apply preimage) but is NEVER called a rollback.
    assert observation.get("recovery_backup")
    assert Path(observation["recovery_backup"]).is_file()
    assert observation["rolled_back"] is True
    assert observation["rollback"] != "backup_only"


# ---------------------------------------------------------------------------
# Backup identity and immutability
# ---------------------------------------------------------------------------


def test_same_second_and_concurrent_auto_backups_never_collide(db_world) -> None:
    _create(db_world)
    scope = _scope("tx.apply2", "create_files", "modify_files", intents=(f"{DB_PLUGIN_ID}.migrate.apply",))
    first = _run(f"{DB_PLUGIN_ID}.migrate.apply", {"name": "txdb", "statements": ["CREATE TABLE a (x INTEGER)"]}, **scope)
    second = _run(f"{DB_PLUGIN_ID}.migrate.apply", {"name": "txdb", "statements": ["CREATE TABLE b (x INTEGER)"]}, **scope)
    assert first.ok and second.ok
    backups_one = first.details["observation"]["auto_backup"]
    backups_two = second.details["observation"]["auto_backup"]
    assert backups_one != backups_two, "same-second automatic backups must not share a name"
    assert Path(backups_one).is_file() and Path(backups_two).is_file()


def test_named_backups_are_immutable_preimages(db_world) -> None:
    _create(db_world)
    scope = _scope("tx.backup", "create_files", intents=(f"{DB_PLUGIN_ID}.backup",))
    first = _run(f"{DB_PLUGIN_ID}.backup", {"name": "txdb", "backup_name": "pin"}, **scope)
    assert first.ok
    pinned = Path(first.details["observation"]["path"])
    original = pinned.read_bytes()
    again = _run(f"{DB_PLUGIN_ID}.backup", {"name": "txdb", "backup_name": "pin"}, **scope)
    assert not again.ok
    assert again.status in {"backup_exists", "refused_immutable"}
    assert pinned.read_bytes() == original, "a named preimage must never be overwritten"


# ---------------------------------------------------------------------------
# Rollback failure preserves the original error
# ---------------------------------------------------------------------------


def test_failed_migration_error_names_the_failing_statement_and_sql(db_world) -> None:
    _create(db_world)
    failed = _approved_apply(db_world, ["INSERT INTO t VALUES (1)", "INSERT INTO missing_table VALUES (2)"])
    text = failed.response_text + str(failed.details.get("error", ""))
    assert "missing_table" in text or "no such table" in text, text[:300]
