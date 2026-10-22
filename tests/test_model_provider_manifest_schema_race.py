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
