"""A guessed database name is refused WITH the names that exist (served run4: the model called
the tool with 'workspace', got a bare refusal, and told the person there was nothing)."""
from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

HANDLER = Path(__file__).resolve().parent.parent / "plugins" / "vool-database" / "bin" / "db_handler"


def _handler(monkeypatch, root: Path):
    loader = importlib.machinery.SourceFileLoader("db_handler_under_test", str(HANDLER))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    monkeypatch.setattr(module, "scratch_root", lambda: str(root))
    return module


def test_refusal_lists_existing_databases(tmp_path, monkeypatch) -> None:
    (tmp_path / "databases").mkdir()
    (tmp_path / "databases" / "orders.sqlite").write_bytes(b"")
    (tmp_path / "databases" / "notes.sqlite").write_bytes(b"")
    fail = _handler(monkeypatch, tmp_path).unknown_database("workspace")
    assert fail.status == "unknown_database"
    assert fail.observation["available_databases"] == ["notes", "orders"]
    assert "notes, orders" in fail.error


def test_refusal_says_so_when_none_exist(tmp_path, monkeypatch) -> None:
    fail = _handler(monkeypatch, tmp_path).unknown_database("workspace")
    assert fail.observation["available_databases"] == []
    assert "no database exists yet" in fail.error
