"""Run6 defects in the database handler, each proved against the handler itself (no sandbox
needed): authorizer disarm on Python < 3.11, text limits, the only-database default."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import sqlite3
from pathlib import Path

import pytest

HANDLER = Path(__file__).resolve().parent.parent / "plugins" / "vool-database" / "bin" / "db_handler"


@pytest.fixture()
def h(tmp_path, monkeypatch):
    (tmp_path / "databases").mkdir()
    c = sqlite3.connect(tmp_path / "databases" / "orders.sqlite")
    c.executescript("create table customers(id integer primary key, name text); insert into customers values (1,'Ada');")
    c.commit(); c.close()
    loader = importlib.machinery.SourceFileLoader("db_handler_robust", str(HANDLER))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    monkeypatch.setattr(module, "scratch_root", lambda: str(tmp_path))
    return module


def _call(module, monkeypatch, intent, args):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"intent": intent, "arguments": args})))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    module.main()
    return json.loads(out.getvalue())


def test_authorizer_is_never_disarmed_with_none() -> None:
    # set_authorizer(None) only disables from Python 3.11; on 3.9/3.10 every statement, even the
    # wrapper's BEGIN, then fails "not authorized" (served run6 migrate.preview).
    assert "set_authorizer(None)" not in HANDLER.read_text(encoding="utf-8")


def test_preview_works_and_says_nothing_was_applied(h, monkeypatch) -> None:
    r = _call(h, monkeypatch, "vool-database.migrate.preview",
              {"name": "orders", "statements": ["ALTER TABLE customers ADD COLUMN email TEXT;"]})
    assert r["ok"] is True and "nothing was applied" in r["text"]


@pytest.mark.parametrize("limit", ["100", "100 rows", 100, 100.0])
def test_text_limits_are_read_as_numbers(h, monkeypatch, limit) -> None:
    r = _call(h, monkeypatch, "vool-database.query",
              {"name": "orders", "sql": "select count(*) c from customers", "row_limit": limit, "max_seconds": "5s"})
    assert r["ok"] is True and r["observation"]["rows"] == [[1]]


def test_limit_without_a_number_is_still_refused(h, monkeypatch) -> None:
    r = _call(h, monkeypatch, "vool-database.query",
              {"name": "orders", "sql": "select 1", "row_limit": "lots"})
    assert r["ok"] is False and r["status"] == "invalid_arguments"


def test_unknown_name_uses_the_only_database_and_says_so(h, monkeypatch) -> None:
    r = _call(h, monkeypatch, "vool-database.schema", {"name": "workspace"})
    assert r["ok"] is True
    assert r["observation"]["asked_database"] == "workspace" and r["observation"]["used_database"] == "orders"
    assert "only database" in r["text"]


def test_two_databases_means_no_guessing(h, monkeypatch, tmp_path) -> None:
    sqlite3.connect(tmp_path / "databases" / "notes.sqlite").close()
    r = _call(h, monkeypatch, "vool-database.schema", {"name": "workspace"})
    assert r["ok"] is False and r["observation"]["available_databases"] == ["notes", "orders"]


def test_mutating_tools_never_substitute(h, monkeypatch) -> None:
    r = _call(h, monkeypatch, "vool-database.migrate.apply",
              {"name": "workspace", "statements": ["ALTER TABLE customers ADD COLUMN x TEXT"]})
    assert r["ok"] is False and r["status"] == "unknown_database"


def test_no_such_column_names_the_columns_that_exist(h, monkeypatch) -> None:
    r = _call(h, monkeypatch, "vool-database.query", {"name": "orders", "sql": "select publication_year from customers"})
    assert r["ok"] is False and "no such column" in r["error"]
    assert "customers(id, name)" in r["error"]


def test_a_database_name_given_as_table_returns_the_whole_database(h, monkeypatch) -> None:
    r = _call(h, monkeypatch, "vool-database.schema", {"name": "orders", "table": "orders"})
    assert r["ok"] is True and r["observation"]["requested_table_not_found"] == "orders"
    assert r["observation"]["tables"]["customers"]["row_count"] == 1
    assert "no table 'orders'" in r["text"]


def test_truncated_text_says_it_is_not_a_count(h, monkeypatch) -> None:
    r = _call(h, monkeypatch, "vool-database.query", {"name": "orders", "sql": "select * from customers", "row_limit": 1})
    assert r["ok"] is True and r["observation"]["truncated"] is True and "not a count" in r["text"]


def test_a_change_sent_to_the_query_tool_points_to_the_preview(h, monkeypatch) -> None:
    r = _call(h, monkeypatch, "vool-database.query", {"name": "orders", "sql": "alter table customers add column x text"})
    assert r["ok"] is False and "migrate.preview" in r["error"] and "nothing was changed" in r["error"]


def test_the_manifest_accepts_limits_written_as_text() -> None:
    """Run14: the executor rejected row_limit='1' before the handler (which reads text numbers) ran."""
    from core.json_schema_lite import assert_supported, validate

    manifest = json.loads((HANDLER.parent.parent / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    schema = next(t for t in manifest["tools"] if t["intent"] == "vool-database.query")["input_schema"]
    assert_supported(schema)
    assert validate({"name": "orders", "sql": "select 1", "row_limit": "1", "max_seconds": "5"}, schema) == []
    assert validate({"name": "orders", "sql": "select 1", "row_limit": 2000}, schema) != []
