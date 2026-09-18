"""PB06 — the vool-database plugin through the REAL plugin authority.

RED at base 3802a3f0 (both causes live-proven before the repairs):
- the pack's mutating tools could not register at all (`contract_from_tool` never
  read a manifest `mutation` block, and tool_registry refuses one without it), so
  `load_all` reported the whole pack as an error;
- the read-only tools registered but every execution answered
  `confinement_unavailable` (empty writable roots made the kernel-confinement
  prefix return None).

These tests load the pack through the existing isolated install flow, then drive
the real registry, permission gate, confined executor, receipt observation and
Blackbox coverage registry — never a mock and never the handler imported as a
module (the handler runs only as the executor's confined child).
"""
from __future__ import annotations

import json
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

        loaded, errors = plugin_tools.load_all(tmp_path)
        assert errors == (), errors
        assert [p.plugin_id for p in loaded] == [DB_PLUGIN_ID]
        yield tmp_path
    reset_mode_permission_state()
    reset_toolchain_state()


ORDERS_SCHEMA = (
    "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_email TEXT, "
    "amount_cents INTEGER, status TEXT NOT NULL)"
)
ORDERS_SEED = (
    "INSERT INTO orders (customer_email, amount_cents, status) VALUES "
    "('a@x.io', 1200, 'paid'), ('b@y.io', 300, 'open'), ('c@z.io', 9900, 'paid')"
)
WRITE_SCOPE = ("create_files", "modify_files")


def _run(intent: str, arguments: dict, session: str = "db", **context):
    from core.tool_intent_executor import execute_tool_intent

    return execute_tool_intent({"intent": intent, "arguments": arguments}, **executor_kwargs(session, **context))


def _create_orders(db_world, **extra):
    scope = internal_scope("test.db.create", "create_files", intents=(f"{DB_PLUGIN_ID}.db.create",))
    return _run(
        f"{DB_PLUGIN_ID}.db.create",
        {"name": "orders", "schema_sql": ORDERS_SCHEMA, "seed_sql": ORDERS_SEED, **extra},
        **scope,
    )


# ---------------------------------------------------------------------------
# Load and registration
# ---------------------------------------------------------------------------


def test_pack_loads_through_the_existing_discovery(db_world) -> None:
    from core.tool_registry import tool_for_intent

    expected = {
        "connect", "db.create", "schema", "query", "explain",
        "migrate.preview", "migrate.apply", "backup", "restore",
    }
    for tool in expected:
        contract = tool_for_intent(f"{DB_PLUGIN_ID}.{tool}")
        assert contract is not None, tool
        assert contract.supported is True
        assert contract.tool_surface == "plugin"


def test_mutating_tools_carry_blackbox_coverage(db_world) -> None:
    from core.blackbox.coverage import registry as coverage_registry
    from core.tool_registry import tool_for_intent

    for tool in ("db.create", "migrate.apply", "backup", "restore"):
        contract = tool_for_intent(f"{DB_PLUGIN_ID}.{tool}")
        mutation = getattr(contract, "mutation", None)
        assert isinstance(mutation, dict) and mutation, tool
        capability = coverage_registry.capability_for(f"{DB_PLUGIN_ID}.{tool}")
        assert capability is not None, tool
        assert capability.problems() == [], tool
    # The reversible ones say how their preimage is captured and how rollback works.
    apply_cap = coverage_registry.capability_for(f"{DB_PLUGIN_ID}.migrate.apply")
    assert apply_cap.snapshot_strategy == "declared_paths"
    assert apply_cap.rollback_support == "exact"


# ---------------------------------------------------------------------------
# Read-only truth
# ---------------------------------------------------------------------------


def test_connect_schema_and_query_are_read_only_and_report(db_world) -> None:
    assert _create_orders(db_world).ok
    connect = _run(f"{DB_PLUGIN_ID}.connect", {"name": "orders"})
    assert connect.ok and connect.mode == "tool_executed"
    assert connect.details["observation"]["opened_mode"] == "ro"
    assert connect.details["observation"]["credential_policy"].startswith("sqlite needs no credentials")

    schema = _run(f"{DB_PLUGIN_ID}.schema", {"name": "orders"})
    assert schema.ok
    tables = schema.details["observation"]["tables"]
    assert tables["orders"]["columns"][0]["name"] == "id"
    assert tables["orders"]["row_count"] == 3
    assert "orders.customer_email" in schema.details["observation"]["potential_pii_columns"]

    query = _run(
        f"{DB_PLUGIN_ID}.query",
        {
            "name": "orders",
            "sql": "SELECT status, COUNT(*) AS n FROM orders WHERE amount_cents > :min GROUP BY status",
            "params": {"min": 500},
        },
    )
    assert query.ok
    observation = query.details["observation"]
    assert observation["parameterized"] is True
    assert observation["rows"] == [["paid", 2]]
    assert query.details["resolved_target"] == "sqlite:orders"


def test_mutation_through_the_query_tool_is_refused(db_world) -> None:
    assert _create_orders(db_world).ok
    for sql in (
        "UPDATE orders SET status = 'x'",
        "DELETE FROM orders",
        "INSERT INTO orders (customer_email, amount_cents, status) VALUES ('z@z.io', 1, 'open')",
        "DROP TABLE orders",
        "PRAGMA writable_schema = 1",
        "ATTACH DATABASE '/tmp/x.sqlite' AS other",
        # The adversarial near-miss: SQLite accepts a CTE before UPDATE, so this
        # passes the SELECT/WITH head check — the engine-level ro mode is the layer
        # that must refuse it.
        "WITH held AS (SELECT 1) UPDATE orders SET status = 'x'",
    ):
        out = _run(f"{DB_PLUGIN_ID}.query", {"name": "orders", "sql": sql})
        assert not out.ok, sql
        assert out.status in {"refused_not_select", "refused_multi_statement", "tool_failed", "sql_error"}, (sql, out.status)
    # And the second statement of a pair never runs: one SELECT per call.
    two = _run(f"{DB_PLUGIN_ID}.query", {"name": "orders", "sql": "SELECT 1; DROP TABLE orders"})
    assert not two.ok and two.status == "refused_multi_statement"


def test_engine_level_read_only_survives_a_disguised_write(db_world) -> None:
    """A WITH-prefixed statement whose body writes is still refused by SQLite's ro mode.

    The shape guard refuses non-SELECT heads; this is the layer behind it: the
    connection itself is opened mode=ro, so even a shape the guard misjudged
    cannot write. (Sabotage finding, 2026-09-03: removing mode=ro from the
    handler is ABSORBED by the kernel confinement — a read-only child has no
    writable root, so the write dies at the kernel first. Defense in depth:
    shape guard, engine ro, kernel confinement. This test pins the middle
    layer's own contract so it cannot silently disappear behind the others.)
    """

    assert _create_orders(db_world).ok
    # The handler's read-only opens carry the engine claim in the connection URI.
    handler_source = Path("plugins/vool-database/bin/db_handler").read_text(encoding="utf-8")
    assert '"file:" + path + "?mode=ro"' in handler_source
    db_file = databases_root(db_world) / "orders.sqlite"
    conn = sqlite3.connect("file:" + str(db_file) + "?mode=ro", uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("WITH held AS (SELECT 1) UPDATE orders SET status = 'x'")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE orders SET status = 'x'")
    finally:
        conn.close()


def test_a_path_where_a_name_belongs_is_refused_before_the_handler(db_world) -> None:
    assert _create_orders(db_world).ok
    out = _run(f"{DB_PLUGIN_ID}.schema", {"name": "../../etc/passwd"})
    assert not out.ok
    # Either guard refuses before execution: the schema pattern or the x-vool-kind
    # shape guard. Both are pre-handler; neither runs the child.
    assert out.status in {"invalid_arguments", "invalid_argument_shape"}
    assert out.details["executed"] is False or "observation" not in out.details


def test_postgres_answers_its_exact_availability_boundary(db_world) -> None:
    out = _run(f"{DB_PLUGIN_ID}.connect", {"name": "orders", "engine": "postgres"})
    assert not out.ok and out.status == "engine_unavailable"
    assert "sandbox" in out.response_text.lower()
    assert "postgres" in out.response_text.lower()


# ---------------------------------------------------------------------------
# Limits and cancellation
# ---------------------------------------------------------------------------


def test_row_limit_truncates_and_says_so(db_world) -> None:
    assert _create_orders(db_world).ok
    out = _run(f"{DB_PLUGIN_ID}.query", {"name": "orders", "sql": "SELECT id FROM orders", "row_limit": 2})
    assert out.ok
    observation = out.details["observation"]
    assert observation["row_count"] == 2
    assert observation["truncated"] is True


def test_long_query_is_cancelled_at_its_deadline(db_world) -> None:
    scope = internal_scope("test.db.create", "create_files", intents=(f"{DB_PLUGIN_ID}.db.create",))
    made = _run(
        f"{DB_PLUGIN_ID}.db.create",
        {"name": "slow", "schema_sql": "CREATE TABLE t (n INTEGER PRIMARY KEY)"},
        **scope,
    )
    assert made.ok
    out = _run(
        f"{DB_PLUGIN_ID}.query",
        {
            "name": "slow",
            "sql": "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT COUNT(*) FROM c",
            "max_seconds": 2,
        },
    )
    assert out.ok  # a typed cancellation is a successful tool result, not a crash
    assert "Cancelled" in out.response_text
    assert out.details["observation"]["status_detail"] == "time_limit_cancelled"
    assert out.details["observation"]["rows_returned"] == 0
    assert out.details["observation"]["partial_rows"] is False


# ---------------------------------------------------------------------------
# Migration lifecycle
# ---------------------------------------------------------------------------


GOOD_MIGRATION = ["ALTER TABLE orders ADD COLUMN note TEXT", "CREATE INDEX idx_orders_status ON orders(status)"]
FAILING_MIGRATION = [
    "DROP TABLE orders",
    "CREATE TABLE broken (x INTEGER PRIMARY KEY, badcol TEXT NOT NULL)",
    "INSERT INTO broken (x) VALUES (1)",
]


def test_mutation_stops_at_the_permission_gate_without_authority(db_world) -> None:
    assert _create_orders(db_world).ok
    pending = _run(f"{DB_PLUGIN_ID}.migrate.apply", {"name": "orders", "statements": GOOD_MIGRATION})
    assert pending.handled and not pending.ok
    assert pending.status == "pending_approval"
    assert pending.details["executed"] is False
    assert pending.details["controller_enforced"] is True
    # The destructive unapproved operation is the failing migration: nothing ran.
    destructive = _run(f"{DB_PLUGIN_ID}.migrate.apply", {"name": "orders", "statements": FAILING_MIGRATION})
    assert destructive.status == "pending_approval"
    assert _run(f"{DB_PLUGIN_ID}.query", {"name": "orders", "sql": "SELECT COUNT(*) AS n FROM orders"}).ok
    still_there = _run(
        f"{DB_PLUGIN_ID}.query", {"name": "orders", "sql": "SELECT COUNT(*) AS n FROM orders"}
    )
    assert still_there.details["observation"]["rows"] == [[3]]


def test_preview_diffs_then_approved_apply_changes_the_real_database(db_world) -> None:
    from core.mode_permission_policy import resolve_approval

    assert _create_orders(db_world).ok
    preview = _run(f"{DB_PLUGIN_ID}.migrate.preview", {"name": "orders", "statements": GOOD_MIGRATION})
    assert preview.ok
    diff = preview.details["observation"]["diff"]
    assert "note" in json.dumps(diff["tables_changed"]["orders"])
    assert preview.details["observation"]["preview_only"] is True
    # The preview cloned: the real file gained nothing yet.
    before = _run(f"{DB_PLUGIN_ID}.schema", {"name": "orders"})
    assert "note" not in json.dumps(before.details["observation"]["tables"])

    pending = _run(f"{DB_PLUGIN_ID}.migrate.apply", {"name": "orders", "statements": GOOD_MIGRATION})
    assert pending.status == "pending_approval"
    token = str(pending.details["approval_request"]["approval_id"])
    assert resolve_approval(token, decision="allow") is not None

    applied = _run(
        f"{DB_PLUGIN_ID}.migrate.apply",
        {"name": "orders", "statements": GOOD_MIGRATION},
        mode_approval_token=token,
    )
    assert applied.ok, (applied.status, applied.response_text)
    observation = applied.details["observation"]
    assert observation["integrity"] == "ok"
    assert Path(observation["auto_backup"]).is_file()
    # Actual DB state verified through the read-only door, not the apply receipt.
    after = _run(f"{DB_PLUGIN_ID}.schema", {"name": "orders"})
    assert "note" in json.dumps(after.details["observation"]["tables"])
    indexes = after.details["observation"]["tables"]["orders"]["indexes"]
    assert any(ix["name"] == "idx_orders_status" for ix in indexes)
    # The engine truth rides the mutation receipt.
    assert observation["engine_truth"]["ddl_transactional"] is True
    assert "never rolls back a remote" in observation["engine_truth"]["remote_rollback"]


def test_failed_migration_rolls_back_and_keeps_the_recovery_backup(db_world) -> None:
    from core.mode_permission_policy import resolve_approval

    assert _create_orders(db_world).ok
    backup = _run(
        f"{DB_PLUGIN_ID}.backup",
        {"name": "orders", "backup_name": "before-bad"},
        **internal_scope("test.db.backup", "create_files", intents=(f"{DB_PLUGIN_ID}.backup",)),
    )
    assert backup.ok

    pending = _run(f"{DB_PLUGIN_ID}.migrate.apply", {"name": "orders", "statements": FAILING_MIGRATION})
    token = str(pending.details["approval_request"]["approval_id"])
    assert resolve_approval(token, decision="allow") is not None
    failed = _run(
        f"{DB_PLUGIN_ID}.migrate.apply",
        {"name": "orders", "statements": FAILING_MIGRATION},
        mode_approval_token=token,
    )
    assert not failed.ok
    assert failed.status == "apply_failed_rolled_back"
    observation = failed.details["observation"]
    assert observation["rolled_back"] is True
    # Amendment 2026-09-03: the failure receipt no longer claims an integrity result;
    # rollback truth is verified from a fresh connection against the pre-migration
    # preimage (see tests/test_vool_database_transaction_truth.py).
    assert observation["outcome"] == "rolled_back_verified"
    assert observation["rollback"] == "transaction_rollback_verified_from_fresh_connection"
    assert Path(observation["recovery_backup"]).is_file()
    # Recovery: the table survived the failed DROP because the transaction rolled back.
    survived = _run(f"{DB_PLUGIN_ID}.query", {"name": "orders", "sql": "SELECT COUNT(*) AS n FROM orders"})
    assert survived.details["observation"]["rows"] == [[3]]


def test_restore_recovers_from_a_named_backup(db_world) -> None:
    from core.mode_permission_policy import resolve_approval

    assert _create_orders(db_world).ok
    assert (
        _run(
            f"{DB_PLUGIN_ID}.backup",
            {"name": "orders", "backup_name": "checkpoint-1"},
            **internal_scope("test.db.backup2", "create_files", intents=(f"{DB_PLUGIN_ID}.backup",)),
        ).ok
    )
    pending = _run(
        f"{DB_PLUGIN_ID}.migrate.apply",
        {"name": "orders", "statements": ["DROP TABLE orders"]},
    )
    token = str(pending.details["approval_request"]["approval_id"])
    assert resolve_approval(token, decision="allow") is not None
    dropped = _run(
        f"{DB_PLUGIN_ID}.migrate.apply",
        {"name": "orders", "statements": ["DROP TABLE orders"]},
        mode_approval_token=token,
    )
    assert dropped.ok
    gone = _run(f"{DB_PLUGIN_ID}.query", {"name": "orders", "sql": "SELECT COUNT(*) AS n FROM orders"})
    assert not gone.ok and gone.status == "sql_error"
    assert "no such table" in gone.response_text

    pending_restore = _run(f"{DB_PLUGIN_ID}.restore", {"name": "orders", "backup_name": "checkpoint-1"})
    assert pending_restore.status == "pending_approval"
    token = str(pending_restore.details["approval_request"]["approval_id"])
    assert resolve_approval(token, decision="allow") is not None
    restored = _run(
        f"{DB_PLUGIN_ID}.restore",
        {"name": "orders", "backup_name": "checkpoint-1"},
        mode_approval_token=token,
    )
    assert restored.ok, (restored.status, restored.response_text)
    assert restored.details["observation"]["integrity"] == "ok"
    assert restored.details["observation"]["pre_restore_copy"]
    back = _run(f"{DB_PLUGIN_ID}.query", {"name": "orders", "sql": "SELECT COUNT(*) AS n FROM orders"})
    assert back.details["observation"]["rows"] == [[3]]


def test_preview_failure_names_the_failing_statement(db_world) -> None:
    assert _create_orders(db_world).ok
    out = _run(
        f"{DB_PLUGIN_ID}.migrate.preview",
        {"name": "orders", "statements": ["CREATE TABLE q (id INTEGER PRIMARY KEY)", "INSERT INTO orders (id) VALUES (1)"]},
    )
    assert not out.ok and out.status == "preview_failed"
    assert out.details["observation"]["failed_at"] == 2


# ---------------------------------------------------------------------------
# The pack skill and the disabled state
# ---------------------------------------------------------------------------


def test_pack_skill_parses_and_ranks_for_database_requests(db_world) -> None:
    from core.plugin_skills import load_skills, parse_skill, rank_skills

    repo_skill = Path("plugins/vool-database/skills/vool-database/SKILL.md")
    skill = parse_skill(repo_skill, plugin_id=DB_PLUGIN_ID)
    assert skill is not None
    assert skill.name == "vool-database"
    assert skill.body.startswith("# Database work through typed tools")
    assert "vool-database.query" in skill.allowed_tools

    skills = load_skills(db_world / "plugins" / DB_PLUGIN_ID, plugin_id=DB_PLUGIN_ID)
    assert [s.name for s in skills] == ["vool-database"]
    ranked = rank_skills(skills, "inspect the orders database schema and answer a data question")
    assert ranked and ranked[0].name == "vool-database"
    # A non-database request must not rank it.
    assert rank_skills(skills, "write a poem about the sea") == ()


def test_disabled_pack_explains_and_never_runs(db_world) -> None:
    from core.plugin_catalog import set_plugin_enabled
    from core.runtime_flags import override

    assert _create_orders(db_world).ok
    assert set_plugin_enabled(DB_PLUGIN_ID, False)
    from core import plugin_tools
    from core.mode_permission_policy import reset_mode_permission_state

    reset_toolchain_state()
    reset_mode_permission_state()
    with override("plugin_runtime_tools", True):
        loaded, errors = plugin_tools.load_all(db_world)
        assert errors == ()
        out = _run(f"{DB_PLUGIN_ID}.connect", {"name": "orders"})
        assert out.handled and not out.ok
        assert out.status == "disabled"
        assert DB_PLUGIN_ID in out.response_text
    reset_mode_permission_state()
    reset_toolchain_state()


def test_db_files_live_only_under_the_isolated_scratch(db_world) -> None:
    assert _create_orders(db_world).ok
    root = databases_root(db_world)
    assert (root / "orders.sqlite").is_file()
    # Nothing outside the scratch root was created for this database name.
    stray = [p for p in (db_world).rglob("orders.sqlite") if root not in p.parents]
    assert stray == []
