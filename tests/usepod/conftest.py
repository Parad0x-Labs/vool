"""Fixtures for the UsePod provider tests. Local and SYNTHETIC only.

Candidate modules are imported inside fixtures, and the authority reset tolerates their absence, so this
directory can be overlaid onto the pinned base for failure-first runs without turning every test into a
fixture error.
"""
from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture(autouse=True)
def _usepod_starts_unconfigured(monkeypatch):
    """Every test starts and ends with no monetary or payment authority and no ambient UsePod token."""
    monkeypatch.delenv("VOOL_USEPOD_TOKEN", raising=False)

    def _reset() -> None:
        try:
            from core.usepod.monetary import reset_monetary_authority
            from core.usepod.transport import reset_payment_authority
        except ImportError:
            return
        reset_monetary_authority()
        reset_payment_authority()

    _reset()
    yield
    _reset()


@pytest.fixture
def usepod_home(tmp_path, monkeypatch):
    from core import runtime_paths

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    runtime_paths.configure_runtime_home(home)
    yield home
    runtime_paths.configure_runtime_home(None)


@pytest.fixture
def usepod_service():
    """A factory for started SYNTHETIC strict services; every one is stopped at teardown."""
    from tests.usepod.strict_usepod_service import StrictUsePodService

    started = []

    def make(cls=StrictUsePodService, **kwargs):
        service = cls(**kwargs).start()
        started.append(service)
        return service

    yield make
    for service in started:
        service.stop()


@pytest.fixture
def x402_journal(tmp_path):
    from core.usepod.transport import X402OperationJournal

    path = tmp_path / "x402_operations.sqlite3"

    def connect():
        conn = sqlite3.connect(str(path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    return X402OperationJournal(connection_factory=connect)
