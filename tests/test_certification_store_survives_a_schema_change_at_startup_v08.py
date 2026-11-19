"""The first certification of a fresh home is not a 500 when another store changes the schema meanwhile.

CI #715 (v0.8 line, 3fb41dcab), tests/wallet/test_wallet_home_served.py: right after the app started, the
certification run answered 500 with "OperationalError: database schema has changed" from the table script,
while the app's other stores were still creating their tables on other connections.
"""
from __future__ import annotations

import sqlite3

import pytest

from storage import model_tool_certification_store as store


class _Conn:
    def __init__(self, failures: int, message: str = "database schema has changed") -> None:
        self.failures = failures
        self.message = message
        self.calls = 0

    def executescript(self, _sql: str) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise sqlite3.OperationalError(self.message)


def test_a_schema_change_during_the_table_script_is_retried(monkeypatch) -> None:
    monkeypatch.setattr(store.time, "sleep", lambda _s: None)
    conn = _Conn(failures=2)
    store._ensure_table(conn)
    assert conn.calls == 3


def test_a_schema_change_that_never_settles_still_fails(monkeypatch) -> None:
    monkeypatch.setattr(store.time, "sleep", lambda _s: None)
    with pytest.raises(sqlite3.OperationalError):
        store._ensure_table(_Conn(failures=99))


def test_any_other_database_error_is_not_retried() -> None:
    conn = _Conn(failures=1, message="database is locked")
    with pytest.raises(sqlite3.OperationalError):
        store._ensure_table(conn)
    assert conn.calls == 1


def test_the_real_table_is_made_in_a_fresh_database(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "fresh.sqlite")
    store._ensure_table(conn)
    store._ensure_table(conn)
    names = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
    assert "local_model_tool_certification_runs" in names
