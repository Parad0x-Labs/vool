"""The manifest table setup survives another service changing the store's schema."""

from __future__ import annotations

import sqlite3

import pytest

from storage import model_provider_manifest as manifests


def test_a_schema_change_during_table_setup_is_retried(monkeypatch):
    calls = []

    def flaky_create() -> None:
        calls.append(1)
        if len(calls) < 3:
            raise sqlite3.OperationalError("database schema has changed")

    monkeypatch.setattr(manifests, "_create_table", flaky_create)
    monkeypatch.setattr(manifests.time, "sleep", lambda _seconds: None)
    manifests._init_table()
    assert len(calls) == 3


def test_other_database_errors_are_not_retried(monkeypatch):
    calls = []

    def broken_create() -> None:
        calls.append(1)
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(manifests, "_create_table", broken_create)
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        manifests._init_table()
    assert len(calls) == 1


def test_a_schema_change_that_never_settles_still_fails(monkeypatch):
    def always_changing() -> None:
        raise sqlite3.OperationalError("database schema has changed")

    monkeypatch.setattr(manifests, "_create_table", always_changing)
    monkeypatch.setattr(manifests.time, "sleep", lambda _seconds: None)
    with pytest.raises(sqlite3.OperationalError, match="schema has changed"):
        manifests._init_table()


_PRE_RUNTIME_DEPENDENCY_TABLE = """
CREATE TABLE model_provider_manifests (
    provider_name TEXT NOT NULL, model_name TEXT NOT NULL, source_type TEXT NOT NULL, adapter_type TEXT,
    license_name TEXT, license_url_or_reference TEXT, weight_location TEXT NOT NULL DEFAULT 'external',
    redistribution_allowed INTEGER, notes TEXT NOT NULL DEFAULT '', capabilities_json TEXT NOT NULL DEFAULT '[]',
    runtime_config_json TEXT NOT NULL DEFAULT '{}', metadata_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (provider_name, model_name)
)
"""


def test_concurrent_first_reads_of_an_older_store_all_get_the_model_list(tmp_path):
    """A store from before runtime_dependency existed: every concurrent first read sees the column
    missing and tries to add it; the losers of that race got "duplicate column name" as a 500."""
    import threading

    from storage import db

    failures: list[str] = []
    try:
        for round_index in range(5):
            db.configure_default_db_path(tmp_path / f"round-{round_index}.db")
            conn = db.get_connection()
            conn.execute(_PRE_RUNTIME_DEPENDENCY_TABLE)
            conn.execute(
                "INSERT INTO model_provider_manifests (provider_name, model_name, source_type, created_at, updated_at)"
                " VALUES ('ollama', 'qwen2.5:7b', 'http', 't', 't')"
            )
            conn.commit()
            conn.close()
            barrier = threading.Barrier(8)
            names: list[list[str]] = []

            def first_read(barrier: threading.Barrier = barrier, names: list[list[str]] = names) -> None:
                barrier.wait()
                try:
                    names.append([m.model_name for m in manifests.list_provider_manifests()])
                except Exception as exc:
                    failures.append(f"{type(exc).__name__}: {exc}")

            threads = [threading.Thread(target=first_read) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            assert names == [["qwen2.5:7b"]] * (8 - len(failures)), names
    finally:
        db.configure_default_db_path(None)
    assert failures == []


def test_a_schema_change_during_a_read_is_retried(monkeypatch):
    calls = []

    def flaky_list(*, enabled_only: bool, limit: int):
        calls.append(1)
        if len(calls) < 2:
            raise sqlite3.OperationalError("database schema has changed")
        return []

    monkeypatch.setattr(manifests, "_init_table", lambda: None)
    monkeypatch.setattr(manifests, "_list", flaky_list)
    monkeypatch.setattr(manifests.time, "sleep", lambda _seconds: None)
    assert manifests.list_provider_manifests() == []
    assert len(calls) == 2
